"""
ZeroDTEMonitor — 0DTE directional signal detection from vol surface.

Extracted from trading_pipeline.py. 10 signals, 5-core composite system.
Uses ORATSClient and StateManager via DI instead of module-level functions.
"""

import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .base import BaseAgent
from ..config import ZeroDTECfg
from ..data.yfinance_client import yf_price, yf_credit
from ..types import AgentResult, C


class ZeroDTEAgent(BaseAgent):
    """0DTE directional signal monitor using ORATS vol surface data.

    Detects regime shifts (skew steepening, contango collapse, IV/RV divergence)
    that historically precede directional SPX moves by 60-90 minutes.
    """

    # Display order for dashboard
    SIGNAL_ORDER = [
        "skewing", "rip", "skew_25d_rr", "contango", "credit_spread",
        "iv_rv_spread", "fbfwd30_20", "rSlp30", "fwd_kink", "rDrv30",
    ]

    def __init__(self, config: ZeroDTECfg = None):
        config = config or ZeroDTECfg()
        super().__init__("ZeroDTE", config)
        self.baseline: Dict[str, Dict] = {}
        self.prev_day: Dict[str, Dict] = {}
        self.log_entries: List[Dict] = []

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _safe(d: Dict, key: str, default: float = 0.0) -> float:
        v = d.get(key)
        if v is None:
            return default
        try:
            return float(v)
        except (ValueError, TypeError):
            return default

    # -- signal computation -----------------------------------------------

    def compute_signals(self, ticker: str, summary: Dict) -> Dict[str, Dict]:
        """Compute all 10 signals from ORATS summary row."""
        cfg = self.config
        base = self.baseline.get(ticker, {})
        prev = self.prev_day.get(ticker, {})
        sf = self._safe
        signals: Dict[str, Dict] = {}

        # 1. IV vs RV spread (Tier 1)
        iv30 = sf(summary, "iv30d")
        rv30 = sf(summary, "rVol30")
        spread = iv30 - rv30
        signals["iv_rv_spread"] = {
            "value": round(spread, 4),
            "level": "ACTION" if spread < cfg.iv_rv_thresh else "OK",
            "tier": 1, "label": "IV vs RV Spread",
        }

        # 2. Skew change (Tier 1)
        skew = sf(summary, "dlt25Iv30d") - sf(summary, "dlt75Iv30d")
        base_skew = sf(base, "dlt25Iv30d", sf(summary, "dlt25Iv30d")) \
                   - sf(base, "dlt75Iv30d", sf(summary, "dlt75Iv30d"))
        skew_chg = skew - base_skew
        signals["skew_25d_rr"] = {
            "value": round(skew, 4),
            "baseline": round(base_skew, 4),
            "change": round(skew_chg, 4),
            "level": "ACTION" if abs(skew_chg) > cfg.skew_change_thresh else "OK",
            "tier": 1, "label": "Skew (25d RR)",
        }

        # 3. Contango collapse (Tier 1)
        ct = sf(summary, "contango")
        ct_base = sf(base, "contango", ct)
        ct_pct = (ct - ct_base) / abs(ct_base) if abs(ct_base) > 0.001 else 0.0
        signals["contango"] = {
            "value": round(ct, 4),
            "baseline": round(ct_base, 4),
            "pct_change": round(ct_pct, 4),
            "level": "ACTION" if (ct_pct < -cfg.contango_drop_thresh or ct < 0) else "OK",
            "tier": 1, "label": "Contango",
        }

        # 4. Forward/backward ratio (Tier 2)
        fb = sf(summary, "fbfwd30_20", 1.0)
        signals["fbfwd30_20"] = {
            "value": round(fb, 4),
            "level": "WARNING" if (fb > cfg.fbfwd_high or fb < cfg.fbfwd_low) else "OK",
            "tier": 2, "label": "ORATS Forecast (fbfwd)",
        }

        # 5. Realized skew slope change (Tier 2)
        rslp = sf(summary, "rSlp30")
        prev_rslp = sf(prev, "rSlp30", rslp)
        slope_chg = rslp - prev_rslp
        signals["rSlp30"] = {
            "value": round(rslp, 4),
            "prev_day": round(prev_rslp, 4),
            "change": round(slope_chg, 4),
            "level": "WARNING" if abs(slope_chg) > cfg.slope_change_thresh else "OK",
            "tier": 2, "label": "Skew Slope (rSlp30)",
        }

        # 6. Forward vol kink (Tier 3)
        kink = abs(sf(summary, "fwd30_20") - sf(summary, "fwd60_30"))
        signals["fwd_kink"] = {
            "value": round(kink, 4),
            "level": "INFO" if kink > cfg.fwd_kink_thresh else "OK",
            "tier": 3, "label": "Fwd Vol Kink",
        }

        # 7. RV derivative climbing while IV flat (Tier 3)
        rdrv = sf(summary, "rDrv30")
        prev_rdrv = sf(prev, "rDrv30", rdrv)
        base_iv = sf(base, "iv30d", iv30)
        iv_flat = abs(iv30 - base_iv) < 0.005
        signals["rDrv30"] = {
            "value": round(rdrv, 4),
            "prev_day": round(prev_rdrv, 4),
            "level": "INFO" if (rdrv > prev_rdrv + 0.01 and iv_flat) else "OK",
            "tier": 3, "label": "RV Derivative",
        }

        # 8. Skewing (Tier 1)
        skewing = sf(summary, "skewing")
        signals["skewing"] = {
            "value": round(skewing, 4),
            "level": "ACTION" if skewing > cfg.skewing_thresh else "OK",
            "tier": 1, "label": "Skewing",
        }

        # 9. RIP (Tier 1)
        rip_val = sf(summary, "rip")
        signals["rip"] = {
            "value": round(rip_val, 2),
            "level": "ACTION" if rip_val > cfg.rip_thresh else "OK",
            "tier": 1, "label": "Risk Implied Premium",
        }

        return signals

    def compute_credit_signal(
        self, hyg: float, tlt: float, hyg_prev: float, tlt_prev: float,
    ) -> Dict[str, Dict]:
        """Credit spread signal from HYG/TLT daily changes."""
        cfg = self.config
        if not all([hyg, tlt, hyg_prev, tlt_prev]):
            return {}
        if hyg_prev == 0 or tlt_prev == 0:
            return {}
        hyg_chg = (hyg - hyg_prev) / hyg_prev
        tlt_chg = (tlt - tlt_prev) / tlt_prev
        credit = hyg_chg - tlt_chg
        return {
            "credit_spread": {
                "value": round(credit, 4),
                "level": "ACTION" if credit < cfg.credit_thresh else "OK",
                "tier": 1, "label": "Credit Spread (HYG-TLT)",
            }
        }

    def determine_direction(
        self, signals: Dict, intraday: bool = False,
    ) -> Tuple[Optional[str], List[str]]:
        """Composite signal using ANY 3-of-5 core signals."""
        cfg = self.config
        t1_all = [k for k, v in signals.items()
                  if v.get("tier") == 1 and v.get("level") == "ACTION"]
        t1_core = [k for k in cfg.core_signals
                   if signals.get(k, {}).get("level") == "ACTION"]

        if len(t1_core) < 2:
            return None, t1_all

        strong = len(t1_core) >= cfg.composite_min

        if intraday:
            tag = "DIRECTIONAL_BEARISH" if strong else "DIRECTIONAL_BEARISH_WEAK"
            return tag, t1_all
        else:
            if strong:
                return "FEAR_BOUNCE_STRONG", t1_all
            return "FEAR_BOUNCE_LONG", t1_all

    # -- BaseAgent interface ----------------------------------------------

    def run(self, context: Dict[str, Any]) -> AgentResult:
        """Compute signals for a single ticker/summary.

        Context keys:
            ticker: str
            summary: Dict — ORATS summary row
            credit: Optional[Tuple] — (hyg, tlt, hyg_prev, tlt_prev)
            intraday: bool (default False)
        """
        ticker = context["ticker"]
        summary = context["summary"]
        intraday = context.get("intraday", False)

        signals = self.compute_signals(ticker, summary)

        # Merge credit signal if available
        credit = context.get("credit")
        if credit and len(credit) == 4:
            signals.update(self.compute_credit_signal(*credit))

        composite, t1 = self.determine_direction(signals, intraday=intraday)

        return self._result(
            success=True,
            data={
                "signals": signals,
                "composite": composite,
                "tier1_firing": t1,
            },
        )

    # -- terminal dashboard -----------------------------------------------

    def _fmt_val(self, key: str, sig: Dict) -> Tuple[str, str, str]:
        v = sig.get("value", 0)
        if key in ("iv_rv_spread", "skew_25d_rr", "fwd_kink", "credit_spread"):
            val_s = f"{v * 100:+.1f}%"
        elif key == "rip":
            val_s = f"{v:.1f}"
        else:
            val_s = f"{v:.4f}"

        base_s = ""
        if "baseline" in sig:
            b = sig["baseline"]
            base_s = f"{b * 100:+.1f}%" if key == "skew_25d_rr" else f"{b:.4f}"
        elif "prev_day" in sig:
            base_s = f"{sig['prev_day']:.4f}"

        chg_s = ""
        if key == "skew_25d_rr" and "change" in sig:
            chg_s = f"{sig['change'] * 100:+.1f}%"
        elif key == "contango" and "pct_change" in sig:
            chg_s = f"{sig['pct_change'] * 100:+.0f}%"
        elif "change" in sig:
            chg_s = f"{sig['change']:+.4f}"
        return val_s, base_s, chg_s

    def print_dashboard(
        self, ticker: str, spot_orats: float, spot_yf: Optional[float],
        signals: Dict, composite: Optional[str], t1_firing: List[str],
    ) -> None:
        now_s = datetime.now().strftime("%Y-%m-%d %I:%M %p ET")
        yf_s = f" (yf: {spot_yf:.2f})" if spot_yf else ""

        print(f"\n{C.BOLD}{C.CYAN}{'=' * 60}{C.RESET}")
        print(f"  {C.BOLD}0DTE SIGNAL MONITOR — {now_s}{C.RESET}")
        print(f"  {ticker}: {spot_orats:.2f}{yf_s}")
        print(f"{C.BOLD}{C.CYAN}{'=' * 60}{C.RESET}")

        print(f"\n  {'SIGNAL':<26} {'VALUE':>8} {'BASELINE':>10} {'CHANGE':>8} STATUS")
        print(f"  {'-' * 62}")

        for key in self.SIGNAL_ORDER:
            sig = signals.get(key, {})
            label = sig.get("label", key)
            level = sig.get("level", "OK")
            val_s, base_s, chg_s = self._fmt_val(key, sig)

            if level == "ACTION":
                st = f"{C.RED}{C.BOLD}! ACTION{C.RESET}"
            elif level == "WARNING":
                st = f"{C.YELLOW}* WARNING{C.RESET}"
            elif level == "INFO":
                st = f"{C.BLUE}i INFO{C.RESET}"
            else:
                st = f"{C.GREEN}  OK{C.RESET}"

            print(f"  {label:<26} {val_s:>8} {base_s:>10} {chg_s:>8}  {st}")

        if composite:
            dm = {
                "DIRECTIONAL_BEARISH": ("BUY PUTS (3+ signals)", C.RED),
                "DIRECTIONAL_BEARISH_WEAK": ("BUY PUTS (2 signals)", C.RED),
                "DIRECTIONAL_BULLISH": ("BUY CALLS", C.GREEN),
                "FEAR_BOUNCE_STRONG": ("FEAR SPIKE -> BUY CALLS (3+ signals, 86%)", C.GREEN),
                "FEAR_BOUNCE_LONG": ("FEAR SPIKE -> BUY CALLS (2 signals, 75%)", C.GREEN),
            }
            action, clr = dm.get(composite, ("ALERT", C.YELLOW))
            cfg = self.config
            core_firing = [k for k in cfg.core_signals
                           if k in t1_firing or
                           signals.get(k, {}).get("level") == "ACTION"]
            print(f"\n  {clr}{C.BOLD}{'=' * 56}{C.RESET}")
            print(f"  {clr}{C.BOLD}>>> {action} <<<{C.RESET}")
            print(f"  {C.DIM}{len(core_firing)}/{len(cfg.core_signals)} core signals: "
                  f"{', '.join(core_firing)}{C.RESET}")
            print(f"  {clr}{C.BOLD}{'=' * 56}{C.RESET}")

    # -- live polling mode ------------------------------------------------

    def run_live(self, orats, state, db=None) -> None:
        """Live polling mode. Runs until Ctrl+C."""
        cfg = self.config
        from ..agents.reporter import send_notification

        print(f"{C.BOLD}{C.CYAN}0DTE Signal Monitor — Live Mode{C.RESET}")
        print(f"  Tickers: {', '.join(cfg.tickers)}")
        print(f"  Interval: {cfg.poll_interval}s | Log: {state.signals_path}")
        print(f"  Core composite: ANY {cfg.composite_min} of "
              f"{len(cfg.core_signals)} -> {', '.join(cfg.core_signals)}\n")

        # Load previous-day summaries
        for days_back in range(1, 5):
            d = (date.today() - timedelta(days=days_back)).strftime("%Y-%m-%d")
            for ticker in cfg.tickers:
                if ticker in self.prev_day:
                    continue
                resp = orats.hist_summaries(ticker, d)
                if resp and resp.get("data"):
                    self.prev_day[ticker] = resp["data"][0]
                    print(f"  Loaded prev-day: {ticker} ({d})")

        n = 0
        try:
            while True:
                n += 1
                hyg, tlt, hyg_p, tlt_p = yf_credit()
                credit_sig = self.compute_credit_signal(hyg, tlt, hyg_p, tlt_p)

                for ticker in cfg.tickers:
                    resp = orats.summaries(ticker)
                    if not resp or not resp.get("data"):
                        print(f"  {C.RED}No data for {ticker}{C.RESET}")
                        continue
                    summary = resp["data"][0]
                    spot = self._safe(summary, "stockPrice")
                    spot_yf = yf_price(ticker)

                    if ticker not in self.baseline:
                        self.baseline[ticker] = dict(summary)
                        print(f"  {C.GREEN}Baseline set: {ticker}{C.RESET}")

                    signals = self.compute_signals(ticker, summary)
                    signals.update(credit_sig)
                    composite, t1 = self.determine_direction(signals, intraday=True)
                    self.print_dashboard(ticker, spot, spot_yf, signals, composite, t1)

                    # Log
                    entry = {
                        "timestamp": datetime.now().isoformat(),
                        "ticker": ticker,
                        "spot_orats": spot,
                        "spot_yfinance": spot_yf,
                        "signals": {
                            k: {kk: vv for kk, vv in v.items() if kk != "label"}
                            for k, v in signals.items()
                        },
                        "composite": composite,
                    }
                    self.log_entries.append(entry)
                    state.append_signal(entry)

                    # DB logging
                    if db and db.enabled:
                        core_firing = [k for k in cfg.core_signals
                                       if signals.get(k, {}).get("level") == "ACTION"]
                        db.run({
                            "action": "log_0dte_signal",
                            "ticker": ticker,
                            "trade_date": date.today().isoformat(),
                            "spot_price": spot,
                            "composite": composite,
                            "core_count": len(core_firing),
                            "signals": signals,
                        })

                    # Notifications
                    if composite:
                        dm = {
                            "DIRECTIONAL_BEARISH": "BUY PUTS (strong)",
                            "DIRECTIONAL_BEARISH_WEAK": "BUY PUTS (watch)",
                            "DIRECTIONAL_BULLISH": "BUY CALLS",
                        }
                        send_notification(
                            title=f"0DTE: {dm.get(composite, 'ALERT')}",
                            subtitle=f"{ticker} — {len(t1)} Tier 1",
                            message=", ".join(t1),
                        )
                    elif any(v["level"] == "WARNING" for v in signals.values()):
                        wn = sum(1 for v in signals.values() if v["level"] == "WARNING")
                        send_notification(
                            f"0DTE: {ticker} Warning",
                            f"{wn} warning(s)",
                            "Check terminal",
                            sound=False,
                        )

                print(f"\n  {C.DIM}Poll #{n}. Next in {cfg.poll_interval}s "
                      f"(Ctrl+C to stop){C.RESET}")
                time.sleep(cfg.poll_interval)

        except KeyboardInterrupt:
            print(f"\n{C.BOLD}Stopped. {len(self.log_entries)} entries "
                  f"-> {state.signals_path}{C.RESET}")

    # -- demo mode --------------------------------------------------------

    def run_demo(self, orats) -> None:
        """Render dashboard using most recent historical data."""
        cfg = self.config
        print(f"{C.BOLD}{C.CYAN}0DTE Signal Monitor — Demo Mode{C.RESET}")
        print(f"  Loading most recent trading day data...\n")

        prev_day = {}
        demo_day = {}
        demo_date = None
        for days_back in range(1, 8):
            d = (date.today() - timedelta(days=days_back)).strftime("%Y-%m-%d")
            for ticker in cfg.tickers:
                if ticker in demo_day:
                    continue
                resp = orats.hist_summaries(ticker, d)
                if resp and resp.get("data"):
                    demo_day[ticker] = resp["data"][0]
                    demo_date = d
            if len(demo_day) == len(cfg.tickers):
                break

        if not demo_day:
            print(f"  {C.RED}No recent data found{C.RESET}")
            return

        demo_dt = datetime.strptime(demo_date, "%Y-%m-%d").date()
        for days_back in range(1, 8):
            d = (demo_dt - timedelta(days=days_back)).strftime("%Y-%m-%d")
            for ticker in cfg.tickers:
                if ticker in prev_day:
                    continue
                resp = orats.hist_summaries(ticker, d)
                if resp and resp.get("data"):
                    prev_day[ticker] = resp["data"][0]
                    self.prev_day[ticker] = resp["data"][0]
            if len(prev_day) == len(cfg.tickers):
                break

        for ticker, summary in demo_day.items():
            self.baseline[ticker] = dict(summary)

        hyg, tlt, hyg_p, tlt_p = yf_credit()
        credit_sig = self.compute_credit_signal(hyg, tlt, hyg_p, tlt_p)

        print(f"  {C.GREEN}Loaded data for {demo_date}{C.RESET}\n")

        for ticker, summary in demo_day.items():
            spot = self._safe(summary, "stockPrice")
            signals = self.compute_signals(ticker, summary)
            signals.update(credit_sig)
            composite, t1 = self.determine_direction(signals, intraday=False)
            self.print_dashboard(ticker, spot, None, signals, composite, t1)

    # -- backtest mode ----------------------------------------------------

    def run_backtest(self, orats, state, months: int = 6, db=None) -> None:
        """Backtest signals against historical data."""
        cfg = self.config
        end = date.today()
        start = end - timedelta(days=months * 30)

        print(f"{C.BOLD}{C.CYAN}0DTE Signal Backtest{C.RESET}")
        print(f"  {start} -> {end} | Tickers: {', '.join(cfg.tickers)}")
        print(f"  Composite: ANY {cfg.composite_min} of "
              f"{len(cfg.core_signals)} core signals\n")

        cache = state.load_cache()

        def _save_cache():
            state.save_cache(cache)

        # Fetch HYG/TLT credit data
        credit_map: Dict = {}
        ck = f"credit_{start}_{end}"
        if ck in cache:
            credit_map = cache[ck]
            print(f"  Credit data: {len(credit_map)} days (cached)")
        else:
            print(f"  Fetching HYG/TLT for credit spread...")
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
                _save_cache()
                print(f"  Credit data: {len(credit_map)} days")
            except Exception as exc:
                print(f"  {C.YELLOW}Credit data unavailable: {exc}{C.RESET}")

        for ticker in cfg.tickers:
            print(f"{'=' * 60}")
            print(f"  {C.BOLD}Backtesting {ticker}{C.RESET}")
            print(f"{'=' * 60}")

            # 1. Historical dailies
            dk = f"daily_{ticker}_{start}_{end}"
            if dk in cache:
                daily_data = cache[dk]
                print(f"  Dailies: {len(daily_data)} days (cached)")
            else:
                print(f"  Fetching dailies...")
                resp = orats.hist_dailies(ticker, f"{start},{end}")
                if not resp or not resp.get("data"):
                    print(f"  {C.RED}Failed to fetch dailies — skipping{C.RESET}")
                    continue
                daily_data = resp["data"]
                cache[dk] = daily_data
                _save_cache()
                print(f"  Dailies: {len(daily_data)} days")

            prices: Dict[str, Dict] = {}
            for d in daily_data:
                dt = str(d.get("tradeDate", ""))[:10]
                prices[dt] = d
            trade_dates = sorted(prices.keys())
            date_idx = {d: i for i, d in enumerate(trade_dates)}

            # 2. Historical summaries
            sk = f"summ_{ticker}_{start}_{end}"
            if sk in cache:
                summ_map = cache[sk]
                print(f"  Summaries: {len(summ_map)} days (cached)")
            else:
                print(f"  Fetching summaries...")
                resp = orats.get("hist/summaries", {
                    "ticker": ticker,
                    "tradeDate": f"{start},{end}",
                })
                if resp and resp.get("data") and len(resp["data"]) > 5:
                    summ_map = {}
                    for row in resp["data"]:
                        dt = str(row.get("tradeDate", ""))[:10]
                        summ_map[dt] = row
                    print(f"  Summaries: {len(summ_map)} days (range query)")
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
                _save_cache()

            # 3. Compute signals per day
            sorted_dates = sorted(d for d in summ_map if d in date_idx)
            results: List[Dict] = []

            for i, dt in enumerate(sorted_dates):
                if i == 0:
                    continue
                prev_dt = sorted_dates[i - 1]
                self.prev_day[ticker] = summ_map[prev_dt]
                self.baseline[ticker] = summ_map[prev_dt]

                signals = self.compute_signals(ticker, summ_map[dt])

                cd = credit_map.get(dt, {})
                cd_prev = credit_map.get(prev_dt, {})
                if cd.get("HYG") and cd.get("TLT") and cd_prev.get("HYG") and cd_prev.get("TLT"):
                    signals.update(self.compute_credit_signal(
                        cd["HYG"], cd["TLT"], cd_prev["HYG"], cd_prev["TLT"],
                    ))

                composite, t1 = self.determine_direction(signals, intraday=False)

                idx = date_idx.get(dt)
                if idx is None or idx + 1 >= len(trade_dates):
                    continue
                next_dt = trade_dates[idx + 1]
                cls_today = float(prices[dt].get("clsPx", 0) or 0)
                cls_next = float(prices[next_dt].get("clsPx", 0) or 0)
                if not cls_today or not cls_next:
                    continue

                nxt_ret = (cls_next - cls_today) / cls_today
                results.append({
                    "date": dt, "signals": signals,
                    "composite": composite, "tier1": t1,
                    "next_return": nxt_ret,
                    "close": cls_today, "next_close": cls_next,
                })

            # Log signal days to DB
            if db and db.enabled:
                for r in results:
                    if r["composite"]:
                        core_firing = [k for k in cfg.core_signals
                                       if r["signals"].get(k, {}).get("level") == "ACTION"]
                        db.run({
                            "action": "log_0dte_signal",
                            "ticker": ticker,
                            "trade_date": r["date"],
                            "spot_price": r["close"],
                            "composite": r["composite"],
                            "core_count": len(core_firing),
                            "signals": r["signals"],
                        })
                print(f"  [DB] Logged {sum(1 for r in results if r['composite'])} signal days")

            self._print_backtest_results(ticker, results, end)

        print(f"\n{C.BOLD}Cache saved to {state.cache_path}{C.RESET}")

    def _print_backtest_results(
        self, ticker: str, results: List[Dict], end_date: date,
    ) -> None:
        if not results:
            print(f"  {C.RED}No results to analyze{C.RESET}")
            return

        print(f"\n  {C.BOLD}Signal Analysis — {ticker} ({len(results)} days){C.RESET}")
        print(f"  {'-' * 56}")

        for sk in self.SIGNAL_ORDER:
            fired = [
                r for r in results
                if r["signals"].get(sk, {}).get("level") in ("ACTION", "WARNING", "INFO")
            ]
            tier = results[0]["signals"].get(sk, {}).get("tier", "?")
            if not fired:
                print(f"  T{tier} {sk:<22} never fired")
                continue
            rets = [r["next_return"] for r in fired]
            avg = sum(rets) / len(rets)
            up = sum(1 for r in rets if r > 0)
            print(f"  T{tier} {sk:<22} {len(fired):>3}d | "
                  f"avg {avg * 100:+.2f}% | "
                  f"up {up}/{len(fired)} ({up / len(fired):.0%})")

        print(f"\n  {C.BOLD}Composite Signals:{C.RESET}")
        for label, direction, exp_sign in [
            ("STRONG (3+)", "FEAR_BOUNCE_STRONG", 1),
            ("MODERATE (2)", "FEAR_BOUNCE_LONG", 1),
        ]:
            days = [r for r in results if r["composite"] == direction]
            if not days:
                print(f"  {label:<15} never fired")
                continue
            rets = [r["next_return"] for r in days]
            avg = sum(rets) / len(rets)
            hits = sum(1 for r in rets if r > 0) if exp_sign == 1 else sum(1 for r in rets if r < 0)
            hr = hits / len(rets)
            hit_rets = [r for r in rets if (r > 0 if exp_sign == 1 else r < 0)]
            avg_hit = (sum(abs(r) for r in hit_rets) / len(hit_rets) * 100
                       if hit_rets else 0)

            clr = C.GREEN if hr > 0.55 else C.RED if hr < 0.45 else C.YELLOW
            print(f"  {label:<15} {len(days):>3}d | "
                  f"hit rate {clr}{hr:.0%}{C.RESET} | "
                  f"avg ret {avg * 100:+.2f}% | "
                  f"avg hit {avg_hit:.2f}%")

        sig_days = [r for r in results if r["composite"]]
        quiet = [r for r in results if not r["composite"]]
        avg_sig = (sum(abs(r["next_return"]) for r in sig_days) / len(sig_days) * 100
                   if sig_days else 0)
        avg_q = (sum(abs(r["next_return"]) for r in quiet) / len(quiet) * 100
                 if quiet else 0)

        print(f"\n  {C.BOLD}Summary:{C.RESET}")
        print(f"  Signal days: {len(sig_days):>3} | avg abs move: {avg_sig:.2f}%")
        print(f"  Quiet days:  {len(quiet):>3} | avg abs move: {avg_q:.2f}%")
        if avg_q > 0:
            print(f"  Move ratio:  {avg_sig / avg_q:.1f}x on signal days")

        cutoff = (end_date - timedelta(days=60)).strftime("%Y-%m-%d")
        recent = [r for r in sig_days if r["date"] >= cutoff]
        if recent:
            print(f"\n  {C.BOLD}Recent signals (last 60d):{C.RESET}")
            for r in recent[-10:]:
                d = r["composite"] or ""
                if "LONG" in d or "BULL" in d or "BOUNCE" in d:
                    arrow, clr = "^", C.GREEN
                    hit = r["next_return"] > 0
                elif "SHORT" in d or "BEAR" in d:
                    arrow, clr = "v", C.RED
                    hit = r["next_return"] < 0
                else:
                    arrow, clr = "~", C.MAGENTA
                    hit = abs(r["next_return"]) > 0.005
                mark = "Y" if hit else "N"
                print(f"    {r['date']} {clr}{arrow}{C.RESET} "
                      f"next day {r['next_return'] * 100:+.2f}% ({mark})")

        print()
