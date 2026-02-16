"""
IBSyncAgent — reconcile positions.json with actual IB account positions.

Handles three scenarios:
  1. Positions in positions.json but not in IB (closed externally or stale)
  2. Positions in IB but not in positions.json (opened manually)
  3. Positions in both but with different data (needs reconciliation)
"""

from datetime import date, datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from .base import BaseAgent
from ..data.ib_client import IBClient
from ..types import AgentResult, C


class IBSyncAgent(BaseAgent):
    """Reconcile local position state with Interactive Brokers."""

    def __init__(self, ib_client: IBClient):
        super().__init__("IBSync", ib_client.config)
        self.ib = ib_client

    def run(self, context: Dict[str, Any]) -> AgentResult:
        """Sync operations.

        Context keys:
            action: str — 'sync', 'reconcile', 'import', 'account'
            positions: List[Dict] — current positions.json data
        """
        action = context.get("action", "sync")

        if not self.ib.is_connected:
            return self._result(
                success=False,
                errors=["IB not connected"],
            )

        if action == "sync":
            return self._sync(context.get("positions", []))
        elif action == "reconcile":
            return self._reconcile(context.get("positions", []))
        elif action == "import":
            return self._import_positions()
        elif action == "account":
            return self._account_status()
        else:
            return self._result(success=False, errors=[f"Unknown action: {action}"])

    def _sync(self, local_positions: List[Dict]) -> AgentResult:
        """Compare local positions with IB — report differences."""
        ib_positions = self.ib.positions()
        ib_options = [p for p in ib_positions if p["secType"] == "OPT"]

        # Group IB positions by symbol + expiry
        ib_groups = self._group_ib_positions(ib_options)

        # Group local positions
        local_open = [p for p in local_positions if p.get("status") == "OPEN"]
        local_groups = {}
        for pos in local_open:
            key = (pos["symbol"], pos["expiration"])
            local_groups[key] = pos

        # Find differences
        only_local = []
        only_ib = []
        matched = []
        mismatched = []

        for key, pos in local_groups.items():
            if key in ib_groups:
                ib_grp = ib_groups[key]
                local_strikes = set(l["strike"] for l in pos["legs"])
                ib_strikes = set(l["strike"] for l in ib_grp["legs"])
                if local_strikes == ib_strikes:
                    matched.append({"local": pos, "ib": ib_grp})
                else:
                    mismatched.append({
                        "local": pos,
                        "ib": ib_grp,
                        "local_strikes": sorted(local_strikes),
                        "ib_strikes": sorted(ib_strikes),
                    })
            else:
                only_local.append(pos)

        for key, grp in ib_groups.items():
            if key not in local_groups:
                only_ib.append(grp)

        return self._result(
            success=True,
            data={
                "matched": len(matched),
                "only_local": len(only_local),
                "only_ib": len(only_ib),
                "mismatched": len(mismatched),
                "details": {
                    "matched": matched,
                    "only_local": only_local,
                    "only_ib": only_ib,
                    "mismatched": mismatched,
                },
            },
        )

    def _reconcile(self, local_positions: List[Dict]) -> AgentResult:
        """Update local positions with actual IB data."""
        sync_result = self._sync(local_positions)
        details = sync_result.data["details"]
        updated_positions = list(local_positions)
        changes = []

        # Update matched positions with IB data
        for match in details["matched"]:
            local = match["local"]
            ib_grp = match["ib"]
            for i, pos in enumerate(updated_positions):
                if pos["id"] == local["id"]:
                    # Update with IB market values
                    pos["ib_market_value"] = ib_grp.get("market_value", 0)
                    pos["ib_unrealized_pnl"] = ib_grp.get("unrealized_pnl", 0)
                    pos["last_ib_sync"] = datetime.now().isoformat()
                    changes.append(f"Updated {pos['id']} with IB market data")
                    break

        # Mark stale local positions (not in IB)
        for stale in details["only_local"]:
            for i, pos in enumerate(updated_positions):
                if pos["id"] == stale["id"]:
                    pos["ib_warning"] = "Not found in IB account"
                    pos["last_ib_sync"] = datetime.now().isoformat()
                    changes.append(f"WARNING: {pos['id']} not in IB")
                    break

        return self._result(
            success=True,
            data={
                "positions": updated_positions,
                "changes": changes,
                "matched": sync_result.data["matched"],
                "only_local": sync_result.data["only_local"],
                "only_ib": sync_result.data["only_ib"],
            },
        )

    def _import_positions(self) -> AgentResult:
        """Import IB positions into positions.json format."""
        ib_positions = self.ib.positions()
        ib_options = [p for p in ib_positions if p["secType"] == "OPT"]
        ib_groups = self._group_ib_positions(ib_options)

        imported = []
        for (symbol, expiry), grp in ib_groups.items():
            today_str = date.today().strftime("%Y-%m-%d")
            exp_fmt = (f"{expiry[:4]}-{expiry[4:6]}-{expiry[6:]}"
                       if len(expiry) == 8 else expiry)
            position_id = f"IB-{symbol}-{expiry}"

            legs = []
            for leg in grp["legs"]:
                legs.append({
                    "type": "CALL" if leg["right"] == "C" else "PUT",
                    "strike": leg["strike"],
                    "action": "SELL" if leg["qty"] < 0 else "BUY",
                    "entry_price": abs(leg["avg_cost"]) / 100,  # IB uses per-share * multiplier
                    "delta": 0,  # Need live Greeks for this
                })

            position = {
                "id": position_id,
                "symbol": symbol,
                "type": self._detect_structure(legs),
                "entry_date": today_str,
                "expiration": exp_fmt,
                "entry_credit": 0,  # Unknown from IB positions alone
                "entry_stock_price": 0,
                "legs": legs,
                "status": "OPEN",
                "execution_method": "IB_IMPORT",
                "import_date": today_str,
            }
            imported.append(position)

        return self._result(
            success=True,
            data={"imported": imported, "count": len(imported)},
        )

    def _account_status(self) -> AgentResult:
        """Get full account status."""
        summary = self.ib.account_summary()
        positions = self.ib.positions()
        pnl = self.ib.portfolio_pnl()

        opt_count = sum(1 for p in positions if p["secType"] == "OPT")
        stk_count = sum(1 for p in positions if p["secType"] == "STK")

        return self._result(
            success=True,
            data={
                "summary": summary,
                "pnl": pnl,
                "position_count": len(positions),
                "option_positions": opt_count,
                "stock_positions": stk_count,
            },
        )

    def _group_ib_positions(self, ib_options: List[Dict]) -> Dict:
        """Group IB option positions by symbol + expiry."""
        groups = {}
        for pos in ib_options:
            key = (pos["symbol"], pos.get("expiry", ""))
            if key not in groups:
                groups[key] = {
                    "symbol": pos["symbol"],
                    "expiry": pos.get("expiry", ""),
                    "legs": [],
                    "market_value": 0,
                    "unrealized_pnl": 0,
                }
            groups[key]["legs"].append({
                "strike": pos.get("strike", 0),
                "right": pos.get("right", "?"),
                "qty": pos["qty"],
                "avg_cost": pos["avg_cost"],
            })
        return groups

    @staticmethod
    def _detect_structure(legs: List[Dict]) -> str:
        """Detect trade structure from legs."""
        sells = [l for l in legs if l["action"] == "SELL"]
        buys = [l for l in legs if l["action"] == "BUY"]
        puts = [l for l in legs if l["type"] == "PUT"]
        calls = [l for l in legs if l["type"] == "CALL"]

        if len(legs) == 4 and len(puts) == 2 and len(calls) == 2:
            return "IRON_CONDOR"
        elif len(legs) == 2 and len(puts) == 2:
            return "PUT_SPREAD"
        elif len(legs) == 2 and len(calls) == 2:
            return "CALL_SPREAD"
        elif len(legs) == 1:
            return f"LONG_{legs[0]['type']}" if legs[0]["action"] == "BUY" else f"SHORT_{legs[0]['type']}"
        return "UNKNOWN"

    def print_sync(self, result: AgentResult) -> None:
        """Pretty-print sync results."""
        d = result.data
        print(f"\n{C.BOLD}{C.CYAN}{'=' * 74}{C.RESET}")
        print(f"  {C.BOLD}IB POSITION SYNC{C.RESET}")
        print(f"{C.BOLD}{C.CYAN}{'=' * 74}{C.RESET}")

        print(f"\n  {C.GREEN}Matched:{C.RESET}      {d['matched']}")
        print(f"  {C.YELLOW}Only local:{C.RESET}   {d['only_local']}")
        print(f"  {C.YELLOW}Only IB:{C.RESET}      {d['only_ib']}")
        print(f"  {C.RED}Mismatched:{C.RESET}   {d['mismatched']}")

        details = d.get("details", {})

        if details.get("only_local"):
            print(f"\n  {C.YELLOW}Positions in local but NOT in IB:{C.RESET}")
            for pos in details["only_local"]:
                print(f"    {pos['id']} — {pos['symbol']} exp {pos['expiration']}")

        if details.get("only_ib"):
            print(f"\n  {C.YELLOW}Positions in IB but NOT in local:{C.RESET}")
            for grp in details["only_ib"]:
                strikes = [l["strike"] for l in grp["legs"]]
                print(f"    {grp['symbol']} exp {grp['expiry']}"
                      f" — strikes: {strikes}")

        if details.get("mismatched"):
            print(f"\n  {C.RED}Strike mismatches:{C.RESET}")
            for mm in details["mismatched"]:
                print(f"    {mm['local']['id']}:")
                print(f"      Local:  {mm['local_strikes']}")
                print(f"      IB:     {mm['ib_strikes']}")
        print()

    def print_account(self, result: AgentResult) -> None:
        """Pretty-print account status."""
        d = result.data
        s = d["summary"]
        pnl = d["pnl"]

        print(f"\n{C.BOLD}{C.CYAN}{'=' * 74}{C.RESET}")
        print(f"  {C.BOLD}IB ACCOUNT STATUS{C.RESET}")
        print(f"{C.BOLD}{C.CYAN}{'=' * 74}{C.RESET}")

        print(f"\n  Account:         {s.get('account', '?')}")
        print(f"  Net Liquidation: ${s.get('NetLiquidation', 0):>12,.2f}")
        print(f"  Buying Power:    ${s.get('BuyingPower', 0):>12,.2f}")
        print(f"  Cash:            ${s.get('TotalCashValue', 0):>12,.2f}")
        print(f"  Position Value:  ${s.get('GrossPositionValue', 0):>12,.2f}")
        print(f"  Init Margin:     ${s.get('InitMarginReq', 0):>12,.2f}")
        print(f"  Maint Margin:    ${s.get('MaintMarginReq', 0):>12,.2f}")
        print(f"  Available:       ${s.get('AvailableFunds', 0):>12,.2f}")

        pnl_clr = C.GREEN if pnl["daily_pnl"] >= 0 else C.RED
        print(f"\n  Daily P&L:       {pnl_clr}${pnl['daily_pnl']:>+12,.2f}{C.RESET}")
        print(f"  Unrealized:      ${pnl['unrealized_pnl']:>+12,.2f}")
        print(f"  Realized:        ${pnl['realized_pnl']:>+12,.2f}")

        print(f"\n  Positions:       {d['position_count']} total"
              f" ({d['option_positions']} options, {d['stock_positions']} stocks)")
        print()
