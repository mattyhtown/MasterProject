"""
ExecutorAgent — simulate trade execution with slippage and commissions.

Extracted from trading_pipeline.py.
Optionally uses execution.FillSimulator for realistic fill modeling.
"""

from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional

from .base import BaseAgent
from ..config import ExecutorCfg, MonitorCfg
from ..types import AgentResult, C

# Optional: enhanced fill simulation via FillSimulator
try:
    from ..execution.fill_simulator import FillSimulator
    _HAS_FILL_SIM = True
except ImportError:
    _HAS_FILL_SIM = False


class ExecutorAgent(BaseAgent):
    """Simulate trade execution (open / close) with slippage and commissions.

    If FillSimulator is available, uses realistic bid/ask spread modeling,
    market session effects, and liquidity adjustments. Otherwise falls back
    to simple percentage slippage.
    """

    def __init__(self, config: ExecutorCfg = None, monitor_config: MonitorCfg = None):
        config = config or ExecutorCfg()
        super().__init__("Executor", config)
        self.monitor_config = monitor_config or MonitorCfg()

        # Initialize FillSimulator if available
        self._fill_sim: Optional[object] = None
        if _HAS_FILL_SIM:
            self._fill_sim = FillSimulator(
                base_spread_pct=config.slippage_pct,
                slippage_bps=config.slippage_pct * 10000,  # convert pct to bps
            )

    def run(self, context: Dict[str, Any]) -> AgentResult:
        """Open approved trades.

        Context keys:
            decisions: List[Dict] from RiskAgent (each has candidate + decision)
        """
        decisions = context["decisions"]
        new_positions = self._run_open(decisions)
        return self._result(
            success=True,
            data={"new_positions": new_positions},
        )

    def _simulate_leg_fill(self, leg: Dict, is_buy: bool) -> float:
        """Simulate a single leg fill price using FillSimulator or flat slippage."""
        if self._fill_sim and _HAS_FILL_SIM:
            # FillSimulator uses Decimal — convert in/out
            bid = Decimal(str(leg.get("bid", leg["price"] * 0.98)))
            ask = Decimal(str(leg.get("ask", leg["price"] * 1.02)))
            volume = leg.get("volume", 500)
            fill = self._fill_sim.simulate_fill(
                order_type="MARKET",
                side="BUY" if is_buy else "SELL",
                quantity=1,
                bid=bid,
                ask=ask,
                volume=volume,
            )
            return float(fill)
        # Fallback: flat percentage slippage
        pct = self.config.slippage_pct
        if is_buy:
            return leg["price"] * (1 + pct)
        return leg["price"] * (1 - pct)

    def _run_open(self, approved: List[Dict]) -> List[Dict]:
        """Create position records for ALLOW candidates."""
        cfg = self.config
        new_positions: List[Dict] = []

        for item in approved:
            if item["decision"] != "ALLOW":
                continue
            cand = item["candidate"]

            # Apply slippage: reduce credit received
            fill_credit = round(cand["total_credit"] * (1 - cfg.slippage_pct), 2)
            max_profit = round(fill_credit * 100 - cfg.commission_per_ic, 2)
            max_width = max(cand["put_width"], cand["call_width"])
            max_loss = round(max_width * 100 - max_profit, 2)

            today_str = date.today().strftime("%Y-%m-%d")
            position_id = f"IC-{cand['symbol']}-{today_str.replace('-', '')}"

            # Breakevens adjusted for fill
            sp_strike = next(l for l in cand["legs"] if l["action"] == "SELL" and l["type"] == "PUT")["strike"]
            sc_strike = next(l for l in cand["legs"] if l["action"] == "SELL" and l["type"] == "CALL")["strike"]

            position = {
                "id": position_id,
                "symbol": cand["symbol"],
                "type": "IRON_CONDOR",
                "entry_date": today_str,
                "expiration": cand["expiration"],
                "entry_credit": fill_credit,
                "entry_stock_price": cand["stock_price"],
                "iv_rank_at_entry": cand.get("iv_rank"),
                "legs": [
                    {
                        "type": leg["type"],
                        "strike": leg["strike"],
                        "action": leg["action"],
                        "entry_price": round(
                            leg["price"] * (1 + cfg.slippage_pct if leg["action"] == "BUY"
                                            else 1 - cfg.slippage_pct if leg["action"] == "SELL"
                                            else 1), 2
                        ),
                        "delta": round(leg["delta"], 4),
                    }
                    for leg in cand["legs"]
                ],
                "max_profit": max_profit,
                "max_loss": max_loss,
                "breakeven_lower": round(sp_strike - fill_credit, 2),
                "breakeven_upper": round(sc_strike + fill_credit, 2),
                "commission": cfg.commission_per_ic,
                "exit_rules": {
                    "profit_target_pct": self.monitor_config.profit_target_pct,
                    "dte_exit": self.monitor_config.dte_exit,
                    "delta_max": self.monitor_config.delta_exit,
                },
                "status": "OPEN",
            }

            print(f"\n{C.BOLD}[Executor]{C.RESET} OPENED {position_id}")
            print(f"  Fill credit: ${fill_credit:.2f} (after {cfg.slippage_pct:.0%} slippage)")
            print(f"  Commission:  ${cfg.commission_per_ic:.2f}")
            print(f"  Max profit:  ${max_profit:.2f}")
            print(f"  Max loss:    ${max_loss:.2f}")

            new_positions.append(position)

        return new_positions

    def run_close(self, position: Dict, exit_reason: str, exit_pnl: float) -> Dict:
        """Mark a position CLOSED. Returns updated position dict."""
        cfg = self.config
        position = dict(position)  # shallow copy
        position["status"] = "CLOSED"
        position["exit_date"] = date.today().strftime("%Y-%m-%d")
        position["exit_reason"] = exit_reason
        position["realized_pnl"] = round(exit_pnl - cfg.commission_per_ic, 2)

        print(f"\n{C.BOLD}[Executor]{C.RESET} CLOSED {position['id']}")
        print(f"  Reason:       {exit_reason}")
        print(f"  Realized P&L: ${position['realized_pnl']:+.2f}")

        return position
