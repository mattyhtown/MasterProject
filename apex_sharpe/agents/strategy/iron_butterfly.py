"""
IronButterflyAgent — Sell ATM call + ATM put, buy OTM wings.

Structure: Sell 1x ATM call, Sell 1x ATM put, Buy 1x OTM call, Buy 1x OTM put
Max risk: (wing_width - credit) * 100
Max profit: total credit received
Best when: Elevated IV, expecting pin near current price.
Maximum theta collection — outperforms IC when vol is rich and move is small.
"""

from typing import Any, Dict, List, Optional

from .base_strategy_agent import StrategyAgentBase
from ...config import TradeBacktestCfg
from ...types import TradeStructure


class IronButterflyAgent(StrategyAgentBase):
    """Iron butterfly strategy agent — ATM sell with wings."""

    STRUCTURE = TradeStructure.IRON_BUTTERFLY
    NUM_LEGS = 4

    def __init__(self, config: TradeBacktestCfg = None):
        config = config or TradeBacktestCfg()
        super().__init__("IronButterfly", config)

    def find_strikes(self, chain: List[Dict],
                     spot: float) -> Optional[Dict]:
        cfg = self.config
        # ATM call and put: delta ~0.50
        atm_calls = self._find_calls(chain, cfg.ifly_atm_delta, cfg.delta_tol)
        if not atm_calls:
            return None
        atm = atm_calls[0]
        atm_strike = atm["strike"]

        # OTM call wing
        wing_calls = self._find_calls(chain, cfg.ifly_wing_delta, cfg.delta_tol)
        # OTM put wing
        wing_puts = self._find_puts(chain, cfg.ifly_wing_delta, cfg.delta_tol)

        if not wing_calls or not wing_puts:
            return None

        wc = wing_calls[0]
        wp = wing_puts[0]

        # Wings must be outside ATM
        if wc["strike"] <= atm_strike or wp["strike"] >= atm_strike:
            return None

        call_width = wc["strike"] - atm_strike
        put_width = atm_strike - wp["strike"]

        return {
            "atm": atm,
            "wing_call": wc,
            "wing_put": wp,
            "atm_strike": atm_strike,
            "wing_call_strike": wc["strike"],
            "wing_put_strike": wp["strike"],
            "call_width": call_width,
            "put_width": put_width,
            "atm_delta": atm.get("delta", 0.50),
        }

    def simulate_entry(self, strikes: Dict,
                       risk_budget: float) -> Optional[Dict]:
        cfg = self.config
        atm = strikes["atm"]
        wc = strikes["wing_call"]
        wp = strikes["wing_put"]

        # Credit = sell ATM call + sell ATM put - buy wing call - buy wing put
        # ATM call and put share the same strike
        call_credit = atm.get("callBidPrice", 0) - wc.get("callAskPrice", 0)
        put_credit = atm.get("putBidPrice", 0) - wp.get("putAskPrice", 0)

        # Put price uses put_delta from _find_puts, but bid/ask are in the row
        # For ATM, putBidPrice is on the same strike row
        atm_put_bid = atm.get("putBidPrice", 0)
        wp_put_ask = wp.get("putAskPrice", 0)
        put_credit = atm_put_bid - wp_put_ask

        total_credit = call_credit + put_credit
        if total_credit <= 0:
            return None

        total_credit_slip = total_credit * (1 - cfg.slippage)
        ba_penalty = (
            self._bid_ask_penalty(atm.get("callBidPrice", 0), atm.get("callAskPrice", 0)) +
            self._bid_ask_penalty(wc.get("callBidPrice", 0), wc.get("callAskPrice", 0)) +
            self._bid_ask_penalty(atm_put_bid, atm.get("putAskPrice", 0)) +
            self._bid_ask_penalty(wp.get("putBidPrice", 0), wp_put_ask)
        )
        total_credit_slip -= ba_penalty

        if total_credit_slip <= 0:
            return None

        # Max risk = wider wing width - credit
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
        be_upper = strikes["atm_strike"] + credit
        be_lower = strikes["atm_strike"] - credit

        return {
            "max_loss": fill["max_risk"],
            "max_profit": fill["max_profit"],
            "breakeven_upper": round(be_upper, 2),
            "breakeven_lower": round(be_lower, 2),
            "atm_strike": strikes["atm_strike"],
        }

    def compute_pnl(self, strikes: Dict, fill: Dict,
                    exit_price: float) -> float:
        atm = strikes["atm_strike"]
        wc_strike = strikes["wing_call_strike"]
        wp_strike = strikes["wing_put_strike"]

        # Short ATM call liability
        sc_liab = max(0, exit_price - atm)
        # Long wing call recovery
        lc_recov = max(0, exit_price - wc_strike)
        # Short ATM put liability
        sp_liab = max(0, atm - exit_price)
        # Long wing put recovery
        lp_recov = max(0, wp_strike - exit_price)

        net_liab = (sc_liab - lc_recov) + (sp_liab - lp_recov)
        pnl_per = fill["entry_credit"] - net_liab
        return round(pnl_per * 100 * fill["qty"] - fill["comm"], 2)

    def check_exit(self, position: Dict,
                   current_price: float) -> Optional[str]:
        credit = position.get("entry_credit", 0)
        atm = position.get("atm_strike", 0)
        max_wing = max(
            position.get("wing_call_strike", 0) - atm,
            atm - position.get("wing_put_strike", 0),
        )

        sc_liab = max(0, current_price - atm)
        sp_liab = max(0, atm - current_price)
        net_liab = sc_liab + sp_liab

        if net_liab <= credit * 0.50:
            return "profit_target_50pct"
        if net_liab >= max_wing:
            return "max_loss"
        return None
