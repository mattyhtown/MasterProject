"""
BullPutSpreadAgent — Sell higher-delta put, buy lower-delta put.

Structure: Sell 1x ~30d put, Buy 1x ~15d put
Max risk: (width - credit) * 100
Max profit: credit received
Best when: High IV + steep skew (selling rich put premium)
"""

from typing import Any, Dict, List, Optional

from .base_strategy_agent import StrategyAgentBase
from ...config import TradeBacktestCfg
from ...types import TradeStructure


class BullPutSpreadAgent(StrategyAgentBase):
    """Bull put credit spread strategy agent."""

    STRUCTURE = TradeStructure.BULL_PUT_SPREAD
    NUM_LEGS = 2

    def __init__(self, config: TradeBacktestCfg = None):
        config = config or TradeBacktestCfg()
        super().__init__("BullPutSpread", config)

    def find_strikes(self, chain: List[Dict],
                     spot: float) -> Optional[Dict]:
        cfg = self.config
        short_puts = self._find_puts(chain, cfg.bull_ps_short, cfg.delta_tol)
        long_puts = self._find_puts(chain, cfg.bull_ps_long, cfg.delta_tol)

        if not short_puts or not long_puts:
            return None

        sp = short_puts[0]
        lp = long_puts[0]

        if sp["strike"] <= lp["strike"]:
            return None

        return {
            "short": sp,
            "long": lp,
            "short_strike": sp["strike"],
            "long_strike": lp["strike"],
            "width": sp["strike"] - lp["strike"],
            "short_delta": sp.get("put_delta", 0),
            "long_delta": lp.get("put_delta", 0),
        }

    def simulate_entry(self, strikes: Dict,
                       risk_budget: float) -> Optional[Dict]:
        cfg = self.config
        sp = strikes["short"]
        lp = strikes["long"]

        credit = sp.get("putBidPrice", 0) - lp.get("putAskPrice", 0)
        if credit <= 0:
            return None

        # Credit gets haircut by slippage
        credit_slip = credit * (1 - cfg.slippage)
        ba_penalty = (self._bid_ask_penalty(
            sp.get("putBidPrice", 0), sp.get("putAskPrice", 0)) +
            self._bid_ask_penalty(
                lp.get("putBidPrice", 0), lp.get("putAskPrice", 0)))
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
        breakeven = strikes["short_strike"] - credit

        return {
            "max_loss": fill["max_risk"],
            "max_profit": fill["max_profit"],
            "breakeven": round(breakeven, 2),
            "width": width,
            "net_delta": round(abs(strikes["short_delta"]) - abs(strikes["long_delta"]), 3),
        }

    def compute_pnl(self, strikes: Dict, fill: Dict,
                    exit_price: float) -> float:
        sp_liab = max(0, strikes["short_strike"] - exit_price)
        lp_recov = max(0, strikes["long_strike"] - exit_price)
        pnl_per = fill["entry_credit"] - (sp_liab - lp_recov)
        return round(pnl_per * 100 * fill["qty"] - fill["comm"], 2)

    def check_exit(self, position: Dict,
                   current_price: float) -> Optional[str]:
        credit = position.get("entry_credit", 0)
        short_strike = position.get("short_strike", 0)
        long_strike = position.get("long_strike", 0)
        width = short_strike - long_strike

        sp_liab = max(0, short_strike - current_price)
        lp_recov = max(0, long_strike - current_price)
        current_cost = sp_liab - lp_recov

        if current_cost <= credit * 0.50:
            return "profit_target_50pct"
        if current_cost >= width:
            return "max_loss"
        return None
