"""
TaxAgent — Tax optimization and tracking.

Responsibilities:
  - Section 1256 tracking (SPX options: 60% long-term, 40% short-term)
  - Flag SPY trades that would be more tax-efficient as SPX
  - Loss harvesting: identify losing positions to close before year-end
  - Wash sale monitoring: flag re-entry within 30 days of realized loss
  - Estimated tax liability per quarter
"""

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from .base import BaseAgent
from ..config import TaxCfg
from ..types import AgentResult, C


class TaxAgent(BaseAgent):
    """Tax optimization and tracking agent."""

    # Section 1256 instruments (60/40 tax treatment)
    SECTION_1256 = {"SPX", "XSP", "VIX", "RUT", "NDX", "DJX"}

    def __init__(self, config: TaxCfg = None):
        config = config or TaxCfg()
        super().__init__("Tax", config)

    def run(self, context: Dict[str, Any]) -> AgentResult:
        """Run tax analysis.

        Context keys:
            action: str — 'summary', 'harvest', 'wash_check', 'optimize'
            positions: List[Dict] — all positions (open + closed)
            closed_ytd: List[Dict] — positions closed this year
        """
        action = context.get("action", "summary")
        positions = context.get("positions", [])
        closed_ytd = context.get("closed_ytd", [])

        if action == "summary":
            return self._tax_summary(positions, closed_ytd)
        elif action == "harvest":
            return self._loss_harvest(positions)
        elif action == "wash_check":
            return self._wash_sale_check(positions, closed_ytd)
        elif action == "optimize":
            return self._optimize(positions)
        else:
            return self._result(success=False,
                                errors=[f"Unknown action: {action}"])

    def _tax_summary(self, positions: List[Dict],
                     closed: List[Dict]) -> AgentResult:
        """Compute YTD tax summary."""
        cfg = self.config

        # Separate 1256 vs non-1256
        gains_1256 = 0.0
        gains_equity = 0.0
        losses_1256 = 0.0
        losses_equity = 0.0

        for pos in closed:
            pnl = pos.get("exit_pnl", 0)
            ticker = pos.get("ticker", "").upper()
            is_1256 = ticker in self.SECTION_1256

            if pnl > 0:
                if is_1256:
                    gains_1256 += pnl
                else:
                    gains_equity += pnl
            else:
                if is_1256:
                    losses_1256 += pnl
                else:
                    losses_equity += pnl

        # 1256 treatment: 60% LT, 40% ST
        net_1256 = gains_1256 + losses_1256
        tax_1256_lt = max(0, net_1256 * cfg.section_1256_lt_pct) * cfg.lt_rate
        tax_1256_st = max(0, net_1256 * cfg.section_1256_st_pct) * cfg.st_rate
        tax_1256 = tax_1256_lt + tax_1256_st

        # Equity: all short-term (held < 1 year in 0DTE context)
        net_equity = gains_equity + losses_equity
        tax_equity = max(0, net_equity) * cfg.st_rate

        total_tax = tax_1256 + tax_equity
        total_gains = gains_1256 + gains_equity
        total_losses = losses_1256 + losses_equity
        net = total_gains + total_losses

        # Tax savings from using SPX vs SPY
        # If all equity gains had been 1256 instead
        hypothetical_1256_tax = (
            max(0, net_equity * cfg.section_1256_lt_pct) * cfg.lt_rate +
            max(0, net_equity * cfg.section_1256_st_pct) * cfg.st_rate
        )
        tax_savings_if_spx = tax_equity - hypothetical_1256_tax

        return self._result(
            success=True,
            data={
                "ytd_gains": round(total_gains, 2),
                "ytd_losses": round(total_losses, 2),
                "ytd_net": round(net, 2),
                "gains_1256": round(gains_1256, 2),
                "gains_equity": round(gains_equity, 2),
                "losses_1256": round(losses_1256, 2),
                "losses_equity": round(losses_equity, 2),
                "tax_1256": round(tax_1256, 2),
                "tax_equity": round(tax_equity, 2),
                "total_tax_est": round(total_tax, 2),
                "effective_rate": round(total_tax / net * 100, 1) if net > 0 else 0,
                "spx_savings_potential": round(tax_savings_if_spx, 2),
            },
        )

    def _loss_harvest(self, positions: List[Dict]) -> AgentResult:
        """Identify open losing positions to harvest before year-end."""
        cfg = self.config
        today = date.today()

        candidates = []
        for pos in positions:
            if pos.get("status") != "OPEN":
                continue
            unrealized = pos.get("unrealized_pnl", 0)
            if unrealized < cfg.harvest_threshold:
                candidates.append({
                    "ticker": pos.get("ticker"),
                    "structure": pos.get("structure"),
                    "unrealized": round(unrealized, 2),
                    "entry_date": pos.get("entry_date"),
                    "is_1256": pos.get("ticker", "").upper() in self.SECTION_1256,
                })

        candidates.sort(key=lambda x: x["unrealized"])

        total_harvestable = sum(c["unrealized"] for c in candidates)
        tax_benefit = abs(total_harvestable) * cfg.st_rate

        return self._result(
            success=True,
            data={
                "candidates": candidates,
                "count": len(candidates),
                "total_harvestable": round(total_harvestable, 2),
                "tax_benefit_est": round(tax_benefit, 2),
            },
        )

    def _wash_sale_check(self, positions: List[Dict],
                         closed: List[Dict]) -> AgentResult:
        """Check for potential wash sale violations."""
        cfg = self.config
        today = date.today()
        violations = []

        # Get recently closed losses
        recent_losses = []
        for pos in closed:
            pnl = pos.get("exit_pnl", 0)
            if pnl >= 0:
                continue
            exit_date_str = pos.get("exit_date", "")
            try:
                exit_date = datetime.strptime(
                    exit_date_str[:10], "%Y-%m-%d").date()
            except (ValueError, TypeError):
                continue
            days_since = (today - exit_date).days
            if days_since <= cfg.wash_sale_days:
                recent_losses.append({
                    "ticker": pos.get("ticker"),
                    "exit_date": exit_date_str,
                    "pnl": pnl,
                    "days_since": days_since,
                    "safe_date": (exit_date + timedelta(
                        days=cfg.wash_sale_days + 1)).strftime("%Y-%m-%d"),
                })

        # Check if any open positions match recent losses
        for loss in recent_losses:
            ticker = loss["ticker"]
            matching_open = [
                p for p in positions
                if p.get("status") == "OPEN"
                and p.get("ticker") == ticker
            ]
            if matching_open:
                violations.append({
                    **loss,
                    "open_position_count": len(matching_open),
                    "warning": f"Wash sale risk: {ticker} loss "
                               f"${loss['pnl']:,.0f} closed "
                               f"{loss['days_since']}d ago, "
                               f"re-entered. Safe after "
                               f"{loss['safe_date']}",
                })

        return self._result(
            success=True,
            data={
                "violations": violations,
                "recent_losses": recent_losses,
                "count": len(violations),
            },
        )

    def _optimize(self, positions: List[Dict]) -> AgentResult:
        """Suggest tax optimization moves."""
        suggestions = []

        for pos in positions:
            if pos.get("status") != "OPEN":
                continue
            ticker = pos.get("ticker", "").upper()

            # Suggest SPX over SPY for new trades
            if ticker == "SPY":
                suggestions.append({
                    "ticker": ticker,
                    "suggestion": "Consider SPX instead of SPY for 60/40 "
                                  "tax treatment (Section 1256)",
                    "impact": "saves ~17% on gains vs ordinary income rate",
                })

        return self._result(
            success=True,
            data={"suggestions": suggestions},
        )

    def print_summary(self, data: Dict) -> None:
        """Pretty-print tax summary."""
        print(f"\n{C.BOLD}{C.CYAN}{'=' * 60}{C.RESET}")
        print(f"  {C.BOLD}TAX SUMMARY — YTD{C.RESET}")
        print(f"{C.BOLD}{C.CYAN}{'=' * 60}{C.RESET}")

        g = data
        print(f"\n  Gains:  ${g['ytd_gains']:>+10,.0f}")
        print(f"  Losses: ${g['ytd_losses']:>+10,.0f}")
        print(f"  Net:    ${g['ytd_net']:>+10,.0f}")

        print(f"\n  {C.BOLD}Section 1256 (SPX/VIX):{C.RESET}")
        print(f"    Gains:  ${g['gains_1256']:>+10,.0f}")
        print(f"    Losses: ${g['losses_1256']:>+10,.0f}")
        print(f"    Tax:    ${g['tax_1256']:>10,.0f} (60/40 treatment)")

        print(f"\n  {C.BOLD}Equity Options (SPY):{C.RESET}")
        print(f"    Gains:  ${g['gains_equity']:>+10,.0f}")
        print(f"    Losses: ${g['losses_equity']:>+10,.0f}")
        print(f"    Tax:    ${g['tax_equity']:>10,.0f} (ordinary income)")

        print(f"\n  {C.BOLD}Total estimated tax: ${g['total_tax_est']:,.0f}{C.RESET}")
        print(f"  Effective rate: {g['effective_rate']:.1f}%")

        if g["spx_savings_potential"] > 0:
            print(f"\n  {C.GREEN}SPX migration savings: "
                  f"${g['spx_savings_potential']:,.0f}/year{C.RESET}")
        print()
