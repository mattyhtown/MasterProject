"""
BrokenWingButterflyAgent — Asymmetric butterfly with bullish bias.

Structure: Buy 1 lower call, Sell 2 middle calls, Buy 1 higher call
  - Lower wing: wider (more bullish bias)
  - Upper wing: narrower (less protection above)
  - Near-zero cost with max profit at center strike

Max profit: lower_width - net debit (at center strike)
Max risk: net debit paid (below) or upper_width - credit (above)
Best when: Very strong signal (5 core), targeting a specific price pin

Execution haircuts:
  - 4 legs = highest commission of all structures
  - Middle legs (2x sell) get worst fills
  - Wide bid-ask on OTM wings adds execution friction
"""

from typing import Any, Dict, List, Optional

from .base_strategy_agent import StrategyAgentBase
from ...config import BrokenWingButterflyCfg
from ...types import TradeStructure


class BrokenWingButterflyAgent(StrategyAgentBase):
    """Broken wing butterfly strategy agent."""

    STRUCTURE = TradeStructure.BROKEN_WING_BUTTERFLY
    NUM_LEGS = 4  # 1 long lower + 2 short middle + 1 long upper

    def __init__(self, config: BrokenWingButterflyCfg = None):
        config = config or BrokenWingButterflyCfg()
        super().__init__("BrokenWingButterfly", config)

    def find_strikes(self, chain: List[Dict],
                     spot: float) -> Optional[Dict]:
        cfg = self.config
        lower = self._find_calls(chain, cfg.lower_delta, cfg.delta_tol)
        middle = self._find_calls(chain, cfg.middle_delta, cfg.delta_tol)
        upper = self._find_calls(chain, cfg.upper_delta, cfg.delta_tol)

        if not lower or not middle or not upper:
            return None

        bl = lower[0]
        bm = middle[0]
        bu = upper[0]

        if not (bl["strike"] < bm["strike"] < bu["strike"]):
            return None

        lower_width = bm["strike"] - bl["strike"]
        upper_width = bu["strike"] - bm["strike"]

        return {
            "lower": bl,
            "middle": bm,
            "upper": bu,
            "lower_strike": bl["strike"],
            "middle_strike": bm["strike"],
            "upper_strike": bu["strike"],
            "lower_width": lower_width,
            "upper_width": upper_width,
            "lower_delta": bl.get("delta", 0),
            "middle_delta": bm.get("delta", 0),
            "upper_delta": bu.get("delta", 0),
        }

    def simulate_entry(self, strikes: Dict,
                       risk_budget: float) -> Optional[Dict]:
        cfg = self.config
        bl = strikes["lower"]
        bm = strikes["middle"]
        bu = strikes["upper"]

        # Buy 1 lower, sell 2 middle, buy 1 upper
        cost = (bl.get("callAskPrice", 0) -
                2 * bm.get("callBidPrice", 0) +
                bu.get("callAskPrice", 0))

        if cost > 0:
            cost_slip = cost * (1 + cfg.slippage)
        else:
            cost_slip = cost * (1 - cfg.slippage)

        # Bid-ask penalty on all 4 legs
        ba_penalty = (
            self._bid_ask_penalty(
                bl.get("callBidPrice", 0), bl.get("callAskPrice", 0)) +
            2 * self._bid_ask_penalty(
                bm.get("callBidPrice", 0), bm.get("callAskPrice", 0)) +
            self._bid_ask_penalty(
                bu.get("callBidPrice", 0), bu.get("callAskPrice", 0))
        )
        cost_slip += ba_penalty

        comm = cfg.commission_per_leg * 4
        lower_width = strikes["lower_width"]
        upper_width = strikes["upper_width"]

        # Risk: max of (debit paid, upper_width - credit if net credit)
        if cost_slip > 0:
            risk_per = cost_slip * 100 + comm
        else:
            risk_per = max(lower_width, upper_width) * 100 + comm

        qty = max(1, int(risk_budget / risk_per))
        max_profit = (lower_width - cost_slip) * 100 * qty - comm * qty

        return {
            "entry_cost": round(cost_slip, 4),
            "is_credit": cost_slip < 0,
            "ba_penalty": round(ba_penalty, 4),
            "qty": qty,
            "comm": round(comm * qty, 2),
            "max_risk": round(risk_per * qty, 2),
            "max_profit": round(max_profit, 2),
            "risk_reward": round(max_profit / (risk_per * qty), 2) if risk_per > 0 else 0,
        }

    def compute_risk(self, strikes: Dict, fill: Dict) -> Dict:
        cost = fill["entry_cost"]
        lower_width = strikes["lower_width"]

        # Breakevens
        lower_be = strikes["lower_strike"] + max(0, cost)
        upper_be = strikes["middle_strike"] + (lower_width - cost)
        # Cap upper BE at upper_strike (defined risk above)
        upper_be = min(upper_be, strikes["upper_strike"])

        return {
            "max_loss": fill["max_risk"],
            "max_profit": fill["max_profit"],
            "lower_breakeven": round(lower_be, 2),
            "upper_breakeven": round(upper_be, 2),
            "profit_zone": f"{lower_be:.0f} - {upper_be:.0f}",
            "lower_width": lower_width,
            "upper_width": strikes["upper_width"],
            "net_delta": round(
                strikes["lower_delta"] -
                2 * strikes["middle_delta"] +
                strikes["upper_delta"], 3),
        }

    def compute_pnl(self, strikes: Dict, fill: Dict,
                    exit_price: float) -> float:
        bl_val = max(0, exit_price - strikes["lower_strike"])
        bm_val = max(0, exit_price - strikes["middle_strike"])
        bu_val = max(0, exit_price - strikes["upper_strike"])
        pnl_per = bl_val - 2 * bm_val + bu_val - fill["entry_cost"]
        return round(pnl_per * 100 * fill["qty"] - fill["comm"], 2)

    def check_exit(self, position: Dict,
                   current_price: float) -> Optional[str]:
        entry = position.get("entry_cost", 0)
        lower_width = position.get("lower_width", 0)
        middle_strike = position.get("middle_strike", 0)

        # Max profit at middle strike
        max_profit_per = lower_width - entry

        # Estimate current value
        bl_val = max(0, current_price - position.get("lower_strike", 0))
        bm_val = max(0, current_price - middle_strike)
        bu_val = max(0, current_price - position.get("upper_strike", 0))
        current_pnl = bl_val - 2 * bm_val + bu_val - entry

        if max_profit_per > 0 and current_pnl >= max_profit_per * 0.50:
            return "profit_target_50pct"

        # Stop loss
        if current_pnl <= -abs(entry) * 1.5:
            return "stop_loss"

        return None
