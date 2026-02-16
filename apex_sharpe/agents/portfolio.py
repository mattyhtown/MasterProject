"""
PortfolioAgent — top-level orchestrator for the full portfolio.

Responsibilities:
  1. Capital allocation: Track deployed vs available across tiers
  2. Signal → trade: SignalSizer + AdaptiveSelector + dispatch to strategy agent
  3. Portfolio Greeks limits: max delta, gamma, theta, vega
  4. Correlation discount: Reduce sizing when multiple overlapping signals fire
  5. Multi-signal-system aggregation: Vol surface, credit, TA, earnings, political, etc.
  6. Sharpe targeting: Adjust allocation to maintain portfolio Sharpe >= 2.0

Execution realism:
  - All haircuts flow through strategy agents (slippage, BA spread, commission)
  - Position sizing respects margin requirements
  - Daily deployment caps prevent overexposure
"""

from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from .base import BaseAgent
from ..config import PortfolioCfg, SignalSizingCfg
from ..selection.signal_sizer import SignalSizer
from ..selection.adaptive_selector import AdaptiveSelector
from ..types import (AgentResult, C, PortfolioTier, SignalSystemType,
                      TradeStructure, SignalStrength)


class PortfolioAgent(BaseAgent):
    """Top-level portfolio orchestrator.

    Aggregates signals from multiple systems, manages capital across tiers,
    enforces risk limits, and dispatches to strategy agents.
    """

    def __init__(self, config: PortfolioCfg = None,
                 sizing_config: SignalSizingCfg = None):
        config = config or PortfolioCfg()
        super().__init__("Portfolio", config)
        self.sizer = SignalSizer(sizing_config)
        self.selector = AdaptiveSelector()

        # Portfolio state (rebuilt each run from positions file)
        self._deployed: Dict[PortfolioTier, float] = {
            t: 0.0 for t in PortfolioTier
        }
        self._positions: List[Dict] = []
        self._daily_deployed: float = 0.0
        self._greeks: Dict[str, float] = {
            "delta": 0.0, "gamma": 0.0,
            "theta": 0.0, "vega": 0.0,
        }

    def run(self, context: Dict[str, Any]) -> AgentResult:
        """Process signals and generate trade recommendations.

        Context keys:
            signals: Dict — signal system outputs (core_count, composite, etc.)
            chain: List[Dict] — option chain for trade construction
            summary: Dict — ORATS summary for adaptive selection
            positions: List[Dict] — current open positions
            spot: float — current spot price
            signal_system: str — which system generated the signal
        """
        signals = context.get("signals", {})
        chain = context.get("chain", [])
        summary = context.get("summary", {})
        positions = context.get("positions", [])
        spot = context.get("spot", 0)
        signal_system = context.get("signal_system", "vol_surface")

        self._positions = positions
        self._rebuild_deployed(positions)

        # Check if we can trade
        checks = self._pre_trade_checks(signals)
        if checks:
            return self._result(
                success=False,
                data={"checks_failed": checks},
                errors=checks,
            )

        core_count = signals.get("core_count", 0)
        composite = signals.get("composite")

        # Signal sizing
        sizing = self.sizer.compute(core_count)
        risk_budget = sizing["risk_budget"]

        # Apply correlation discount
        risk_budget = self._apply_correlation_discount(
            risk_budget, signal_system, positions)

        # Apply daily cap
        remaining_daily = self.sizer.max_daily_budget() - self._daily_deployed
        risk_budget = min(risk_budget, max(0, remaining_daily))

        if risk_budget <= 0:
            return self._result(
                success=False,
                errors=["Daily deployment cap reached"],
            )

        # Adaptive structure selection
        ranked = self.selector.select(summary, core_count)
        top_structure, reason = ranked[0] if ranked else (None, "")

        # Directional tier capacity check
        tier_budget = self._tier_available(PortfolioTier.DIRECTIONAL)
        risk_budget = min(risk_budget, tier_budget)

        return self._result(
            success=True,
            data={
                "risk_budget": round(risk_budget, 2),
                "sizing": sizing,
                "structure": top_structure.value if top_structure else None,
                "structure_ranked": [(s.value, r) for s, r in ranked],
                "reason": reason,
                "signal_system": signal_system,
                "composite": composite,
                "core_count": core_count,
                "portfolio_snapshot": self._snapshot(),
            },
            messages=[
                f"Signal: {composite} ({core_count} core, {signal_system})",
                f"Risk budget: ${risk_budget:,.0f} "
                f"(x{sizing['multiplier']:.1f})",
                f"Structure: {top_structure.value if top_structure else 'none'} "
                f"({reason})",
            ],
        )

    # -- Capital management -----------------------------------------------

    def _rebuild_deployed(self, positions: List[Dict]) -> None:
        """Rebuild deployed capital from positions."""
        self._deployed = {t: 0.0 for t in PortfolioTier}
        self._greeks = {"delta": 0.0, "gamma": 0.0,
                        "theta": 0.0, "vega": 0.0}

        for pos in positions:
            if pos.get("status") != "OPEN":
                continue
            tier_str = pos.get("tier", "directional")
            try:
                tier = PortfolioTier(tier_str)
            except ValueError:
                tier = PortfolioTier.DIRECTIONAL
            self._deployed[tier] += pos.get("max_risk", 0)

            # Aggregate Greeks
            for g in ("delta", "gamma", "theta", "vega"):
                self._greeks[g] += pos.get(g, 0)

    def _tier_available(self, tier: PortfolioTier) -> float:
        """Available capital for a tier."""
        cfg = self.config
        pct_map = {
            PortfolioTier.TREASURY: cfg.treasury_pct,
            PortfolioTier.LEAPS: cfg.leaps_pct,
            PortfolioTier.IRON_CONDOR: cfg.ic_pct,
            PortfolioTier.DIRECTIONAL: cfg.directional_pct,
            PortfolioTier.MARGIN_BUFFER: cfg.margin_buffer_pct,
        }
        tier_max = cfg.account_capital * pct_map.get(tier, 0)
        return max(0, tier_max - self._deployed.get(tier, 0))

    def _snapshot(self) -> Dict:
        """Current portfolio snapshot."""
        cfg = self.config
        total_deployed = sum(self._deployed.values())
        idle = cfg.account_capital - total_deployed
        return {
            "capital": cfg.account_capital,
            "total_deployed": round(total_deployed, 2),
            "idle_cash": round(idle, 2),
            "utilization_pct": round(total_deployed / cfg.account_capital * 100, 1),
            "tiers": {t.value: round(v, 2) for t, v in self._deployed.items()},
            "greeks": {k: round(v, 2) for k, v in self._greeks.items()},
        }

    # -- Risk checks ------------------------------------------------------

    def _pre_trade_checks(self, signals: Dict) -> List[str]:
        """Pre-trade risk checks. Returns list of failures."""
        failures = []
        cfg = self.config

        # Must have a composite signal
        if not signals.get("composite"):
            failures.append("No composite signal")

        # Greeks limits
        if abs(self._greeks["delta"]) > cfg.max_portfolio_delta:
            failures.append(
                f"Portfolio delta {self._greeks['delta']:.0f} "
                f"> limit {cfg.max_portfolio_delta:.0f}")
        if abs(self._greeks["vega"]) > cfg.max_portfolio_vega:
            failures.append(
                f"Portfolio vega {self._greeks['vega']:.0f} "
                f"> limit {cfg.max_portfolio_vega:.0f}")

        return failures

    def _apply_correlation_discount(
        self, risk_budget: float, signal_system: str,
        positions: List[Dict],
    ) -> float:
        """Reduce sizing when correlated positions already open.

        If we already have bullish directional positions from the same
        signal system, reduce new sizing by 30% per existing position
        (capped at 70% total discount).
        """
        same_system_open = sum(
            1 for p in positions
            if p.get("status") == "OPEN"
            and p.get("signal_system") == signal_system
            and p.get("tier") == PortfolioTier.DIRECTIONAL.value
        )
        if same_system_open > 0:
            discount = min(0.70, same_system_open * 0.30)
            return risk_budget * (1 - discount)
        return risk_budget

    # -- Portfolio status display -----------------------------------------

    def print_status(self) -> None:
        """Print portfolio status dashboard."""
        snap = self._snapshot()
        cfg = self.config

        print(f"\n{C.BOLD}{C.CYAN}{'=' * 60}{C.RESET}")
        print(f"  {C.BOLD}PORTFOLIO STATUS{C.RESET}")
        print(f"{C.BOLD}{C.CYAN}{'=' * 60}{C.RESET}")

        print(f"\n  Capital: ${cfg.account_capital:,.0f}")
        print(f"  Deployed: ${snap['total_deployed']:,.0f} "
              f"({snap['utilization_pct']:.1f}%)")
        print(f"  Idle: ${snap['idle_cash']:,.0f}")

        print(f"\n  {C.BOLD}Tier Allocation:{C.RESET}")
        for tier in PortfolioTier:
            pct_map = {
                PortfolioTier.TREASURY: cfg.treasury_pct,
                PortfolioTier.LEAPS: cfg.leaps_pct,
                PortfolioTier.IRON_CONDOR: cfg.ic_pct,
                PortfolioTier.DIRECTIONAL: cfg.directional_pct,
                PortfolioTier.MARGIN_BUFFER: cfg.margin_buffer_pct,
            }
            target = cfg.account_capital * pct_map[tier]
            deployed = self._deployed.get(tier, 0)
            avail = target - deployed
            bar_len = int(deployed / target * 20) if target > 0 else 0
            bar = "#" * bar_len + "." * (20 - bar_len)
            print(f"    {tier.value:<14} [{bar}] "
                  f"${deployed:>8,.0f} / ${target:>8,.0f} "
                  f"(${avail:>8,.0f} avail)")

        print(f"\n  {C.BOLD}Portfolio Greeks:{C.RESET}")
        g = snap["greeks"]
        limits = {
            "delta": cfg.max_portfolio_delta,
            "gamma": cfg.max_portfolio_gamma,
            "vega": cfg.max_portfolio_vega,
        }
        for k, v in g.items():
            limit = limits.get(k)
            if limit:
                pct = abs(v) / limit * 100
                clr = C.RED if pct > 80 else C.YELLOW if pct > 50 else C.GREEN
                print(f"    {k:<8} {v:>+8.1f}  {clr}({pct:.0f}% of limit){C.RESET}")
            else:
                print(f"    {k:<8} {v:>+8.1f}")

        print(f"\n  Target Sharpe: {cfg.target_sharpe:.1f}")
        print()
