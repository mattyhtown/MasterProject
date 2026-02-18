"""
IntradayCollector — scrape and store intraday price + vol surface data.

Sources:
    - IB: 1-min SPX bars (real-time or historical backfill)
    - ORATS: intraday summaries (skewing, contango, IV surface)

Storage: JSON cache file (~/.0dte_intraday_cache.json) keyed by date.
Each day stores timestamped snapshots for replay backtesting.

Usage:
    collector = IntradayCollector(config, ib_client, orats_client)
    collector.run_live()          # Poll during market hours
    collector.backfill("30 D")    # Backfill 1-min bars from IB
    collector.export_day("2026-02-14")  # Export a day's data
"""

import json
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import IBCfg
from ..types import C

CACHE_PATH = os.path.expanduser("~/.0dte_intraday_cache.json")

# Market hours (ET)
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MIN = 30
MARKET_CLOSE_HOUR = 16
MARKET_CLOSE_MIN = 0


class IntradayCollector:
    """Collect and store intraday price + signal data."""

    def __init__(self, ib_client=None, orats_client=None):
        self.ib = ib_client
        self.orats = orats_client
        self._cache = self._load_cache()

    def _load_cache(self) -> Dict:
        if os.path.exists(CACHE_PATH):
            try:
                with open(CACHE_PATH) as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save_cache(self) -> None:
        with open(CACHE_PATH, "w") as f:
            json.dump(self._cache, f)

    # -- IB Historical Backfill ---------------------------------------------

    def backfill_bars(self, ticker: str = "SPX",
                      duration: str = "30 D",
                      bar_size: str = "1 min") -> int:
        """Backfill intraday bars from IB.

        Stores bars keyed by date in the cache.
        IB limits:
            1 min:  up to 30 days
            5 mins: up to 120 days
            1 hour: up to 1 year

        Returns number of bars stored.
        """
        if not self.ib or not self.ib.is_connected:
            print(f"  {C.RED}IB not connected — cannot backfill{C.RESET}")
            return 0

        print(f"  Fetching {ticker} {bar_size} bars ({duration})...", flush=True)

        # SPX needs TRADES for index, use MIDPOINT as fallback
        what_to_show = "TRADES"
        if ticker.upper() in ("SPX", "NDX", "RUT", "VIX"):
            what_to_show = "TRADES"

        bars = self.ib.historical_bars(
            ticker, duration, bar_size, what_to_show=what_to_show)

        if not bars:
            print(f"  {C.RED}No bars returned{C.RESET}")
            return 0

        # Group by date
        by_date: Dict[str, List[Dict]] = {}
        for bar in bars:
            dt_str = str(bar["date"])
            # bar["date"] for intraday is "YYYY-MM-DD HH:MM:SS"
            day_str = dt_str[:10]
            if day_str not in by_date:
                by_date[day_str] = []
            by_date[day_str].append({
                "time": dt_str,
                "open": bar["open"],
                "high": bar["high"],
                "low": bar["low"],
                "close": bar["close"],
                "volume": bar["volume"],
            })

        # Store in cache
        bars_key = f"ib_bars_{ticker}"
        if bars_key not in self._cache:
            self._cache[bars_key] = {}

        total = 0
        for day_str, day_bars in by_date.items():
            self._cache[bars_key][day_str] = day_bars
            total += len(day_bars)

        self._save_cache()
        print(f"  Stored {total} bars across {len(by_date)} days"
              f" ({min(by_date.keys())} → {max(by_date.keys())})")
        return total

    def backfill_5min(self, ticker: str = "SPX",
                      duration: str = "120 D") -> int:
        """Backfill 5-minute bars (up to 120 days from IB)."""
        return self.backfill_bars(ticker, duration, "5 mins")

    def backfill_hourly(self, ticker: str = "SPX",
                        duration: str = "1 Y") -> int:
        """Backfill hourly bars (up to 1 year from IB)."""
        return self.backfill_bars(ticker, duration, "1 hour")

    # -- ORATS Intraday Summaries -------------------------------------------

    def snapshot_orats_summary(self, ticker: str = "SPX") -> Optional[Dict]:
        """Take a single ORATS intraday summary snapshot.

        Returns the summary dict with timestamp, or None on failure.
        """
        if not self.orats:
            return None

        resp = self.orats.summaries(ticker)
        if not resp or not resp.get("data"):
            return None

        summary = resp["data"][0]
        now = datetime.now()
        summary["_snapshot_time"] = now.isoformat()
        summary["_ticker"] = ticker

        # Store in cache
        day_str = now.strftime("%Y-%m-%d")
        snap_key = f"orats_intraday_{ticker}"
        if snap_key not in self._cache:
            self._cache[snap_key] = {}
        if day_str not in self._cache[snap_key]:
            self._cache[snap_key][day_str] = []

        self._cache[snap_key][day_str].append(summary)
        return summary

    # -- Live Polling -------------------------------------------------------

    def run_live(self, tickers: List[str] = None,
                 interval: int = 120,
                 ib_bar_interval: int = 300) -> None:
        """Live polling during market hours.

        Collects:
          1. ORATS intraday summaries every `interval` seconds
          2. IB 1-min bars every `ib_bar_interval` seconds (last 5 min)

        Args:
            tickers: Symbols to poll. Default: ["SPX"]
            interval: Seconds between ORATS snapshots.
            ib_bar_interval: Seconds between IB bar fetches.
        """
        if tickers is None:
            tickers = ["SPX"]

        print(f"\n{C.BOLD}{C.CYAN}Intraday Collector — Live Mode{C.RESET}")
        print(f"  Tickers: {', '.join(tickers)}")
        print(f"  ORATS interval: {interval}s")
        if self.ib and self.ib.is_connected:
            print(f"  IB bar interval: {ib_bar_interval}s")
        else:
            print(f"  IB: {C.YELLOW}not connected (ORATS only){C.RESET}")
        print()

        n = 0
        last_ib_fetch = 0

        try:
            while True:
                n += 1
                now = datetime.now()

                # Check market hours (rough — no holiday calendar)
                if (now.hour < MARKET_OPEN_HOUR or
                    (now.hour == MARKET_OPEN_HOUR and now.minute < MARKET_OPEN_MIN) or
                    now.hour >= MARKET_CLOSE_HOUR):
                    if n == 1:
                        print(f"  {C.DIM}Outside market hours. "
                              f"Waiting...{C.RESET}")
                    time.sleep(60)
                    continue

                # ORATS snapshots
                for ticker in tickers:
                    snap = self.snapshot_orats_summary(ticker)
                    if snap:
                        spot = snap.get("stockPrice", 0)
                        skewing = snap.get("skewing", 0)
                        contango = snap.get("contango", 0)
                        iv10d = snap.get("iv10d", 0)
                        print(f"  [{now.strftime('%H:%M:%S')}] {ticker}"
                              f"  spot={spot:.2f}"
                              f"  skew={skewing:.4f}"
                              f"  ctgo={contango:.4f}"
                              f"  iv10d={iv10d:.4f}")

                # IB bar fetch (less frequent)
                elapsed_ib = time.time() - last_ib_fetch
                if (self.ib and self.ib.is_connected
                        and elapsed_ib >= ib_bar_interval):
                    for ticker in tickers:
                        bars = self.ib.historical_bars(
                            ticker, "300 S", "1 min")
                        if bars:
                            day_str = now.strftime("%Y-%m-%d")
                            bars_key = f"ib_bars_{ticker}"
                            if bars_key not in self._cache:
                                self._cache[bars_key] = {}
                            if day_str not in self._cache[bars_key]:
                                self._cache[bars_key][day_str] = []

                            # Append new bars (deduplicate by time)
                            existing_times = {
                                b["time"]
                                for b in self._cache[bars_key][day_str]
                            }
                            new_bars = 0
                            for bar in bars:
                                dt_str = str(bar["date"])
                                if dt_str not in existing_times:
                                    self._cache[bars_key][day_str].append({
                                        "time": dt_str,
                                        "open": bar["open"],
                                        "high": bar["high"],
                                        "low": bar["low"],
                                        "close": bar["close"],
                                        "volume": bar["volume"],
                                    })
                                    new_bars += 1
                            if new_bars:
                                print(f"  [{now.strftime('%H:%M:%S')}] "
                                      f"IB: +{new_bars} bars for {ticker}")
                    last_ib_fetch = time.time()

                # Save periodically
                if n % 5 == 0:
                    self._save_cache()

                time.sleep(interval)

        except KeyboardInterrupt:
            self._save_cache()
            print(f"\n{C.BOLD}Collector stopped. Cache saved.{C.RESET}")
            self._print_status()

    # -- Query Methods ------------------------------------------------------

    def get_bars(self, ticker: str, day: str) -> List[Dict]:
        """Get stored intraday bars for a specific day."""
        bars_key = f"ib_bars_{ticker}"
        return self._cache.get(bars_key, {}).get(day, [])

    def get_orats_snapshots(self, ticker: str, day: str) -> List[Dict]:
        """Get stored ORATS intraday snapshots for a specific day."""
        snap_key = f"orats_intraday_{ticker}"
        return self._cache.get(snap_key, {}).get(day, [])

    def get_available_days(self, ticker: str = "SPX") -> Dict[str, Dict]:
        """List all days with stored data and what's available."""
        bars_key = f"ib_bars_{ticker}"
        snap_key = f"orats_intraday_{ticker}"

        bar_days = set(self._cache.get(bars_key, {}).keys())
        snap_days = set(self._cache.get(snap_key, {}).keys())
        all_days = sorted(bar_days | snap_days)

        result = {}
        for day in all_days:
            n_bars = len(self._cache.get(bars_key, {}).get(day, []))
            n_snaps = len(self._cache.get(snap_key, {}).get(day, []))
            result[day] = {
                "ib_bars": n_bars,
                "orats_snapshots": n_snaps,
            }
        return result

    # -- Status -------------------------------------------------------------

    def _print_status(self) -> None:
        """Print summary of stored data."""
        print(f"\n  {C.BOLD}Stored Data:{C.RESET}")
        for key in sorted(self._cache.keys()):
            if isinstance(self._cache[key], dict):
                n_days = len(self._cache[key])
                total_entries = sum(
                    len(v) if isinstance(v, list) else 1
                    for v in self._cache[key].values()
                )
                print(f"    {key}: {n_days} days, {total_entries} entries")

    def print_day(self, ticker: str, day: str) -> None:
        """Print summary of a specific day's data."""
        bars = self.get_bars(ticker, day)
        snaps = self.get_orats_snapshots(ticker, day)

        print(f"\n  {C.BOLD}{ticker} — {day}{C.RESET}")

        if bars:
            first = bars[0]
            last = bars[-1]
            highs = [b["high"] for b in bars]
            lows = [b["low"] for b in bars]
            print(f"  IB Bars: {len(bars)}"
                  f"  {first['time'][-8:]} → {last['time'][-8:]}")
            print(f"    Open: {first['open']:.2f}"
                  f"  High: {max(highs):.2f}"
                  f"  Low: {min(lows):.2f}"
                  f"  Close: {last['close']:.2f}")

        if snaps:
            print(f"  ORATS Snapshots: {len(snaps)}")
            for s in snaps[:5]:
                t = s.get("_snapshot_time", "?")[-8:]
                print(f"    [{t}] spot={s.get('stockPrice', 0):.2f}"
                      f"  skewing={s.get('skewing', 0):.4f}"
                      f"  contango={s.get('contango', 0):.4f}"
                      f"  iv10d={s.get('iv10d', 0):.4f}")
            if len(snaps) > 5:
                print(f"    ... +{len(snaps) - 5} more snapshots")

        if not bars and not snaps:
            print(f"  {C.DIM}No data for this day{C.RESET}")
