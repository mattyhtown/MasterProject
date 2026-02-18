"""
BearCallSpreadAgent — Sell lower-delta call, buy higher-delta call.

Structure: Sell 1x ~30d call (closer to ATM), Buy 1x ~15d call (OTM)
Max risk: (width - credit) * 100
Max profit: credit received
Best when: Bearish, selling rich call premium after vol spike.
Mirror of BullPutSpread but on the call side.
"""

from typing import Any, Dict, List, Optional

from .base_strategy_agent import StrategyAgentBase
from ...config import TradeBacktestCfg
from ...types import TradeStructure


class BearCallSpreadAgent(StrategyAgentBase):
    """Bear call credit spread strategy agent."""

    STRUCTURE = TradeStructure.BEAR_CALL_SPREAD
    NUM_LEGS = 2

    def __init__(self, config: TradeBacktestCfg = None):
        config = config or TradeBacktestCfg()
        super().__init__("BearCallSpread", config)

    def find_strikes(self, chain: List[Dict],
                     spot: float) -> Optional[Dict]:
        cfg = self.config
        # Short call = closer to ATM (higher delta)
        short_calls = self._find_calls(chain, cfg.bear_cs_short, cfg.delta_tol)
        # Long call = more OTM (lower delta)
        long_calls = self._find_calls(chain, cfg.bear_cs_long, cfg.delta_tol)

        if not short_calls or not long_calls:
            return None

        sc = short_calls[0]
        lc = long_calls[0]

        # Short call must be lower strike than long call
        if sc["strike"] >= lc["strike"]:
            return None

        return {
            "short": sc,
            "long": lc,
            "short_strike": sc["strike"],
            "long_strike": lc["strike"],
            "width": lc["strike"] - sc["strike"],
            "short_delta": sc.get("delta", 0),
            "long_delta": lc.get("delta", 0),
        }

    def simulate_entry(self, strikes: Dict,
                       risk_budget: float) -> Optional[Dict]:
        cfg = self.config
        sc = strikes["short"]
        lc = strikes["long"]

        credit = sc.get("callBidPrice", 0) - lc.get("callAskPrice", 0)
        if credit <= 0:
            return None

        credit_slip = credit * (1 - cfg.slippage)
        ba_penalty = (self._bid_ask_penalty(
            sc.get("callBidPrice", 0), sc.get("callAskPrice", 0)) +
            self._bid_ask_penalty(
                lc.get("callBidPrice", 0), lc.get("callAskPrice", 0)))
        credit_slip -= ba_penalty

        if credit_slip <= 0:
            return None

        width = strikes["width"]
        comm = cfg.commission_per_leg * 2
        risk_per = (width - credit_slip) * 100 + comm
        if risk_per <= 0:
            return None

        qty = max(1, int(risk_budget / risk_per))
        max_profit = credit_slip * 100 * qty - comm * qty

        return {
            "entry_credit": round(credit_slip, 4),
            "raw_credit": round(credit, 4),
            "ba_penalty": round(ba_penalty, 4),
            "qty": qty,
            "comm": round(comm * qty, 2),
            "max_risk": round(risk_per * qty, 2),
            "max_profit": round(max_profit, 2),
            "risk_reward": round(max_profit / (risk_per * qty), 2) if risk_per > 0 else 0,
        }

    def compute_risk(self, strikes: Dict, fill: Dict) -> Dict:
        width = strikes["width"]
        credit = fill["entry_credit"]
        breakeven = strikes["short_strike"] + credit

        return {
            "max_loss": fill["max_risk"],
            "max_profit": fill["max_profit"],
            "breakeven": round(breakeven, 2),
            "width": width,
            "net_delta": round(strikes["long_delta"] - strikes["short_delta"], 3),
        }

    def compute_pnl(self, strikes: Dict, fill: Dict,
                    exit_price: float) -> float:
        # Short call liability
        sc_liab = max(0, exit_price - strikes["short_strike"])
        # Long call recovery
        lc_recov = max(0, exit_price - strikes["long_strike"])
        pnl_per = fill["entry_credit"] - (sc_liab - lc_recov)
        return round(pnl_per * 100 * fill["qty"] - fill["comm"], 2)

    def check_exit(self, position: Dict,
                   current_price: float) -> Optional[str]:
        credit = position.get("entry_credit", 0)
        short_strike = position.get("short_strike", 0)
        long_strike = position.get("long_strike", 0)
        width = long_strike - short_strike

        sc_liab = max(0, current_price - short_strike)
        lc_recov = max(0, current_price - long_strike)
        current_cost = sc_liab - lc_recov

        if current_cost <= credit * 0.50:
            return "profit_target_50pct"
        if current_cost >= width:
            return "max_loss"
        return None
