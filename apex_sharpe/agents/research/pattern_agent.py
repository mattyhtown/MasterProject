"""
PatternAgent — finds recurring patterns in historical price data.

Detects:
  - Seasonal patterns (month-of-year, day-of-week, OPEX week effects)
  - Mean reversion setups (RSI extremes, BB touches, VIX spikes)
  - Momentum patterns (breakouts, trend continuation)
  - Volatility clustering
  - Post-event patterns (after VIX spikes, after large drawdowns)
"""

import math
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..base import BaseAgent
from ...types import AgentResult, C


class PatternAgent(BaseAgent):
    """Finds recurring patterns in historical price data."""

    def __init__(self, config=None):
        super().__init__("PatternFinder", config)

    def run(self, context: Dict[str, Any]) -> AgentResult:
        action = context.get("action", "seasonal")
        loader = context.get("loader")

        if not loader:
            return self._result(success=False, errors=["No loader provided"])

        ticker = context.get("ticker", "SPY")

        if action == "seasonal":
            return self._seasonal(loader, ticker,
                                  context.get("start", ""),
                                  context.get("end", ""))
        elif action == "mean_reversion":
            return self._mean_reversion(loader, ticker,
                                        context.get("start", ""),
                                        context.get("end", ""))
        elif action == "momentum":
            return self._momentum(loader, ticker,
                                  context.get("start", ""),
                                  context.get("end", ""))
        elif action == "post_event":
            return self._post_event(loader, ticker,
                                    context.get("start", ""),
                                    context.get("end", ""),
                                    context.get("vix_data", {}))
        elif action == "vol_clustering":
            return self._vol_clustering(loader, ticker,
                                        context.get("start", ""),
                                        context.get("end", ""))
        else:
            return self._result(success=False, errors=[f"Unknown action: {action}"])

    def _seasonal(self, loader, ticker: str,
                  start: str, end: str) -> AgentResult:
        """Seasonal patterns: monthly, day-of-week, OPEX effects."""
        daily = loader.load_daily(ticker, start, end)
        if not daily:
            return self._result(success=False, errors=[f"No data for {ticker}"])

        # Monthly returns
        monthly = defaultdict(list)
        for r in daily:
            dt = datetime.strptime(r["date"], "%Y-%m-%d")
            monthly[dt.month].append(r["daily_return"])

        month_stats = {}
        month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                       "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        for m in range(1, 13):
            rets = monthly[m]
            if rets:
                month_stats[month_names[m-1]] = {
                    "days": len(rets),
                    "avg_return_pct": round(self._mean(rets) * 100, 4),
                    "win_rate": round(
                        sum(1 for r in rets if r > 0) / len(rets) * 100, 1
                    ),
                    "avg_abs_move": round(
                        self._mean([abs(r) for r in rets]) * 100, 4
                    ),
                }

        # Day of week returns
        dow_returns = defaultdict(list)
        dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri"]
        for r in daily:
            dt = datetime.strptime(r["date"], "%Y-%m-%d")
            dow = dt.weekday()
            if dow < 5:
                dow_returns[dow_names[dow]].append(r["daily_return"])

        dow_stats = {}
        for day in dow_names:
            rets = dow_returns[day]
            if rets:
                dow_stats[day] = {
                    "days": len(rets),
                    "avg_return_pct": round(self._mean(rets) * 100, 4),
                    "win_rate": round(
                        sum(1 for r in rets if r > 0) / len(rets) * 100, 1
                    ),
                }

        # OPEX week (3rd Friday of each month)
        opex_returns = []
        non_opex_returns = []
        for r in daily:
            dt = datetime.strptime(r["date"], "%Y-%m-%d")
            # 3rd Friday: find which week the 3rd Friday falls in
            # Day 15-21 contains the 3rd Friday if day.weekday() == 4
            day_of_month = dt.day
            if 15 <= day_of_month <= 21:
                opex_returns.append(r["daily_return"])
            else:
                non_opex_returns.append(r["daily_return"])

        opex_stats = {
            "opex_week": {
                "days": len(opex_returns),
                "avg_return_pct": round(
                    self._mean(opex_returns) * 100, 4
                ) if opex_returns else 0,
                "win_rate": round(
                    sum(1 for r in opex_returns if r > 0) / len(opex_returns) * 100, 1
                ) if opex_returns else 0,
            },
            "non_opex": {
                "days": len(non_opex_returns),
                "avg_return_pct": round(
                    self._mean(non_opex_returns) * 100, 4
                ) if non_opex_returns else 0,
            },
        }

        # Turn-of-month (last 2 + first 2 trading days)
        tom_rets = []
        mid_rets = []
        for r in daily:
            dt = datetime.strptime(r["date"], "%Y-%m-%d")
            if dt.day <= 2 or dt.day >= 28:
                tom_rets.append(r["daily_return"])
            else:
                mid_rets.append(r["daily_return"])

        tom_stats = {
            "turn_of_month": {
                "days": len(tom_rets),
                "avg_return_pct": round(
                    self._mean(tom_rets) * 100, 4
                ) if tom_rets else 0,
                "win_rate": round(
                    sum(1 for r in tom_rets if r > 0) / len(tom_rets) * 100, 1
                ) if tom_rets else 0,
            },
            "mid_month": {
                "days": len(mid_rets),
                "avg_return_pct": round(
                    self._mean(mid_rets) * 100, 4
                ) if mid_rets else 0,
            },
        }

        return self._result(
            success=True,
            data={
                "ticker": ticker,
                "total_days": len(daily),
                "monthly": month_stats,
                "day_of_week": dow_stats,
                "opex": opex_stats,
                "turn_of_month": tom_stats,
            },
        )

    def _mean_reversion(self, loader, ticker: str,
                        start: str, end: str) -> AgentResult:
        """Mean reversion patterns: RSI/BB extremes and forward returns."""
        daily = loader.load_daily(ticker, start, end)
        if not daily:
            return self._result(success=False, errors=[f"No data for {ticker}"])

        setups = {
            "rsi_below_30": {"entries": [], "label": "RSI < 30"},
            "rsi_below_20": {"entries": [], "label": "RSI < 20"},
            "rsi_above_70": {"entries": [], "label": "RSI > 70"},
            "rsi_above_80": {"entries": [], "label": "RSI > 80"},
            "bb_below_lower": {"entries": [], "label": "Below lower BB"},
            "bb_above_upper": {"entries": [], "label": "Above upper BB"},
            "down_3_days": {"entries": [], "label": "3+ consecutive down days"},
            "up_3_days": {"entries": [], "label": "3+ consecutive up days"},
        }

        for i, r in enumerate(daily):
            rsi = r.get("rsi", 50)
            bb_pos = r.get("bb_position", 0.5)

            # Forward returns
            fwd = {}
            for horizon, label in [(1, "1d"), (5, "5d"), (20, "20d")]:
                if i + horizon < len(daily):
                    fwd[label] = (daily[i + horizon]["close"] - r["close"]) / r["close"]

            if not fwd:
                continue

            entry = {"date": r["date"], "close": r["close"], **fwd}

            if rsi > 0 and rsi < 30:
                setups["rsi_below_30"]["entries"].append(entry)
            if rsi > 0 and rsi < 20:
                setups["rsi_below_20"]["entries"].append(entry)
            if rsi > 70:
                setups["rsi_above_70"]["entries"].append(entry)
            if rsi > 80:
                setups["rsi_above_80"]["entries"].append(entry)
            if bb_pos < 0:
                setups["bb_below_lower"]["entries"].append(entry)
            if bb_pos > 1:
                setups["bb_above_upper"]["entries"].append(entry)

            # Consecutive days
            if i >= 2:
                if all(daily[i-j]["daily_return"] < 0 for j in range(3)):
                    setups["down_3_days"]["entries"].append(entry)
                if all(daily[i-j]["daily_return"] > 0 for j in range(3)):
                    setups["up_3_days"]["entries"].append(entry)

        # Compute stats per setup
        results = {}
        for key, setup in setups.items():
            entries = setup["entries"]
            if not entries:
                continue

            stats = {"label": setup["label"], "count": len(entries)}
            for horizon in ["1d", "5d", "20d"]:
                rets = [e[horizon] for e in entries if horizon in e]
                if rets:
                    stats[f"{horizon}_avg_pct"] = round(self._mean(rets) * 100, 3)
                    stats[f"{horizon}_win_rate"] = round(
                        sum(1 for r in rets if r > 0) / len(rets) * 100, 1
                    )
                    stats[f"{horizon}_median_pct"] = round(
                        sorted(rets)[len(rets) // 2] * 100, 3
                    )

            results[key] = stats

        return self._result(
            success=True,
            data={
                "ticker": ticker,
                "total_days": len(daily),
                "setups": results,
            },
        )

    def _momentum(self, loader, ticker: str,
                  start: str, end: str) -> AgentResult:
        """Momentum patterns: SMA crossovers, trend continuation."""
        daily = loader.load_daily(ticker, start, end)
        if not daily:
            return self._result(success=False, errors=[f"No data for {ticker}"])

        setups = {
            "golden_cross": {"entries": [], "label": "SMA20 crosses above SMA200"},
            "death_cross": {"entries": [], "label": "SMA20 crosses below SMA200"},
            "above_sma200": {"entries": [], "label": "Price above SMA200"},
            "below_sma200": {"entries": [], "label": "Price below SMA200"},
            "macd_bullish": {"entries": [], "label": "MACD crosses above signal"},
            "macd_bearish": {"entries": [], "label": "MACD crosses below signal"},
        }

        prev_sma_diff = None
        prev_macd_diff = None

        for i, r in enumerate(daily):
            sma20 = r.get("sma_20", 0)
            sma200 = r.get("sma_200", 0)
            macd = r.get("macd", 0)
            macd_sig = r.get("macd_signal", 0)

            # Forward returns
            fwd = {}
            for horizon, label in [(1, "1d"), (5, "5d"), (20, "20d")]:
                if i + horizon < len(daily):
                    fwd[label] = (daily[i + horizon]["close"] - r["close"]) / r["close"]

            if not fwd:
                continue

            entry = {"date": r["date"], "close": r["close"], **fwd}

            # SMA crossovers
            if sma20 > 0 and sma200 > 0:
                sma_diff = sma20 - sma200
                if prev_sma_diff is not None:
                    if prev_sma_diff <= 0 and sma_diff > 0:
                        setups["golden_cross"]["entries"].append(entry)
                    elif prev_sma_diff >= 0 and sma_diff < 0:
                        setups["death_cross"]["entries"].append(entry)
                prev_sma_diff = sma_diff

                if r["close"] > sma200:
                    setups["above_sma200"]["entries"].append(entry)
                else:
                    setups["below_sma200"]["entries"].append(entry)

            # MACD crossovers
            if macd != 0 and macd_sig != 0:
                macd_diff = macd - macd_sig
                if prev_macd_diff is not None:
                    if prev_macd_diff <= 0 and macd_diff > 0:
                        setups["macd_bullish"]["entries"].append(entry)
                    elif prev_macd_diff >= 0 and macd_diff < 0:
                        setups["macd_bearish"]["entries"].append(entry)
                prev_macd_diff = macd_diff

        # Compute stats
        results = {}
        for key, setup in setups.items():
            entries = setup["entries"]
            if not entries:
                continue

            stats = {"label": setup["label"], "count": len(entries)}
            for horizon in ["1d", "5d", "20d"]:
                rets = [e[horizon] for e in entries if horizon in e]
                if rets:
                    stats[f"{horizon}_avg_pct"] = round(self._mean(rets) * 100, 3)
                    stats[f"{horizon}_win_rate"] = round(
                        sum(1 for r in rets if r > 0) / len(rets) * 100, 1
                    )

            results[key] = stats

        return self._result(
            success=True,
            data={
                "ticker": ticker,
                "total_days": len(daily),
                "setups": results,
            },
        )

    def _post_event(self, loader, ticker: str,
                    start: str, end: str,
                    vix_data: Dict[str, float]) -> AgentResult:
        """Forward returns after significant events."""
        daily = loader.load_daily(ticker, start, end)
        if not daily:
            return self._result(success=False, errors=[f"No data for {ticker}"])

        if not vix_data:
            vix_data = loader.load_vix(start, end)

        events = {
            "vix_above_30": {"entries": [], "label": "VIX spikes above 30"},
            "vix_above_25": {"entries": [], "label": "VIX above 25"},
            "drop_2pct": {"entries": [], "label": "Daily drop > 2%"},
            "drop_3pct": {"entries": [], "label": "Daily drop > 3%"},
            "gap_down_1pct": {"entries": [], "label": "Gap down > 1%"},
            "rally_2pct": {"entries": [], "label": "Daily rally > 2%"},
            "vol_spike": {"entries": [], "label": "20d vol > 2x 50d vol"},
        }

        prev_vix = None

        for i, r in enumerate(daily):
            vix = vix_data.get(r["date"], 0)

            # Forward returns
            fwd = {}
            for horizon, label in [(1, "1d"), (5, "5d"), (10, "10d"), (20, "20d")]:
                if i + horizon < len(daily):
                    fwd[label] = (daily[i + horizon]["close"] - r["close"]) / r["close"]

            if not fwd:
                continue

            entry = {"date": r["date"], "close": r["close"], "vix": vix, **fwd}

            # VIX events
            if vix > 30 and (prev_vix is None or prev_vix <= 30):
                events["vix_above_30"]["entries"].append(entry)
            if vix > 25:
                events["vix_above_25"]["entries"].append(entry)

            # Price events
            ret = r["daily_return"]
            if ret < -0.02:
                events["drop_2pct"]["entries"].append(entry)
            if ret < -0.03:
                events["drop_3pct"]["entries"].append(entry)
            if ret > 0.02:
                events["rally_2pct"]["entries"].append(entry)

            # Gap down
            if i > 0 and daily[i-1]["close"] > 0:
                gap = (r["open"] - daily[i-1]["close"]) / daily[i-1]["close"]
                if gap < -0.01:
                    events["gap_down_1pct"]["entries"].append(entry)

            # Vol spike
            v20 = r.get("volatility_20d", 0)
            v50 = r.get("volatility_50d", 0)
            if v20 > 0 and v50 > 0 and v20 > v50 * 2:
                events["vol_spike"]["entries"].append(entry)

            prev_vix = vix

        # Compute stats
        results = {}
        for key, event in events.items():
            entries = event["entries"]
            if not entries:
                continue

            stats = {"label": event["label"], "count": len(entries)}
            for horizon in ["1d", "5d", "10d", "20d"]:
                rets = [e[horizon] for e in entries if horizon in e]
                if rets:
                    stats[f"{horizon}_avg_pct"] = round(self._mean(rets) * 100, 3)
                    stats[f"{horizon}_win_rate"] = round(
                        sum(1 for r in rets if r > 0) / len(rets) * 100, 1
                    )

            results[key] = stats

        return self._result(
            success=True,
            data={
                "ticker": ticker,
                "total_days": len(daily),
                "events": results,
            },
        )

    def _vol_clustering(self, loader, ticker: str,
                        start: str, end: str) -> AgentResult:
        """Analyze volatility clustering — do volatile days cluster?"""
        daily = loader.load_daily(ticker, start, end)
        if not daily:
            return self._result(success=False, errors=[f"No data for {ticker}"])

        returns = [r["daily_return"] for r in daily]
        abs_returns = [abs(r) for r in returns if r != 0]

        if len(abs_returns) < 20:
            return self._result(success=False, errors=["Insufficient data"])

        # Autocorrelation of absolute returns (measures clustering)
        acf_1 = self._autocorrelation(abs_returns, 1)
        acf_5 = self._autocorrelation(abs_returns, 5)

        # Classify days into vol regimes
        vol_threshold = sorted(abs_returns)[int(len(abs_returns) * 0.75)]
        high_vol_days = []
        low_vol_days = []

        for i, r in enumerate(daily):
            if abs(r["daily_return"]) >= vol_threshold:
                high_vol_days.append(i)
            else:
                low_vol_days.append(i)

        # After high vol day, what happens?
        post_high_vol = []
        for idx in high_vol_days:
            if idx + 1 < len(daily):
                post_high_vol.append(abs(daily[idx + 1]["daily_return"]))

        post_low_vol = []
        for idx in low_vol_days:
            if idx + 1 < len(daily):
                post_low_vol.append(abs(daily[idx + 1]["daily_return"]))

        return self._result(
            success=True,
            data={
                "ticker": ticker,
                "total_days": len(daily),
                "abs_return_acf_lag1": round(acf_1, 4),
                "abs_return_acf_lag5": round(acf_5, 4),
                "vol_threshold_pct": round(vol_threshold * 100, 4),
                "high_vol_days": len(high_vol_days),
                "avg_move_after_high_vol_pct": round(
                    self._mean(post_high_vol) * 100, 4
                ) if post_high_vol else 0,
                "avg_move_after_low_vol_pct": round(
                    self._mean(post_low_vol) * 100, 4
                ) if post_low_vol else 0,
                "clustering_ratio": round(
                    self._mean(post_high_vol) / self._mean(post_low_vol), 3
                ) if post_low_vol and self._mean(post_low_vol) > 0 else 0,
                "interpretation": (
                    "Strong clustering" if acf_1 > 0.3
                    else "Moderate clustering" if acf_1 > 0.15
                    else "Weak clustering"
                ),
            },
        )

    @staticmethod
    def _autocorrelation(series: List[float], lag: int) -> float:
        """Compute autocorrelation at given lag."""
        n = len(series)
        if n <= lag:
            return 0.0
        m = sum(series) / n
        var = sum((x - m) ** 2 for x in series) / n
        if var == 0:
            return 0.0
        cov = sum(
            (series[i] - m) * (series[i + lag] - m)
            for i in range(n - lag)
        ) / (n - lag)
        return cov / var

    @staticmethod
    def _mean(values: List[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    def print_seasonal(self, result: AgentResult) -> None:
        d = result.data
        print(f"\n{C.BOLD}{'='*74}")
        print(f"  SEASONAL PATTERNS: {d.get('ticker', '?')}")
        print(f"{'='*74}{C.RESET}")

        monthly = d.get("monthly", {})
        if monthly:
            print(f"\n  {'Month':<6} {'Avg%':>8} {'Win%':>7} {'AvgAbs%':>8}")
            print(f"  {'-'*29}")
            for month, s in monthly.items():
                clr = C.GREEN if s["avg_return_pct"] > 0 else C.RED
                print(f"  {month:<6}"
                      f" {clr}{s['avg_return_pct']:>+7.4f}%{C.RESET}"
                      f" {s['win_rate']:>6.1f}%"
                      f" {s['avg_abs_move']:>7.4f}%")

        dow = d.get("day_of_week", {})
        if dow:
            print(f"\n  {'Day':<6} {'Avg%':>8} {'Win%':>7}")
            print(f"  {'-'*21}")
            for day, s in dow.items():
                clr = C.GREEN if s["avg_return_pct"] > 0 else C.RED
                print(f"  {day:<6}"
                      f" {clr}{s['avg_return_pct']:>+7.4f}%{C.RESET}"
                      f" {s['win_rate']:>6.1f}%")
        print()

    def print_setups(self, result: AgentResult) -> None:
        """Print mean reversion or momentum setups."""
        d = result.data
        setups = d.get("setups", d.get("events", {}))

        print(f"\n{C.BOLD}{'='*74}")
        print(f"  PATTERN ANALYSIS: {d.get('ticker', '?')}")
        print(f"{'='*74}{C.RESET}")
        print(f"  Total days: {d.get('total_days', 0):,}")

        print(f"\n  {'Setup':<30} {'Count':>6} {'1d%':>7} {'5d%':>7} {'20d%':>7} {'5dW%':>6}")
        print(f"  {'-'*63}")

        for key, s in setups.items():
            label = s.get("label", key)[:30]
            avg_1d = s.get("1d_avg_pct", 0)
            avg_5d = s.get("5d_avg_pct", 0)
            avg_20d = s.get("20d_avg_pct", 0)
            win_5d = s.get("5d_win_rate", 0)
            clr = C.GREEN if avg_5d > 0 else C.RED
            print(f"  {label:<30} {s['count']:>6}"
                  f" {avg_1d:>+6.3f}%"
                  f" {clr}{avg_5d:>+6.3f}%{C.RESET}"
                  f" {avg_20d:>+6.3f}%"
                  f" {win_5d:>5.1f}%")
        print()
