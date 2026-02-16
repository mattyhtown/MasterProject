"""
PutDebitSpreadAgent — Buy higher-delta put, sell lower-delta put.

Structure: Buy 1x ~40d put (closer to ATM), Sell 1x ~25d put (OTM)
Max risk: net debit paid
Max profit: (width - debit) * 100
Best when: Bearish conviction, intraday fear signals firing (skewing spike + contango collapse)
"""

from typing import Any, Dict, List, Optional

from .base_strategy_agent import StrategyAgentBase
from ...config import TradeBacktestCfg
from ...types import TradeStructure


class PutDebitSpreadAgent(StrategyAgentBase):
    """Put debit spread (bear put spread) strategy agent."""

    STRUCTURE = TradeStructure.PUT_DEBIT_SPREAD
    NUM_LEGS = 2

    def __init__(self, config: TradeBacktestCfg = None):
        config = config or TradeBacktestCfg()
        super().__init__("PutDebitSpread", config)

    def find_strikes(self, chain: List[Dict],
                     spot: float) -> Optional[Dict]:
        cfg = self.config
        # Long put = higher abs delta (closer to ATM, higher strike)
        long_puts = self._find_puts(chain, cfg.put_ds_long, cfg.delta_tol)
        # Short put = lower abs delta (more OTM, lower strike)
        short_puts = self._find_puts(chain, cfg.put_ds_short, cfg.delta_tol)

        if not long_puts or not short_puts:
            return None

        lp = long_puts[0]   # higher delta = higher strike
        sp = short_puts[0]  # lower delta = lower strike

        # Long put must have higher strike than short put
        if lp["strike"] <= sp["strike"]:
            return None

        return {
            "long": lp,
            "short": sp,
            "long_strike": lp["strike"],
            "short_strike": sp["strike"],
            "width": lp["strike"] - sp["strike"],
            "long_delta": lp.get("put_delta", 0),
            "short_delta": sp.get("put_delta", 0),
        }

    def simulate_entry(self, strikes: Dict,
                       risk_budget: float) -> Optional[Dict]:
        cfg = self.config
        lp = strikes["long"]
        sp = strikes["short"]

        # Buy the higher-strike put (more expensive), sell the lower-strike put
        cost = lp.get("putAskPrice", 0) - sp.get("putBidPrice", 0)
        if cost <= 0:
            return None

        # Apply slippage + bid-ask penalty
        cost_slip = cost * (1 + cfg.slippage)
        ba_penalty = (self._bid_ask_penalty(
            lp.get("putBidPrice", 0), lp.get("putAskPrice", 0)) +
            self._bid_ask_penalty(
                sp.get("putBidPrice", 0), sp.get("putAskPrice", 0)))
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
        # Breakeven = long strike - debit paid
        breakeven = strikes["long_strike"] - cost

        return {
            "max_loss": fill["max_risk"],
            "max_profit": fill["max_profit"],
            "breakeven": round(breakeven, 2),
            "width": width,
            "net_delta": round(strikes["long_delta"] - strikes["short_delta"], 3),
        }

    def compute_pnl(self, strikes: Dict, fill: Dict,
                    exit_price: float) -> float:
        # Long put value = max(0, long_strike - exit_price)
        lv = max(0, strikes["long_strike"] - exit_price)
        # Short put liability = max(0, short_strike - exit_price)
        sv = max(0, strikes["short_strike"] - exit_price)
        pnl_per = (lv - sv) - fill["entry_cost"]
        return round(pnl_per * 100 * fill["qty"] - fill["comm"], 2)

    def check_exit(self, position: Dict,
                   current_price: float) -> Optional[str]:
        entry = position.get("entry_cost", 0)
        width = position.get("width", 0)
        long_strike = position.get("long_strike", 0)
        short_strike = position.get("short_strike", 0)
        max_profit_per = width - entry

        # Current intrinsic value of the spread
        lv = max(0, long_strike - current_price)
        sv = max(0, short_strike - current_price)
        current_value = lv - sv
        pnl_per = current_value - entry

        if max_profit_per > 0 and pnl_per >= max_profit_per * 0.50:
            return "profit_target_50pct"
        if pnl_per <= -entry:
            return "max_loss"
        return None
