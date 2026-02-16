"""
MarginAgent — Margin calculation and buying power tracking.

Responsibilities:
  - SPAN margin estimation for spreads
  - Portfolio margin (PM) vs Reg-T calculation
  - Buying power utilization tracking
  - Margin efficiency scoring per trade (return / margin)
  - Alerts when approaching margin limits
"""

from typing import Any, Dict, List, Optional

from .base import BaseAgent
from ..config import MarginCfg
from ..types import AgentResult, C


class MarginAgent(BaseAgent):
    """Margin calculation and buying power management."""

    def __init__(self, config: MarginCfg = None):
        config = config or MarginCfg()
        super().__init__("Margin", config)

    def run(self, context: Dict[str, Any]) -> AgentResult:
        """Compute margin status.

        Context keys:
            positions: List[Dict] — open positions
            account_capital: float — total account value
            action: str — 'status', 'check_trade', 'efficiency'
            proposed_trade: Dict (for check_trade)
        """
        action = context.get("action", "status")
        positions = context.get("positions", [])
        capital = context.get("account_capital", 250000.0)

        if action == "status":
            return self._margin_status(positions, capital)
        elif action == "check_trade":
            return self._check_trade(
                positions, capital, context.get("proposed_trade", {}))
        elif action == "efficiency":
            return self._efficiency_report(positions)
        else:
            return self._result(success=False,
                                errors=[f"Unknown action: {action}"])

    def _margin_status(self, positions: List[Dict],
                       capital: float) -> AgentResult:
        """Compute overall margin utilization."""
        cfg = self.config
        total_margin = 0.0
        position_details = []

        for pos in positions:
            if pos.get("status") != "OPEN":
                continue
            margin = self._compute_margin(pos)
            total_margin += margin
            position_details.append({
                "ticker": pos.get("ticker"),
                "structure": pos.get("structure", pos.get("type")),
                "margin_req": round(margin, 2),
            })

        utilization = total_margin / capital if capital > 0 else 0
        available = capital - total_margin

        # Determine status
        if utilization >= cfg.buying_power_max:
            status = "CRITICAL"
        elif utilization >= cfg.buying_power_warning:
            status = "WARNING"
        else:
            status = "OK"

        return self._result(
            success=True,
            data={
                "total_margin": round(total_margin, 2),
                "available_bp": round(available, 2),
                "utilization_pct": round(utilization * 100, 1),
                "status": status,
                "margin_type": "PM" if cfg.portfolio_margin else "Reg-T",
                "positions": position_details,
            },
        )

    def _compute_margin(self, position: Dict) -> float:
        """Estimate margin requirement for a position."""
        cfg = self.config
        structure = position.get("structure", position.get("type", ""))
        max_risk = position.get("max_risk", 0)
        qty = position.get("qty", 1)
        width = position.get("width", 0)

        # Spreads: PM uses fraction of notional, Reg-T uses full width
        if "spread" in structure.lower() or "condor" in structure.lower():
            if cfg.portfolio_margin:
                return width * 100 * qty * cfg.pm_spread_margin_pct
            else:
                return width * 100 * qty * cfg.reg_t_spread_margin_pct

        # Naked or complex: use max_risk as margin estimate
        return max_risk

    def _check_trade(self, positions: List[Dict], capital: float,
                     trade: Dict) -> AgentResult:
        """Check if a proposed trade fits within margin limits."""
        cfg = self.config

        # Current margin usage
        current_margin = sum(
            self._compute_margin(p) for p in positions
            if p.get("status") == "OPEN"
        )

        # Proposed trade margin
        trade_margin = self._compute_margin(trade)
        new_total = current_margin + trade_margin
        new_util = new_total / capital if capital > 0 else 1.0

        approved = new_util < cfg.buying_power_max

        return self._result(
            success=approved,
            data={
                "current_margin": round(current_margin, 2),
                "trade_margin": round(trade_margin, 2),
                "new_total": round(new_total, 2),
                "new_utilization_pct": round(new_util * 100, 1),
                "approved": approved,
            },
            messages=[
                f"Trade margin: ${trade_margin:,.0f}",
                f"New utilization: {new_util:.1%} "
                f"({'OK' if approved else 'EXCEEDS LIMIT'})",
            ],
        )

    def _efficiency_report(self, positions: List[Dict]) -> AgentResult:
        """Score each position by return per margin dollar."""
        scores = []

        for pos in positions:
            if pos.get("status") != "OPEN":
                continue
            margin = self._compute_margin(pos)
            pnl = pos.get("unrealized_pnl", 0)

            if margin > 0:
                efficiency = pnl / margin
            else:
                efficiency = 0

            scores.append({
                "ticker": pos.get("ticker"),
                "structure": pos.get("structure", pos.get("type")),
                "margin": round(margin, 2),
                "pnl": round(pnl, 2),
                "efficiency": round(efficiency, 4),
            })

        scores.sort(key=lambda x: x["efficiency"], reverse=True)

        return self._result(
            success=True,
            data={"scores": scores},
        )

    def print_status(self, data: Dict) -> None:
        """Pretty-print margin status."""
        print(f"\n{C.BOLD}{C.CYAN}{'=' * 60}{C.RESET}")
        print(f"  {C.BOLD}MARGIN STATUS ({data['margin_type']}){C.RESET}")
        print(f"{C.BOLD}{C.CYAN}{'=' * 60}{C.RESET}")

        util = data["utilization_pct"]
        clr = (C.RED if data["status"] == "CRITICAL"
               else C.YELLOW if data["status"] == "WARNING"
               else C.GREEN)

        bar_len = int(util / 5)
        bar = "#" * bar_len + "." * (20 - bar_len)

        print(f"\n  [{bar}] {clr}{util:.1f}% utilized{C.RESET}")
        print(f"  Total margin:   ${data['total_margin']:>10,.0f}")
        print(f"  Available BP:   ${data['available_bp']:>10,.0f}")
        print()
