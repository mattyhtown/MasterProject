"""
CallRatioSpreadAgent — Buy 1x deep call, sell 2x OTM calls.

Structure: Buy 1x ~50d call, Sell 2x ~25d call (1x2 ratio)
Max profit: at short strike = width - net debit
Max risk: unlimited above upper breakeven (capped in backtest)
Best when: Strong signal (4+), moderate-high IV, moderate up-move expected

The 1x2 creates a net credit or small debit that profits from a moderate
move to the short strike. Above that, the naked short leg creates unlimited
risk — this is managed by setting a hard stop at the upper breakeven.

Execution haircuts:
  - 3 legs = higher commission
  - Wider fills on the 2x short leg (selling into bid)
  - Conservative upper-breakeven stop in live trading
"""

from typing import Any, Dict, List, Optional

from .base_strategy_agent import StrategyAgentBase
from ...config import CallRatioSpreadCfg
from ...types import TradeStructure


class CallRatioSpreadAgent(StrategyAgentBase):
    """Call ratio spread (1x2) strategy agent."""

    STRUCTURE = TradeStructure.CALL_RATIO_SPREAD
    NUM_LEGS = 3  # 1 long + 2 short

    def __init__(self, config: CallRatioSpreadCfg = None):
        config = config or CallRatioSpreadCfg()
        super().__init__("CallRatioSpread", config)

    def find_strikes(self, chain: List[Dict],
                     spot: float) -> Optional[Dict]:
        cfg = self.config
        long_calls = self._find_calls(chain, cfg.long_delta, cfg.delta_tol)
        short_calls = self._find_calls(chain, cfg.short_delta, cfg.delta_tol)

        if not long_calls or not short_calls:
            return None

        lc = long_calls[0]
        sc = short_calls[0]

        if lc["strike"] >= sc["strike"]:
            return None

        width = sc["strike"] - lc["strike"]

        return {
            "long": lc,
            "short": sc,
            "long_strike": lc["strike"],
            "short_strike": sc["strike"],
            "width": width,
            "long_delta": lc.get("delta", 0),
            "short_delta": sc.get("delta", 0),
            "ratio": "1x2",
        }

    def simulate_entry(self, strikes: Dict,
                       risk_budget: float) -> Optional[Dict]:
        cfg = self.config
        lc = strikes["long"]
        sc = strikes["short"]

        # Buy 1 long, sell 2 short
        cost = (lc.get("callAskPrice", 0) -
                2 * sc.get("callBidPrice", 0))

        # Apply slippage: adverse on both directions
        if cost > 0:
            cost_slip = cost * (1 + cfg.slippage)
        else:
            cost_slip = cost * (1 - cfg.slippage)

        # Bid-ask penalty on all 3 legs
        ba_penalty = (
            self._bid_ask_penalty(
                lc.get("callBidPrice", 0), lc.get("callAskPrice", 0)) +
            2 * self._bid_ask_penalty(
                sc.get("callBidPrice", 0), sc.get("callAskPrice", 0))
        )
        cost_slip += ba_penalty

        comm = cfg.commission_per_leg * 3
        width = strikes["width"]

        # Risk calculation: downside is debit; upside is naked short
        # For backtest sizing, use width as conservative risk estimate
        if cost_slip > 0:
            risk_per = cost_slip * 100 + comm
        else:
            risk_per = width * 100 + comm

        qty = max(1, int(risk_budget / risk_per))
        max_profit = (width - cost_slip) * 100 * qty - comm * qty

        # Upper breakeven: short_strike + (width - cost_slip)
        upper_be = strikes["short_strike"] + (width - cost_slip)

        return {
            "entry_cost": round(cost_slip, 4),
            "is_credit": cost_slip < 0,
            "ba_penalty": round(ba_penalty, 4),
            "qty": qty,
            "comm": round(comm * qty, 2),
            "max_risk": round(risk_per * qty, 2),
            "max_profit": round(max_profit, 2),
            "upper_breakeven": round(upper_be, 2),
            "risk_reward": round(max_profit / (risk_per * qty), 2) if risk_per > 0 else 0,
        }

    def compute_risk(self, strikes: Dict, fill: Dict) -> Dict:
        width = strikes["width"]
        cost = fill["entry_cost"]

        # Lower breakeven: long_strike + cost (if net debit)
        lower_be = strikes["long_strike"] + max(0, cost)
        upper_be = fill.get("upper_breakeven", strikes["short_strike"] + width)

        return {
            "max_loss_down": fill["max_risk"],
            "max_loss_up": "unlimited (stop at upper BE)",
            "max_profit": fill["max_profit"],
            "lower_breakeven": round(lower_be, 2),
            "upper_breakeven": round(upper_be, 2),
            "width": width,
            "net_delta": round(
                strikes["long_delta"] - 2 * strikes["short_delta"], 3),
        }

    def compute_pnl(self, strikes: Dict, fill: Dict,
                    exit_price: float) -> float:
        long_val = max(0, exit_price - strikes["long_strike"])
        short_val = max(0, exit_price - strikes["short_strike"])
        pnl_per = long_val - 2 * short_val - fill["entry_cost"]
        return round(pnl_per * 100 * fill["qty"] - fill["comm"], 2)

    def check_exit(self, position: Dict,
                   current_price: float) -> Optional[str]:
        upper_be = position.get("upper_breakeven", float("inf"))
        short_strike = position.get("short_strike", 0)
        entry_cost = position.get("entry_cost", 0)
        width = position.get("width", 0)

        # Exit if approaching upper breakeven (naked short risk)
        if current_price >= upper_be * 0.95:
            return "upper_breakeven_proximity"

        # Take profit near short strike (max profit zone)
        max_profit_per = width - entry_cost
        long_val = max(0, current_price - position.get("long_strike", 0))
        short_val = max(0, current_price - short_strike)
        current_pnl = long_val - 2 * short_val - entry_cost
        if max_profit_per > 0 and current_pnl >= max_profit_per * 0.60:
            return "profit_target_60pct"

        return None
