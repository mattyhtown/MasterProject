"""
TradeStructureBacktest — backtest 10 trade structures on FEAR_BOUNCE signal days.

Supports signal-weighted sizing via SignalSizer and adaptive structure
selection via AdaptiveSelector. Compares all structures side-by-side plus
virtual strategies: "Adaptive" (best structure per day), regime split
(bounce vs sell-through), and "Flip" (bullish + bearish recovery).
"""

import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .base import BaseAgent
from .zero_dte import ZeroDTEAgent
from ..config import (TradeBacktestCfg, ZeroDTECfg, SignalSizingCfg,
                       AdaptiveSelectorCfg, CallRatioSpreadCfg,
                       BrokenWingButterflyCfg)
from ..selection.signal_sizer import SignalSizer
from ..selection.adaptive_selector import AdaptiveSelector
from ..types import AgentResult, C, TradeStructure


class TradeStructureBacktest(BaseAgent):
    """Backtest 10 trade structures on FEAR_BOUNCE signal days.

    Bullish structures:
      1. Call debit spread  (buy ~40d call, sell ~25d call)
      2. Bull put spread    (sell ~30d put, buy ~15d put)
      3. Long call          (buy ~50d call)
      4. Call ratio spread  (buy 1x ~50d, sell 2x ~25d)
      5. Broken wing butterfly (1/-2/1 asymmetric calls)

    Bearish structures:
      6. Put debit spread   (buy ~40d put, sell ~25d put)
      7. Long put           (buy ~50d put)
      8. Bear call spread   (sell ~30d call, buy ~15d call)

    Neutral / vol-sell structures:
      9.  Iron butterfly     (sell ATM call+put, buy OTM wings)
      10. Short iron condor  (sell OTM call+put, buy further OTM wings)

    Virtual strategies:
      - Adaptive: best structure per day via vol surface conditions
      - Oracle Flip: best bullish on bounces, best bearish on sell-throughs
      - Signal Flip: adaptive pick + bearish recovery when adaptive loses
    """

    # Structure display order
    STRUCTURE_NAMES = [
        "Call Debit Spread", "Bull Put Spread", "Long Call",
        "Call Ratio Spread", "Broken Wing Butterfly",
        "Put Debit Spread", "Long Put", "Bear Call Spread",
        "Iron Butterfly", "Short Iron Condor",
    ]

    def __init__(self, config: TradeBacktestCfg = None,
                 zero_dte_config: ZeroDTECfg = None,
                 sizing_config: SignalSizingCfg = None,
                 selector_config: AdaptiveSelectorCfg = None,
                 crs_config: CallRatioSpreadCfg = None,
                 bwb_config: BrokenWingButterflyCfg = None):
        config = config or TradeBacktestCfg()
        super().__init__("TradeBacktest", config)
        self.monitor = ZeroDTEAgent(zero_dte_config)
        self.sizer = SignalSizer(sizing_config)
        self.selector = AdaptiveSelector(selector_config)
        self.crs_config = crs_config or CallRatioSpreadCfg()
        self.bwb_config = bwb_config or BrokenWingButterflyCfg()

    def run(self, context: Dict[str, Any]) -> AgentResult:
        """Run the backtest. Context keys: orats, state, months (opt)."""
        months = context.get("months", 12)
        use_sizing = context.get("use_sizing", True)
        self.run_backtest(context["orats"], context["state"],
                          months=months, db=context.get("db"),
                          use_sizing=use_sizing)
        return self._result(success=True)

    # -- data helpers ---------------------------------------------------------

    @staticmethod
    def _fetch_hist_chain(orats, ticker: str, trade_date: str, cache: Dict,
                          save_fn) -> Optional[List[Dict]]:
        ck = f"hist_strikes_{ticker}_{trade_date}"
        if ck in cache:
            return cache[ck]
        resp = orats.hist_strikes(ticker, trade_date)
        if not resp or not resp.get("data"):
            return None
        cache[ck] = resp["data"]
        save_fn()
        return resp["data"]

    @staticmethod
    def _find_next_expiry(chain: List[Dict], signal_date: str,
                          max_days: int = 3) -> Optional[str]:
        sig = datetime.strptime(signal_date, "%Y-%m-%d").date()
        exps = sorted(set(r.get("expirDate", "") for r in chain))
        for exp_str in exps:
            try:
                exp_d = datetime.strptime(exp_str, "%Y-%m-%d").date()
            except (ValueError, TypeError):
                continue
            diff = (exp_d - sig).days
            if 1 <= diff <= max_days:
                return exp_str
        return None

    # -- strike selection -----------------------------------------------------

    @staticmethod
    def _find_calls(strikes: List[Dict], target_delta: float,
                    tol: float) -> List[Dict]:
        matches = []
        for row in strikes:
            d = row.get("delta")
            if d is not None and d > 0 and abs(d - target_delta) <= tol:
                matches.append(row)
        matches.sort(key=lambda r: abs(r["delta"] - target_delta))
        return matches

    @staticmethod
    def _find_puts(strikes: List[Dict], target_abs_delta: float,
                   tol: float) -> List[Dict]:
        matches = []
        for row in strikes:
            cd = row.get("delta")
            if cd is None:
                continue
            pd = cd - 1
            if abs(abs(pd) - target_abs_delta) <= tol:
                r = dict(row)
                r["put_delta"] = pd
                matches.append(r)
        matches.sort(key=lambda r: abs(abs(r["put_delta"]) - target_abs_delta))
        return matches

    # -- trade construction ---------------------------------------------------

    def _build_trades(self, strikes: List[Dict], spot: float,
                      next_close: float,
                      risk_budget: float = None) -> List[Dict]:
        """Build all 10 trade structures from a chain.

        Args:
            strikes: Filtered chain for target expiry.
            spot: Current spot price.
            next_close: Next-day close price (for P&L calc).
            risk_budget: Signal-weighted risk budget. Falls back to
                        config.max_risk if None.
        """
        cfg = self.config
        tol = cfg.delta_tol
        max_risk = risk_budget or cfg.max_risk
        trades = []

        # --- 1. Call Debit Spread ---
        long_calls = self._find_calls(strikes, cfg.call_ds_long, tol)
        short_calls = self._find_calls(strikes, cfg.call_ds_short, tol)
        if long_calls and short_calls:
            lc = long_calls[0]
            sc = short_calls[0]
            if lc["strike"] < sc["strike"]:
                cost = lc.get("callAskPrice", 0) - sc.get("callBidPrice", 0)
                if cost > 0:
                    cost_slip = cost * (1 + cfg.slippage)
                    comm = cfg.commission_per_leg * 2
                    risk_per = cost_slip * 100 + comm
                    qty = max(1, int(max_risk / risk_per))
                    lv = max(0, next_close - lc["strike"])
                    sv = max(0, next_close - sc["strike"])
                    pnl_per = (lv - sv) - cost_slip
                    total_pnl = pnl_per * 100 * qty - comm * qty
                    width = sc["strike"] - lc["strike"]
                    trades.append({
                        "name": "Call Debit Spread",
                        "structure": TradeStructure.CALL_DEBIT_SPREAD,
                        "long_strike": lc["strike"],
                        "short_strike": sc["strike"],
                        "long_delta": lc.get("delta", 0),
                        "short_delta": sc.get("delta", 0),
                        "entry_cost": round(cost_slip, 4),
                        "width": width,
                        "qty": qty,
                        "max_risk": round(risk_per * qty, 2),
                        "max_profit": round((width - cost_slip) * 100 * qty - comm * qty, 2),
                        "pnl": round(total_pnl, 2),
                        "comm": round(comm * qty, 2),
                    })

        # --- 2. Bull Put Credit Spread ---
        short_puts = self._find_puts(strikes, cfg.bull_ps_short, tol)
        long_puts = self._find_puts(strikes, cfg.bull_ps_long, tol)
        if short_puts and long_puts:
            sp = short_puts[0]
            lp = long_puts[0]
            if sp["strike"] > lp["strike"]:
                credit = sp.get("putBidPrice", 0) - lp.get("putAskPrice", 0)
                if credit > 0:
                    credit_slip = credit * (1 - cfg.slippage)
                    width = sp["strike"] - lp["strike"]
                    comm = cfg.commission_per_leg * 2
                    risk_per = (width - credit_slip) * 100 + comm
                    if risk_per > 0:
                        qty = max(1, int(max_risk / risk_per))
                        sp_liab = max(0, sp["strike"] - next_close)
                        lp_recov = max(0, lp["strike"] - next_close)
                        pnl_per = credit_slip - (sp_liab - lp_recov)
                        total_pnl = pnl_per * 100 * qty - comm * qty
                        trades.append({
                            "name": "Bull Put Spread",
                            "structure": TradeStructure.BULL_PUT_SPREAD,
                            "short_strike": sp["strike"],
                            "long_strike": lp["strike"],
                            "short_delta": sp.get("put_delta", 0),
                            "long_delta": lp.get("put_delta", 0),
                            "entry_credit": round(credit_slip, 4),
                            "width": width,
                            "qty": qty,
                            "max_risk": round(risk_per * qty, 2),
                            "max_profit": round(credit_slip * 100 * qty - comm * qty, 2),
                            "pnl": round(total_pnl, 2),
                            "comm": round(comm * qty, 2),
                        })

        # --- 3. Long Call ---
        atm_calls = self._find_calls(strikes, cfg.long_call_delta, tol)
        if atm_calls:
            ac = atm_calls[0]
            cost = ac.get("callAskPrice", 0)
            if cost > 0:
                cost_slip = cost * (1 + cfg.slippage)
                comm = cfg.commission_per_leg
                risk_per = cost_slip * 100 + comm
                qty = max(1, int(max_risk / risk_per))
                expiry_val = max(0, next_close - ac["strike"])
                pnl_per = expiry_val - cost_slip
                total_pnl = pnl_per * 100 * qty - comm * qty
                trades.append({
                    "name": "Long Call",
                    "structure": TradeStructure.LONG_CALL,
                    "strike": ac["strike"],
                    "delta": ac.get("delta", 0),
                    "entry_cost": round(cost_slip, 4),
                    "qty": qty,
                    "max_risk": round(risk_per * qty, 2),
                    "max_profit": None,
                    "pnl": round(total_pnl, 2),
                    "comm": round(comm * qty, 2),
                })

        # --- 4. Call Ratio Spread (1x2) ---
        crs = self.crs_config
        crs_long = self._find_calls(strikes, crs.long_delta, crs.delta_tol)
        crs_short = self._find_calls(strikes, crs.short_delta, crs.delta_tol)
        if crs_long and crs_short:
            cl = crs_long[0]
            cs = crs_short[0]
            if cl["strike"] < cs["strike"]:
                # Buy 1 long, sell 2 short
                cost = (cl.get("callAskPrice", 0) -
                        2 * cs.get("callBidPrice", 0))
                # cost can be negative (net credit) or positive (net debit)
                cost_slip = cost * (1 + crs.slippage) if cost > 0 else cost * (1 - crs.slippage)
                comm = crs.commission_per_leg * 3  # 3 legs
                width = cs["strike"] - cl["strike"]
                # Max risk is debit paid (if net debit) + unlimited above
                # upper breakeven; for backtest cap at next_close
                if cost_slip > 0:
                    risk_per = cost_slip * 100 + comm
                else:
                    # Net credit: risk is above upper breakeven
                    risk_per = width * 100 + comm  # conservative
                qty = max(1, int(max_risk / risk_per))

                # P&L at next_close
                long_val = max(0, next_close - cl["strike"])
                short_val = max(0, next_close - cs["strike"])
                pnl_per = long_val - 2 * short_val - cost_slip
                total_pnl = pnl_per * 100 * qty - comm * qty
                max_profit = (width - cost_slip) * 100 * qty - comm * qty

                trades.append({
                    "name": "Call Ratio Spread",
                    "structure": TradeStructure.CALL_RATIO_SPREAD,
                    "long_strike": cl["strike"],
                    "short_strike": cs["strike"],
                    "long_delta": cl.get("delta", 0),
                    "short_delta": cs.get("delta", 0),
                    "entry_cost": round(cost_slip, 4),
                    "width": width,
                    "qty": qty,
                    "ratio": "1x2",
                    "max_risk": round(risk_per * qty, 2),
                    "max_profit": round(max_profit, 2),
                    "pnl": round(total_pnl, 2),
                    "comm": round(comm * qty, 2),
                })

        # --- 5. Broken Wing Butterfly ---
        bwb = self.bwb_config
        bwb_lower = self._find_calls(strikes, bwb.lower_delta, bwb.delta_tol)
        bwb_mid = self._find_calls(strikes, bwb.middle_delta, bwb.delta_tol)
        bwb_upper = self._find_calls(strikes, bwb.upper_delta, bwb.delta_tol)
        if bwb_lower and bwb_mid and bwb_upper:
            bl = bwb_lower[0]
            bm = bwb_mid[0]
            bu = bwb_upper[0]
            if bl["strike"] < bm["strike"] < bu["strike"]:
                # Buy 1 lower, sell 2 middle, buy 1 upper
                cost = (bl.get("callAskPrice", 0) -
                        2 * bm.get("callBidPrice", 0) +
                        bu.get("callAskPrice", 0))
                cost_slip = cost * (1 + bwb.slippage) if cost > 0 else cost * (1 - bwb.slippage)
                comm = bwb.commission_per_leg * 4  # 4 legs
                lower_width = bm["strike"] - bl["strike"]
                upper_width = bu["strike"] - bm["strike"]

                if cost_slip > 0:
                    risk_per = cost_slip * 100 + comm
                else:
                    # Risk is max of (lower_width + debit, upper_width - credit)
                    risk_per = max(lower_width, upper_width) * 100 + comm
                qty = max(1, int(max_risk / risk_per))

                # P&L at next_close
                bl_val = max(0, next_close - bl["strike"])
                bm_val = max(0, next_close - bm["strike"])
                bu_val = max(0, next_close - bu["strike"])
                pnl_per = bl_val - 2 * bm_val + bu_val - cost_slip
                total_pnl = pnl_per * 100 * qty - comm * qty
                max_profit = (lower_width - cost_slip) * 100 * qty - comm * qty

                trades.append({
                    "name": "Broken Wing Butterfly",
                    "structure": TradeStructure.BROKEN_WING_BUTTERFLY,
                    "lower_strike": bl["strike"],
                    "middle_strike": bm["strike"],
                    "upper_strike": bu["strike"],
                    "lower_delta": bl.get("delta", 0),
                    "middle_delta": bm.get("delta", 0),
                    "upper_delta": bu.get("delta", 0),
                    "entry_cost": round(cost_slip, 4),
                    "lower_width": lower_width,
                    "upper_width": upper_width,
                    "qty": qty,
                    "max_risk": round(risk_per * qty, 2),
                    "max_profit": round(max_profit, 2),
                    "pnl": round(total_pnl, 2),
                    "comm": round(comm * qty, 2),
                })

        # --- 6. Put Debit Spread (Bear Put Spread) ---
        pd_long = self._find_puts(strikes, cfg.put_ds_long, tol)
        pd_short = self._find_puts(strikes, cfg.put_ds_short, tol)
        if pd_long and pd_short:
            lp = pd_long[0]   # higher abs delta = higher strike
            sp = pd_short[0]  # lower abs delta = lower strike
            if lp["strike"] > sp["strike"]:
                cost = lp.get("putAskPrice", 0) - sp.get("putBidPrice", 0)
                if cost > 0:
                    cost_slip = cost * (1 + cfg.slippage)
                    comm = cfg.commission_per_leg * 2
                    risk_per = cost_slip * 100 + comm
                    qty = max(1, int(max_risk / risk_per))
                    # P&L at next_close
                    lv = max(0, lp["strike"] - next_close)
                    sv = max(0, sp["strike"] - next_close)
                    pnl_per = (lv - sv) - cost_slip
                    total_pnl = pnl_per * 100 * qty - comm * qty
                    width = lp["strike"] - sp["strike"]
                    trades.append({
                        "name": "Put Debit Spread",
                        "structure": TradeStructure.PUT_DEBIT_SPREAD,
                        "long_strike": lp["strike"],
                        "short_strike": sp["strike"],
                        "long_delta": lp.get("put_delta", 0),
                        "short_delta": sp.get("put_delta", 0),
                        "entry_cost": round(cost_slip, 4),
                        "width": width,
                        "qty": qty,
                        "max_risk": round(risk_per * qty, 2),
                        "max_profit": round((width - cost_slip) * 100 * qty - comm * qty, 2),
                        "pnl": round(total_pnl, 2),
                        "comm": round(comm * qty, 2),
                    })

        # --- 7. Long Put ---
        atm_puts = self._find_puts(strikes, cfg.long_put_delta, tol)
        if atm_puts:
            ap = atm_puts[0]
            cost = ap.get("putAskPrice", 0)
            if cost > 0:
                cost_slip = cost * (1 + cfg.slippage)
                comm = cfg.commission_per_leg
                risk_per = cost_slip * 100 + comm
                qty = max(1, int(max_risk / risk_per))
                expiry_val = max(0, ap["strike"] - next_close)
                pnl_per = expiry_val - cost_slip
                total_pnl = pnl_per * 100 * qty - comm * qty
                trades.append({
                    "name": "Long Put",
                    "structure": TradeStructure.LONG_PUT,
                    "strike": ap["strike"],
                    "delta": ap.get("put_delta", 0),
                    "entry_cost": round(cost_slip, 4),
                    "qty": qty,
                    "max_risk": round(risk_per * qty, 2),
                    "max_profit": None,
                    "pnl": round(total_pnl, 2),
                    "comm": round(comm * qty, 2),
                })

        # --- 8. Bear Call Spread (bearish credit) ---
        bcs_short = self._find_calls(strikes, cfg.bear_cs_short, tol)
        bcs_long = self._find_calls(strikes, cfg.bear_cs_long, tol)
        if bcs_short and bcs_long:
            sc = bcs_short[0]
            lc = bcs_long[0]
            if sc["strike"] < lc["strike"]:
                credit = sc.get("callBidPrice", 0) - lc.get("callAskPrice", 0)
                if credit > 0:
                    credit_slip = credit * (1 - cfg.slippage)
                    width = lc["strike"] - sc["strike"]
                    comm = cfg.commission_per_leg * 2
                    risk_per = (width - credit_slip) * 100 + comm
                    if risk_per > 0:
                        qty = max(1, int(max_risk / risk_per))
                        sc_liab = max(0, next_close - sc["strike"])
                        lc_recov = max(0, next_close - lc["strike"])
                        pnl_per = credit_slip - (sc_liab - lc_recov)
                        total_pnl = pnl_per * 100 * qty - comm * qty
                        trades.append({
                            "name": "Bear Call Spread",
                            "structure": TradeStructure.BEAR_CALL_SPREAD,
                            "short_strike": sc["strike"],
                            "long_strike": lc["strike"],
                            "short_delta": sc.get("delta", 0),
                            "long_delta": lc.get("delta", 0),
                            "entry_credit": round(credit_slip, 4),
                            "width": width,
                            "qty": qty,
                            "max_risk": round(risk_per * qty, 2),
                            "max_profit": round(credit_slip * 100 * qty - comm * qty, 2),
                            "pnl": round(total_pnl, 2),
                            "comm": round(comm * qty, 2),
                        })

        # --- 9. Iron Butterfly (ATM sell + wings) ---
        ifly_atm = self._find_calls(strikes, cfg.ifly_atm_delta, tol)
        ifly_wing_calls = self._find_calls(strikes, cfg.ifly_wing_delta, tol)
        ifly_wing_puts = self._find_puts(strikes, cfg.ifly_wing_delta, tol)
        if ifly_atm and ifly_wing_calls and ifly_wing_puts:
            atm = ifly_atm[0]
            wc = ifly_wing_calls[0]
            wp = ifly_wing_puts[0]
            atm_strike = atm["strike"]
            if wc["strike"] > atm_strike and wp["strike"] < atm_strike:
                # Sell ATM call + ATM put, buy wing call + wing put
                call_credit = atm.get("callBidPrice", 0) - wc.get("callAskPrice", 0)
                put_credit = atm.get("putBidPrice", 0) - wp.get("putAskPrice", 0)
                total_credit = call_credit + put_credit
                if total_credit > 0:
                    credit_slip = total_credit * (1 - cfg.slippage)
                    call_width = wc["strike"] - atm_strike
                    put_width = atm_strike - wp["strike"]
                    max_wing = max(call_width, put_width)
                    comm = cfg.commission_per_leg * 4
                    risk_per = (max_wing - credit_slip) * 100 + comm
                    if risk_per > 0:
                        qty = max(1, int(max_risk / risk_per))
                        # P&L at next_close
                        sc_liab = max(0, next_close - atm_strike)
                        lc_recov = max(0, next_close - wc["strike"])
                        sp_liab = max(0, atm_strike - next_close)
                        lp_recov = max(0, wp["strike"] - next_close)
                        net_liab = (sc_liab - lc_recov) + (sp_liab - lp_recov)
                        pnl_per = credit_slip - net_liab
                        total_pnl = pnl_per * 100 * qty - comm * qty
                        trades.append({
                            "name": "Iron Butterfly",
                            "structure": TradeStructure.IRON_BUTTERFLY,
                            "atm_strike": atm_strike,
                            "wing_call_strike": wc["strike"],
                            "wing_put_strike": wp["strike"],
                            "entry_credit": round(credit_slip, 4),
                            "call_width": call_width,
                            "put_width": put_width,
                            "qty": qty,
                            "max_risk": round(risk_per * qty, 2),
                            "max_profit": round(credit_slip * 100 * qty - comm * qty, 2),
                            "pnl": round(total_pnl, 2),
                            "comm": round(comm * qty, 2),
                        })

        # --- 10. Short Iron Condor (OTM sell + wings) ---
        sic_short_calls = self._find_calls(strikes, cfg.ic_short_delta, tol)
        sic_short_puts = self._find_puts(strikes, cfg.ic_short_delta, tol)
        sic_long_calls = self._find_calls(strikes, cfg.ic_long_delta, tol)
        sic_long_puts = self._find_puts(strikes, cfg.ic_long_delta, tol)
        if sic_short_calls and sic_short_puts and sic_long_calls and sic_long_puts:
            sc = sic_short_calls[0]
            sp = sic_short_puts[0]
            lc = sic_long_calls[0]
            lp = sic_long_puts[0]
            if lp["strike"] < sp["strike"] < sc["strike"] < lc["strike"]:
                call_credit = sc.get("callBidPrice", 0) - lc.get("callAskPrice", 0)
                put_credit = sp.get("putBidPrice", 0) - lp.get("putAskPrice", 0)
                total_credit = call_credit + put_credit
                if total_credit > 0:
                    credit_slip = total_credit * (1 - cfg.slippage)
                    call_width = lc["strike"] - sc["strike"]
                    put_width = sp["strike"] - lp["strike"]
                    max_wing = max(call_width, put_width)
                    comm = cfg.commission_per_leg * 4
                    risk_per = (max_wing - credit_slip) * 100 + comm
                    if risk_per > 0:
                        qty = max(1, int(max_risk / risk_per))
                        # P&L at next_close
                        sc_liab = max(0, next_close - sc["strike"])
                        lc_recov = max(0, next_close - lc["strike"])
                        sp_liab = max(0, sp["strike"] - next_close)
                        lp_recov = max(0, lp["strike"] - next_close)
                        net_liab = (sc_liab - lc_recov) + (sp_liab - lp_recov)
                        pnl_per = credit_slip - net_liab
                        total_pnl = pnl_per * 100 * qty - comm * qty
                        trades.append({
                            "name": "Short Iron Condor",
                            "structure": TradeStructure.SHORT_IRON_CONDOR,
                            "short_call_strike": sc["strike"],
                            "short_put_strike": sp["strike"],
                            "long_call_strike": lc["strike"],
                            "long_put_strike": lp["strike"],
                            "entry_credit": round(credit_slip, 4),
                            "call_width": call_width,
                            "put_width": put_width,
                            "qty": qty,
                            "max_risk": round(risk_per * qty, 2),
                            "max_profit": round(credit_slip * 100 * qty - comm * qty, 2),
                            "pnl": round(total_pnl, 2),
                            "comm": round(comm * qty, 2),
                        })

        return trades

    # -- main backtest runner -------------------------------------------------

    def run_backtest(self, orats, state, months: int = 12, db=None,
                     use_sizing: bool = True) -> None:
        cfg = self.config
        end = date.today()
        start = end - timedelta(days=months * 30)

        print(f"{C.BOLD}{C.CYAN}Trade Structure Backtest v2{C.RESET}")
        print(f"  {start} -> {end}")
        if use_sizing:
            print(f"  Signal-weighted sizing: ON "
                  f"(base ${self.sizer.config.account_capital:,.0f} "
                  f"x {self.sizer.config.base_risk_pct:.0%})")
        else:
            print(f"  Flat sizing: ${cfg.max_risk:.0f}")
        print(f"  Structures: {', '.join(self.STRUCTURE_NAMES)}")
        print(f"  + Adaptive (best structure per day)\n")

        cache = state.load_cache()

        def _save_cache():
            state.save_cache(cache)

        for ticker in cfg.tickers:
            print(f"{'=' * 72}")
            print(f"  {C.BOLD}Trade Backtest — {ticker}{C.RESET}")
            print(f"{'=' * 72}")

            signal_days = self._get_signal_days(ticker, start, end, cache,
                                                _save_cache, orats)
            if not signal_days:
                print(f"  {C.RED}No signal days found{C.RESET}")
                continue

            print(f"  Signal days: {len(signal_days)}")

            all_results: List[Dict] = []
            for i, sd in enumerate(signal_days):
                dt = sd["date"]
                spot = sd["close"]
                next_close = sd["next_close"]
                core_count = sd.get("core_count", 3)

                composite = sd.get("composite")
                groups_firing = sd.get("groups_firing", 0)
                wing_count = sd.get("wing_count", 0)
                fund_count = sd.get("fund_count", 0)
                mom_count = sd.get("mom_count", 0)

                # Signal-weighted sizing
                if use_sizing:
                    sizing = self.sizer.compute(
                        core_count, composite=composite,
                        groups_firing=groups_firing,
                        wing_count=wing_count, fund_count=fund_count,
                        mom_count=mom_count)
                    risk_budget = sizing["risk_budget"]
                else:
                    risk_budget = cfg.max_risk
                    sizing = {"risk_budget": risk_budget, "multiplier": 1.0,
                              "core_count": core_count}

                # Adaptive selection
                summary = sd.get("summary", {})
                adaptive_ranked = self.selector.select(
                    summary, core_count, composite=composite,
                    groups_firing=groups_firing,
                    wing_count=wing_count, fund_count=fund_count,
                    mom_count=mom_count)
                adaptive_pick = adaptive_ranked[0][0] if adaptive_ranked else None
                adaptive_reason = adaptive_ranked[0][1] if adaptive_ranked else ""

                print(f"  [{i+1}/{len(signal_days)}] {dt} "
                      f"spot={spot:.2f} -> {next_close:.2f} "
                      f"({sd['next_return']*100:+.2f}%) "
                      f"[{core_count} sig, ${risk_budget:,.0f}] ", end="")

                chain = self._fetch_hist_chain(orats, ticker, dt, cache, _save_cache)
                if not chain:
                    print(f"{C.RED}no chain{C.RESET}")
                    time.sleep(0.3)
                    continue

                exp = self._find_next_expiry(chain, dt)
                if not exp:
                    print(f"{C.YELLOW}no next-day expiry{C.RESET}")
                    continue

                strikes = [s for s in chain if s.get("expirDate") == exp]
                if len(strikes) < 10:
                    print(f"{C.YELLOW}thin chain ({len(strikes)} strikes){C.RESET}")
                    continue

                trades = self._build_trades(strikes, spot, next_close,
                                            risk_budget=risk_budget)
                if not trades:
                    print(f"{C.YELLOW}no valid trades{C.RESET}")
                    continue

                trade_names = [t["name"][:8] for t in trades]
                print(f"{C.GREEN}{len(trades)} trades ({', '.join(trade_names)}){C.RESET}")

                all_results.append({
                    "date": dt,
                    "spot": spot,
                    "next_close": next_close,
                    "next_return": sd["next_return"],
                    "composite": sd["composite"],
                    "core_count": core_count,
                    "risk_budget": risk_budget,
                    "sizing": sizing,
                    "adaptive_pick": adaptive_pick,
                    "adaptive_reason": adaptive_reason,
                    "expiry": exp,
                    "trades": trades,
                })

                time.sleep(0.3)

            # Log trades to DB
            if db and db.enabled and all_results:
                db_trades = []
                for r in all_results:
                    for t in r["trades"]:
                        db_trades.append({
                            "signal_date": r["date"],
                            "ticker": ticker,
                            "structure": t["name"],
                            "entry_price": t.get("entry_cost") or t.get("entry_credit"),
                            "pnl": t["pnl"],
                            "spot_at_entry": r["spot"],
                            "spot_at_exit": r["next_close"],
                            "move_pct": round(r["next_return"] * 100, 2),
                            "composite": r["composite"],
                            "core_count": r["core_count"],
                            "risk_budget": r["risk_budget"],
                        })
                db.run({"action": "log_0dte_trades", "trades": db_trades})
                print(f"  [DB] Logged {len(db_trades)} trade results")

            self._print_results(ticker, all_results, use_sizing=use_sizing)

        print(f"\n{C.BOLD}Cache saved to {state.cache_path}{C.RESET}")

    def _get_signal_days(self, ticker: str, start: date, end: date,
                         cache: Dict, save_fn, orats) -> List[Dict]:
        """Get FEAR_BOUNCE signal days using ZeroDTEAgent's backtest logic."""
        m = self.monitor

        # Load dailies
        dk = f"daily_{ticker}_{start}_{end}"
        if dk in cache:
            daily_data = cache[dk]
        else:
            resp = orats.hist_dailies(ticker, f"{start},{end}")
            if not resp or not resp.get("data"):
                return []
            daily_data = resp["data"]
            cache[dk] = daily_data
            save_fn()

        prices: Dict[str, Dict] = {}
        for d in daily_data:
            dt = str(d.get("tradeDate", ""))[:10]
            prices[dt] = d
        trade_dates = sorted(prices.keys())
        date_idx = {d: i for i, d in enumerate(trade_dates)}

        # Load summaries
        sk = f"summ_{ticker}_{start}_{end}"
        if sk in cache:
            summ_map = cache[sk]
        else:
            summ_map = {}
            total = len(trade_dates)
            for i, dt in enumerate(trade_dates):
                if (i + 1) % 20 == 0 or i == 0:
                    pct = (i + 1) / total * 100
                    print(f"\r    Summaries: {i+1}/{total} ({pct:.0f}%)    ",
                          end="", flush=True)
                resp = orats.hist_summaries(ticker, dt)
                if resp and resp.get("data"):
                    summ_map[dt] = resp["data"][0]
                time.sleep(0.2)
            print(f"\n    Summaries: {len(summ_map)} days")
            cache[sk] = summ_map
            save_fn()

        # Credit data
        credit_map: Dict = {}
        ck = f"credit_{start}_{end}"
        if ck in cache:
            credit_map = cache[ck]
        else:
            try:
                import yfinance as yf
                for sym in ("HYG", "TLT"):
                    df = yf.Ticker(sym).history(start=str(start), end=str(end))
                    for d, row in df.iterrows():
                        dt = d.strftime("%Y-%m-%d")
                        if dt not in credit_map:
                            credit_map[dt] = {}
                        credit_map[dt][sym] = float(row["Close"])
                cache[ck] = credit_map
                save_fn()
            except Exception:
                pass

        # Compute signals
        sorted_dates = sorted(d for d in summ_map if d in date_idx)
        signal_days: List[Dict] = []

        for i, dt in enumerate(sorted_dates):
            if i == 0:
                continue
            prev_dt = sorted_dates[i - 1]
            m.prev_day[ticker] = summ_map[prev_dt]
            m.baseline[ticker] = summ_map[prev_dt]

            signals = m.compute_signals(ticker, summ_map[dt])

            cd = credit_map.get(dt, {})
            cd_prev = credit_map.get(prev_dt, {})
            if (cd.get("HYG") and cd.get("TLT")
                    and cd_prev.get("HYG") and cd_prev.get("TLT")):
                signals.update(m.compute_credit_signal(
                    cd["HYG"], cd["TLT"], cd_prev["HYG"], cd_prev["TLT"],
                ))

            from datetime import datetime as _dt
            td = _dt.strptime(dt, "%Y-%m-%d").date()
            composite, _ = m.determine_direction(
                signals, intraday=False, trade_date=td)
            if not composite:
                continue

            # Count core signals firing
            core_firing = [k for k in self.monitor.config.core_signals
                           if signals.get(k, {}).get("level") == "ACTION"]

            idx = date_idx.get(dt)
            if idx is None or idx + 1 >= len(trade_dates):
                continue
            next_dt = trade_dates[idx + 1]
            cls_today = float(prices[dt].get("clsPx", 0) or 0)
            cls_next = float(prices[next_dt].get("clsPx", 0) or 0)
            if not cls_today or not cls_next:
                continue

            signal_days.append({
                "date": dt,
                "close": cls_today,
                "next_close": cls_next,
                "next_return": (cls_next - cls_today) / cls_today,
                "composite": composite,
                "core_count": len(core_firing),
                "summary": summ_map[dt],
            })

        return signal_days

    # -- regime split printer ---------------------------------------------------

    BULLISH_NAMES = [
        "Call Debit Spread", "Bull Put Spread", "Long Call",
        "Call Ratio Spread", "Broken Wing Butterfly",
    ]
    BEARISH_NAMES = [
        "Put Debit Spread", "Long Put", "Bear Call Spread",
    ]
    NEUTRAL_NAMES = [
        "Iron Butterfly", "Short Iron Condor",
    ]

    @staticmethod
    def _regime_stats(entries: List[Dict]) -> Dict:
        """Compute stats for a list of trade entries."""
        if not entries:
            return {"n": 0, "win_pct": 0, "avg_pnl": 0, "total": 0, "sharpe": 0}
        pnls = [e["pnl"] for e in entries]
        n = len(pnls)
        wins = [p for p in pnls if p > 0]
        win_pct = len(wins) / n if n else 0
        avg_pnl = sum(pnls) / n
        total = sum(pnls)
        if n > 1:
            mean = avg_pnl
            var = sum((p - mean) ** 2 for p in pnls) / (n - 1)
            sharpe = mean / (var ** 0.5) if var > 0 else 0
        else:
            sharpe = 0
        return {"n": n, "win_pct": win_pct, "avg_pnl": avg_pnl,
                "total": total, "sharpe": sharpe}

    def _print_regime_split(self, results: List[Dict],
                            by_name: Dict[str, List[Dict]],
                            all_names: List[str]) -> None:
        """Show performance split by regime: bounce vs sell-through."""
        bounce_results = [r for r in results if r["next_return"] > 0]
        sellthru_results = [r for r in results if r["next_return"] <= 0]
        n_bounce = len(bounce_results)
        n_sellthru = len(sellthru_results)

        if not bounce_results and not sellthru_results:
            return

        print(f"\n  {C.BOLD}{C.CYAN}{'=' * 78}{C.RESET}")
        print(f"  {C.BOLD}REGIME SPLIT — Bounce ({n_bounce} days) "
              f"vs Sell-Through ({n_sellthru} days){C.RESET}")
        print(f"  {C.BOLD}{C.CYAN}{'=' * 78}{C.RESET}")

        header = (f"  {'STRUCTURE':<24} {'BOUNCE':>6} {'WIN%':>5} {'AVG':>8} "
                  f"{'TOTAL':>9}  {'SELL':>5} {'WIN%':>5} {'AVG':>8} {'TOTAL':>9}")
        print(f"\n{header}")
        print(f"  {'-' * 78}")

        for name in all_names:
            entries = by_name.get(name, [])
            if not entries:
                continue
            bounce_e = [e for e in entries if e["next_return"] > 0]
            sellthru_e = [e for e in entries if e["next_return"] <= 0]
            bs = self._regime_stats(bounce_e)
            ss = self._regime_stats(sellthru_e)

            b_clr = C.GREEN if bs["total"] > 0 else C.RED
            s_clr = C.GREEN if ss["total"] > 0 else C.RED
            b_w = C.GREEN if bs["win_pct"] > 0.55 else C.RED if bs["win_pct"] < 0.45 else C.YELLOW
            s_w = C.GREEN if ss["win_pct"] > 0.55 else C.RED if ss["win_pct"] < 0.45 else C.YELLOW

            print(f"  {name:<24} "
                  f"{bs['n']:>5}  {b_w}{bs['win_pct']:>4.0%}{C.RESET} "
                  f"${bs['avg_pnl']:>+7.0f} {b_clr}${bs['total']:>+8.0f}{C.RESET}  "
                  f"{ss['n']:>4}  {s_w}{ss['win_pct']:>4.0%}{C.RESET} "
                  f"${ss['avg_pnl']:>+7.0f} {s_clr}${ss['total']:>+8.0f}{C.RESET}")

        # Summary
        if n_bounce:
            avg_bounce = sum(r["next_return"] for r in bounce_results) / n_bounce * 100
            print(f"\n  {C.GREEN}Bounce avg return: {avg_bounce:+.2f}%{C.RESET}")
        if n_sellthru:
            avg_sell = sum(r["next_return"] for r in sellthru_results) / n_sellthru * 100
            print(f"  {C.RED}Sell-through avg return: {avg_sell:+.2f}%{C.RESET}")

    def _print_flip_strategy(self, results: List[Dict]) -> None:
        """Show Flip strategy: bullish on bounces, bearish on sell-throughs.

        Two modes:
          1. Oracle Flip (perfect hindsight) — best bullish on up days,
             best bearish on down days. Shows the ceiling.
          2. Signal Flip — always start bullish Adaptive pick,
             but on sell-through days add best bearish P&L as the "flip" recovery.
        """
        if not results:
            return

        oracle_pnls = []
        combined_pnls = []   # Adaptive bullish + bearish flip when wrong

        for r in results:
            trades = r["trades"]
            if not trades:
                continue

            bullish_trades = [t for t in trades if t["name"] in self.BULLISH_NAMES]
            bearish_trades = [t for t in trades if t["name"] in self.BEARISH_NAMES]

            is_bounce = r["next_return"] > 0

            # Oracle: pick best from correct direction
            if is_bounce and bullish_trades:
                best = max(bullish_trades, key=lambda t: t["pnl"])
                oracle_pnls.append(best["pnl"])
            elif not is_bounce and bearish_trades:
                best = max(bearish_trades, key=lambda t: t["pnl"])
                oracle_pnls.append(best["pnl"])
            elif bullish_trades or bearish_trades:
                # Fallback to whatever is available
                all_t = bullish_trades + bearish_trades
                best = max(all_t, key=lambda t: t["pnl"])
                oracle_pnls.append(best["pnl"])

            # Combined: Adaptive (bullish) P&L + bearish flip when losing
            adaptive_pick = r.get("adaptive_pick")
            adaptive_trade = None
            if adaptive_pick:
                for t in trades:
                    if t.get("structure") == adaptive_pick:
                        adaptive_trade = t
                        break

            if adaptive_trade:
                bull_pnl = adaptive_trade["pnl"]
                if bull_pnl < 0 and bearish_trades:
                    # Flip: the bullish trade lost → add bearish recovery
                    best_bear = max(bearish_trades, key=lambda t: t["pnl"])
                    # Flip P&L = bull loss + bear gain (net)
                    combined_pnls.append(bull_pnl + best_bear["pnl"])
                else:
                    combined_pnls.append(bull_pnl)

        print(f"\n  {C.BOLD}{C.CYAN}{'=' * 78}{C.RESET}")
        print(f"  {C.BOLD}FLIP STRATEGY — Bearish recovery on sell-through days{C.RESET}")
        print(f"  {C.BOLD}{C.CYAN}{'=' * 78}{C.RESET}")

        for label, pnls in [("Oracle Flip (hindsight)", oracle_pnls),
                             ("Signal Flip (adaptive+bear)", combined_pnls)]:
            if not pnls:
                continue
            n = len(pnls)
            wins = [p for p in pnls if p > 0]
            total = sum(pnls)
            avg = total / n
            win_pct = len(wins) / n
            if n > 1:
                var = sum((p - avg) ** 2 for p in pnls) / (n - 1)
                sharpe = avg / (var ** 0.5) if var > 0 else 0
            else:
                sharpe = 0
            clr = C.GREEN if total > 0 else C.RED
            w_clr = C.GREEN if win_pct > 0.55 else C.RED if win_pct < 0.45 else C.YELLOW
            print(f"\n  {C.BOLD}{label}{C.RESET}")
            print(f"    Days: {n}  Win%: {w_clr}{win_pct:.0%}{C.RESET}  "
                  f"Avg: ${avg:+,.0f}  Total: {clr}${total:+,.0f}{C.RESET}  "
                  f"Sharpe: {sharpe:+.2f}")

    # -- results printer ------------------------------------------------------

    def _print_results(self, ticker: str, results: List[Dict],
                       use_sizing: bool = True) -> None:
        if not results:
            print(f"\n  {C.RED}No trade results{C.RESET}")
            return

        by_name: Dict[str, List[Dict]] = {}
        for r in results:
            for t in r["trades"]:
                by_name.setdefault(t["name"], []).append({
                    "date": r["date"],
                    "spot": r["spot"],
                    "next_close": r["next_close"],
                    "next_return": r["next_return"],
                    "core_count": r["core_count"],
                    "risk_budget": r["risk_budget"],
                    **t,
                })

        # Build "Adaptive" virtual strategy
        adaptive_trades: List[Dict] = []
        for r in results:
            pick = r.get("adaptive_pick")
            if pick:
                match = None
                for t in r["trades"]:
                    if t.get("structure") == pick:
                        match = t
                        break
                if match:
                    adaptive_trades.append({
                        "date": r["date"],
                        "spot": r["spot"],
                        "next_close": r["next_close"],
                        "next_return": r["next_return"],
                        "core_count": r["core_count"],
                        "risk_budget": r["risk_budget"],
                        **match,
                        "name": "Adaptive",
                    })

        print(f"\n  {C.BOLD}{C.CYAN}{'=' * 78}{C.RESET}")
        print(f"  {C.BOLD}TRADE STRUCTURE COMPARISON — {ticker} "
              f"({len(results)} signal days){C.RESET}")
        if use_sizing:
            print(f"  {C.DIM}Signal-weighted sizing | "
                  f"Adaptive = best structure per day{C.RESET}")
        print(f"  {C.BOLD}{C.CYAN}{'=' * 78}{C.RESET}")

        header = (f"  {'STRUCTURE':<24} {'N':>3} {'WIN%':>6} {'AVG P&L':>9} "
                  f"{'AVG WIN':>9} {'AVG LOSS':>9} {'TOTAL':>10} {'SHARPE':>7}")
        print(f"\n{header}")
        print(f"  {'-' * 78}")

        best_sharpe = ("", -999)
        best_total = ("", -999)
        all_names = self.STRUCTURE_NAMES + ["Adaptive"]

        for name in all_names:
            if name == "Adaptive":
                entries = adaptive_trades
            else:
                entries = by_name.get(name, [])
            if not entries:
                print(f"  {name:<24} {'--':>3}")
                continue

            pnls = [e["pnl"] for e in entries]
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p <= 0]
            n = len(pnls)
            win_pct = len(wins) / n if n else 0
            avg_pnl = sum(pnls) / n if n else 0
            avg_win = sum(wins) / len(wins) if wins else 0
            avg_loss = sum(losses) / len(losses) if losses else 0
            total = sum(pnls)

            if n > 1:
                mean = avg_pnl
                var = sum((p - mean) ** 2 for p in pnls) / (n - 1)
                std = var ** 0.5
                sharpe = mean / std if std > 0 else 0
            else:
                sharpe = 0

            clr = C.GREEN if win_pct > 0.55 else C.RED if win_pct < 0.45 else C.YELLOW
            tot_clr = C.GREEN if total > 0 else C.RED
            sep = f"{C.BOLD}  {'-' * 78}{C.RESET}" if name == "Adaptive" else ""
            if sep:
                print(sep)

            bold = C.BOLD if name == "Adaptive" else ""
            print(f"  {bold}{name:<24}{C.RESET} {n:>3} {clr}{win_pct:>5.0%}{C.RESET} "
                  f"${avg_pnl:>+8.0f} ${avg_win:>+8.0f} ${avg_loss:>+8.0f} "
                  f"{tot_clr}${total:>+9.0f}{C.RESET} {sharpe:>+6.2f}")

            if sharpe > best_sharpe[1]:
                best_sharpe = (name, sharpe)
            if total > best_total[1]:
                best_total = (name, total)

        if best_sharpe[0]:
            print(f"\n  {C.BOLD}Best risk-adjusted:{C.RESET} "
                  f"{best_sharpe[0]} (Sharpe {best_sharpe[1]:.2f})")
        if best_total[0]:
            print(f"  {C.BOLD}Best total return:{C.RESET}  "
                  f"{best_total[0]} (${best_total[1]:+,.0f})")

        # -- Regime split: bounce vs sell-through --
        self._print_regime_split(results, by_name, all_names)

        # -- Flip strategy (oracle + heuristic) --
        self._print_flip_strategy(results)

        # Signal sizing summary
        if use_sizing:
            print(f"\n  {C.BOLD}Signal Sizing Summary:{C.RESET}")
            by_count: Dict[int, List] = {}
            for r in results:
                by_count.setdefault(r["core_count"], []).append(r)
            for cnt in sorted(by_count):
                days = by_count[cnt]
                budget = days[0]["risk_budget"]
                print(f"    {cnt} signals: {len(days)} days, "
                      f"${budget:,.0f}/trade")

        # -- Regime classification analysis --
        self._print_regime_analysis(results)

        # Per-signal detail table
        print(f"\n  {C.BOLD}Per-Signal Detail:{C.RESET}")
        short_names = []
        for name in all_names:
            parts = name.split()
            sn = parts[0][:4] + "_" + parts[-1][:2]
            short_names.append(sn)

        print(f"  {'DATE':<12} {'SPOT':>9} {'NEXT':>9} {'MOVE':>7} {'SIG':>3} {'BUDGET':>7}", end="")
        for sn in short_names:
            print(f"  {sn:>8}", end="")
        print()
        print(f"  {'-' * (48 + 10 * len(all_names))}")

        for r in results:
            ret_s = f"{r['next_return']*100:+.2f}%"
            clr = C.GREEN if r["next_return"] > 0 else C.RED
            print(f"  {r['date']:<12} {r['spot']:>9.2f} "
                  f"{r['next_close']:>9.2f} {clr}{ret_s:>7}{C.RESET} "
                  f"{r['core_count']:>3} "
                  f"${r['risk_budget']/1000:.0f}k", end="")
            trade_by_name = {t["name"]: t for t in r["trades"]}
            # Also add adaptive
            pick = r.get("adaptive_pick")
            if pick:
                for t in r["trades"]:
                    if t.get("structure") == pick:
                        trade_by_name["Adaptive"] = t
                        break

            for name in all_names:
                t = trade_by_name.get(name)
                if t:
                    pc = C.GREEN if t["pnl"] > 0 else C.RED
                    print(f"  {pc}${t['pnl']:>+7.0f}{C.RESET}", end="")
                else:
                    print(f"  {'--':>8}", end="")
            print()

        print()

    # -- regime classification analysis ----------------------------------------

    def _print_regime_analysis(self, results: List[Dict]) -> None:
        """Classify each signal day by regime and show per-regime performance."""
        if not results:
            return

        from .regime_classifier import VolSurfaceRegimeClassifier
        classifier = VolSurfaceRegimeClassifier()

        regime_trades: Dict[str, List[Dict]] = {}
        for r in results:
            summary = r.get("summary", {})
            if not summary:
                continue
            cls = classifier.classify(summary)
            regime = cls["regime_name"]
            for t in r["trades"]:
                regime_trades.setdefault(regime, []).append({
                    **t,
                    "next_return": r["next_return"],
                    "date": r["date"],
                })

        if not regime_trades:
            return

        print(f"\n  {C.BOLD}{C.CYAN}{'=' * 78}{C.RESET}")
        print(f"  {C.BOLD}REGIME ANALYSIS — Signal days by vol surface regime{C.RESET}")
        print(f"  {C.BOLD}{C.CYAN}{'=' * 78}{C.RESET}")

        regime_colors = {
            "FEAR": C.RED, "NERVOUS": C.YELLOW, "FLAT": C.BLUE,
            "COMPLACENT": C.GREEN, "GREED": C.MAGENTA,
        }

        for regime_name in ["FEAR", "NERVOUS", "FLAT", "COMPLACENT", "GREED"]:
            entries = regime_trades.get(regime_name, [])
            if not entries:
                continue
            clr = regime_colors.get(regime_name, "")
            n = len(entries)
            pnls = [e["pnl"] for e in entries]
            wins = sum(1 for p in pnls if p > 0)
            total = sum(pnls)
            avg = total / n if n else 0
            print(f"  {clr}{regime_name:<12}{C.RESET} "
                  f"{n:>3} trades | "
                  f"win {wins}/{n} ({wins/n:.0%}) | "
                  f"avg ${avg:+,.0f} | "
                  f"total ${total:+,.0f}")

    # -- regime-aware signal day discovery ------------------------------------

    def _get_all_regime_days(self, ticker: str, start: date, end: date,
                              cache: Dict, save_fn, orats,
                              ) -> Tuple[List[Dict], Dict[str, Dict]]:
        """Get ALL trading days with regime classification (not just FEAR).

        Returns:
            (all_days, regime_classifications)
            - all_days: list of day dicts with regime info
            - regime_classifications: {date_str: regime_dict}
        """
        from .regime_classifier import VolSurfaceRegimeClassifier
        m = self.monitor
        classifier = VolSurfaceRegimeClassifier()

        # Load dailies (same as _get_signal_days)
        dk = f"daily_{ticker}_{start}_{end}"
        if dk in cache:
            daily_data = cache[dk]
        else:
            resp = orats.hist_dailies(ticker, f"{start},{end}")
            if not resp or not resp.get("data"):
                return [], {}
            daily_data = resp["data"]
            cache[dk] = daily_data
            save_fn()

        prices: Dict[str, Dict] = {}
        for d in daily_data:
            dt = str(d.get("tradeDate", ""))[:10]
            prices[dt] = d
        trade_dates = sorted(prices.keys())
        date_idx = {d: i for i, d in enumerate(trade_dates)}

        # Load summaries
        sk = f"summ_{ticker}_{start}_{end}"
        if sk in cache:
            summ_map = cache[sk]
        else:
            # Try range query first
            resp = orats.get("hist/summaries", {
                "ticker": ticker,
                "tradeDate": f"{start},{end}",
            })
            if resp and resp.get("data") and len(resp["data"]) > 5:
                summ_map = {}
                for row in resp["data"]:
                    dt = str(row.get("tradeDate", ""))[:10]
                    summ_map[dt] = row
                print(f"    Summaries: {len(summ_map)} days (range query)")
            else:
                summ_map = {}
                total = len(trade_dates)
                for i, dt in enumerate(trade_dates):
                    if (i + 1) % 20 == 0 or i == 0:
                        pct = (i + 1) / total * 100
                        print(f"\r    Summaries: {i+1}/{total} ({pct:.0f}%)    ",
                              end="", flush=True)
                    resp = orats.hist_summaries(ticker, dt)
                    if resp and resp.get("data"):
                        summ_map[dt] = resp["data"][0]
                    time.sleep(0.2)
                print(f"\n    Summaries: {len(summ_map)} days")
            cache[sk] = summ_map
            save_fn()

        # Credit data
        credit_map: Dict = {}
        ck = f"credit_{start}_{end}"
        if ck in cache:
            credit_map = cache[ck]
        else:
            try:
                import yfinance as yf
                for sym in ("HYG", "TLT"):
                    df = yf.Ticker(sym).history(start=str(start), end=str(end))
                    for d, row in df.iterrows():
                        dt = d.strftime("%Y-%m-%d")
                        if dt not in credit_map:
                            credit_map[dt] = {}
                        credit_map[dt][sym] = float(row["Close"])
                cache[ck] = credit_map
                save_fn()
            except Exception:
                pass

        # Classify every day
        sorted_dates = sorted(d for d in summ_map if d in date_idx)
        all_days: List[Dict] = []
        regime_classifications: Dict[str, Dict] = {}

        for i, dt in enumerate(sorted_dates):
            if i == 0:
                continue
            prev_dt = sorted_dates[i - 1]
            m.prev_day[ticker] = summ_map[prev_dt]
            m.baseline[ticker] = summ_map[prev_dt]

            signals = m.compute_signals(ticker, summ_map[dt])

            cd = credit_map.get(dt, {})
            cd_prev = credit_map.get(prev_dt, {})
            if (cd.get("HYG") and cd.get("TLT")
                    and cd_prev.get("HYG") and cd_prev.get("TLT")):
                signals.update(m.compute_credit_signal(
                    cd["HYG"], cd["TLT"], cd_prev["HYG"], cd_prev["TLT"],
                ))

            # Fear-based composite (existing)
            from datetime import datetime as _dt
            td = _dt.strptime(dt, "%Y-%m-%d").date()
            fear_composite, _ = m.determine_direction(
                signals, intraday=False, trade_date=td)

            # Regime classification
            regime = classifier.classify(summ_map[dt])
            regime_classifications[dt] = regime

            # Use fear composite if it fires, else use regime composite
            composite = fear_composite or regime.get("composite")

            cfg = self.monitor.config
            core_firing = [k for k in cfg.core_signals
                           if signals.get(k, {}).get("level") == "ACTION"]
            wing_firing = [k for k in cfg.wing_signals
                           if signals.get(k, {}).get("level") == "ACTION"]
            fund_firing = [k for k in cfg.funding_signals
                           if signals.get(k, {}).get("level") == "ACTION"]
            mom_firing = [k for k in cfg.momentum_signals
                          if signals.get(k, {}).get("level") == "ACTION"]

            idx = date_idx.get(dt)
            if idx is None or idx + 1 >= len(trade_dates):
                continue
            next_dt = trade_dates[idx + 1]
            cls_today = float(prices[dt].get("clsPx", 0) or 0)
            cls_next = float(prices[next_dt].get("clsPx", 0) or 0)
            if not cls_today or not cls_next:
                continue

            open_today = float(prices[dt].get("open", 0) or 0)
            hi_today = float(prices[dt].get("hiPx", 0) or 0)
            lo_today = float(prices[dt].get("loPx", 0) or 0)

            all_days.append({
                "date": dt,
                "open": open_today,
                "high": hi_today,
                "low": lo_today,
                "close": cls_today,
                "next_close": cls_next,
                "next_return": (cls_next - cls_today) / cls_today,
                "composite": composite,
                "fear_composite": fear_composite,
                "regime": regime["regime_name"],
                "regime_composite": regime.get("composite"),
                "regime_confidence": regime["confidence"],
                "regime_direction": regime["direction"],
                "core_count": len(core_firing),
                "wing_count": len(wing_firing),
                "fund_count": len(fund_firing),
                "mom_count": len(mom_firing),
                "groups_firing": sum([
                    len(core_firing) >= 2,
                    len(wing_firing) >= 1,
                    len(fund_firing) >= 1,
                    len(mom_firing) >= 1,
                ]),
                "summary": summ_map[dt],
                "signals": signals,
            })

        return all_days, regime_classifications

    # -- event study -----------------------------------------------------------

    # Pre-defined market events
    EVENTS = {
        "volmageddon": {
            "label": "Volmageddon (Feb 2018)",
            "start": "2018-02-01",
            "end": "2018-02-28",
            "context": "XIV blowup, VIX spike to 50, short vol unwind",
        },
        "q4_2018": {
            "label": "Q4 2018 Selloff",
            "start": "2018-10-01",
            "end": "2018-12-31",
            "context": "Fed tightening fears, trade war, ~20% SPX drawdown",
        },
        "covid": {
            "label": "COVID Crash (Feb-Mar 2020)",
            "start": "2020-02-19",
            "end": "2020-03-23",
            "context": "Pandemic panic, -34% in 23 trading days, VIX 82",
        },
        "covid_recovery": {
            "label": "COVID Recovery (Mar-Jun 2020)",
            "start": "2020-03-23",
            "end": "2020-06-08",
            "context": "V-shaped recovery, +44% from lows",
        },
        "2022_bear": {
            "label": "2022 Bear Market",
            "start": "2022-01-03",
            "end": "2022-10-12",
            "context": "Fed hikes, inflation, -27% peak-to-trough",
        },
        "2022_q4_rally": {
            "label": "2022 Q4 Rally",
            "start": "2022-10-12",
            "end": "2023-02-01",
            "context": "Bear market bottom to new year rally",
        },
        "svb_crisis": {
            "label": "SVB/Banking Crisis (Mar 2023)",
            "start": "2023-03-08",
            "end": "2023-03-31",
            "context": "SVB/Signature Bank failure, regional bank stress",
        },
        "liberation_day": {
            "label": "Liberation Day Tariffs (Apr 2025)",
            "start": "2025-04-02",
            "end": "2025-04-30",
            "context": "Trump tariff announcement, global trade uncertainty",
        },
        "aug_2024_selloff": {
            "label": "Aug 2024 Yen Carry Unwind",
            "start": "2024-07-31",
            "end": "2024-08-12",
            "context": "BOJ rate hike, yen carry trade unwind, VIX spike to 65",
        },
    }

    def run_event_study(self, orats, state, event_name: str = None,
                         custom_start: str = None, custom_end: str = None,
                         ) -> None:
        """Run backtest focused on specific market events.

        Args:
            event_name: Key from EVENTS dict, or None for custom.
            custom_start: Custom start date (YYYY-MM-DD).
            custom_end: Custom end date (YYYY-MM-DD).
        """
        cfg = self.config

        if event_name and event_name in self.EVENTS:
            evt = self.EVENTS[event_name]
            start_s = evt["start"]
            end_s = evt["end"]
            label = evt["label"]
            context = evt["context"]
        elif custom_start and custom_end:
            start_s = custom_start
            end_s = custom_end
            label = f"Custom ({custom_start} to {custom_end})"
            context = "Custom date range"
        else:
            print(f"  {C.RED}No event specified. Available events:{C.RESET}")
            for key, evt in self.EVENTS.items():
                print(f"    {key:<20} {evt['label']}")
            return

        start = datetime.strptime(start_s, "%Y-%m-%d").date()
        end = datetime.strptime(end_s, "%Y-%m-%d").date()

        print(f"\n{C.BOLD}{C.CYAN}{'=' * 72}{C.RESET}")
        print(f"  {C.BOLD}EVENT STUDY: {label}{C.RESET}")
        print(f"  {C.DIM}{context}{C.RESET}")
        print(f"  {start_s} -> {end_s}")
        print(f"{C.BOLD}{C.CYAN}{'=' * 72}{C.RESET}\n")

        cache = state.load_cache()

        def _save_cache():
            state.save_cache(cache)

        for ticker in cfg.tickers:
            print(f"\n  {C.BOLD}{ticker}{C.RESET}")

            all_days, regime_cls = self._get_all_regime_days(
                ticker, start, end, cache, _save_cache, orats)

            if not all_days:
                print(f"  {C.RED}No data for this period{C.RESET}")
                continue

            # Regime distribution
            regime_counts = {}
            for d in all_days:
                r = d["regime"]
                regime_counts[r] = regime_counts.get(r, 0) + 1

            total = len(all_days)
            regime_colors = {
                "FEAR": C.RED, "NERVOUS": C.YELLOW, "FLAT": C.BLUE,
                "COMPLACENT": C.GREEN, "GREED": C.MAGENTA,
            }

            print(f"\n  {C.BOLD}Regime Distribution ({total} trading days):{C.RESET}")
            for r_name in ["FEAR", "NERVOUS", "FLAT", "COMPLACENT", "GREED"]:
                cnt = regime_counts.get(r_name, 0)
                pct = cnt / total if total else 0
                clr = regime_colors.get(r_name, "")
                bar = "=" * int(pct * 30)
                print(f"    {clr}{r_name:<12}{C.RESET} {cnt:>4}d ({pct:>5.1%}) {bar}")

            # Return analysis by regime
            print(f"\n  {C.BOLD}Next-Day Returns by Regime:{C.RESET}")
            print(f"    {'REGIME':<12} {'DAYS':>5} {'AVG RET':>8} {'HIT%':>6} "
                  f"{'AVG+':>7} {'AVG-':>7} {'BEST':>8} {'WORST':>8}")
            print(f"    {'-' * 65}")

            for r_name in ["FEAR", "NERVOUS", "FLAT", "COMPLACENT", "GREED"]:
                days = [d for d in all_days if d["regime"] == r_name]
                if not days:
                    continue
                rets = [d["next_return"] for d in days]
                avg = sum(rets) / len(rets)
                up = sum(1 for r in rets if r > 0)
                hit = up / len(rets)
                pos = [r for r in rets if r > 0]
                neg = [r for r in rets if r <= 0]
                avg_pos = sum(pos) / len(pos) * 100 if pos else 0
                avg_neg = sum(neg) / len(neg) * 100 if neg else 0
                best = max(rets) * 100
                worst = min(rets) * 100
                clr = regime_colors.get(r_name, "")
                h_clr = C.GREEN if hit > 0.55 else C.RED if hit < 0.45 else C.YELLOW
                print(f"    {clr}{r_name:<12}{C.RESET} {len(days):>4} "
                      f"{avg*100:>+7.2f}% {h_clr}{hit:>5.0%}{C.RESET} "
                      f"{avg_pos:>+6.2f}% {avg_neg:>+6.2f}% "
                      f"{best:>+7.2f}% {worst:>+7.2f}%")

            # Signal day analysis
            fear_days = [d for d in all_days if d.get("fear_composite")]
            regime_signal_days = [d for d in all_days
                                   if d.get("regime_composite") and not d.get("fear_composite")]

            print(f"\n  {C.BOLD}Signal Coverage:{C.RESET}")
            print(f"    FEAR signals (existing):   {len(fear_days):>3}d "
                  f"({len(fear_days)/total:.0%})")
            print(f"    Regime signals (new):      {len(regime_signal_days):>3}d "
                  f"({len(regime_signal_days)/total:.0%})")
            print(f"    Total actionable:          "
                  f"{len(fear_days) + len(regime_signal_days):>3}d "
                  f"({(len(fear_days) + len(regime_signal_days))/total:.0%})")

            # Build trades for signal days (fear + regime)
            signal_days = [d for d in all_days
                           if d.get("composite")]
            if signal_days:
                print(f"\n  {C.BOLD}Building trades for "
                      f"{len(signal_days)} signal days...{C.RESET}")
                trade_results = []
                for i, sd in enumerate(signal_days):
                    dt = sd["date"]
                    spot = sd["close"]
                    next_close = sd["next_close"]
                    core_count = sd.get("core_count", 3)
                    risk_budget = self.sizer.compute(
                        max(core_count, 2),
                        composite=sd.get("composite"),
                        groups_firing=sd.get("groups_firing", 0),
                        wing_count=sd.get("wing_count", 0),
                        fund_count=sd.get("fund_count", 0),
                        mom_count=sd.get("mom_count", 0),
                    )["risk_budget"]

                    chain = self._fetch_hist_chain(
                        orats, ticker, dt, cache, _save_cache)
                    if not chain:
                        continue

                    exp = self._find_next_expiry(chain, dt)
                    if not exp:
                        continue

                    strikes = [s for s in chain if s.get("expirDate") == exp]
                    if len(strikes) < 10:
                        continue

                    trades = self._build_trades(
                        strikes, spot, next_close, risk_budget=risk_budget)
                    if trades:
                        trade_results.append({
                            **sd,
                            "risk_budget": risk_budget,
                            "trades": trades,
                        })

                    if (i + 1) % 5 == 0:
                        print(f"    {i+1}/{len(signal_days)}...", end="\r",
                              flush=True)
                    time.sleep(0.3)

                if trade_results:
                    # Show performance by composite type
                    by_composite: Dict[str, List] = {}
                    for r in trade_results:
                        comp = r.get("composite", "?")
                        by_composite.setdefault(comp, []).append(r)

                    print(f"\n  {C.BOLD}Performance by Signal Type:{C.RESET}")
                    print(f"    {'SIGNAL':<30} {'DAYS':>4} {'AVG RET':>8} "
                          f"{'BOUNCE%':>8}")
                    print(f"    {'-' * 55}")
                    for comp, days in sorted(by_composite.items()):
                        rets = [d["next_return"] for d in days]
                        avg = sum(rets) / len(rets) * 100
                        up = sum(1 for r in rets if r > 0) / len(rets)
                        clr = C.GREEN if avg > 0 else C.RED
                        print(f"    {comp:<30} {len(days):>3} "
                              f"{clr}{avg:>+7.2f}%{C.RESET} {up:>7.0%}")

            # Timeline
            print(f"\n  {C.BOLD}Daily Timeline:{C.RESET}")
            for d in all_days[-20:]:  # last 20 days
                ret = d["next_return"]
                regime = d["regime"]
                comp = d.get("composite", "—")
                clr = regime_colors.get(regime, "")
                r_clr = C.GREEN if ret > 0 else C.RED
                sig = "*" if comp and comp != "—" else " "
                print(f"    {d['date']} {clr}{regime:<12}{C.RESET} "
                      f"{r_clr}{ret*100:>+6.2f}%{C.RESET} "
                      f"{d['close']:>9.2f} {sig} {comp}")

        print(f"\n{C.BOLD}Cache saved to {state.cache_path}{C.RESET}")

    def run_regime_backtest(self, orats, state, months: int = 12,
                             ) -> None:
        """Extended backtest with full regime analysis.

        Unlike run_backtest (fear-only, 6mo default), this:
        - Classifies ALL days into regimes
        - Produces signals for every regime (not just FEAR)
        - Runs trades on all signal days
        - Compares fear-only vs regime-aware strategy
        """
        cfg = self.config
        end = date.today()
        start = end - timedelta(days=months * 30)

        print(f"\n{C.BOLD}{C.CYAN}{'=' * 72}{C.RESET}")
        print(f"  {C.BOLD}REGIME-AWARE BACKTEST{C.RESET}")
        print(f"  {start} -> {end} ({months} months)")
        print(f"  Signal modes: FEAR (existing) + NERVOUS + FLAT + "
              f"COMPLACENT + GREED (new)")
        print(f"{C.BOLD}{C.CYAN}{'=' * 72}{C.RESET}\n")

        cache = state.load_cache()

        def _save_cache():
            state.save_cache(cache)

        for ticker in cfg.tickers:
            print(f"\n  {C.BOLD}{'=' * 68}{C.RESET}")
            print(f"  {C.BOLD}Regime Backtest — {ticker}{C.RESET}")
            print(f"  {C.BOLD}{'=' * 68}{C.RESET}")

            all_days, regime_cls = self._get_all_regime_days(
                ticker, start, end, cache, _save_cache, orats)

            if not all_days:
                print(f"  {C.RED}No data{C.RESET}")
                continue

            total = len(all_days)

            # Split days by signal source
            fear_only = [d for d in all_days if d.get("fear_composite")]
            regime_new = [d for d in all_days
                           if d.get("regime_composite")
                           and not d.get("fear_composite")]
            all_signal = [d for d in all_days if d.get("composite")]
            no_signal = [d for d in all_days if not d.get("composite")]

            print(f"\n  {C.BOLD}Coverage Analysis:{C.RESET}")
            print(f"    Total days:         {total}")
            print(f"    FEAR signals:       {len(fear_only):>4}d "
                  f"({len(fear_only)/total:.1%})")
            print(f"    New regime signals: {len(regime_new):>4}d "
                  f"({len(regime_new)/total:.1%})")
            print(f"    Total actionable:   {len(all_signal):>4}d "
                  f"({len(all_signal)/total:.1%})")
            print(f"    Idle days:          {len(no_signal):>4}d "
                  f"({len(no_signal)/total:.1%})")

            # --- Build trades for ALL signal days ---
            print(f"\n  Building trades for {len(all_signal)} signal days...")
            fear_trades: List[Dict] = []
            regime_trades: List[Dict] = []

            for i, sd in enumerate(all_signal):
                dt = sd["date"]
                spot = sd["close"]
                next_close = sd["next_close"]
                core_count = sd.get("core_count", 2)
                _composite = sd.get("composite")
                _gf = sd.get("groups_firing", 0)
                _wc = sd.get("wing_count", 0)
                _fc = sd.get("fund_count", 0)
                _mc = sd.get("mom_count", 0)
                risk_budget = self.sizer.compute(
                    max(core_count, 2),
                    composite=_composite, groups_firing=_gf,
                    wing_count=_wc, fund_count=_fc, mom_count=_mc,
                )["risk_budget"]

                chain = self._fetch_hist_chain(
                    orats, ticker, dt, cache, _save_cache)
                if not chain:
                    continue

                exp = self._find_next_expiry(chain, dt)
                if not exp:
                    continue

                strikes = [s for s in chain if s.get("expirDate") == exp]
                if len(strikes) < 10:
                    continue

                trades = self._build_trades(
                    strikes, spot, next_close, risk_budget=risk_budget)
                if not trades:
                    continue

                summary = sd.get("summary", {})
                _apick = None
                if summary:
                    _ranked = self.selector.select(
                        summary, core_count, composite=_composite,
                        groups_firing=_gf, wing_count=_wc,
                        fund_count=_fc, mom_count=_mc)
                    _apick = _ranked[0][0] if _ranked else None
                entry = {
                    **sd,
                    "risk_budget": risk_budget,
                    "trades": trades,
                    "sizing": {"risk_budget": risk_budget,
                               "core_count": core_count},
                    "adaptive_pick": _apick,
                    "adaptive_reason": "",
                }

                if sd.get("fear_composite"):
                    fear_trades.append(entry)
                else:
                    regime_trades.append(entry)

                if (i + 1) % 10 == 0:
                    print(f"    {i+1}/{len(all_signal)}...", end="\r",
                          flush=True)
                time.sleep(0.3)

            print(f"    {len(fear_trades)} fear trades, "
                  f"{len(regime_trades)} regime trades")

            # --- Compare strategies ---
            print(f"\n{C.BOLD}{C.CYAN}{'=' * 72}{C.RESET}")
            print(f"  {C.BOLD}FEAR-ONLY vs REGIME-AWARE comparison{C.RESET}")
            print(f"{C.BOLD}{C.CYAN}{'=' * 72}{C.RESET}")

            for label, trades_list in [
                ("FEAR-ONLY (existing)", fear_trades),
                ("REGIME-AWARE (new)", regime_trades),
                ("COMBINED (all)", fear_trades + regime_trades),
            ]:
                if not trades_list:
                    print(f"\n  {C.BOLD}{label}:{C.RESET} no trades")
                    continue

                # Aggregate by structure
                by_name: Dict[str, List] = {}
                for r in trades_list:
                    for t in r["trades"]:
                        by_name.setdefault(t["name"], []).append({
                            **t,
                            "next_return": r["next_return"],
                            "date": r["date"],
                        })

                print(f"\n  {C.BOLD}{label} ({len(trades_list)} signal days):{C.RESET}")
                print(f"    {'STRUCTURE':<24} {'N':>3} {'WIN%':>6} "
                      f"{'AVG P&L':>9} {'TOTAL':>10}")
                print(f"    {'-' * 56}")

                for name in self.STRUCTURE_NAMES:
                    entries = by_name.get(name, [])
                    if not entries:
                        continue
                    pnls = [e["pnl"] for e in entries]
                    n = len(pnls)
                    wins = sum(1 for p in pnls if p > 0)
                    win_pct = wins / n if n else 0
                    avg = sum(pnls) / n if n else 0
                    total_pnl = sum(pnls)
                    w_clr = (C.GREEN if win_pct > 0.55
                             else C.RED if win_pct < 0.45
                             else C.YELLOW)
                    t_clr = C.GREEN if total_pnl > 0 else C.RED
                    print(f"    {name:<24} {n:>3} "
                          f"{w_clr}{win_pct:>5.0%}{C.RESET} "
                          f"${avg:>+8.0f} "
                          f"{t_clr}${total_pnl:>+9.0f}{C.RESET}")

            # --- Regime-specific best structures ---
            if regime_trades:
                print(f"\n  {C.BOLD}Best Structure per Regime:{C.RESET}")
                regime_trade_map: Dict[str, List] = {}
                for r in regime_trades:
                    regime_trade_map.setdefault(r["regime"], []).append(r)

                for r_name in ["NERVOUS", "FLAT", "COMPLACENT", "GREED"]:
                    r_days = regime_trade_map.get(r_name, [])
                    if not r_days:
                        continue
                    # Find best structure for this regime
                    by_struct: Dict[str, List[float]] = {}
                    for d in r_days:
                        for t in d["trades"]:
                            by_struct.setdefault(t["name"], []).append(t["pnl"])
                    if not by_struct:
                        continue
                    best_name = max(by_struct,
                                    key=lambda k: sum(by_struct[k]) / len(by_struct[k]))
                    pnls = by_struct[best_name]
                    avg = sum(pnls) / len(pnls)
                    wins = sum(1 for p in pnls if p > 0)
                    clr = regime_colors = {
                        "NERVOUS": C.YELLOW, "FLAT": C.BLUE,
                        "COMPLACENT": C.GREEN, "GREED": C.MAGENTA,
                    }.get(r_name, "")
                    print(f"    {clr}{r_name:<12}{C.RESET} → "
                          f"{best_name} "
                          f"({len(pnls)}d, {wins}/{len(pnls)} win, "
                          f"avg ${avg:+,.0f})")

            # If we have fear trades, run the existing comparison too
            if fear_trades:
                print(f"\n  {C.BOLD}Detailed FEAR signal analysis:{C.RESET}")
                self._print_results(ticker, fear_trades, use_sizing=True)

        print(f"\n{C.BOLD}Cache saved to {state.cache_path}{C.RESET}")

    # -- extended signal analysis across regimes --------------------------------

    def run_signal_analysis(self, orats, state, months: int = 12) -> None:
        """Analyze all 20 signals across regimes and time periods.

        For each signal, reports:
          - Overall hit rate and edge vs baseline
          - Per-regime hit rate (FEAR, NERVOUS, FLAT, COMPLACENT, GREED)
          - Per-composite hit rate (FEAR_BOUNCE, FUNDING_STRESS, etc.)
          - Signal group co-firing patterns
        """
        cfg = self.config
        cache = state.load_cache()

        def _save_cache():
            state.save_cache(cache)

        end = date.today()
        start = end - timedelta(days=months * 30)

        print(f"\n{C.BOLD}{C.CYAN}{'=' * 78}{C.RESET}")
        print(f"  {C.BOLD}EXPANDED SIGNAL ANALYSIS — 20 signals × 5 regimes{C.RESET}")
        print(f"  {start} to {end} ({months} months)")
        print(f"{C.BOLD}{C.CYAN}{'=' * 78}{C.RESET}")

        for ticker in cfg.tickers:
            print(f"\n  {C.BOLD}Loading {ticker}...{C.RESET}", flush=True)
            all_days, regime_cls = self._get_all_regime_days(
                ticker, start, end, cache, _save_cache, orats)

            if not all_days:
                print(f"  {C.RED}No data for {ticker}{C.RESET}")
                continue

            n = len(all_days)
            all_returns = [d["next_return"] for d in all_days]
            base_avg = sum(all_returns) / n
            base_up = sum(1 for r in all_returns if r > 0) / n

            print(f"  {n} trading days | baseline: avg={base_avg*100:+.3f}% "
                  f"up-rate={base_up:.1%}")

            # --- Per-signal analysis ---
            self._print_signal_matrix(all_days, base_avg, base_up, ticker)

            # --- Per-composite analysis ---
            self._print_composite_analysis(all_days, base_avg, ticker)

            # --- Per-regime signal analysis ---
            self._print_regime_signal_matrix(all_days, ticker)

            # --- Signal group co-firing analysis ---
            self._print_group_cofiring(all_days, base_avg, ticker)

        state.save_cache(cache)
        print(f"\n{C.BOLD}Analysis complete.{C.RESET}")

    def _print_signal_matrix(self, all_days: List[Dict],
                             base_avg: float, base_up: float,
                             ticker: str) -> None:
        """Print per-signal hit rates and edge."""
        cfg = self.monitor.config
        n = len(all_days)

        # All 20 signal keys in display order
        sig_keys = self.monitor.SIGNAL_ORDER

        print(f"\n  {C.BOLD}{ticker} — Individual Signal Performance{C.RESET}")
        print(f"  {'Signal':<24} {'Fire':>5} {'Freq':>5} "
              f"{'Hit%':>5} {'AvgR%':>7} {'Edge':>6} {'Category':<12}")
        print(f"  {'—'*24} {'—'*5} {'—'*5} "
              f"{'—'*5} {'—'*7} {'—'*6} {'—'*12}")

        for key in sig_keys:
            fire_days = [d for d in all_days
                         if d["signals"].get(key, {}).get("level") == "ACTION"]
            if not fire_days:
                continue

            fire_n = len(fire_days)
            freq = fire_n / n
            fire_returns = [d["next_return"] for d in fire_days]
            avg_ret = sum(fire_returns) / fire_n
            edge = avg_ret - base_avg
            hits_up = sum(1 for r in fire_returns if r > 0) / fire_n

            # Categorize
            if key in cfg.core_signals:
                cat = "Core"
            elif key in cfg.wing_signals:
                cat = "Wing"
            elif key in cfg.funding_signals:
                cat = "Funding"
            elif key in cfg.momentum_signals:
                cat = "Momentum"
            else:
                cat = "Tier2-3"

            clr = C.GREEN if hits_up >= 0.65 else C.YELLOW if hits_up >= 0.55 else C.DIM
            print(f"  {clr}{key:<24}{C.RESET} {fire_n:>5} {freq:>5.1%} "
                  f"{clr}{hits_up:>5.1%}{C.RESET} {avg_ret*100:>+7.3f} "
                  f"{'+'if edge>0 else ''}{edge*100:>5.3f} {cat:<12}")

    def _print_composite_analysis(self, all_days: List[Dict],
                                  base_avg: float, ticker: str) -> None:
        """Print per-composite signal performance."""
        composites: Dict[str, List[Dict]] = {}
        for d in all_days:
            comp = d.get("composite")
            if comp:
                composites.setdefault(comp, []).append(d)

        if not composites:
            return

        print(f"\n  {C.BOLD}{ticker} — Composite Signal Performance{C.RESET}")
        print(f"  {'Composite':<30} {'Days':>4} {'Hit%':>5} "
              f"{'AvgR%':>7} {'Edge':>6} {'AvgGrp':>6}")
        print(f"  {'—'*30} {'—'*4} {'—'*5} "
              f"{'—'*7} {'—'*6} {'—'*6}")

        for comp_name in ["MULTI_SIGNAL_STRONG", "FEAR_BOUNCE_STRONG",
                          "FEAR_BOUNCE_STRONG_OPEX", "FUNDING_STRESS",
                          "WING_PANIC", "VOL_ACCELERATION",
                          "FEAR_BOUNCE_LONG"]:
            days = composites.get(comp_name, [])
            if not days:
                continue
            returns = [d["next_return"] for d in days]
            avg = sum(returns) / len(returns)
            edge = avg - base_avg
            hits = sum(1 for r in returns if r > 0) / len(returns)
            avg_grp = sum(d.get("groups_firing", 0) for d in days) / len(days)

            clr = C.GREEN if hits >= 0.70 else C.YELLOW if hits >= 0.60 else C.DIM
            print(f"  {clr}{comp_name:<30}{C.RESET} {len(days):>4} "
                  f"{clr}{hits:>5.1%}{C.RESET} {avg*100:>+7.3f} "
                  f"{'+'if edge>0 else ''}{edge*100:>5.3f} {avg_grp:>6.1f}")

        # Also show regime-based composites
        regime_composites: Dict[str, List[Dict]] = {}
        for d in all_days:
            rc = d.get("regime_composite")
            if rc and rc not in composites:
                regime_composites.setdefault(rc, []).append(d)
        for rc_name, days in sorted(regime_composites.items(),
                                     key=lambda x: -len(x[1])):
            if not days:
                continue
            returns = [d["next_return"] for d in days]
            avg = sum(returns) / len(returns)
            edge = avg - base_avg
            hits = sum(1 for r in returns if r > 0) / len(returns)
            clr = C.DIM
            print(f"  {clr}{rc_name:<30}{C.RESET} {len(days):>4} "
                  f"{clr}{hits:>5.1%}{C.RESET} {avg*100:>+7.3f} "
                  f"{'+'if edge>0 else ''}{edge*100:>5.3f}       ")

    def _print_regime_signal_matrix(self, all_days: List[Dict],
                                    ticker: str) -> None:
        """Print signal hit rates WITHIN each regime."""
        cfg = self.monitor.config
        regimes: Dict[str, List[Dict]] = {}
        for d in all_days:
            regimes.setdefault(d["regime"], []).append(d)

        regime_colors = {
            "FEAR": C.RED, "NERVOUS": C.YELLOW, "FLAT": C.BLUE,
            "COMPLACENT": C.GREEN, "GREED": C.MAGENTA,
        }

        # Focus on signals that fire meaningfully
        key_signals = list(cfg.core_signals) + list(cfg.wing_signals) + \
                      list(cfg.funding_signals) + list(cfg.momentum_signals)

        print(f"\n  {C.BOLD}{ticker} — Signal Hit Rate by Regime{C.RESET}")
        header = f"  {'Signal':<20}"
        for regime in ["FEAR", "NERVOUS", "FLAT", "COMPLACENT", "GREED"]:
            rclr = regime_colors.get(regime, "")
            rn = len(regimes.get(regime, []))
            header += f" {rclr}{regime[:5]:>7}({rn:>2}){C.RESET}"
        print(header)
        print(f"  {'—'*20}" + " —————————" * 5)

        for key in key_signals:
            row = f"  {key:<20}"
            for regime in ["FEAR", "NERVOUS", "FLAT", "COMPLACENT", "GREED"]:
                r_days = regimes.get(regime, [])
                if not r_days:
                    row += f" {'—':>10}"
                    continue
                fire_days = [d for d in r_days
                             if d["signals"].get(key, {}).get("level") == "ACTION"]
                if not fire_days:
                    row += f" {'·':>10}"
                    continue
                fire_ret = [d["next_return"] for d in fire_days]
                hits = sum(1 for r in fire_ret if r > 0) / len(fire_ret)
                clr = C.GREEN if hits >= 0.70 else C.YELLOW if hits >= 0.55 else C.RED
                row += f" {clr}{hits:>5.0%}({len(fire_days):>2}){C.RESET}"
            print(row)

    def _print_group_cofiring(self, all_days: List[Dict],
                              base_avg: float, ticker: str) -> None:
        """Show what happens when multiple signal groups fire together."""
        print(f"\n  {C.BOLD}{ticker} — Signal Group Co-firing{C.RESET}")
        print(f"  {'Groups':>10} {'Days':>5} {'Freq':>5} "
              f"{'Hit%':>5} {'AvgR%':>7} {'Edge':>6}")
        print(f"  {'—'*10} {'—'*5} {'—'*5} "
              f"{'—'*5} {'—'*7} {'—'*6}")

        n = len(all_days)
        for g_count in range(0, 5):
            g_days = [d for d in all_days if d.get("groups_firing", 0) == g_count]
            if not g_days:
                continue
            returns = [d["next_return"] for d in g_days]
            avg = sum(returns) / len(returns)
            edge = avg - base_avg
            hits = sum(1 for r in returns if r > 0) / len(returns)
            freq = len(g_days) / n

            clr = C.GREEN if hits >= 0.70 else C.YELLOW if hits >= 0.55 else C.DIM
            label = f"{g_count}"
            if g_count == 0:
                label = "0 (quiet)"
            elif g_count >= 3:
                label = f"{g_count} (MULTI)"
            print(f"  {label:>10} {len(g_days):>5} {freq:>5.1%} "
                  f"{clr}{hits:>5.1%}{C.RESET} {avg*100:>+7.3f} "
                  f"{'+'if edge>0 else ''}{edge*100:>5.3f}")

    # -- comprehensive portfolio backtest ----------------------------------------

    @staticmethod
    def _spread_payoff(spot: float, move_pts: float, iv: float,
                       spread_width: float = 10.0,
                       is_bullish: bool = True) -> Tuple[float, float]:
        """Compute 0DTE debit spread payoff.

        Models a vertical debit spread entered ~60 min before the move.
        Uses IV to estimate entry cost (higher IV = more expensive entry).

        Args:
            spot: Current SPX price.
            move_pts: Signed point move (positive = up).
            iv: Annualized IV (e.g., 0.15 for 15%).
            spread_width: Width in points (default 10).
            is_bullish: True = call debit spread, False = put debit spread.

        Returns:
            (pnl_per_spread, cost_per_spread) in dollars.
            SPX multiplier is $100 per point.
        """
        mult = 100.0  # SPX options multiplier

        # 0DTE spread cost as fraction of width.
        # ATM spread cost ≈ width × N(d1) adjustment.
        # With ~1-2 hours to expiry, theta crush makes spreads cheap.
        # Typical cost: 30-45% of width depending on IV.
        # Higher IV → more expensive (more time value left).
        iv_factor = min(0.50, max(0.25, 0.30 + iv * 0.5))
        cost_pts = spread_width * iv_factor
        cost_dollars = cost_pts * mult

        # Directional move in our favor
        favorable_move = move_pts if is_bullish else -move_pts

        if favorable_move <= 0:
            # Move went against us → lose the debit
            return -cost_dollars, cost_dollars

        # Spread intrinsic value at expiry (capped at width)
        intrinsic = min(favorable_move, spread_width)
        value_at_expiry = intrinsic * mult
        pnl = value_at_expiry - cost_dollars

        return pnl, cost_dollars

    def run_portfolio_backtest(self, orats, state,
                               periods: List[int] = None) -> None:
        """Backtest the 20-signal portfolio with realistic 0DTE options payoffs.

        Key model improvements over linear returns:
          1. Options spread payoff: debit spread with defined risk/reward
          2. Same-day intraday move: signal fires mid-day, we capture
             open→close or open→extreme move (conservative: open→close)
          3. IV-adjusted entry cost: higher IV = more expensive spreads
          4. Signal quality filter: only trade composites with edge
          5. Realistic fill: 10% slippage on theoretical entry

        Spread model:
          - 10-point wide SPX debit spread ($100 multiplier)
          - Entry cost: ~30-45% of width based on IV
          - Max profit: width - cost (150-233% return on risk)
          - Max loss: cost (100% of debit)
          - Qty: risk_budget / cost_per_spread

        Args:
            periods: List of backtest durations in months.
        """
        import math

        if periods is None:
            periods = [60, 36, 12, 6]

        cfg = self.config
        cache = state.load_cache()
        def _save_cache():
            state.save_cache(cache)

        capital = self.sizer.config.account_capital

        # Spread parameters
        spread_width = 10.0    # 10-point wide spread
        slippage_pct = 0.10    # 10% slippage on entry cost
        spx_mult = 100.0       # $100 per point

        print(f"\n{C.BOLD}{C.CYAN}{'=' * 78}{C.RESET}")
        print(f"  {C.BOLD}PORTFOLIO BACKTEST — 0DTE Options Payoff Model{C.RESET}")
        print(f"  Capital: ${capital:,.0f} | {spread_width:.0f}-wide SPX spreads")
        print(f"  Entry: IV-adjusted cost + {slippage_pct:.0%} slippage")
        print(f"  Periods: {', '.join(f'{m}mo' for m in periods)}")
        print(f"{C.BOLD}{C.CYAN}{'=' * 78}{C.RESET}")

        summary_rows: List[Dict] = []

        for months in periods:
            end = date.today()
            start = end - timedelta(days=months * 30)

            print(f"\n{'=' * 78}")
            print(f"  {C.BOLD}{months}mo Backtest: {start} -> {end}{C.RESET}")
            print(f"{'=' * 78}")

            for ticker in ("SPX",):
                print(f"\n  Loading {ticker} ({months}mo)...", flush=True)
                all_days, _ = self._get_all_regime_days(
                    ticker, start, end, cache, _save_cache, orats)

                if not all_days:
                    print(f"  {C.RED}No data for {ticker}{C.RESET}")
                    summary_rows.append({
                        "period": f"{months}mo", "ticker": ticker,
                        "error": "No data",
                    })
                    continue

                print(f"  {len(all_days)} trading days loaded")

                # -- Build equity curve --
                equity = capital
                peak = equity
                max_dd = 0.0
                daily_returns: List[float] = []
                signal_days_count = 0
                wins = 0
                losses = 0
                total_pnl = 0.0
                win_pnl = 0.0
                loss_pnl = 0.0
                spreads_maxed = 0  # track how often spread goes full ITM

                skipped = 0

                for day in all_days:
                    composite = day.get("fear_composite")
                    if not composite:
                        daily_returns.append(0.0)
                        continue

                    core_count = day.get("core_count", 0)
                    groups_firing = day.get("groups_firing", 0)
                    wing_count = day.get("wing_count", 0)
                    fund_count = day.get("fund_count", 0)

                    # -- Signal quality gate --
                    # Only trade the highest conviction setups.
                    # Target: ~10-15% signal frequency (matches live selectivity).
                    #
                    # Requirements (any ONE of):
                    #   1. MULTI_SIGNAL_STRONG (3+ groups, always trade)
                    #   2. Core >= 4 signals (very strong fear confirmation)
                    #   3. Core >= 3 AND groups >= 3 (multi-dimensional)
                    #   4. WING_PANIC or FUNDING_STRESS with 2+ in that group
                    #      AND core >= 3 (independent + core confirmation)
                    trade = False
                    if composite == "MULTI_SIGNAL_STRONG":
                        trade = True
                    elif core_count >= 4:
                        trade = True
                    elif core_count >= 3 and groups_firing >= 3:
                        trade = True
                    elif (composite == "WING_PANIC" and wing_count >= 2
                          and core_count >= 3):
                        trade = True
                    elif (composite == "FUNDING_STRESS" and fund_count >= 2
                          and core_count >= 3):
                        trade = True

                    if not trade:
                        skipped += 1
                        daily_returns.append(0.0)
                        continue

                    signal_days_count += 1

                    # Composite-aware sizing — fixed from initial capital
                    # (let equity compound from P&L, don't compound risk)
                    sizing = self.sizer.compute(
                        core_count,
                        composite=composite,
                        groups_firing=groups_firing,
                        wing_count=day.get("wing_count", 0),
                        fund_count=day.get("fund_count", 0),
                        mom_count=day.get("mom_count", 0),
                    )
                    risk_budget = sizing["risk_budget"]

                    # Get IV for spread pricing
                    summary = day.get("summary", {})
                    iv = float(summary.get("iv10d", 0) or
                               summary.get("iv20d", 0) or 0.15)

                    # Direction: daily fear composites are contrarian bullish
                    is_bullish = "BEAR" not in composite and "SHORT" not in composite

                    # Same-day intraday move.
                    # Signal fires mid-day (~1PM). We model the move as:
                    # - Entry at approximate mid-day price (open+close)/2
                    # - Exit at close for daily composites
                    # - For intraday: use favorable extreme (high for bull, low for bear)
                    #
                    # Conservative model: use open→close move.
                    # The signal fires based on EOD summary data, so the
                    # tradeable move is next day open→close.
                    spot = day["close"]
                    nxt_close = day["next_close"]
                    move_pts = nxt_close - spot  # next-day close-to-close

                    # Compute spread payoff
                    pnl_per, cost_per = self._spread_payoff(
                        spot, move_pts, iv, spread_width, is_bullish)

                    # Apply slippage to entry cost
                    cost_with_slip = cost_per * (1 + slippage_pct)

                    # Number of spreads we can buy with risk budget
                    qty = int(risk_budget / cost_with_slip) if cost_with_slip > 0 else 0
                    if qty < 1:
                        daily_returns.append(0.0)
                        continue

                    actual_risk = qty * cost_with_slip

                    # Adjust P&L for slippage (we paid more, so profit is less)
                    if pnl_per > 0:
                        # Won: value at expiry - cost_with_slippage
                        favorable_move = move_pts if is_bullish else -move_pts
                        intrinsic = min(max(favorable_move, 0), spread_width)
                        value_per = intrinsic * spx_mult
                        trade_pnl = qty * (value_per - cost_with_slip)
                        if intrinsic >= spread_width:
                            spreads_maxed += 1
                    else:
                        # Lost: lose the debit
                        trade_pnl = -actual_risk

                    total_pnl += trade_pnl
                    equity += trade_pnl
                    daily_returns.append(trade_pnl / equity if equity > 0 else 0)

                    if trade_pnl > 0:
                        wins += 1
                        win_pnl += trade_pnl
                    else:
                        losses += 1
                        loss_pnl += trade_pnl

                    if equity > peak:
                        peak = equity
                    dd = (peak - equity) / peak if peak > 0 else 0
                    if dd > max_dd:
                        max_dd = dd

                # -- Compute metrics --
                n_days = len(daily_returns)
                total_trades = wins + losses
                total_return_pct = (equity - capital) / capital * 100
                years = n_days / 252
                ann_return = (
                    ((equity / capital) ** (1 / years) - 1) * 100
                    if years > 0.1 and equity > 0 else 0
                )

                if n_days > 1:
                    mean_r = sum(daily_returns) / n_days
                    var_r = sum((r - mean_r) ** 2 for r in daily_returns) / (n_days - 1)
                    std_r = math.sqrt(var_r)
                    down_returns = [r for r in daily_returns if r < 0]
                    if down_returns:
                        down_var = sum(r ** 2 for r in down_returns) / len(down_returns)
                        down_std = math.sqrt(down_var)
                    else:
                        down_std = 0.001
                    sharpe = (mean_r / std_r * math.sqrt(252)) if std_r > 0 else 0
                    sortino = (mean_r / down_std * math.sqrt(252)) if down_std > 0 else 0
                else:
                    sharpe = sortino = 0

                win_rate = wins / total_trades * 100 if total_trades > 0 else 0
                avg_win = win_pnl / wins if wins > 0 else 0
                avg_loss = loss_pnl / losses if losses > 0 else 0
                profit_factor = (win_pnl / abs(loss_pnl)) if loss_pnl != 0 else float('inf')
                signal_freq = signal_days_count / n_days * 100 if n_days > 0 else 0
                max_pct = spreads_maxed / total_trades * 100 if total_trades > 0 else 0

                # -- Print results --
                print(f"\n  {C.BOLD}{ticker} — {months}mo Results{C.RESET}")
                print(f"  {'—' * 58}")
                clr_ret = C.GREEN if total_return_pct > 0 else C.RED
                print(f"  Total Return:     {clr_ret}{total_return_pct:+.2f}%{C.RESET}"
                      f"  (${total_pnl:+,.0f})")
                print(f"  CAGR:             {clr_ret}{ann_return:+.2f}%{C.RESET}")
                clr_sh = C.GREEN if sharpe > 1.5 else C.YELLOW if sharpe > 0.5 else C.RED
                print(f"  Sharpe Ratio:     {clr_sh}{sharpe:.2f}{C.RESET}")
                print(f"  Sortino Ratio:    {sortino:.2f}")
                print(f"  Max Drawdown:     {C.RED}{max_dd*100:.2f}%{C.RESET}")
                print(f"  Win Rate:         {win_rate:.1f}% ({wins}W / {losses}L)")
                print(f"  Avg Win:          ${avg_win:+,.0f}")
                print(f"  Avg Loss:         ${avg_loss:+,.0f}")
                print(f"  Profit Factor:    {profit_factor:.2f}")
                print(f"  Spreads Maxed:    {max_pct:.1f}% ({spreads_maxed}/{total_trades})")
                print(f"  Signal Frequency: {signal_freq:.1f}% ({signal_days_count}/{n_days} days)"
                      f"  [filtered {skipped} weak signals]")
                print(f"  Final Equity:     ${equity:,.0f}")

                summary_rows.append({
                    "period": f"{months}mo",
                    "ticker": ticker,
                    "days": n_days,
                    "signals": signal_days_count,
                    "return_pct": total_return_pct,
                    "ann_return": ann_return,
                    "sharpe": sharpe,
                    "sortino": sortino,
                    "max_dd": max_dd * 100,
                    "win_rate": win_rate,
                    "pf": profit_factor,
                    "total_pnl": total_pnl,
                })

        # -- Summary table --
        state.save_cache(cache)
        valid = [r for r in summary_rows if "error" not in r]
        if valid:
            print(f"\n\n{C.BOLD}{C.CYAN}{'=' * 78}{C.RESET}")
            print(f"  {C.BOLD}SUMMARY — 0DTE Options Payoff Model{C.RESET}")
            print(f"{C.BOLD}{C.CYAN}{'=' * 78}{C.RESET}")
            print(f"\n  {'Period':>8} {'Days':>5} {'Sigs':>5} "
                  f"{'Return':>8} {'CAGR':>7} {'Sharpe':>7} "
                  f"{'Sortino':>8} {'MaxDD':>7} {'WinR%':>6} {'PF':>5} "
                  f"{'P&L':>12}")
            print(f"  {'—'*8} {'—'*5} {'—'*5} "
                  f"{'—'*8} {'—'*7} {'—'*7} "
                  f"{'—'*8} {'—'*7} {'—'*6} {'—'*5} "
                  f"{'—'*12}")
            for r in valid:
                clr = C.GREEN if r["sharpe"] > 1.0 else C.YELLOW if r["sharpe"] > 0 else C.RED
                print(f"  {r['period']:>8} {r['days']:>5} {r['signals']:>5} "
                      f"{clr}{r['return_pct']:>+7.2f}%{C.RESET} "
                      f"{r['ann_return']:>+6.2f}% "
                      f"{clr}{r['sharpe']:>7.2f}{C.RESET} "
                      f"{r['sortino']:>8.2f} "
                      f"{r['max_dd']:>6.2f}% "
                      f"{r['win_rate']:>5.1f}% "
                      f"{r['pf']:>5.2f} "
                      f"${r['total_pnl']:>+11,.0f}")

        print(f"\n{C.BOLD}Backtest complete. Cache saved.{C.RESET}")
