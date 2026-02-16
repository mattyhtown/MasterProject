"""
HistoricalLoader — loads CSV market data from the extracted archive.

Provides unified access to daily OHLCV, technicals, credit spread,
VIX, and intraday hourly data across all asset classes.
"""

import csv
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# Ticker → (subdirectory, filename) mapping
_TICKER_MAP = {
    # Indices (stored as IDX_*)
    "SPX": ("indices", "IDX_GSPC.csv"),
    "^GSPC": ("indices", "IDX_GSPC.csv"),
    "VIX": ("indices", "IDX_VIX.csv"),
    "^VIX": ("indices", "IDX_VIX.csv"),
    "DJI": ("indices", "IDX_DJI.csv"),
    "^DJI": ("indices", "IDX_DJI.csv"),
    "IXIC": ("indices", "IDX_IXIC.csv"),
    "^IXIC": ("indices", "IDX_IXIC.csv"),
    "RUT": ("indices", "IDX_RUT.csv"),
    "^RUT": ("indices", "IDX_RUT.csv"),
    "SOX": ("indices", "IDX_SOX.csv"),
    # Bond yields
    "YIELD_IRX": ("bonds", "YIELD_IRX.csv"),
    "YIELD_FVX": ("bonds", "YIELD_FVX.csv"),
    "YIELD_TNX": ("bonds", "YIELD_TNX.csv"),
    "YIELD_TYX": ("bonds", "YIELD_TYX.csv"),
    # Crypto aliases
    "BTC": ("crypto", "BTC_USD.csv"),
    "BTC_USD": ("crypto", "BTC_USD.csv"),
    "ETH": ("crypto", "ETH_USD.csv"),
    "ETH_USD": ("crypto", "ETH_USD.csv"),
}

# Directories to search (in order) for unlisted tickers
_SEARCH_DIRS = ["etfs", "stocks/us", "indices", "crypto", "forex",
                "commodities", "futures", "bonds", "crypto_extended"]


class HistoricalLoader:
    """Loads CSV market data from the archive directory."""

    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir) / "data"
        if not self.data_dir.exists():
            raise FileNotFoundError(f"Data directory not found: {self.data_dir}")

    def _find_file(self, ticker: str) -> Optional[Path]:
        """Resolve a ticker to its CSV file path."""
        upper = ticker.upper()

        # Check explicit mapping first
        if upper in _TICKER_MAP:
            subdir, fname = _TICKER_MAP[upper]
            p = self.data_dir / subdir / fname
            if p.exists():
                return p

        # Search directories for {TICKER}.csv
        for subdir in _SEARCH_DIRS:
            p = self.data_dir / subdir / f"{upper}.csv"
            if p.exists():
                return p

        return None

    def _parse_date(self, date_str: str) -> str:
        """Extract YYYY-MM-DD from various date formats."""
        return date_str[:10]

    def _safe_float(self, val: str) -> float:
        """Convert string to float, returning 0.0 on failure."""
        try:
            return float(val) if val else 0.0
        except (ValueError, TypeError):
            return 0.0

    def load_daily(self, ticker: str,
                   start: str = "", end: str = "") -> List[Dict[str, Any]]:
        """Load daily OHLCV + technicals for a ticker.

        Args:
            ticker: Symbol (SPY, SPX, VIX, AAPL, etc.)
            start: Optional start date 'YYYY-MM-DD'
            end: Optional end date 'YYYY-MM-DD'

        Returns:
            List of dicts with: date, open, high, low, close, volume,
            daily_return, rsi, atr, sma_20, sma_200, volatility_20d
        """
        path = self._find_file(ticker)
        if not path:
            return []

        rows = []
        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                dt = self._parse_date(row.get("Date", ""))
                if not dt or dt < "1900":
                    continue
                if start and dt < start:
                    continue
                if end and dt > end:
                    continue

                rows.append({
                    "date": dt,
                    "open": self._safe_float(row.get("Open")),
                    "high": self._safe_float(row.get("High")),
                    "low": self._safe_float(row.get("Low")),
                    "close": self._safe_float(row.get("Close")),
                    "volume": self._safe_float(row.get("Volume")),
                    "daily_return": self._safe_float(row.get("Daily_Return")),
                    "rsi": self._safe_float(row.get("RSI")),
                    "atr": self._safe_float(row.get("ATR")),
                    "sma_20": self._safe_float(row.get("SMA_20")),
                    "sma_50": self._safe_float(row.get("SMA_50")),
                    "sma_200": self._safe_float(row.get("SMA_200")),
                    "volatility_20d": self._safe_float(row.get("Volatility_20d")),
                    "volatility_50d": self._safe_float(row.get("Volatility_50d")),
                    "macd": self._safe_float(row.get("MACD")),
                    "macd_signal": self._safe_float(row.get("MACD_Signal")),
                    "bb_upper": self._safe_float(row.get("BB_Upper")),
                    "bb_lower": self._safe_float(row.get("BB_Lower")),
                    "bb_position": self._safe_float(row.get("BB_Position")),
                })

        return rows

    def load_hourly(self, ticker: str,
                    start: str = "", end: str = "") -> List[Dict[str, Any]]:
        """Load intraday hourly bars."""
        fname = f"{ticker.upper()}_hourly.csv"
        path = self.data_dir / "intraday_hourly" / fname
        if not path.exists():
            return []

        rows = []
        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            # Skip the second header row (ticker names)
            first_data = True
            for row in reader:
                dt_str = row.get("Datetime", "")
                if not dt_str or dt_str.startswith(","):
                    continue
                # Skip rows where Close is a ticker name
                close_val = row.get("Close", "")
                if not close_val or not close_val.replace(".", "").replace("-", "").isdigit():
                    continue

                dt = dt_str[:19]  # YYYY-MM-DD HH:MM:SS
                date_part = dt[:10]
                if start and date_part < start:
                    continue
                if end and date_part > end:
                    continue

                rows.append({
                    "datetime": dt,
                    "date": date_part,
                    "open": self._safe_float(row.get("Open")),
                    "high": self._safe_float(row.get("High")),
                    "low": self._safe_float(row.get("Low")),
                    "close": self._safe_float(row.get("Close")),
                    "volume": self._safe_float(row.get("Volume")),
                })

        return rows

    def load_credit_spread(self, start: str = "",
                           end: str = "") -> List[Dict[str, Any]]:
        """Load HYG-TLT credit spread data.

        Returns list of {date, hyg_close, tlt_close, spread, spread_change}.
        """
        hyg = {r["date"]: r["close"] for r in self.load_daily("HYG", start, end)}
        tlt = {r["date"]: r["close"] for r in self.load_daily("TLT", start, end)}

        common_dates = sorted(set(hyg) & set(tlt))
        rows = []
        prev_spread = None
        for dt in common_dates:
            h, t = hyg[dt], tlt[dt]
            if t == 0:
                continue
            spread = h / t
            change = (spread - prev_spread) / prev_spread if prev_spread else 0.0
            rows.append({
                "date": dt,
                "hyg_close": round(h, 4),
                "tlt_close": round(t, 4),
                "spread": round(spread, 6),
                "spread_change": round(change, 6),
            })
            prev_spread = spread

        return rows

    def load_vix(self, start: str = "", end: str = "") -> Dict[str, float]:
        """Load VIX closes as {date: close} mapping."""
        return {r["date"]: r["close"] for r in self.load_daily("VIX", start, end)}

    def date_range(self, ticker: str) -> Tuple[str, str]:
        """Return (first_date, last_date) for a ticker."""
        rows = self.load_daily(ticker)
        if not rows:
            return ("", "")
        return (rows[0]["date"], rows[-1]["date"])

    def available_tickers(self) -> Dict[str, List[str]]:
        """List all available tickers by asset class."""
        result = {}
        for subdir in _SEARCH_DIRS:
            d = self.data_dir / subdir
            if d.exists():
                tickers = sorted(
                    p.stem for p in d.glob("*.csv")
                )
                if tickers:
                    result[subdir] = tickers
        return result

    def ticker_info(self, ticker: str) -> Dict[str, Any]:
        """Get metadata about a ticker's data."""
        path = self._find_file(ticker)
        if not path:
            return {"found": False, "ticker": ticker}

        rows = self.load_daily(ticker)
        if not rows:
            return {"found": True, "ticker": ticker, "rows": 0}

        closes = [r["close"] for r in rows if r["close"] > 0]
        return {
            "found": True,
            "ticker": ticker,
            "file": str(path),
            "rows": len(rows),
            "start": rows[0]["date"],
            "end": rows[-1]["date"],
            "last_close": closes[-1] if closes else 0,
            "min_close": min(closes) if closes else 0,
            "max_close": max(closes) if closes else 0,
        }
