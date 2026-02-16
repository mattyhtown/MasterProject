"""
IBExecutorAgent — execute real trades via Interactive Brokers.

Replaces the simulated ExecutorAgent with live IB order placement.
Uses BAG (combo) contracts for iron condors and multi-leg spreads.

Safety features:
  - whatIfOrder() margin preview before every trade
  - Max position limit
  - Order timeout with auto-cancel
  - Kill switch via IBCfg.enabled
  - Paper mode default (port 4002)
"""

from datetime import date
from typing import Any, Dict, List, Optional

from .base import BaseAgent
from ..config import IBCfg, ExecutorCfg, MonitorCfg
from ..data.ib_client import IBClient
from ..types import AgentResult, C


class IBExecutorAgent(BaseAgent):
    """Execute trades via Interactive Brokers.

    Supports iron condors (4-leg BAG), vertical spreads (2-leg BAG),
    and single-leg orders. All trades go through margin preview
    (whatIfOrder) before execution.
    """

    def __init__(self, ib_client: IBClient,
                 executor_config: ExecutorCfg = None,
                 monitor_config: MonitorCfg = None):
        super().__init__("IBExecutor", ib_client.config)
        self.ib = ib_client
        self.executor_config = executor_config or ExecutorCfg()
        self.monitor_config = monitor_config or MonitorCfg()

    def run(self, context: Dict[str, Any]) -> AgentResult:
        """Execute approved trades via IB.

        Context keys:
            action: str — 'open', 'close', 'status' (default: 'open')
            decisions: List[Dict] from RiskAgent (for 'open')
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
            return self._run_open(context.get("decisions", []))
        elif action == "close":
            return self._run_close(
                context["position"],
                context.get("exit_reason", "MANUAL"),
            )
        elif action == "status":
            return self._run_status()
        else:
            return self._result(success=False, errors=[f"Unknown action: {action}"])

    def _run_open(self, decisions: List[Dict]) -> AgentResult:
        """Place orders for approved candidates."""
        new_positions = []
        errors = []

        # Check position limit
        current_positions = self.ib.positions()
        opt_positions = [p for p in current_positions if p["secType"] == "OPT"]
        # Rough count: 4 legs per IC
        open_ic_count = len(opt_positions) // 4
        max_positions = self.config.max_positions

        for item in decisions:
            if item["decision"] != "ALLOW":
                continue
            cand = item["candidate"]

            if open_ic_count >= max_positions:
                msg = (f"Position limit reached ({open_ic_count}/{max_positions})"
                       f" — skipping {cand['symbol']}")
                print(f"\n{C.RED}[IBExecutor] {msg}{C.RESET}")
                errors.append(msg)
                continue

            # Build legs for IB order
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

            # 1. Margin preview
            print(f"\n{C.BOLD}[IBExecutor]{C.RESET} Previewing margin for "
                  f"{cand['symbol']} {cand['expiration']}...")
            margin = self.ib.what_if_order(
                ib_legs, action="SELL", quantity=1,
                limit_price=cand["total_credit"],
            )
            if margin:
                print(f"  Margin impact: init=${margin['init_margin_change']:,.0f}"
                      f"  maint=${margin['maint_margin_change']:,.0f}"
                      f"  commission=${margin['commission']:.2f}")

                # Safety check: reject if margin exceeds buying power
                summary = self.ib.account_summary()
                avail = summary.get("AvailableFunds", 0)
                if margin["init_margin_change"] > avail * 0.5:
                    msg = (f"Margin impact ${margin['init_margin_change']:,.0f} > "
                           f"50% of available ${avail:,.0f} — BLOCKED")
                    print(f"  {C.RED}{msg}{C.RESET}")
                    errors.append(msg)
                    continue

            # 2. Place order
            print(f"  Placing order: {cand['symbol']} IC "
                  f"credit ${cand['total_credit']:.2f}...")
            result = self.ib.place_combo_order(
                ib_legs,
                action="SELL",
                quantity=1,
                limit_price=round(cand["total_credit"], 2),
                timeout=self.config.order_timeout,
            )

            if result["status"] in ("Filled", "ApiCancelled"):
                if result["status"] == "Filled":
                    # Build position dict (same format as simulated executor)
                    actual_credit = abs(result["avg_price"])
                    total_commission = sum(f.get("commission", 0) for f in result["fills"])

                    sp_strike = next(
                        l for l in cand["legs"]
                        if l["action"] == "SELL" and l["type"] == "PUT"
                    )["strike"]
                    sc_strike = next(
                        l for l in cand["legs"]
                        if l["action"] == "SELL" and l["type"] == "CALL"
                    )["strike"]
                    max_width = max(cand["put_width"], cand["call_width"])
                    max_profit = round(actual_credit * 100 - total_commission, 2)
                    max_loss = round(max_width * 100 - max_profit, 2)

                    today_str = date.today().strftime("%Y-%m-%d")
                    position_id = f"IC-{cand['symbol']}-{today_str.replace('-', '')}"

                    position = {
                        "id": position_id,
                        "symbol": cand["symbol"],
                        "type": "IRON_CONDOR",
                        "entry_date": today_str,
                        "expiration": cand["expiration"],
                        "entry_credit": actual_credit,
                        "entry_stock_price": cand["stock_price"],
                        "iv_rank_at_entry": cand.get("iv_rank"),
                        "legs": [],
                        "max_profit": max_profit,
                        "max_loss": max_loss,
                        "breakeven_lower": round(sp_strike - actual_credit, 2),
                        "breakeven_upper": round(sc_strike + actual_credit, 2),
                        "commission": total_commission,
                        "exit_rules": {
                            "profit_target_pct": self.monitor_config.profit_target_pct,
                            "dte_exit": self.monitor_config.dte_exit,
                            "delta_max": self.monitor_config.delta_exit,
                        },
                        "status": "OPEN",
                        "execution_method": "IB",
                        "ib_order_id": result["order_id"],
                        "ib_perm_id": result["perm_id"],
                    }

                    # Map fills back to legs
                    for leg in cand["legs"]:
                        fill_match = next(
                            (f for f in result["fills"]
                             if f.get("conId")),
                            None,
                        )
                        position["legs"].append({
                            "type": leg["type"],
                            "strike": leg["strike"],
                            "action": leg["action"],
                            "entry_price": fill_match["price"] if fill_match else leg["price"],
                            "delta": round(leg["delta"], 4),
                        })

                    print(f"  {C.GREEN}FILLED{C.RESET} — credit ${actual_credit:.2f}"
                          f"  commission ${total_commission:.2f}")
                    new_positions.append(position)
                    open_ic_count += 1
                else:
                    msg = f"Order cancelled: {cand['symbol']}"
                    print(f"  {C.RED}{msg}{C.RESET}")
                    errors.append(msg)
            else:
                msg = f"Order status: {result['status']} for {cand['symbol']}"
                print(f"  {C.YELLOW}{msg}{C.RESET}")
                if result["status"] == "CANCELLED_TIMEOUT":
                    errors.append(f"Timeout after {self.config.order_timeout}s — {cand['symbol']}")
                else:
                    errors.append(msg)

        return self._result(
            success=len(errors) == 0,
            data={"new_positions": new_positions},
            errors=errors,
        )

    def _run_close(self, position: Dict, exit_reason: str) -> AgentResult:
        """Close a position via IB."""
        # Build closing legs (reverse actions)
        ib_legs = []
        for leg in position["legs"]:
            ib_legs.append({
                "symbol": position["symbol"],
                "expiry": position["expiration"],
                "strike": leg["strike"],
                "right": "C" if leg["type"] == "CALL" else "P",
                "action": "BUY" if leg["action"] == "SELL" else "SELL",
                "ratio": 1,
            })

        print(f"\n{C.BOLD}[IBExecutor]{C.RESET} Closing {position['id']}...")

        # Place closing order (BUY to close an IC that was SOLD)
        # For an IC, we need to buy back at market or set a limit
        result = self.ib.place_combo_order(
            ib_legs,
            action="BUY",
            quantity=1,
            limit_price=0.0,  # market order for close
            timeout=self.config.order_timeout,
        )

        if result["status"] == "Filled":
            exit_cost = abs(result["avg_price"])
            total_commission = sum(f.get("commission", 0) for f in result["fills"])
            realized_pnl = round(
                (position["entry_credit"] - exit_cost) * 100 - total_commission, 2
            )

            updated = dict(position)
            updated["status"] = "CLOSED"
            updated["exit_date"] = date.today().strftime("%Y-%m-%d")
            updated["exit_reason"] = exit_reason
            updated["realized_pnl"] = realized_pnl
            updated["exit_cost"] = exit_cost
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
            # Fallback to simulated close
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
            # Fallback if IB close fails
            updated = dict(position)
            updated["status"] = "CLOSED"
            updated["exit_date"] = date.today().strftime("%Y-%m-%d")
            updated["exit_reason"] = f"{exit_reason} (IB close failed)"
            updated["realized_pnl"] = round(exit_pnl - self.executor_config.commission_per_ic, 2)
            print(f"  {C.YELLOW}IB close failed — using estimated P&L{C.RESET}")
            return updated

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
