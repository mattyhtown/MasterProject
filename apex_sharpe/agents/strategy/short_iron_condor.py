"""
ShortIronCondorAgent — Sell OTM put + OTM call, buy wings.

Structure: Sell 1x ~25d put, Sell 1x ~25d call, Buy 1x ~10d put, Buy 1x ~10d call
Max risk: (wider_width - credit) * 100
Max profit: total credit received
Best when: Elevated IV, expecting range-bound / small bounce.
0DTE version of the monthly IC with tighter wings for signal days.
"""

from typing import Any, Dict, List, Optional

from .base_strategy_agent import StrategyAgentBase
from ...config import TradeBacktestCfg
from ...types import TradeStructure


class ShortIronCondorAgent(StrategyAgentBase):
    """Short iron condor strategy agent — OTM sell with wings."""

    STRUCTURE = TradeStructure.SHORT_IRON_CONDOR
    NUM_LEGS = 4

    def __init__(self, config: TradeBacktestCfg = None):
        config = config or TradeBacktestCfg()
        super().__init__("ShortIronCondor", config)

    def find_strikes(self, chain: List[Dict],
                     spot: float) -> Optional[Dict]:
        cfg = self.config
        # Short sides: ~25 delta (same as monthly IC short delta)
        short_calls = self._find_calls(chain, cfg.ic_short_delta, cfg.delta_tol)
        short_puts = self._find_puts(chain, cfg.ic_short_delta, cfg.delta_tol)
        # Long sides: ~10 delta (wider wings for 0DTE)
        long_calls = self._find_calls(chain, cfg.ic_long_delta, cfg.delta_tol)
        long_puts = self._find_puts(chain, cfg.ic_long_delta, cfg.delta_tol)

        if not short_calls or not short_puts or not long_calls or not long_puts:
            return None

        sc = short_calls[0]
        sp = short_puts[0]
        lc = long_calls[0]
        lp = long_puts[0]

        # Validate ordering: lp < sp < sc < lc
        if not (lp["strike"] < sp["strike"] < sc["strike"] < lc["strike"]):
            return None

        call_width = lc["strike"] - sc["strike"]
        put_width = sp["strike"] - lp["strike"]

        return {
            "short_call": sc,
            "short_put": sp,
            "long_call": lc,
            "long_put": lp,
            "short_call_strike": sc["strike"],
            "short_put_strike": sp["strike"],
            "long_call_strike": lc["strike"],
            "long_put_strike": lp["strike"],
            "call_width": call_width,
            "put_width": put_width,
        }

    def simulate_entry(self, strikes: Dict,
                       risk_budget: float) -> Optional[Dict]:
        cfg = self.config
        sc = strikes["short_call"]
        sp = strikes["short_put"]
        lc = strikes["long_call"]
        lp = strikes["long_put"]

        # Call side credit
        call_credit = sc.get("callBidPrice", 0) - lc.get("callAskPrice", 0)
        # Put side credit
        put_credit = sp.get("putBidPrice", 0) - lp.get("putAskPrice", 0)

        total_credit = call_credit + put_credit
        if total_credit <= 0:
            return None

        total_credit_slip = total_credit * (1 - cfg.slippage)
        ba_penalty = (
            self._bid_ask_penalty(sc.get("callBidPrice", 0), sc.get("callAskPrice", 0)) +
            self._bid_ask_penalty(lc.get("callBidPrice", 0), lc.get("callAskPrice", 0)) +
            self._bid_ask_penalty(sp.get("putBidPrice", 0), sp.get("putAskPrice", 0)) +
            self._bid_ask_penalty(lp.get("putBidPrice", 0), lp.get("putAskPrice", 0))
        )
        total_credit_slip -= ba_penalty

        if total_credit_slip <= 0:
            return None

        max_wing = max(strikes["call_width"], strikes["put_width"])
        comm = cfg.commission_per_leg * 4
        risk_per = (max_wing - total_credit_slip) * 100 + comm
        if risk_per <= 0:
            return None

        qty = max(1, int(risk_budget / risk_per))
        max_profit = total_credit_slip * 100 * qty - comm * qty

        return {
            "entry_credit": round(total_credit_slip, 4),
            "raw_credit": round(total_credit, 4),
            "ba_penalty": round(ba_penalty, 4),
            "qty": qty,
            "comm": round(comm * qty, 2),
            "max_risk": round(risk_per * qty, 2),
            "max_profit": round(max_profit, 2),
            "risk_reward": round(max_profit / (risk_per * qty), 2) if risk_per > 0 else 0,
        }

    def compute_risk(self, strikes: Dict, fill: Dict) -> Dict:
        credit = fill["entry_credit"]
        be_upper = strikes["short_call_strike"] + credit
        be_lower = strikes["short_put_strike"] - credit

        return {
            "max_loss": fill["max_risk"],
            "max_profit": fill["max_profit"],
            "breakeven_upper": round(be_upper, 2),
            "breakeven_lower": round(be_lower, 2),
        }

    def compute_pnl(self, strikes: Dict, fill: Dict,
                    exit_price: float) -> float:
        # Short call liability
        sc_liab = max(0, exit_price - strikes["short_call_strike"])
        # Long call recovery
        lc_recov = max(0, exit_price - strikes["long_call_strike"])
        # Short put liability
        sp_liab = max(0, strikes["short_put_strike"] - exit_price)
        # Long put recovery
        lp_recov = max(0, strikes["long_put_strike"] - exit_price)

        call_cost = sc_liab - lc_recov
        put_cost = sp_liab - lp_recov
        pnl_per = fill["entry_credit"] - (call_cost + put_cost)
        return round(pnl_per * 100 * fill["qty"] - fill["comm"], 2)

    def check_exit(self, position: Dict,
                   current_price: float) -> Optional[str]:
        credit = position.get("entry_credit", 0)
        sc_strike = position.get("short_call_strike", 0)
        sp_strike = position.get("short_put_strike", 0)
        max_wing = max(
            position.get("long_call_strike", 0) - sc_strike,
            sp_strike - position.get("long_put_strike", 0),
        )

        sc_liab = max(0, current_price - sc_strike)
        sp_liab = max(0, sp_strike - current_price)
        net_liab = sc_liab + sp_liab

        if net_liab <= credit * 0.50:
            return "profit_target_50pct"
        if net_liab >= max_wing:
            return "max_loss"
        return None
