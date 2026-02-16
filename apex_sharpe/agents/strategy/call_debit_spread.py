"""
CallDebitSpreadAgent — Buy lower-delta call, sell higher-delta call.

Structure: Buy 1x ~40d call, Sell 1x ~25d call
Max risk: net debit paid
Max profit: (width - debit) * 100
Best when: Moderate IV, balanced R:R, directional conviction
"""

from typing import Any, Dict, List, Optional

from .base_strategy_agent import StrategyAgentBase
from ...config import TradeBacktestCfg
from ...types import TradeStructure


class CallDebitSpreadAgent(StrategyAgentBase):
    """Call debit spread (bull call spread) strategy agent."""

    STRUCTURE = TradeStructure.CALL_DEBIT_SPREAD
    NUM_LEGS = 2

    def __init__(self, config: TradeBacktestCfg = None):
        config = config or TradeBacktestCfg()
        super().__init__("CallDebitSpread", config)

    def find_strikes(self, chain: List[Dict],
                     spot: float) -> Optional[Dict]:
        cfg = self.config
        long_calls = self._find_calls(chain, cfg.call_ds_long, cfg.delta_tol)
        short_calls = self._find_calls(chain, cfg.call_ds_short, cfg.delta_tol)

        if not long_calls or not short_calls:
            return None

        lc = long_calls[0]
        sc = short_calls[0]

        if lc["strike"] >= sc["strike"]:
            return None

        return {
            "long": lc,
            "short": sc,
            "long_strike": lc["strike"],
            "short_strike": sc["strike"],
            "width": sc["strike"] - lc["strike"],
            "long_delta": lc.get("delta", 0),
            "short_delta": sc.get("delta", 0),
        }

    def simulate_entry(self, strikes: Dict,
                       risk_budget: float) -> Optional[Dict]:
        cfg = self.config
        lc = strikes["long"]
        sc = strikes["short"]

        cost = lc.get("callAskPrice", 0) - sc.get("callBidPrice", 0)
        if cost <= 0:
            return None

        # Apply slippage + bid-ask penalty
        cost_slip = cost * (1 + cfg.slippage)
        ba_penalty = (self._bid_ask_penalty(
            lc.get("callBidPrice", 0), lc.get("callAskPrice", 0)) +
            self._bid_ask_penalty(
                sc.get("callBidPrice", 0), sc.get("callAskPrice", 0)))
        cost_slip += ba_penalty

        comm = cfg.commission_per_leg * 2
        risk_per = cost_slip * 100 + comm
        qty = max(1, int(risk_budget / risk_per))
        width = strikes["width"]
        max_profit = (width - cost_slip) * 100 * qty - comm * qty

        return {
            "entry_cost": round(cost_slip, 4),
            "raw_cost": round(cost, 4),
            "ba_penalty": round(ba_penalty, 4),
            "qty": qty,
            "comm": round(comm * qty, 2),
            "max_risk": round(risk_per * qty, 2),
            "max_profit": round(max_profit, 2),
            "risk_reward": round(max_profit / (risk_per * qty), 2) if risk_per > 0 else 0,
        }

    def compute_risk(self, strikes: Dict, fill: Dict) -> Dict:
        width = strikes["width"]
        cost = fill["entry_cost"]
        lower_be = strikes["long_strike"] + cost
        upper_be = strikes["short_strike"]

        return {
            "max_loss": fill["max_risk"],
            "max_profit": fill["max_profit"],
            "breakeven": round(lower_be, 2),
            "width": width,
            "net_delta": round(strikes["long_delta"] - strikes["short_delta"], 3),
        }

    def compute_pnl(self, strikes: Dict, fill: Dict,
                    exit_price: float) -> float:
        lv = max(0, exit_price - strikes["long_strike"])
        sv = max(0, exit_price - strikes["short_strike"])
        pnl_per = (lv - sv) - fill["entry_cost"]
        return round(pnl_per * 100 * fill["qty"] - fill["comm"], 2)

    def check_exit(self, position: Dict,
                   current_price: float) -> Optional[str]:
        # Exit at 50% max profit or 100% loss
        entry = position.get("entry_cost", 0)
        width = position.get("width", 0)
        max_profit_per = width - entry
        current_value = max(0, min(width, current_price - position.get("long_strike", 0)))
        pnl_per = current_value - entry

        if max_profit_per > 0 and pnl_per >= max_profit_per * 0.50:
            return "profit_target_50pct"
        if pnl_per <= -entry:
            return "max_loss"
        return None
