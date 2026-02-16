"""
DataCatalogAgent — catalogs all available historical data.

Provides a database-like interface over the archive:
  - Enumerate all tickers by asset class
  - Metadata: date range, row count, data quality
  - Cross-reference: which tickers overlap in date coverage
  - Data quality: missing days, gaps, stale data detection
"""

from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..base import BaseAgent
from ...types import AgentResult, C


class DataCatalogAgent(BaseAgent):
    """Catalogs and inspects all available historical market data."""

    def __init__(self, config=None):
        super().__init__("DataCatalog", config)

    def run(self, context: Dict[str, Any]) -> AgentResult:
        action = context.get("action", "summary")
        loader = context.get("loader")

        if not loader:
            return self._result(success=False, errors=["No loader provided"])

        if action == "summary":
            return self._summary(loader)
        elif action == "inspect":
            return self._inspect(loader, context.get("ticker", ""))
        elif action == "coverage":
            return self._coverage(loader, context.get("tickers", []))
        elif action == "quality":
            return self._quality(loader, context.get("ticker", ""))
        elif action == "search":
            return self._search(loader, context.get("query", ""),
                                context.get("asset_class", ""))
        else:
            return self._result(success=False, errors=[f"Unknown action: {action}"])

    def _summary(self, loader) -> AgentResult:
        """Full catalog summary: all asset classes, ticker counts, date ranges."""
        available = loader.available_tickers()

        catalog = {}
        total_tickers = 0
        total_rows = 0

        for asset_class, tickers in available.items():
            class_info = {
                "count": len(tickers),
                "tickers": tickers,
            }

            # Sample first and last ticker for date range
            if tickers:
                first_info = loader.ticker_info(tickers[0])
                last_info = loader.ticker_info(tickers[-1])
                class_info["sample_range"] = {
                    "first_ticker": tickers[0],
                    "start": first_info.get("start", ""),
                    "last_ticker": tickers[-1],
                    "end": last_info.get("end", ""),
                }

            catalog[asset_class] = class_info
            total_tickers += len(tickers)

        # Check intraday availability
        hourly_dir = loader.data_dir / "intraday_hourly"
        hourly_tickers = []
        if hourly_dir.exists():
            hourly_tickers = sorted(
                p.stem.replace("_hourly", "")
                for p in hourly_dir.glob("*_hourly.csv")
            )

        # Check economic indicators
        econ_dir = loader.data_dir / "economic_indicators"
        econ_files = []
        if econ_dir.exists():
            econ_files = sorted(p.stem for p in econ_dir.glob("*.csv"))

        return self._result(
            success=True,
            data={
                "catalog": catalog,
                "total_tickers": total_tickers,
                "asset_classes": list(catalog.keys()),
                "hourly_tickers": hourly_tickers,
                "economic_indicators": econ_files,
            },
        )

    def _inspect(self, loader, ticker: str) -> AgentResult:
        """Deep inspection of a single ticker's data."""
        if not ticker:
            return self._result(success=False, errors=["No ticker specified"])

        info = loader.ticker_info(ticker)
        if not info.get("found"):
            return self._result(success=False,
                                errors=[f"Ticker '{ticker}' not found"])

        # Load full data for detailed stats
        daily = loader.load_daily(ticker)
        if not daily:
            return self._result(success=True, data=info)

        closes = [r["close"] for r in daily if r["close"] > 0]
        returns = [r["daily_return"] for r in daily if r["daily_return"] != 0]

        # Check for gaps (missing trading days)
        dates = [r["date"] for r in daily]
        gaps = self._find_gaps(dates)

        # Available columns (non-zero)
        sample = daily[len(daily) // 2]  # middle row
        available_fields = [
            k for k, v in sample.items()
            if isinstance(v, (int, float)) and v != 0 and k != "date"
        ]

        # Hourly data check
        hourly = loader.load_hourly(ticker)

        stats = {
            **info,
            "rows": len(daily),
            "close_min": round(min(closes), 2) if closes else 0,
            "close_max": round(max(closes), 2) if closes else 0,
            "close_last": round(closes[-1], 2) if closes else 0,
            "avg_daily_return": round(
                sum(returns) / len(returns) * 100, 4
            ) if returns else 0,
            "volatility": round(
                self._std(returns) * 100, 4
            ) if len(returns) > 1 else 0,
            "total_return_pct": round(
                (closes[-1] / closes[0] - 1) * 100, 2
            ) if len(closes) > 1 else 0,
            "data_gaps": gaps[:10],  # first 10 gaps
            "gap_count": len(gaps),
            "available_fields": available_fields,
            "has_hourly": len(hourly) > 0,
            "hourly_bars": len(hourly),
        }

        return self._result(success=True, data=stats)

    def _coverage(self, loader, tickers: List[str]) -> AgentResult:
        """Compare date coverage across multiple tickers."""
        if not tickers:
            return self._result(success=False, errors=["No tickers specified"])

        coverage = {}
        all_dates = {}

        for ticker in tickers:
            daily = loader.load_daily(ticker)
            if not daily:
                coverage[ticker] = {"found": False, "rows": 0}
                continue

            dates = set(r["date"] for r in daily)
            all_dates[ticker] = dates
            coverage[ticker] = {
                "found": True,
                "rows": len(daily),
                "start": daily[0]["date"],
                "end": daily[-1]["date"],
            }

        # Common dates across all found tickers
        found_tickers = [t for t in tickers if all_dates.get(t)]
        if len(found_tickers) >= 2:
            common = set.intersection(*(all_dates[t] for t in found_tickers))
            common_sorted = sorted(common)
            overlap = {
                "common_days": len(common),
                "start": common_sorted[0] if common_sorted else "",
                "end": common_sorted[-1] if common_sorted else "",
            }
        else:
            overlap = {"common_days": 0}

        return self._result(
            success=True,
            data={
                "tickers": coverage,
                "overlap": overlap,
            },
        )

    def _quality(self, loader, ticker: str) -> AgentResult:
        """Data quality report for a ticker."""
        if not ticker:
            return self._result(success=False, errors=["No ticker specified"])

        daily = loader.load_daily(ticker)
        if not daily:
            return self._result(success=False,
                                errors=[f"No data for '{ticker}'"])

        issues = []

        # Check for zero closes
        zero_closes = sum(1 for r in daily if r["close"] == 0)
        if zero_closes:
            issues.append(f"{zero_closes} days with zero close price")

        # Check for duplicate dates
        dates = [r["date"] for r in daily]
        dupes = len(dates) - len(set(dates))
        if dupes:
            issues.append(f"{dupes} duplicate dates")

        # Check for gaps
        gaps = self._find_gaps(dates)
        if gaps:
            issues.append(f"{len(gaps)} date gaps detected")

        # Check for extreme returns (>20% in a day)
        extreme = [
            (r["date"], r["daily_return"])
            for r in daily
            if abs(r["daily_return"]) > 0.20 and r["daily_return"] != 0
        ]
        if extreme:
            issues.append(f"{len(extreme)} extreme return days (>20%)")

        # Check for stale data (same close 5+ days)
        stale_runs = self._find_stale_runs(daily)
        if stale_runs:
            issues.append(f"{len(stale_runs)} stale price runs (5+ identical closes)")

        # Technical indicator coverage
        tech_coverage = {}
        for field in ["rsi", "atr", "sma_20", "sma_200", "macd",
                       "bb_upper", "volatility_20d"]:
            non_zero = sum(1 for r in daily if r.get(field, 0) != 0)
            tech_coverage[field] = round(non_zero / len(daily) * 100, 1)

        quality_score = 100
        if zero_closes:
            quality_score -= min(zero_closes * 2, 20)
        if dupes:
            quality_score -= min(dupes * 5, 20)
        if gaps:
            quality_score -= min(len(gaps), 20)
        if extreme:
            quality_score -= min(len(extreme) * 2, 10)

        return self._result(
            success=True,
            data={
                "ticker": ticker,
                "rows": len(daily),
                "start": dates[0],
                "end": dates[-1],
                "quality_score": max(quality_score, 0),
                "issues": issues,
                "extreme_returns": extreme[:5],
                "gaps": gaps[:5],
                "stale_runs": stale_runs[:3],
                "technical_coverage": tech_coverage,
            },
        )

    def _search(self, loader, query: str, asset_class: str) -> AgentResult:
        """Search for tickers matching a query."""
        available = loader.available_tickers()
        results = []

        query_upper = query.upper()
        for cls, tickers in available.items():
            if asset_class and cls != asset_class:
                continue
            for t in tickers:
                if query_upper in t.upper():
                    info = loader.ticker_info(t)
                    results.append({
                        "ticker": t,
                        "asset_class": cls,
                        "rows": info.get("rows", 0),
                        "start": info.get("start", ""),
                        "end": info.get("end", ""),
                    })

        return self._result(
            success=True,
            data={
                "query": query,
                "results": results,
                "count": len(results),
            },
        )

    def _find_gaps(self, dates: List[str]) -> List[Dict]:
        """Find gaps in date series (>3 calendar days between entries)."""
        gaps = []
        for i in range(1, len(dates)):
            d1 = datetime.strptime(dates[i - 1], "%Y-%m-%d")
            d2 = datetime.strptime(dates[i], "%Y-%m-%d")
            delta = (d2 - d1).days
            if delta > 4:  # Allow weekends + 1 holiday
                gaps.append({
                    "after": dates[i - 1],
                    "before": dates[i],
                    "gap_days": delta,
                })
        return gaps

    def _find_stale_runs(self, daily: List[Dict]) -> List[Dict]:
        """Find runs of 5+ identical closes."""
        runs = []
        run_start = 0
        for i in range(1, len(daily)):
            if daily[i]["close"] != daily[run_start]["close"]:
                if i - run_start >= 5:
                    runs.append({
                        "start": daily[run_start]["date"],
                        "end": daily[i - 1]["date"],
                        "days": i - run_start,
                        "price": daily[run_start]["close"],
                    })
                run_start = i
        return runs

    @staticmethod
    def _std(values: List[float]) -> float:
        if len(values) < 2:
            return 0.0
        m = sum(values) / len(values)
        import math
        return math.sqrt(sum((v - m) ** 2 for v in values) / (len(values) - 1))

    def print_catalog(self, result: AgentResult) -> None:
        """Pretty-print the data catalog."""
        d = result.data

        print(f"\n{C.BOLD}{'='*74}")
        print(f"  DATA CATALOG")
        print(f"{'='*74}{C.RESET}")
        print(f"  Total tickers: {d.get('total_tickers', 0)}")
        print(f"  Asset classes: {len(d.get('asset_classes', []))}")

        catalog = d.get("catalog", {})
        print(f"\n  {'Asset Class':<24} {'Tickers':>8} {'Sample Range'}")
        print(f"  {'-'*70}")
        for cls, info in catalog.items():
            sample = info.get("sample_range", {})
            rng = f"{sample.get('start', '?')} to {sample.get('end', '?')}" if sample else "?"
            print(f"  {cls:<24} {info['count']:>8}  {rng}")

        hourly = d.get("hourly_tickers", [])
        if hourly:
            print(f"\n  {C.CYAN}Hourly data:{C.RESET} {', '.join(hourly)}")

        econ = d.get("economic_indicators", [])
        if econ:
            print(f"  {C.CYAN}Economic indicators:{C.RESET} {len(econ)} files")

        print()

    def print_inspect(self, result: AgentResult) -> None:
        """Pretty-print ticker inspection."""
        d = result.data

        print(f"\n{C.BOLD}{'='*74}")
        print(f"  TICKER INSPECTION: {d.get('ticker', '?')}")
        print(f"{'='*74}{C.RESET}")
        print(f"  File: {d.get('file', '?')}")
        print(f"  Rows: {d.get('rows', 0):,}")
        print(f"  Date range: {d.get('start', '?')} to {d.get('end', '?')}")
        print(f"  Price range: ${d.get('close_min', 0):,.2f} — "
              f"${d.get('close_max', 0):,.2f}")
        print(f"  Last close: ${d.get('close_last', 0):,.2f}")
        print(f"  Total return: {d.get('total_return_pct', 0):+.2f}%")
        print(f"  Avg daily return: {d.get('avg_daily_return', 0):+.4f}%")
        print(f"  Daily volatility: {d.get('volatility', 0):.4f}%")
        print(f"  Data gaps: {d.get('gap_count', 0)}")
        print(f"  Hourly data: {'Yes' if d.get('has_hourly') else 'No'}"
              f" ({d.get('hourly_bars', 0):,} bars)")

        fields = d.get("available_fields", [])
        if fields:
            print(f"  Fields: {', '.join(fields)}")
        print()

    def print_quality(self, result: AgentResult) -> None:
        """Pretty-print quality report."""
        d = result.data

        score = d.get("quality_score", 0)
        score_clr = C.GREEN if score >= 80 else C.YELLOW if score >= 60 else C.RED

        print(f"\n{C.BOLD}{'='*74}")
        print(f"  DATA QUALITY: {d.get('ticker', '?')}")
        print(f"{'='*74}{C.RESET}")
        print(f"  Quality score: {score_clr}{score}/100{C.RESET}")
        print(f"  Rows: {d.get('rows', 0):,}")
        print(f"  Period: {d.get('start', '?')} to {d.get('end', '?')}")

        issues = d.get("issues", [])
        if issues:
            print(f"\n  {C.YELLOW}Issues:{C.RESET}")
            for issue in issues:
                print(f"    - {issue}")
        else:
            print(f"\n  {C.GREEN}No issues detected{C.RESET}")

        tech = d.get("technical_coverage", {})
        if tech:
            print(f"\n  {'Indicator':<20} {'Coverage':>8}")
            print(f"  {'-'*28}")
            for field, pct in tech.items():
                clr = C.GREEN if pct > 80 else C.YELLOW if pct > 50 else C.RED
                print(f"  {field:<20} {clr}{pct:>7.1f}%{C.RESET}")
        print()
