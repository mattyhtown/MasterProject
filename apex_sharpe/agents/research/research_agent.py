"""
ResearchAgent — cross-asset analysis and correlation research.

Runs queries across the historical dataset:
  - Correlation matrix between any set of tickers
  - Return analysis by timeframe (daily, weekly, monthly)
  - Drawdown analysis
  - Relative performance (ratio analysis)
  - Screening: find tickers matching criteria
"""

import math
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from ..base import BaseAgent
from ...types import AgentResult, C


class ResearchAgent(BaseAgent):
    """Cross-asset research and analysis on historical data."""

    def __init__(self, config=None):
        super().__init__("Research", config)

    def run(self, context: Dict[str, Any]) -> AgentResult:
        action = context.get("action", "correlation")
        loader = context.get("loader")

        if not loader:
            return self._result(success=False, errors=["No loader provided"])

        if action == "correlation":
            return self._correlation(
                loader,
                context.get("tickers", ["SPY", "QQQ", "IWM"]),
                context.get("start", ""),
                context.get("end", ""),
            )
        elif action == "returns":
            return self._return_analysis(
                loader,
                context.get("ticker", "SPY"),
                context.get("start", ""),
                context.get("end", ""),
            )
        elif action == "drawdown":
            return self._drawdown_analysis(
                loader,
                context.get("ticker", "SPY"),
                context.get("start", ""),
                context.get("end", ""),
            )
        elif action == "compare":
            return self._relative_performance(
                loader,
                context.get("tickers", []),
                context.get("start", ""),
                context.get("end", ""),
            )
        elif action == "screen":
            return self._screen(
                loader,
                context.get("min_return", None),
                context.get("max_volatility", None),
                context.get("min_sharpe", None),
                context.get("asset_class", ""),
                context.get("start", ""),
                context.get("end", ""),
            )
        else:
            return self._result(success=False, errors=[f"Unknown action: {action}"])

    def _correlation(self, loader, tickers: List[str],
                     start: str, end: str) -> AgentResult:
        """Compute correlation matrix between tickers."""
        # Load returns for each ticker
        returns_by_ticker = {}
        dates_by_ticker = {}

        for ticker in tickers:
            daily = loader.load_daily(ticker, start, end)
            if not daily:
                continue
            rets = {}
            for r in daily:
                if r["daily_return"] != 0 or r["close"] > 0:
                    rets[r["date"]] = r["daily_return"]
            returns_by_ticker[ticker] = rets
            dates_by_ticker[ticker] = set(rets.keys())

        found = list(returns_by_ticker.keys())
        if len(found) < 2:
            return self._result(success=False,
                                errors=["Need at least 2 tickers with data"])

        # Common dates
        common = set.intersection(*(dates_by_ticker[t] for t in found))
        common_sorted = sorted(common)

        # Build aligned return vectors
        aligned = {t: [returns_by_ticker[t][d] for d in common_sorted]
                   for t in found}

        # Correlation matrix
        matrix = {}
        for t1 in found:
            matrix[t1] = {}
            for t2 in found:
                matrix[t1][t2] = round(
                    self._pearson(aligned[t1], aligned[t2]), 4
                )

        return self._result(
            success=True,
            data={
                "tickers": found,
                "common_days": len(common_sorted),
                "period": {
                    "start": common_sorted[0] if common_sorted else "",
                    "end": common_sorted[-1] if common_sorted else "",
                },
                "matrix": matrix,
            },
        )

    def _return_analysis(self, loader, ticker: str,
                         start: str, end: str) -> AgentResult:
        """Detailed return analysis for a single ticker."""
        daily = loader.load_daily(ticker, start, end)
        if not daily:
            return self._result(success=False,
                                errors=[f"No data for {ticker}"])

        closes = [r["close"] for r in daily if r["close"] > 0]
        returns = [r["daily_return"] for r in daily if r["daily_return"] != 0]

        if not returns:
            return self._result(success=False, errors=["No return data"])

        # Weekly returns
        weekly = self._aggregate_returns(daily, "weekly")
        monthly = self._aggregate_returns(daily, "monthly")

        # Percentiles
        sorted_rets = sorted(returns)
        n = len(sorted_rets)

        # Best/worst days
        best_days = sorted(
            [(r["date"], r["daily_return"]) for r in daily if r["daily_return"] != 0],
            key=lambda x: x[1], reverse=True
        )[:5]
        worst_days = sorted(
            [(r["date"], r["daily_return"]) for r in daily if r["daily_return"] != 0],
            key=lambda x: x[1]
        )[:5]

        return self._result(
            success=True,
            data={
                "ticker": ticker,
                "period": {
                    "start": daily[0]["date"],
                    "end": daily[-1]["date"],
                },
                "total_days": len(daily),
                "total_return_pct": round(
                    (closes[-1] / closes[0] - 1) * 100, 2
                ) if len(closes) > 1 else 0,
                "cagr_pct": round(self._cagr(closes, len(daily)), 2),
                "daily": {
                    "mean": round(self._mean(returns) * 100, 4),
                    "std": round(self._std(returns) * 100, 4),
                    "sharpe": round(self._annualized_sharpe(returns), 3),
                    "skew": round(self._skewness(returns), 3),
                    "kurtosis": round(self._kurtosis(returns), 3),
                    "p5": round(sorted_rets[int(n * 0.05)] * 100, 3),
                    "p25": round(sorted_rets[int(n * 0.25)] * 100, 3),
                    "p50": round(sorted_rets[int(n * 0.50)] * 100, 3),
                    "p75": round(sorted_rets[int(n * 0.75)] * 100, 3),
                    "p95": round(sorted_rets[int(n * 0.95)] * 100, 3),
                    "positive_pct": round(
                        sum(1 for r in returns if r > 0) / len(returns) * 100, 1
                    ),
                },
                "weekly": {
                    "mean": round(self._mean(weekly) * 100, 3),
                    "std": round(self._std(weekly) * 100, 3),
                    "count": len(weekly),
                },
                "monthly": {
                    "mean": round(self._mean(monthly) * 100, 3),
                    "std": round(self._std(monthly) * 100, 3),
                    "count": len(monthly),
                },
                "best_days": [(d, round(r * 100, 3)) for d, r in best_days],
                "worst_days": [(d, round(r * 100, 3)) for d, r in worst_days],
            },
        )

    def _drawdown_analysis(self, loader, ticker: str,
                           start: str, end: str) -> AgentResult:
        """Drawdown analysis — peak-to-trough metrics."""
        daily = loader.load_daily(ticker, start, end)
        if not daily:
            return self._result(success=False,
                                errors=[f"No data for {ticker}"])

        closes = [r["close"] for r in daily if r["close"] > 0]
        dates = [r["date"] for r in daily if r["close"] > 0]

        if len(closes) < 2:
            return self._result(success=False, errors=["Insufficient data"])

        # Compute drawdown series
        peak = closes[0]
        drawdowns = []
        current_dd_start = 0
        max_dd = 0
        max_dd_peak_idx = 0
        max_dd_trough_idx = 0

        for i, c in enumerate(closes):
            if c > peak:
                peak = c
                current_dd_start = i
            dd = (c - peak) / peak
            drawdowns.append(dd)
            if dd < max_dd:
                max_dd = dd
                max_dd_peak_idx = current_dd_start
                max_dd_trough_idx = i

        # Find recovery point for max drawdown
        recovery_idx = None
        peak_at_max = closes[max_dd_peak_idx]
        for i in range(max_dd_trough_idx, len(closes)):
            if closes[i] >= peak_at_max:
                recovery_idx = i
                break

        # Top 5 drawdowns
        dd_events = self._find_drawdown_events(closes, dates)

        return self._result(
            success=True,
            data={
                "ticker": ticker,
                "period": {
                    "start": dates[0],
                    "end": dates[-1],
                },
                "max_drawdown_pct": round(max_dd * 100, 2),
                "max_dd_peak": {
                    "date": dates[max_dd_peak_idx],
                    "price": round(closes[max_dd_peak_idx], 2),
                },
                "max_dd_trough": {
                    "date": dates[max_dd_trough_idx],
                    "price": round(closes[max_dd_trough_idx], 2),
                },
                "max_dd_recovery": {
                    "date": dates[recovery_idx] if recovery_idx else "N/A",
                    "days": (recovery_idx - max_dd_trough_idx) if recovery_idx else None,
                },
                "current_drawdown_pct": round(drawdowns[-1] * 100, 2),
                "top_drawdowns": dd_events[:5],
                "avg_drawdown_pct": round(self._mean(drawdowns) * 100, 2),
                "time_in_drawdown_pct": round(
                    sum(1 for d in drawdowns if d < -0.05) / len(drawdowns) * 100, 1
                ),
            },
        )

    def _relative_performance(self, loader, tickers: List[str],
                              start: str, end: str) -> AgentResult:
        """Compare performance across tickers."""
        if len(tickers) < 2:
            return self._result(success=False,
                                errors=["Need at least 2 tickers"])

        performances = []
        for ticker in tickers:
            daily = loader.load_daily(ticker, start, end)
            if not daily:
                continue

            closes = [r["close"] for r in daily if r["close"] > 0]
            returns = [r["daily_return"] for r in daily if r["daily_return"] != 0]

            if not closes or not returns:
                continue

            total_return = (closes[-1] / closes[0] - 1) * 100 if len(closes) > 1 else 0

            # Max drawdown
            peak = closes[0]
            max_dd = 0
            for c in closes:
                if c > peak:
                    peak = c
                dd = (c - peak) / peak
                if dd < max_dd:
                    max_dd = dd

            performances.append({
                "ticker": ticker,
                "start": daily[0]["date"],
                "end": daily[-1]["date"],
                "days": len(daily),
                "total_return_pct": round(total_return, 2),
                "cagr_pct": round(self._cagr(closes, len(daily)), 2),
                "daily_vol_pct": round(self._std(returns) * 100, 4),
                "annualized_vol_pct": round(
                    self._std(returns) * math.sqrt(252) * 100, 2
                ),
                "sharpe": round(self._annualized_sharpe(returns), 3),
                "max_drawdown_pct": round(max_dd * 100, 2),
                "positive_days_pct": round(
                    sum(1 for r in returns if r > 0) / len(returns) * 100, 1
                ),
            })

        # Sort by Sharpe ratio
        performances.sort(key=lambda x: x["sharpe"], reverse=True)

        return self._result(
            success=True,
            data={
                "performances": performances,
                "count": len(performances),
                "best_sharpe": performances[0]["ticker"] if performances else "",
                "best_return": max(performances, key=lambda x: x["total_return_pct"])["ticker"] if performances else "",
            },
        )

    def _screen(self, loader, min_return: Optional[float],
                max_volatility: Optional[float],
                min_sharpe: Optional[float],
                asset_class: str,
                start: str, end: str) -> AgentResult:
        """Screen tickers matching criteria."""
        available = loader.available_tickers()
        results = []

        for cls, tickers in available.items():
            if asset_class and cls != asset_class:
                continue
            for ticker in tickers:
                daily = loader.load_daily(ticker, start, end)
                if len(daily) < 60:  # Need at least 60 days
                    continue

                closes = [r["close"] for r in daily if r["close"] > 0]
                returns = [r["daily_return"] for r in daily if r["daily_return"] != 0]

                if len(closes) < 2 or len(returns) < 20:
                    continue

                total_return = (closes[-1] / closes[0] - 1) * 100
                vol = self._std(returns) * math.sqrt(252) * 100
                sharpe = self._annualized_sharpe(returns)

                # Apply filters
                if min_return is not None and total_return < min_return:
                    continue
                if max_volatility is not None and vol > max_volatility:
                    continue
                if min_sharpe is not None and sharpe < min_sharpe:
                    continue

                results.append({
                    "ticker": ticker,
                    "asset_class": cls,
                    "return_pct": round(total_return, 2),
                    "vol_pct": round(vol, 2),
                    "sharpe": round(sharpe, 3),
                    "days": len(daily),
                })

        results.sort(key=lambda x: x["sharpe"], reverse=True)

        return self._result(
            success=True,
            data={
                "results": results[:50],  # Top 50
                "total_matches": len(results),
                "filters": {
                    "min_return": min_return,
                    "max_volatility": max_volatility,
                    "min_sharpe": min_sharpe,
                    "asset_class": asset_class,
                },
            },
        )

    def _aggregate_returns(self, daily: List[Dict],
                           freq: str) -> List[float]:
        """Aggregate daily returns to weekly or monthly."""
        if not daily:
            return []

        groups = {}
        for r in daily:
            dt = datetime.strptime(r["date"], "%Y-%m-%d")
            if freq == "weekly":
                key = dt.strftime("%Y-W%W")
            else:  # monthly
                key = dt.strftime("%Y-%m")
            groups.setdefault(key, []).append(r["close"])

        returns = []
        prev_last = None
        for key in sorted(groups):
            closes = groups[key]
            if prev_last is not None and prev_last > 0:
                ret = (closes[-1] - prev_last) / prev_last
                returns.append(ret)
            prev_last = closes[-1]

        return returns

    def _find_drawdown_events(self, closes: List[float],
                              dates: List[str]) -> List[Dict]:
        """Find distinct drawdown events (>5%)."""
        events = []
        peak = closes[0]
        peak_idx = 0
        trough = closes[0]
        trough_idx = 0
        in_dd = False

        for i, c in enumerate(closes):
            if c > peak:
                if in_dd and (trough - peak) / peak < -0.05:
                    events.append({
                        "peak_date": dates[peak_idx],
                        "trough_date": dates[trough_idx],
                        "drawdown_pct": round((trough - peak) / peak * 100, 2),
                        "peak_price": round(peak, 2),
                        "trough_price": round(trough, 2),
                    })
                peak = c
                peak_idx = i
                trough = c
                trough_idx = i
                in_dd = False
            elif c < trough:
                trough = c
                trough_idx = i
                in_dd = True

        # Check final drawdown
        if in_dd and (trough - peak) / peak < -0.05:
            events.append({
                "peak_date": dates[peak_idx],
                "trough_date": dates[trough_idx],
                "drawdown_pct": round((trough - peak) / peak * 100, 2),
                "peak_price": round(peak, 2),
                "trough_price": round(trough, 2),
            })

        events.sort(key=lambda x: x["drawdown_pct"])
        return events

    @staticmethod
    def _pearson(x: List[float], y: List[float]) -> float:
        """Pearson correlation coefficient."""
        n = len(x)
        if n < 2:
            return 0.0
        mx = sum(x) / n
        my = sum(y) / n
        cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
        sx = math.sqrt(sum((xi - mx) ** 2 for xi in x))
        sy = math.sqrt(sum((yi - my) ** 2 for yi in y))
        if sx == 0 or sy == 0:
            return 0.0
        return cov / (sx * sy)

    @staticmethod
    def _mean(values: List[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    @staticmethod
    def _std(values: List[float]) -> float:
        if len(values) < 2:
            return 0.0
        m = sum(values) / len(values)
        return math.sqrt(sum((v - m) ** 2 for v in values) / (len(values) - 1))

    @staticmethod
    def _annualized_sharpe(daily_returns: List[float],
                           rf_daily: float = 0.0) -> float:
        """Annualized Sharpe ratio from daily returns."""
        if len(daily_returns) < 20:
            return 0.0
        excess = [r - rf_daily for r in daily_returns]
        m = sum(excess) / len(excess)
        var = sum((r - m) ** 2 for r in excess) / (len(excess) - 1)
        std = math.sqrt(var) if var > 0 else 0
        if std == 0:
            return 0.0
        return (m / std) * math.sqrt(252)

    @staticmethod
    def _cagr(closes: List[float], days: int) -> float:
        """Compound annual growth rate."""
        if len(closes) < 2 or closes[0] <= 0 or days < 1:
            return 0.0
        years = days / 252.0
        if years < 0.01:
            return 0.0
        return ((closes[-1] / closes[0]) ** (1 / years) - 1) * 100

    @staticmethod
    def _skewness(values: List[float]) -> float:
        n = len(values)
        if n < 3:
            return 0.0
        m = sum(values) / n
        s2 = sum((v - m) ** 2 for v in values) / (n - 1)
        if s2 == 0:
            return 0.0
        s = math.sqrt(s2)
        return (n / ((n - 1) * (n - 2))) * sum(((v - m) / s) ** 3 for v in values)

    @staticmethod
    def _kurtosis(values: List[float]) -> float:
        n = len(values)
        if n < 4:
            return 0.0
        m = sum(values) / n
        s2 = sum((v - m) ** 2 for v in values) / (n - 1)
        if s2 == 0:
            return 0.0
        s = math.sqrt(s2)
        k = (n * (n + 1) / ((n - 1) * (n - 2) * (n - 3))) * \
            sum(((v - m) / s) ** 4 for v in values)
        return k - 3 * (n - 1) ** 2 / ((n - 2) * (n - 3))

    def print_correlation(self, result: AgentResult) -> None:
        d = result.data
        tickers = d.get("tickers", [])
        matrix = d.get("matrix", {})

        print(f"\n{C.BOLD}{'='*74}")
        print(f"  CORRELATION MATRIX")
        print(f"{'='*74}{C.RESET}")
        print(f"  Period: {d['period']['start']} to {d['period']['end']}")
        print(f"  Common days: {d['common_days']:,}")

        # Header
        print(f"\n  {'':>10}", end="")
        for t in tickers:
            print(f" {t:>8}", end="")
        print()
        print(f"  {'-'*(10 + 9*len(tickers))}")

        for t1 in tickers:
            print(f"  {t1:>10}", end="")
            for t2 in tickers:
                val = matrix.get(t1, {}).get(t2, 0)
                clr = C.GREEN if val > 0.7 else C.YELLOW if val > 0.3 else C.RED
                if t1 == t2:
                    clr = C.DIM
                print(f" {clr}{val:>7.4f}{C.RESET}", end="")
            print()
        print()

    def print_returns(self, result: AgentResult) -> None:
        d = result.data
        daily = d.get("daily", {})

        print(f"\n{C.BOLD}{'='*74}")
        print(f"  RETURN ANALYSIS: {d.get('ticker', '?')}")
        print(f"{'='*74}{C.RESET}")
        print(f"  Period: {d['period']['start']} to {d['period']['end']}")
        print(f"  Total days: {d['total_days']:,}")
        print(f"  Total return: {d['total_return_pct']:+.2f}%")
        print(f"  CAGR: {d['cagr_pct']:+.2f}%")
        print(f"  Sharpe: {daily['sharpe']:.3f}")
        print(f"  Positive days: {daily['positive_pct']:.1f}%")
        print(f"  Skewness: {daily['skew']:.3f}")
        print(f"  Excess kurtosis: {daily['kurtosis']:.3f}")

        print(f"\n  {'Percentile':<15} {'Daily%':>8}")
        print(f"  {'-'*23}")
        for p in ['p5', 'p25', 'p50', 'p75', 'p95']:
            print(f"  {p:<15} {daily[p]:>+7.3f}%")

        print(f"\n  {C.GREEN}Best days:{C.RESET}")
        for dt, ret in d.get("best_days", []):
            print(f"    {dt}  {ret:>+7.3f}%")
        print(f"\n  {C.RED}Worst days:{C.RESET}")
        for dt, ret in d.get("worst_days", []):
            print(f"    {dt}  {ret:>+7.3f}%")
        print()

    def print_compare(self, result: AgentResult) -> None:
        d = result.data
        perfs = d.get("performances", [])

        print(f"\n{C.BOLD}{'='*74}")
        print(f"  RELATIVE PERFORMANCE")
        print(f"{'='*74}{C.RESET}")

        print(f"\n  {'Ticker':<8} {'Return%':>9} {'CAGR%':>7} {'Vol%':>7}"
              f" {'Sharpe':>7} {'MaxDD%':>8} {'Win%':>6}")
        print(f"  {'-'*54}")

        for p in perfs:
            sh_clr = C.GREEN if p["sharpe"] > 0.5 else C.YELLOW if p["sharpe"] > 0 else C.RED
            print(f"  {p['ticker']:<8}"
                  f" {p['total_return_pct']:>+8.2f}%"
                  f" {p['cagr_pct']:>+6.2f}%"
                  f" {p['annualized_vol_pct']:>6.2f}%"
                  f" {sh_clr}{p['sharpe']:>7.3f}{C.RESET}"
                  f" {C.RED}{p['max_drawdown_pct']:>7.2f}%{C.RESET}"
                  f" {p['positive_days_pct']:>5.1f}%")
        print()
