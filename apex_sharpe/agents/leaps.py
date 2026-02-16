"""
LEAPSAgent — Poor Man's Covered Call (PMCC) management.

Strategy:
  - Buy deep ITM LEAPS calls (0.70 delta, 9-18 month expiry)
  - Sell OTM calls against them (0.30 delta, 30-45 DTE)
  - Roll short leg at 50% profit, 21 DTE, or delta > 0.50
  - Roll LEAPS when DTE < 180 days

Capital efficiency:
  - Controls ~$150K of SPY for ~$50K (3x leverage vs shares)
  - Monthly income from short premium: ~$200-400/contract
  - Downside: LEAPS loses value if underlying drops significantly

Actions:
  - scan: Find new LEAPS entry opportunities
  - roll_short: Roll short leg (profit target, DTE, delta)
  - roll_leaps: Roll LEAPS to further expiry
  - status: Current PMCC positions with Greeks and P&L
"""

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from .base import BaseAgent
from ..config import LEAPSCfg
from ..types import AgentResult, C


class LEAPSAgent(BaseAgent):
    """LEAPS / Poor Man's Covered Call management agent."""

    def __init__(self, config: LEAPSCfg = None):
        config = config or LEAPSCfg()
        super().__init__("LEAPS", config)

    def run(self, context: Dict[str, Any]) -> AgentResult:
        """Execute LEAPS action.

        Context keys:
            action: str — 'scan', 'roll_short', 'roll_leaps', 'status'
            orats: ORATSClient
            positions: List[Dict] — current positions
            ticker: str (default 'SPY')
        """
        action = context.get("action", "scan")
        orats = context["orats"]
        positions = context.get("positions", [])
        ticker = context.get("ticker", "SPY")

        if action == "scan":
            return self._scan_entry(orats, ticker, positions)
        elif action == "roll_short":
            return self._check_short_rolls(orats, positions)
        elif action == "roll_leaps":
            return self._check_leaps_rolls(orats, positions)
        elif action == "status":
            return self._status(orats, positions)
        else:
            return self._result(success=False,
                                errors=[f"Unknown action: {action}"])

    # -- Scan for LEAPS entry --------------------------------------------

    def _scan_entry(self, orats, ticker: str,
                    positions: List[Dict]) -> AgentResult:
        """Find LEAPS entry opportunity."""
        cfg = self.config

        # Check if we already have a LEAPS position
        leaps_open = [p for p in positions
                      if p.get("tier") == "leaps"
                      and p.get("status") == "OPEN"]
        if leaps_open:
            return self._result(
                success=False,
                messages=["LEAPS position already open"],
                data={"existing": leaps_open},
            )

        # Get expirations
        resp = orats.expirations(ticker)
        if not resp or not resp.get("data"):
            return self._result(success=False,
                                errors=["Failed to fetch expirations"])

        expirations = resp["data"]
        today = date.today()

        # Find LEAPS expiry (9-18 months out)
        leaps_expiry = None
        for exp in expirations:
            try:
                exp_str = str(exp) if isinstance(exp, str) else exp.get("expirDate", "")
                exp_date = datetime.strptime(exp_str[:10], "%Y-%m-%d").date()
            except (ValueError, TypeError, AttributeError):
                continue
            dte = (exp_date - today).days
            if cfg.min_dte <= dte <= cfg.max_dte:
                leaps_expiry = exp_str[:10]
                leaps_dte = dte
                break

        if not leaps_expiry:
            return self._result(success=False,
                                errors=["No LEAPS expiry in range"])

        # Fetch chain for LEAPS expiry
        chain_resp = orats.chain(ticker, leaps_expiry)
        if not chain_resp or not chain_resp.get("data"):
            return self._result(success=False,
                                errors=["Failed to fetch LEAPS chain"])

        chain = [s for s in chain_resp["data"]
                 if s.get("expirDate") == leaps_expiry]

        # Find deep ITM call (~0.70 delta)
        leaps_call = self._find_call(chain, cfg.target_delta, 0.10)
        if not leaps_call:
            return self._result(success=False,
                                errors=["No suitable LEAPS call found"])

        # Find short call expiry (30-45 DTE)
        short_expiry = None
        for exp in expirations:
            try:
                exp_str = str(exp) if isinstance(exp, str) else exp.get("expirDate", "")
                exp_date = datetime.strptime(exp_str[:10], "%Y-%m-%d").date()
            except (ValueError, TypeError, AttributeError):
                continue
            dte = (exp_date - today).days
            if cfg.short_dte_min <= dte <= cfg.short_dte_max:
                short_expiry = exp_str[:10]
                short_dte = dte
                break

        short_call = None
        if short_expiry:
            short_resp = orats.chain(ticker, short_expiry)
            if short_resp and short_resp.get("data"):
                short_chain = [s for s in short_resp["data"]
                               if s.get("expirDate") == short_expiry]
                short_call = self._find_call(
                    short_chain, cfg.short_delta, 0.05)

        # Build recommendation
        leaps_cost = leaps_call.get("callAskPrice", 0) * 100
        short_premium = (short_call.get("callBidPrice", 0) * 100
                         if short_call else 0)

        return self._result(
            success=True,
            data={
                "action": "scan",
                "ticker": ticker,
                "leaps": {
                    "expiry": leaps_expiry,
                    "dte": leaps_dte,
                    "strike": leaps_call["strike"],
                    "delta": leaps_call.get("delta", 0),
                    "ask": leaps_call.get("callAskPrice", 0),
                    "cost": round(leaps_cost, 2),
                    "iv": leaps_call.get("smvVol", 0),
                },
                "short": {
                    "expiry": short_expiry,
                    "dte": short_dte if short_expiry else 0,
                    "strike": short_call["strike"] if short_call else 0,
                    "delta": short_call.get("delta", 0) if short_call else 0,
                    "bid": short_call.get("callBidPrice", 0) if short_call else 0,
                    "premium": round(short_premium, 2),
                } if short_call else None,
                "net_cost": round(leaps_cost - short_premium, 2),
                "monthly_income_est": round(short_premium, 2),
            },
            messages=[
                f"LEAPS: {ticker} {leaps_expiry} ${leaps_call['strike']:.0f}C "
                f"(Δ{leaps_call.get('delta', 0):.2f}) @ ${leaps_cost:,.0f}",
                f"Short: {short_expiry} ${short_call['strike']:.0f}C "
                f"(Δ{short_call.get('delta', 0):.2f}) @ ${short_premium:,.0f}/mo"
                if short_call else "No short leg available",
            ],
        )

    # -- Roll checks -----------------------------------------------------

    def _check_short_rolls(self, orats,
                           positions: List[Dict]) -> AgentResult:
        """Check if any short legs need rolling."""
        cfg = self.config
        rolls = []

        leaps_positions = [p for p in positions
                           if p.get("tier") == "leaps"
                           and p.get("status") == "OPEN"]

        for pos in leaps_positions:
            short_leg = pos.get("short_leg", {})
            if not short_leg:
                continue

            today = date.today()
            try:
                exp_date = datetime.strptime(
                    short_leg.get("expiry", ""), "%Y-%m-%d").date()
            except (ValueError, TypeError):
                continue

            dte = (exp_date - today).days
            current_delta = short_leg.get("current_delta", 0)
            entry_credit = short_leg.get("entry_credit", 0)
            current_value = short_leg.get("current_value", 0)

            roll_reason = None
            if entry_credit > 0 and current_value <= entry_credit * (1 - cfg.short_roll_profit_pct):
                roll_reason = f"profit target ({cfg.short_roll_profit_pct:.0%})"
            elif dte <= cfg.short_roll_dte:
                roll_reason = f"DTE {dte} <= {cfg.short_roll_dte}"
            elif abs(current_delta) >= cfg.short_roll_delta:
                roll_reason = f"delta {current_delta:.2f} >= {cfg.short_roll_delta}"

            if roll_reason:
                rolls.append({
                    "position_id": pos.get("id"),
                    "ticker": pos.get("ticker"),
                    "short_expiry": short_leg.get("expiry"),
                    "short_strike": short_leg.get("strike"),
                    "reason": roll_reason,
                    "dte": dte,
                    "current_delta": current_delta,
                })

        return self._result(
            success=True,
            data={"rolls": rolls, "count": len(rolls)},
            messages=[f"{len(rolls)} short leg(s) need rolling"]
            if rolls else ["No rolls needed"],
        )

    def _check_leaps_rolls(self, orats,
                           positions: List[Dict]) -> AgentResult:
        """Check if any LEAPS need rolling to further expiry."""
        cfg = self.config
        rolls = []

        leaps_positions = [p for p in positions
                           if p.get("tier") == "leaps"
                           and p.get("status") == "OPEN"]

        for pos in leaps_positions:
            leaps_leg = pos.get("leaps_leg", {})
            today = date.today()
            try:
                exp_date = datetime.strptime(
                    leaps_leg.get("expiry", ""), "%Y-%m-%d").date()
            except (ValueError, TypeError):
                continue

            dte = (exp_date - today).days
            if dte <= cfg.roll_dte:
                rolls.append({
                    "position_id": pos.get("id"),
                    "ticker": pos.get("ticker"),
                    "leaps_expiry": leaps_leg.get("expiry"),
                    "leaps_strike": leaps_leg.get("strike"),
                    "dte": dte,
                    "reason": f"DTE {dte} <= {cfg.roll_dte}",
                })

        return self._result(
            success=True,
            data={"rolls": rolls, "count": len(rolls)},
            messages=[f"{len(rolls)} LEAPS need rolling"]
            if rolls else ["No LEAPS rolls needed"],
        )

    # -- Status ----------------------------------------------------------

    def _status(self, orats, positions: List[Dict]) -> AgentResult:
        """Display LEAPS/PMCC position status."""
        leaps_positions = [p for p in positions
                           if p.get("tier") == "leaps"
                           and p.get("status") == "OPEN"]

        if not leaps_positions:
            return self._result(
                success=True,
                data={"positions": []},
                messages=["No LEAPS positions"],
            )

        return self._result(
            success=True,
            data={"positions": leaps_positions,
                  "count": len(leaps_positions)},
        )

    # -- Helpers ---------------------------------------------------------

    @staticmethod
    def _find_call(chain: List[Dict], target_delta: float,
                   tol: float) -> Optional[Dict]:
        """Find best call matching target delta."""
        matches = []
        for row in chain:
            d = row.get("delta")
            if d is not None and d > 0 and abs(d - target_delta) <= tol:
                matches.append(row)
        if not matches:
            return None
        matches.sort(key=lambda r: abs(r["delta"] - target_delta))
        return matches[0]

    def print_scan(self, data: Dict) -> None:
        """Pretty-print LEAPS scan results."""
        print(f"\n{C.BOLD}{C.CYAN}{'=' * 60}{C.RESET}")
        print(f"  {C.BOLD}LEAPS / PMCC SCAN — {data['ticker']}{C.RESET}")
        print(f"{C.BOLD}{C.CYAN}{'=' * 60}{C.RESET}")

        leaps = data["leaps"]
        print(f"\n  {C.BOLD}LEAPS (long):{C.RESET}")
        print(f"    Expiry: {leaps['expiry']} ({leaps['dte']}d)")
        print(f"    Strike: ${leaps['strike']:.0f}")
        print(f"    Delta:  {leaps['delta']:.2f}")
        print(f"    Cost:   ${leaps['cost']:,.0f}")
        print(f"    IV:     {leaps['iv']:.1%}")

        short = data.get("short")
        if short:
            print(f"\n  {C.BOLD}Short call (monthly):{C.RESET}")
            print(f"    Expiry: {short['expiry']} ({short['dte']}d)")
            print(f"    Strike: ${short['strike']:.0f}")
            print(f"    Delta:  {short['delta']:.2f}")
            print(f"    Premium: ${short['premium']:,.0f}")
        else:
            print(f"\n  {C.YELLOW}No short leg available{C.RESET}")

        print(f"\n  {C.BOLD}Net cost:{C.RESET} ${data['net_cost']:,.0f}")
        if short:
            monthly = short["premium"]
            annual = monthly * 12
            roi = annual / leaps["cost"] * 100 if leaps["cost"] > 0 else 0
            print(f"  Monthly income: ${monthly:,.0f}")
            print(f"  Annual income:  ${annual:,.0f} ({roi:.1f}% ROI)")
        print()
