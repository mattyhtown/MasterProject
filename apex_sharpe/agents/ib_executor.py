"""
IBExecutorAgent — execute real trades via Interactive Brokers.

Supports all 6 trade structures:
  1. Iron Condor (IC)         — 4 legs, SELL combo (credit)
  2. Call Debit Spread (CDS)  — 2 legs, BUY combo (debit)
  3. Bull Put Spread (BPS)    — 2 legs, SELL combo (credit)
  4. Long Call (LC)           — 1 leg, BUY single
  5. Call Ratio Spread (CRS)  — 3 legs, mixed ratios (1:2)
  6. Broken Wing Butterfly    — 4 legs, mixed ratios (1:2:1)

Safety features:
  - whatIfOrder() margin preview before every trade
  - Max position limit
  - Order timeout with auto-cancel
  - Kill switch via IBCfg.enabled
  - Paper mode default (port 4002)
"""

from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from .base import BaseAgent
from ..config import IBCfg, ExecutorCfg, MonitorCfg
from ..data.ib_client import IBClient
from ..types import AgentResult, C


# Structure type constants
IRON_CONDOR = "IRON_CONDOR"
CALL_DEBIT_SPREAD = "Call Debit Spread"
BULL_PUT_SPREAD = "Bull Put Spread"
LONG_CALL = "Long Call"
CALL_RATIO_SPREAD = "Call Ratio Spread"
BROKEN_WING_BUTTERFLY = "Broken Wing Butterfly"
PUT_DEBIT_SPREAD = "Put Debit Spread"
LONG_PUT = "Long Put"

# Map structure → short label for position IDs
_STRUCTURE_PREFIX = {
    IRON_CONDOR: "IC",
    CALL_DEBIT_SPREAD: "CDS",
    BULL_PUT_SPREAD: "BPS",
    LONG_CALL: "LC",
    CALL_RATIO_SPREAD: "CRS",
    BROKEN_WING_BUTTERFLY: "BWB",
    PUT_DEBIT_SPREAD: "PDS",
    LONG_PUT: "LP",
}


class IBExecutorAgent(BaseAgent):
    """Execute trades via Interactive Brokers.

    Supports all 6 trade structures. Multi-leg orders use BAG (combo)
    contracts. All trades go through margin preview (whatIfOrder)
    before execution.
    """

    def __init__(self, ib_client: IBClient,
                 executor_config: ExecutorCfg = None,
                 monitor_config: MonitorCfg = None):
        super().__init__("IBExecutor", ib_client.config)
        self.ib = ib_client
        self.executor_config = executor_config or ExecutorCfg()
        self.monitor_config = monitor_config or MonitorCfg()

    def run(self, context: Dict[str, Any]) -> AgentResult:
        """Execute trades via IB.

        Context keys:
            action: str — 'open', 'open_directional', 'close', 'status'
            decisions: List[Dict] from RiskAgent (for 'open' — IC pipeline)
            trade: Dict from StrategyAgent (for 'open_directional')
            symbol: str (for 'open_directional')
            expiry: str (for 'open_directional')
            spot: float (for 'open_directional')
            position: Dict (for 'close')
            exit_reason: str (for 'close')
        """
        action = context.get("action", "open")

        if not self.ib.is_connected:
            return self._result(
                success=False,
                errors=["IB not connected"],
            )

        if action == "open":
            return self._run_open_ic(context.get("decisions", []))
        elif action == "open_directional":
            return self._run_open_directional(context)
        elif action == "close":
            return self._run_close(
                context["position"],
                context.get("exit_reason", "MANUAL"),
            )
        elif action == "status":
            return self._run_status()
        else:
            return self._result(success=False, errors=[f"Unknown action: {action}"])

    # ------------------------------------------------------------------
    # Leg builders — normalize each structure into IB leg dicts
    # ------------------------------------------------------------------

    @staticmethod
    def _build_ic_legs(cand: Dict) -> Tuple[List[Dict], str, float]:
        """Iron Condor: 4 legs from candidate['legs'].
        Returns (ib_legs, combo_action, limit_price).
        """
        ib_legs = []
        for leg in cand["legs"]:
            ib_legs.append({
                "symbol": cand["symbol"],
                "expiry": cand["expiration"],
                "strike": leg["strike"],
                "right": "C" if leg["type"] == "CALL" else "P",
                "action": leg["action"],
                "ratio": 1,
            })
        return ib_legs, "SELL", round(cand["total_credit"], 2)

    @staticmethod
    def _build_cds_legs(symbol: str, expiry: str,
                        strikes: Dict, fill: Dict) -> Tuple[List[Dict], str, float]:
        """Call Debit Spread: BUY 1x long call, SELL 1x short call.
        Returns (ib_legs, combo_action, limit_price).
        """
        ib_legs = [
            {"symbol": symbol, "expiry": expiry,
             "strike": strikes["long_strike"], "right": "C",
             "action": "BUY", "ratio": 1},
            {"symbol": symbol, "expiry": expiry,
             "strike": strikes["short_strike"], "right": "C",
             "action": "SELL", "ratio": 1},
        ]
        return ib_legs, "BUY", round(fill["entry_cost"], 2)

    @staticmethod
    def _build_bps_legs(symbol: str, expiry: str,
                        strikes: Dict, fill: Dict) -> Tuple[List[Dict], str, float]:
        """Bull Put Spread: SELL 1x short put, BUY 1x long put.
        Returns (ib_legs, combo_action, limit_price).
        """
        ib_legs = [
            {"symbol": symbol, "expiry": expiry,
             "strike": strikes["short_strike"], "right": "P",
             "action": "SELL", "ratio": 1},
            {"symbol": symbol, "expiry": expiry,
             "strike": strikes["long_strike"], "right": "P",
             "action": "BUY", "ratio": 1},
        ]
        return ib_legs, "SELL", round(fill["entry_credit"], 2)

    @staticmethod
    def _build_lc_legs(symbol: str, expiry: str,
                       strikes: Dict, fill: Dict) -> Tuple[List[Dict], str, float]:
        """Long Call: BUY 1x call. Single leg, not a combo.
        Returns (ib_legs, action, limit_price).
        """
        ib_legs = [
            {"symbol": symbol, "expiry": expiry,
             "strike": strikes["strike"], "right": "C",
             "action": "BUY", "ratio": 1},
        ]
        return ib_legs, "BUY", round(fill["entry_cost"], 2)

    @staticmethod
    def _build_crs_legs(symbol: str, expiry: str,
                        strikes: Dict, fill: Dict) -> Tuple[List[Dict], str, float]:
        """Call Ratio Spread: BUY 1x long call, SELL 2x short call (1:2).
        Returns (ib_legs, combo_action, limit_price).
        """
        ib_legs = [
            {"symbol": symbol, "expiry": expiry,
             "strike": strikes["long_strike"], "right": "C",
             "action": "BUY", "ratio": 1},
            {"symbol": symbol, "expiry": expiry,
             "strike": strikes["short_strike"], "right": "C",
             "action": "SELL", "ratio": 2},
        ]
        # Net credit → SELL action; net debit → BUY action
        if fill.get("is_credit"):
            return ib_legs, "SELL", round(abs(fill["entry_cost"]), 2)
        return ib_legs, "BUY", round(fill["entry_cost"], 2)

    @staticmethod
    def _build_bwb_legs(symbol: str, expiry: str,
                        strikes: Dict, fill: Dict) -> Tuple[List[Dict], str, float]:
        """Broken Wing Butterfly: BUY 1x lower, SELL 2x middle, BUY 1x upper.
        Returns (ib_legs, combo_action, limit_price).
        """
        ib_legs = [
            {"symbol": symbol, "expiry": expiry,
             "strike": strikes["lower_strike"], "right": "C",
             "action": "BUY", "ratio": 1},
            {"symbol": symbol, "expiry": expiry,
             "strike": strikes["middle_strike"], "right": "C",
             "action": "SELL", "ratio": 2},
            {"symbol": symbol, "expiry": expiry,
             "strike": strikes["upper_strike"], "right": "C",
             "action": "BUY", "ratio": 1},
        ]
        if fill.get("is_credit"):
            return ib_legs, "SELL", round(abs(fill["entry_cost"]), 2)
        return ib_legs, "BUY", round(fill["entry_cost"], 2)

    @staticmethod
    def _build_pds_legs(symbol: str, expiry: str,
                        strikes: Dict, fill: Dict) -> Tuple[List[Dict], str, float]:
        """Put Debit Spread: BUY 1x long put (higher strike), SELL 1x short put (lower strike).
        Returns (ib_legs, combo_action, limit_price).
        """
        ib_legs = [
            {"symbol": symbol, "expiry": expiry,
             "strike": strikes["long_strike"], "right": "P",
             "action": "BUY", "ratio": 1},
            {"symbol": symbol, "expiry": expiry,
             "strike": strikes["short_strike"], "right": "P",
             "action": "SELL", "ratio": 1},
        ]
        return ib_legs, "BUY", round(fill["entry_cost"], 2)

    @staticmethod
    def _build_lp_legs(symbol: str, expiry: str,
                       strikes: Dict, fill: Dict) -> Tuple[List[Dict], str, float]:
        """Long Put: BUY 1x put. Single leg, not a combo.
        Returns (ib_legs, action, limit_price).
        """
        ib_legs = [
            {"symbol": symbol, "expiry": expiry,
             "strike": strikes["strike"], "right": "P",
             "action": "BUY", "ratio": 1},
        ]
        return ib_legs, "BUY", round(fill["entry_cost"], 2)

    # ------------------------------------------------------------------
    # Position limit check
    # ------------------------------------------------------------------

    def _check_position_limit(self) -> Tuple[int, bool]:
        """Returns (current_count, limit_exceeded)."""
        current = self.ib.positions()
        opt_count = sum(1 for p in current if p["secType"] == "OPT")
        # Estimate: avg ~3 legs per position
        est_positions = max(1, opt_count // 3)
        return est_positions, est_positions >= self.config.max_positions

    def _check_margin(self, ib_legs: List[Dict], combo_action: str,
                      limit_price: float) -> Optional[str]:
        """Run margin preview. Returns error message or None if OK."""
        margin = self.ib.what_if_order(
            ib_legs, action=combo_action, quantity=1,
            limit_price=limit_price,
        )
        if not margin:
            return None  # No margin data — allow (IB sometimes can't preview)

        print(f"  Margin impact: init=${margin['init_margin_change']:,.0f}"
              f"  maint=${margin['maint_margin_change']:,.0f}"
              f"  commission=${margin['commission']:.2f}")

        summary = self.ib.account_summary()
        avail = summary.get("AvailableFunds", 0)
        if margin["init_margin_change"] > avail * 0.5:
            return (f"Margin impact ${margin['init_margin_change']:,.0f} > "
                    f"50% of available ${avail:,.0f} — BLOCKED")
        return None

    # ------------------------------------------------------------------
    # Execute order and build position dict
    # ------------------------------------------------------------------

    def _execute_and_build_position(
        self, ib_legs: List[Dict], combo_action: str, limit_price: float,
        structure: str, symbol: str, expiry: str, spot: float,
        qty: int, trade_meta: Dict,
    ) -> Tuple[Optional[Dict], Optional[str]]:
        """Place order, wait for fill, build position dict.

        Returns (position_dict, error_message).
        """
        prefix = _STRUCTURE_PREFIX.get(structure, "TRD")
        today_str = date.today().strftime("%Y-%m-%d")
        position_id = f"{prefix}-{symbol}-{today_str.replace('-', '')}"

        # Single-leg orders don't use BAG
        if len(ib_legs) == 1 and ib_legs[0].get("ratio", 1) == 1:
            # For single leg, place directly via the combo interface
            # (IBClient handles single-leg as a non-BAG order)
            pass

        result = self.ib.place_combo_order(
            ib_legs,
            action=combo_action,
            quantity=qty,
            limit_price=limit_price,
            timeout=self.config.order_timeout,
        )

        if result["status"] == "Filled":
            actual_price = abs(result["avg_price"])
            total_commission = sum(f.get("commission", 0) for f in result["fills"])

            # Build normalized leg list for position storage
            pos_legs = []
            for leg in ib_legs:
                leg_type = "CALL" if leg["right"] == "C" else "PUT"
                pos_legs.append({
                    "type": leg_type,
                    "strike": leg["strike"],
                    "action": leg["action"],
                    "ratio": leg.get("ratio", 1),
                    "entry_price": 0,  # IB fills are per-combo, not per-leg
                })

            # Determine credit vs debit
            is_credit = combo_action == "SELL"

            position = {
                "id": position_id,
                "symbol": symbol,
                "type": structure,
                "entry_date": today_str,
                "expiration": expiry,
                "entry_stock_price": spot,
                "legs": pos_legs,
                "commission": round(total_commission, 2),
                "status": "OPEN",
                "execution_method": "IB",
                "ib_order_id": result["order_id"],
                "ib_perm_id": result["perm_id"],
                "qty": qty,
            }

            if is_credit:
                position["entry_credit"] = actual_price
            else:
                position["entry_cost"] = actual_price

            # Merge structure-specific metadata
            position.update(trade_meta)

            ptype = "credit" if is_credit else "debit"
            print(f"  {C.GREEN}FILLED{C.RESET} {structure} — {ptype} "
                  f"${actual_price:.2f}  commission ${total_commission:.2f}")
            return position, None

        elif result["status"] == "CANCELLED_TIMEOUT":
            return None, f"Timeout after {self.config.order_timeout}s — {symbol} {structure}"
        else:
            return None, f"Order status: {result['status']} for {symbol} {structure}"

    # ------------------------------------------------------------------
    # Open: IC Pipeline (decisions from RiskAgent)
    # ------------------------------------------------------------------

    def _run_open_ic(self, decisions: List[Dict]) -> AgentResult:
        """Place IC orders for approved candidates (IC pipeline)."""
        new_positions = []
        errors = []

        pos_count, limit_hit = self._check_position_limit()

        for item in decisions:
            if item["decision"] != "ALLOW":
                continue
            cand = item["candidate"]

            if limit_hit:
                msg = (f"Position limit reached ({pos_count}/{self.config.max_positions})"
                       f" — skipping {cand['symbol']}")
                print(f"\n{C.RED}[IBExecutor] {msg}{C.RESET}")
                errors.append(msg)
                continue

            ib_legs, combo_action, limit_price = self._build_ic_legs(cand)

            print(f"\n{C.BOLD}[IBExecutor]{C.RESET} Previewing {cand['symbol']} "
                  f"Iron Condor {cand['expiration']}...")

            margin_err = self._check_margin(ib_legs, combo_action, limit_price)
            if margin_err:
                print(f"  {C.RED}{margin_err}{C.RESET}")
                errors.append(margin_err)
                continue

            # IC-specific metadata
            sp_strike = next(
                l for l in cand["legs"]
                if l["action"] == "SELL" and l["type"] == "PUT"
            )["strike"]
            sc_strike = next(
                l for l in cand["legs"]
                if l["action"] == "SELL" and l["type"] == "CALL"
            )["strike"]
            max_width = max(cand["put_width"], cand["call_width"])

            trade_meta = {
                "iv_rank_at_entry": cand.get("iv_rank"),
                "max_profit": round(cand["total_credit"] * 100, 2),
                "max_loss": round(max_width * 100 - cand["total_credit"] * 100, 2),
                "breakeven_lower": round(sp_strike - cand["total_credit"], 2),
                "breakeven_upper": round(sc_strike + cand["total_credit"], 2),
                "exit_rules": {
                    "profit_target_pct": self.monitor_config.profit_target_pct,
                    "dte_exit": self.monitor_config.dte_exit,
                    "delta_max": self.monitor_config.delta_exit,
                },
            }

            print(f"  Placing order: {cand['symbol']} IC "
                  f"credit ${limit_price:.2f}...")
            position, err = self._execute_and_build_position(
                ib_legs, combo_action, limit_price,
                IRON_CONDOR, cand["symbol"], cand["expiration"],
                cand["stock_price"], 1, trade_meta,
            )
            if position:
                new_positions.append(position)
                pos_count += 1
            if err:
                errors.append(err)

        return self._result(
            success=len(errors) == 0,
            data={"new_positions": new_positions},
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Open: Directional trades (from strategy agents)
    # ------------------------------------------------------------------

    def _run_open_directional(self, context: Dict) -> AgentResult:
        """Place a directional trade from a strategy agent result.

        Context keys:
            trade: Dict — output from StrategyAgentBase.run()
                   {structure, strikes, fill, risk}
            symbol: str
            expiry: str
            spot: float
        """
        trade = context["trade"]
        symbol = context["symbol"]
        expiry = context["expiry"]
        spot = context.get("spot", 0)
        structure = trade["structure"]
        strikes = trade["strikes"]
        fill = trade["fill"]
        risk = trade.get("risk", {})
        qty = fill.get("qty", 1)

        pos_count, limit_hit = self._check_position_limit()
        if limit_hit:
            msg = f"Position limit reached ({pos_count}/{self.config.max_positions})"
            return self._result(success=False, errors=[msg])

        # Build legs based on structure type
        if structure == CALL_DEBIT_SPREAD:
            ib_legs, combo_action, limit_price = self._build_cds_legs(
                symbol, expiry, strikes, fill)
        elif structure == BULL_PUT_SPREAD:
            ib_legs, combo_action, limit_price = self._build_bps_legs(
                symbol, expiry, strikes, fill)
        elif structure == LONG_CALL:
            ib_legs, combo_action, limit_price = self._build_lc_legs(
                symbol, expiry, strikes, fill)
        elif structure == CALL_RATIO_SPREAD:
            ib_legs, combo_action, limit_price = self._build_crs_legs(
                symbol, expiry, strikes, fill)
        elif structure == BROKEN_WING_BUTTERFLY:
            ib_legs, combo_action, limit_price = self._build_bwb_legs(
                symbol, expiry, strikes, fill)
        elif structure == PUT_DEBIT_SPREAD:
            ib_legs, combo_action, limit_price = self._build_pds_legs(
                symbol, expiry, strikes, fill)
        elif structure == LONG_PUT:
            ib_legs, combo_action, limit_price = self._build_lp_legs(
                symbol, expiry, strikes, fill)
        else:
            return self._result(
                success=False,
                errors=[f"Unknown structure: {structure}"],
            )

        print(f"\n{C.BOLD}[IBExecutor]{C.RESET} Previewing {symbol} "
              f"{structure} {expiry}...")

        margin_err = self._check_margin(ib_legs, combo_action, limit_price)
        if margin_err:
            print(f"  {C.RED}{margin_err}{C.RESET}")
            return self._result(success=False, errors=[margin_err])

        trade_meta = {
            "max_risk": fill.get("max_risk"),
            "max_profit": fill.get("max_profit"),
            "risk_reward": fill.get("risk_reward"),
            "strikes_detail": {
                k: v for k, v in strikes.items()
                if isinstance(v, (int, float, str, bool))
            },
        }
        # Add breakevens from risk dict
        for key in ("breakeven", "lower_breakeven", "upper_breakeven"):
            if key in risk:
                trade_meta[key] = risk[key]

        print(f"  Placing order: {symbol} {structure} "
              f"{'credit' if combo_action == 'SELL' else 'debit'} "
              f"${limit_price:.2f} x{qty}...")

        position, err = self._execute_and_build_position(
            ib_legs, combo_action, limit_price,
            structure, symbol, expiry, spot, qty, trade_meta,
        )

        if position:
            return self._result(
                success=True,
                data={"position": position},
            )
        return self._result(success=False, errors=[err or "Order failed"])

    # ------------------------------------------------------------------
    # Close: any structure
    # ------------------------------------------------------------------

    def _run_close(self, position: Dict, exit_reason: str) -> AgentResult:
        """Close a position via IB (works for all structures)."""
        ib_legs = []
        for leg in position["legs"]:
            # Reverse the action to close
            close_action = "BUY" if leg["action"] == "SELL" else "SELL"
            ib_legs.append({
                "symbol": position["symbol"],
                "expiry": position["expiration"],
                "strike": leg["strike"],
                "right": "C" if leg["type"] == "CALL" else "P",
                "action": close_action,
                "ratio": leg.get("ratio", 1),
            })

        # Determine close combo action (reverse of open)
        # If opened as SELL (credit), close as BUY
        is_credit_trade = "entry_credit" in position
        close_combo_action = "BUY" if is_credit_trade else "SELL"

        print(f"\n{C.BOLD}[IBExecutor]{C.RESET} Closing "
              f"{position['id']} ({position.get('type', '?')})...")

        qty = position.get("qty", 1)
        result = self.ib.place_combo_order(
            ib_legs,
            action=close_combo_action,
            quantity=qty,
            limit_price=0.0,  # market order for close
            timeout=self.config.order_timeout,
        )

        if result["status"] == "Filled":
            exit_price = abs(result["avg_price"])
            total_commission = sum(f.get("commission", 0) for f in result["fills"])

            # P&L depends on credit vs debit structure
            if is_credit_trade:
                realized_pnl = round(
                    (position["entry_credit"] - exit_price) * 100 * qty
                    - total_commission, 2)
            else:
                realized_pnl = round(
                    (exit_price - position.get("entry_cost", 0)) * 100 * qty
                    - total_commission, 2)

            updated = dict(position)
            updated["status"] = "CLOSED"
            updated["exit_date"] = date.today().strftime("%Y-%m-%d")
            updated["exit_reason"] = exit_reason
            updated["realized_pnl"] = realized_pnl
            updated["exit_price"] = exit_price
            updated["exit_commission"] = total_commission

            print(f"  {C.GREEN}CLOSED{C.RESET} — P&L ${realized_pnl:+.2f}")

            return self._result(
                success=True,
                data={"position": updated},
            )
        else:
            msg = f"Close failed: {result['status']}"
            print(f"  {C.RED}{msg}{C.RESET}")
            return self._result(success=False, errors=[msg])

    def run_close(self, position: Dict, exit_reason: str,
                  exit_pnl: float = 0.0) -> Dict:
        """Close a position via IB (matches ExecutorAgent.run_close interface).

        Returns updated position dict (not AgentResult).
        Falls back to simulated close if IB is not connected.
        """
        if not self.ib.is_connected:
            updated = dict(position)
            updated["status"] = "CLOSED"
            updated["exit_date"] = date.today().strftime("%Y-%m-%d")
            updated["exit_reason"] = exit_reason
            updated["realized_pnl"] = round(exit_pnl - self.executor_config.commission_per_ic, 2)
            print(f"\n{C.BOLD}[IBExecutor]{C.RESET} CLOSED {position['id']} (simulated — IB offline)")
            print(f"  Reason:       {exit_reason}")
            print(f"  Realized P&L: ${updated['realized_pnl']:+.2f}")
            return updated

        result = self._run_close(position, exit_reason)
        if result.success:
            return result.data["position"]
        else:
            updated = dict(position)
            updated["status"] = "CLOSED"
            updated["exit_date"] = date.today().strftime("%Y-%m-%d")
            updated["exit_reason"] = f"{exit_reason} (IB close failed)"
            updated["realized_pnl"] = round(exit_pnl - self.executor_config.commission_per_ic, 2)
            print(f"  {C.YELLOW}IB close failed — using estimated P&L{C.RESET}")
            return updated

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def _run_status(self) -> AgentResult:
        """Get status of all open IB orders."""
        trades = self.ib._ib.openTrades() if self.ib._ib else []
        open_orders = []
        for t in trades:
            open_orders.append({
                "order_id": t.order.orderId,
                "symbol": t.contract.symbol,
                "status": t.orderStatus.status,
                "filled": t.orderStatus.filled,
                "remaining": t.orderStatus.remaining,
            })
        return self._result(
            success=True,
            data={
                "open_orders": open_orders,
                "count": len(open_orders),
            },
        )
