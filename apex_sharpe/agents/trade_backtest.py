"""
TradeStructureBacktest — backtest 7 trade structures on FEAR_BOUNCE signal days.

Supports signal-weighted sizing via SignalSizer and adaptive structure
selection via AdaptiveSelector. Compares all structures side-by-side plus
virtual strategies: "Adaptive" (best structure per day), regime split
(bounce vs sell-through), and "Flip" (bullish + bearish recovery).
"""

import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from .base import BaseAgent
from .zero_dte import ZeroDTEAgent
from ..config import (TradeBacktestCfg, ZeroDTECfg, SignalSizingCfg,
                       AdaptiveSelectorCfg, CallRatioSpreadCfg,
                       BrokenWingButterflyCfg)
from ..selection.signal_sizer import SignalSizer
from ..selection.adaptive_selector import AdaptiveSelector
from ..types import AgentResult, C, TradeStructure


class TradeStructureBacktest(BaseAgent):
    """Backtest 7 trade structures on FEAR_BOUNCE signal days.

    Bullish structures:
      1. Call debit spread  (buy ~40d call, sell ~25d call)
      2. Bull put spread    (sell ~30d put, buy ~15d put)
      3. Long call          (buy ~50d call)
      4. Call ratio spread  (buy 1x ~50d, sell 2x ~25d)
      5. Broken wing butterfly (1/-2/1 asymmetric calls)

    Bearish structures:
      6. Put debit spread   (buy ~40d put, sell ~25d put)
      7. Long put           (buy ~50d put)

    Virtual strategies:
      - Adaptive: best structure per day via vol surface conditions
      - Oracle Flip: best bullish on bounces, best bearish on sell-throughs
      - Signal Flip: adaptive pick + bearish recovery when adaptive loses
    """

    # Structure display order
    STRUCTURE_NAMES = [
        "Call Debit Spread", "Bull Put Spread", "Long Call",
        "Call Ratio Spread", "Broken Wing Butterfly",
        "Put Debit Spread", "Long Put",
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
        months = context.get("months", 6)
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
        """Build all 5 trade structures from a chain.

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

        return trades

    # -- main backtest runner -------------------------------------------------

    def run_backtest(self, orats, state, months: int = 6, db=None,
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

                # Signal-weighted sizing
                if use_sizing:
                    sizing = self.sizer.compute(core_count)
                    risk_budget = sizing["risk_budget"]
                else:
                    risk_budget = cfg.max_risk
                    sizing = {"risk_budget": risk_budget, "multiplier": 1.0,
                              "core_count": core_count}

                # Adaptive selection
                summary = sd.get("summary", {})
                adaptive_ranked = self.selector.select(summary, core_count)
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

            composite, _ = m.determine_direction(signals, intraday=False)
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
        "Put Debit Spread", "Long Put",
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
