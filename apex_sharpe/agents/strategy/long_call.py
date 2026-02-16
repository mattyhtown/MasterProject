"""
LongCallAgent — Buy ATM call for directional convexity.

Structure: Buy 1x ~50d call
Max risk: premium paid
Max profit: unlimited (capped in backtest)
Best when: Low IV (cheap premium), strong signal conviction
"""

from typing import Any, Dict, List, Optional

from .base_strategy_agent import StrategyAgentBase
from ...config import TradeBacktestCfg
from ...types import TradeStructure


class LongCallAgent(StrategyAgentBase):
    """Long call strategy agent for directional convexity."""

    STRUCTURE = TradeStructure.LONG_CALL
    NUM_LEGS = 1

    def __init__(self, config: TradeBacktestCfg = None):
        config = config or TradeBacktestCfg()
        super().__init__("LongCall", config)

    def find_strikes(self, chain: List[Dict],
                     spot: float) -> Optional[Dict]:
        cfg = self.config
        atm_calls = self._find_calls(chain, cfg.long_call_delta, cfg.delta_tol)
        if not atm_calls:
            return None

        ac = atm_calls[0]
        return {
            "call": ac,
            "strike": ac["strike"],
            "delta": ac.get("delta", 0),
            "iv": ac.get("smvVol", ac.get("callMidIv", 0)),
        }

    def simulate_entry(self, strikes: Dict,
                       risk_budget: float) -> Optional[Dict]:
        cfg = self.config
        ac = strikes["call"]
        cost = ac.get("callAskPrice", 0)
        if cost <= 0:
            return None

        cost_slip = cost * (1 + cfg.slippage)
        ba_penalty = self._bid_ask_penalty(
            ac.get("callBidPrice", 0), ac.get("callAskPrice", 0))
        cost_slip += ba_penalty

        comm = cfg.commission_per_leg
        risk_per = cost_slip * 100 + comm
        qty = max(1, int(risk_budget / risk_per))

        return {
            "entry_cost": round(cost_slip, 4),
            "raw_cost": round(cost, 4),
            "ba_penalty": round(ba_penalty, 4),
            "qty": qty,
            "comm": round(comm * qty, 2),
            "max_risk": round(risk_per * qty, 2),
            "max_profit": None,  # Unlimited
            "risk_reward": None,  # Unlimited upside
        }

    def compute_risk(self, strikes: Dict, fill: Dict) -> Dict:
        cost = fill["entry_cost"]
        breakeven = strikes["strike"] + cost

        return {
            "max_loss": fill["max_risk"],
            "max_profit": "unlimited",
            "breakeven": round(breakeven, 2),
            "net_delta": round(strikes["delta"], 3),
        }

    def compute_pnl(self, strikes: Dict, fill: Dict,
                    exit_price: float) -> float:
        expiry_val = max(0, exit_price - strikes["strike"])
        pnl_per = expiry_val - fill["entry_cost"]
        return round(pnl_per * 100 * fill["qty"] - fill["comm"], 2)

    def check_exit(self, position: Dict,
                   current_price: float) -> Optional[str]:
        entry = position.get("entry_cost", 0)
        strike = position.get("strike", 0)
        intrinsic = max(0, current_price - strike)

        # Take profit at 100% gain
        if intrinsic >= entry * 2:
            return "profit_target_100pct"
        # Stop loss at 80% of premium
        if intrinsic < entry * 0.20 and current_price < strike:
            return "stop_loss_80pct"
        return None
