"""
TreasuryAgent — Idle cash management.

Responsibilities:
  - Track idle cash (capital not deployed in any strategy)
  - Recommend T-bill / money market allocation
  - Calculate yield on idle capital (~5% annualized)
  - Suggest T-bill ladder for liquidity management
  - Ensure minimum cash reserve for trade entries
"""

from datetime import date, timedelta
from typing import Any, Dict, List

from .base import BaseAgent
from ..config import TreasuryCfg
from ..types import AgentResult, C


class TreasuryAgent(BaseAgent):
    """Idle cash and treasury management agent."""

    def __init__(self, config: TreasuryCfg = None):
        config = config or TreasuryCfg()
        super().__init__("Treasury", config)

    def run(self, context: Dict[str, Any]) -> AgentResult:
        """Compute treasury allocation.

        Context keys:
            account_capital: float
            deployed: float — total currently deployed
            positions: List[Dict] — open positions (for upcoming expirations)
        """
        capital = context.get("account_capital", 250000.0)
        deployed = context.get("deployed", 0.0)
        positions = context.get("positions", [])

        idle = capital - deployed
        cfg = self.config

        # Minimum cash reserve (always liquid)
        min_reserve = capital * cfg.min_cash_reserve_pct

        # Available for T-bills
        available_for_tbills = max(0, idle - min_reserve)

        # Calculate upcoming capital needs (positions expiring soon)
        upcoming_needs = self._upcoming_needs(positions)

        # Reduce T-bill allocation for upcoming needs
        tbill_allocation = max(0, available_for_tbills - upcoming_needs)

        # T-bill ladder suggestion
        ladder = self._suggest_ladder(tbill_allocation)

        # Annual yield projection
        annual_yield = tbill_allocation * cfg.tbill_yield
        monthly_yield = annual_yield / 12

        return self._result(
            success=True,
            data={
                "idle_cash": round(idle, 2),
                "min_reserve": round(min_reserve, 2),
                "available_for_tbills": round(available_for_tbills, 2),
                "upcoming_needs": round(upcoming_needs, 2),
                "tbill_allocation": round(tbill_allocation, 2),
                "annual_yield": round(annual_yield, 2),
                "monthly_yield": round(monthly_yield, 2),
                "yield_rate": cfg.tbill_yield,
                "ladder": ladder,
            },
            messages=[
                f"Idle cash: ${idle:,.0f}",
                f"T-bill allocation: ${tbill_allocation:,.0f} "
                f"(~${annual_yield:,.0f}/yr at {cfg.tbill_yield:.1%})",
            ],
        )

    def _upcoming_needs(self, positions: List[Dict]) -> float:
        """Estimate capital needed for upcoming expirations/rolls."""
        today = date.today()
        needs = 0.0

        for pos in positions:
            if pos.get("status") != "OPEN":
                continue
            # Check if position needs capital in next 2 weeks
            expiry = pos.get("expiry", pos.get("exit_date", ""))
            try:
                from datetime import datetime
                exp_date = datetime.strptime(str(expiry)[:10], "%Y-%m-%d").date()
                days_to_expiry = (exp_date - today).days
                if 0 < days_to_expiry <= 14:
                    needs += pos.get("max_risk", 0)
            except (ValueError, TypeError):
                pass

        return needs

    def _suggest_ladder(self, total: float) -> List[Dict]:
        """Suggest a T-bill maturity ladder."""
        cfg = self.config
        if total <= 0:
            return []

        today = date.today()
        n_rungs = len(cfg.ladder_intervals)
        per_rung = total / n_rungs

        ladder = []
        for weeks in cfg.ladder_intervals:
            maturity = today + timedelta(weeks=weeks)
            ladder.append({
                "maturity_weeks": weeks,
                "maturity_date": maturity.strftime("%Y-%m-%d"),
                "amount": round(per_rung, 2),
                "yield_at_maturity": round(
                    per_rung * cfg.tbill_yield * weeks / 52, 2),
            })

        return ladder

    def print_status(self, data: Dict) -> None:
        """Pretty-print treasury status."""
        print(f"\n{C.BOLD}{C.CYAN}{'=' * 60}{C.RESET}")
        print(f"  {C.BOLD}TREASURY / IDLE CASH{C.RESET}")
        print(f"{C.BOLD}{C.CYAN}{'=' * 60}{C.RESET}")

        print(f"\n  Idle cash:         ${data['idle_cash']:>10,.0f}")
        print(f"  Min reserve:       ${data['min_reserve']:>10,.0f}")
        print(f"  Upcoming needs:    ${data['upcoming_needs']:>10,.0f}")
        print(f"  T-bill allocation: ${data['tbill_allocation']:>10,.0f}")
        print(f"  Annual yield:      ${data['annual_yield']:>10,.0f} "
              f"({data['yield_rate']:.1%})")
        print(f"  Monthly yield:     ${data['monthly_yield']:>10,.0f}")

        ladder = data.get("ladder", [])
        if ladder:
            print(f"\n  {C.BOLD}T-bill Ladder:{C.RESET}")
            for rung in ladder:
                print(f"    {rung['maturity_weeks']:>2}w "
                      f"({rung['maturity_date']}) "
                      f"${rung['amount']:>10,.0f} "
                      f"-> ${rung['yield_at_maturity']:>6,.0f} yield")
        print()
