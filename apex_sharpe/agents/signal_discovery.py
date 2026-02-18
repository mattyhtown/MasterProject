"""
SignalDiscoveryAgent — mine untapped ORATS fields + cross-asset data for
new predictive signals against SPX next-day returns.

Currently we use 14 of 128 ORATS summary fields (11%). This agent
systematically tests the other 114 fields plus cross-asset signals
(VIX, credit spread, yield curve, sector rotation, gold, yen) for
statistically meaningful predictive power.

Each candidate signal is tested at multiple thresholds and scored by:
  - Hit rate (% of signal days where SPX moves in predicted direction)
  - Average next-day return on signal days vs all days
  - Overlap with existing FEAR_BOUNCE signals (independence)
  - Signal frequency (% of days that fire)

Runs on cached data from .0dte_backtest_cache.json + market_data CSVs.
"""

import json
import math
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .base import BaseAgent
from .zero_dte import ZeroDTEAgent
from ..types import AgentResult, C


class SignalDiscoveryAgent(BaseAgent):
    """Discover new predictive signals from untapped data sources."""

    # Fields already used by the 10-signal system + regime classifier
    USED_FIELDS = {
        "iv30d", "rVol30", "dlt25Iv30d", "dlt75Iv30d", "contango",
        "fbfwd30_20", "rSlp30", "fwd30_20", "fwd60_30", "rDrv30",
        "skewing", "rip", "ivRank1m", "stockPrice", "ticker",
        "tradeDate", "updatedAt",
    }

    def __init__(self, config=None):
        super().__init__("SignalDiscovery", config)
        self._monitor = ZeroDTEAgent()

    # -- data loading ---------------------------------------------------------

    @staticmethod
    def _load_cache(cache_path: str = None) -> Dict:
        """Load the backtest cache."""
        path = cache_path or str(Path.home() / ".0dte_backtest_cache.json")
        try:
            with open(path) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _build_dataset(self, cache: Dict, ticker: str = "SPX",
                       loader=None) -> List[Dict]:
        """Build aligned daily dataset: ORATS summaries + returns + cross-asset.

        Returns list of day dicts sorted by date, each containing:
            date, close, next_close, next_return, summary (128 fields),
            plus cross-asset data when available.
        """
        # Find summary and daily keys
        summ_map = None
        daily_data = None
        for k, v in cache.items():
            if k.startswith(f"summ_{ticker}_"):
                summ_map = v
            if k.startswith(f"daily_{ticker}_"):
                daily_data = v

        if not summ_map or not daily_data:
            return []

        # Build price lookup from dailies
        prices: Dict[str, Dict] = {}
        for d in daily_data:
            dt = str(d.get("tradeDate", ""))[:10]
            prices[dt] = d
        trade_dates = sorted(prices.keys())
        date_idx = {d: i for i, d in enumerate(trade_dates)}

        # Load credit spread data from cache
        credit_map = {}
        for k, v in cache.items():
            if k.startswith("credit_"):
                credit_map = v
                break

        # Load cross-asset data from HistoricalLoader
        cross_asset = {}
        if loader:
            start_d = trade_dates[0] if trade_dates else ""
            end_d = trade_dates[-1] if trade_dates else ""
            cross_asset = self._load_cross_asset(loader, start_d, end_d)

        # Build dataset
        sorted_dates = sorted(d for d in summ_map if d in date_idx)
        dataset = []

        for i, dt in enumerate(sorted_dates):
            idx = date_idx.get(dt)
            if idx is None or idx + 1 >= len(trade_dates):
                continue
            next_dt = trade_dates[idx + 1]
            cls_today = float(prices[dt].get("clsPx", 0) or 0)
            cls_next = float(prices[next_dt].get("clsPx", 0) or 0)
            if not cls_today or not cls_next:
                continue

            prev_dt = sorted_dates[i - 1] if i > 0 else None
            prev_summary = summ_map.get(prev_dt, {}) if prev_dt else {}

            # Existing signals for overlap detection
            if prev_summary:
                self._monitor.prev_day[ticker] = prev_summary
                self._monitor.baseline[ticker] = prev_summary
            signals = self._monitor.compute_signals(ticker, summ_map[dt])

            # Credit signal
            cd = credit_map.get(dt, {})
            cd_prev = credit_map.get(prev_dt, {}) if prev_dt else {}
            if (cd.get("HYG") and cd.get("TLT")
                    and cd_prev.get("HYG") and cd_prev.get("TLT")):
                signals.update(self._monitor.compute_credit_signal(
                    cd["HYG"], cd["TLT"], cd_prev["HYG"], cd_prev["TLT"],
                ))

            # Fear composite
            td = datetime.strptime(dt, "%Y-%m-%d").date()
            fear_composite, _ = self._monitor.determine_direction(
                signals, intraday=False, trade_date=td)
            core_firing = [
                k for k in self._monitor.config.core_signals
                if signals.get(k, {}).get("level") == "ACTION"
            ]

            row = {
                "date": dt,
                "close": cls_today,
                "next_close": cls_next,
                "next_return": (cls_next - cls_today) / cls_today,
                "summary": summ_map[dt],
                "prev_summary": prev_summary,
                "fear_composite": fear_composite,
                "fear_core_count": len(core_firing),
                "is_fear_day": bool(fear_composite and "FEAR" in fear_composite),
            }

            # Merge cross-asset data for this date
            if dt in cross_asset:
                row["cross"] = cross_asset[dt]

            dataset.append(row)

        return dataset

    def _load_cross_asset(self, loader, start: str, end: str) -> Dict[str, Dict]:
        """Load cross-asset features from HistoricalLoader CSVs."""
        result: Dict[str, Dict] = {}

        assets = {
            "VIX": "vix",
            "HYG": "hyg",
            "TLT": "tlt",
            "GLD": "gld",
            "IWM": "iwm",
            "SPY": "spy",
            "XLF": "xlf",
            "XLK": "xlk",
            "XLE": "xle",
        }

        data_cache = {}
        for ticker, key in assets.items():
            rows = loader.load_daily(ticker, start, end)
            if rows:
                data_cache[key] = {r["date"]: r for r in rows}

        # Also try yield data
        for yield_ticker in ("YIELD_TNX", "YIELD_IRX"):
            key = yield_ticker.lower().replace("yield_", "yield_")
            rows = loader.load_daily(yield_ticker, start, end)
            if rows:
                data_cache[key] = {r["date"]: r for r in rows}

        # Build per-date feature dict
        all_dates = set()
        for asset_data in data_cache.values():
            all_dates.update(asset_data.keys())

        for dt in sorted(all_dates):
            feat = {}

            # VIX level and changes
            vix_data = data_cache.get("vix", {}).get(dt)
            if vix_data:
                feat["vix_close"] = vix_data["close"]
                feat["vix_rsi"] = vix_data["rsi"]
                feat["vix_sma_20"] = vix_data["sma_20"]
                feat["vix_bb_position"] = vix_data["bb_position"]
                feat["vix_daily_return"] = vix_data["daily_return"]
                if vix_data["sma_20"] > 0:
                    feat["vix_vs_sma20"] = vix_data["close"] / vix_data["sma_20"]

            # Credit spread: HYG/TLT ratio
            hyg_data = data_cache.get("hyg", {}).get(dt)
            tlt_data = data_cache.get("tlt", {}).get(dt)
            if hyg_data and tlt_data and tlt_data["close"] > 0:
                feat["credit_ratio"] = hyg_data["close"] / tlt_data["close"]
                feat["hyg_return"] = hyg_data["daily_return"]
                feat["tlt_return"] = tlt_data["daily_return"]

            # Gold (safe haven)
            gld_data = data_cache.get("gld", {}).get(dt)
            if gld_data:
                feat["gld_return"] = gld_data["daily_return"]
                feat["gld_rsi"] = gld_data["rsi"]

            # Small cap breadth: IWM/SPY ratio
            iwm_data = data_cache.get("iwm", {}).get(dt)
            spy_data = data_cache.get("spy", {}).get(dt)
            if iwm_data and spy_data and spy_data["close"] > 0:
                feat["iwm_spy_ratio"] = iwm_data["close"] / spy_data["close"]
                feat["iwm_return"] = iwm_data["daily_return"]

            # Sector rotation: relative to SPY
            for sec_key in ("xlf", "xlk", "xle"):
                sec_data = data_cache.get(sec_key, {}).get(dt)
                if sec_data and spy_data:
                    feat[f"{sec_key}_rel_return"] = (
                        sec_data["daily_return"] - spy_data["daily_return"]
                    )

            # Yield curve
            tnx = data_cache.get("yield_tnx", {}).get(dt)
            irx = data_cache.get("yield_irx", {}).get(dt)
            if tnx and irx:
                feat["yield_10y"] = tnx["close"]
                feat["yield_3m"] = irx["close"]
                feat["yield_curve_slope"] = tnx["close"] - irx["close"]

            if feat:
                result[dt] = feat

        return result

    # -- signal candidate generators ------------------------------------------

    def _generate_orats_candidates(self, dataset: List[Dict]) -> List[Dict]:
        """Generate candidate signals from untapped ORATS summary fields.

        Each candidate is a dict:
            name, description, category, compute_fn(row) -> float|None,
            direction ('bullish'|'bearish'|'neutral'),
            threshold_type ('above'|'below'|'abs_above'|'percentile')
        """
        candidates = []

        # -- Wing skew signals (deep OTM IVs) --

        def _wing_skew_30d(row):
            """95d-5d IV spread at 30-day tenor — extreme skew."""
            s = row["summary"]
            v95 = _sf(s, "dlt95Iv30d")
            v5 = _sf(s, "dlt5Iv30d")
            return v95 - v5 if v95 and v5 else None

        candidates.append({
            "name": "wing_skew_30d",
            "description": "95d-5d IV spread (crash skew)",
            "category": "ORATS Wing",
            "compute": _wing_skew_30d,
            "direction": "bullish",  # extreme = fear, contrarian buy
            "threshold_type": "percentile_high",
        })

        def _wing_skew_10d(row):
            s = row["summary"]
            v95 = _sf(s, "dlt95Iv10d")
            v5 = _sf(s, "dlt5Iv10d")
            return v95 - v5 if v95 and v5 else None

        candidates.append({
            "name": "wing_skew_10d",
            "description": "10d tenor wing skew (near-term crash demand)",
            "category": "ORATS Wing",
            "compute": _wing_skew_10d,
            "direction": "bullish",
            "threshold_type": "percentile_high",
        })

        def _wing_skew_ratio(row):
            """Short/long wing skew ratio — term structure of crash demand."""
            s = row["summary"]
            s10 = _sf(s, "dlt95Iv10d") - _sf(s, "dlt5Iv10d")
            s90 = _sf(s, "dlt95Iv90d") - _sf(s, "dlt5Iv90d")
            return s10 / s90 if s90 > 0.01 else None

        candidates.append({
            "name": "wing_skew_term",
            "description": "10d/90d wing skew ratio (panic vs calm)",
            "category": "ORATS Wing",
            "compute": _wing_skew_ratio,
            "direction": "bullish",
            "threshold_type": "percentile_high",
        })

        # -- Short-tenor IV acceleration --

        def _iv10_iv30_ratio(row):
            s = row["summary"]
            iv10 = _sf(s, "iv10d")
            iv30 = _sf(s, "iv30d")
            return iv10 / iv30 if iv30 > 0.01 else None

        candidates.append({
            "name": "iv10_iv30_ratio",
            "description": "10d/30d IV ratio (near-term fear acceleration)",
            "category": "ORATS Tenor",
            "compute": _iv10_iv30_ratio,
            "direction": "bullish",
            "threshold_type": "percentile_high",
        })

        def _iv10_iv90_ratio(row):
            s = row["summary"]
            iv10 = _sf(s, "iv10d")
            iv90 = _sf(s, "iv90d")
            return iv10 / iv90 if iv90 > 0.01 else None

        candidates.append({
            "name": "iv10_iv90_ratio",
            "description": "10d/90d IV ratio (short-term panic vs baseline)",
            "category": "ORATS Tenor",
            "compute": _iv10_iv90_ratio,
            "direction": "bullish",
            "threshold_type": "percentile_high",
        })

        def _iv_term_spread(row):
            """iv90d - iv10d: positive = normal contango, negative = inversion."""
            s = row["summary"]
            return _sf(s, "iv90d") - _sf(s, "iv10d")

        candidates.append({
            "name": "iv_term_spread",
            "description": "90d-10d IV spread (term structure inversion)",
            "category": "ORATS Tenor",
            "compute": _iv_term_spread,
            "direction": "bullish",  # inversion (negative) = fear
            "threshold_type": "percentile_low",
        })

        # -- Multi-tenor forward ratios --

        def _fbfwd90_30(row):
            return _sf(row["summary"], "fbfwd90_30")

        candidates.append({
            "name": "fbfwd90_30",
            "description": "Forward/backward 90/30 ratio",
            "category": "ORATS Forward",
            "compute": _fbfwd90_30,
            "direction": "bullish",
            "threshold_type": "percentile_high",
        })

        def _fbfwd180_90(row):
            return _sf(row["summary"], "fbfwd180_90")

        candidates.append({
            "name": "fbfwd180_90",
            "description": "Forward/backward 180/90 ratio",
            "category": "ORATS Forward",
            "compute": _fbfwd180_90,
            "direction": "bullish",
            "threshold_type": "percentile_high",
        })

        def _fbfwd60_30(row):
            return _sf(row["summary"], "fbfwd60_30")

        candidates.append({
            "name": "fbfwd60_30",
            "description": "Forward/backward 60/30 ratio",
            "category": "ORATS Forward",
            "compute": _fbfwd60_30,
            "direction": "bullish",
            "threshold_type": "percentile_high",
        })

        def _fbfwd90_60(row):
            return _sf(row["summary"], "fbfwd90_60")

        candidates.append({
            "name": "fbfwd90_60",
            "description": "Forward/backward 90/60 ratio",
            "category": "ORATS Forward",
            "compute": _fbfwd90_60,
            "direction": "bullish",
            "threshold_type": "percentile_high",
        })

        # -- Flat forward divergence (earnings-stripped) --

        def _flat_vs_cal_30_20(row):
            """Flat forward vs calendar forward divergence."""
            s = row["summary"]
            ff = _sf(s, "ffwd30_20")
            fw = _sf(s, "fwd30_20")
            return ff - fw if ff and fw else None

        candidates.append({
            "name": "flat_cal_diverge_30_20",
            "description": "Flat forward - calendar forward 30/20 (event vol)",
            "category": "ORATS Forward",
            "compute": _flat_vs_cal_30_20,
            "direction": "neutral",
            "threshold_type": "abs_percentile_high",
        })

        def _flat_vs_cal_60_30(row):
            s = row["summary"]
            ff = _sf(s, "ffwd60_30")
            fw = _sf(s, "fwd60_30")
            return ff - fw if ff and fw else None

        candidates.append({
            "name": "flat_cal_diverge_60_30",
            "description": "Flat forward - calendar forward 60/30 (event vol)",
            "category": "ORATS Forward",
            "compute": _flat_vs_cal_60_30,
            "direction": "neutral",
            "threshold_type": "abs_percentile_high",
        })

        # -- Model confidence (dislocation detector) --

        def _confidence(row):
            return _sf(row["summary"], "confidence")

        candidates.append({
            "name": "model_confidence",
            "description": "ORATS model confidence (low = dislocation)",
            "category": "ORATS Model",
            "compute": _confidence,
            "direction": "bullish",
            "threshold_type": "percentile_low",
        })

        def _total_error(row):
            return _sf(row["summary"], "totalErrorConf")

        candidates.append({
            "name": "total_error_conf",
            "description": "ORATS total error (high = model stress)",
            "category": "ORATS Model",
            "compute": _total_error,
            "direction": "bullish",
            "threshold_type": "percentile_high",
        })

        # -- Long-dated RV context --

        def _rv_term_spread(row):
            """rVol2y - rVol30: positive = vol expanding."""
            s = row["summary"]
            return _sf(s, "rVol2y") - _sf(s, "rVol30")

        candidates.append({
            "name": "rv_term_spread",
            "description": "2y-30d realized vol spread (trend context)",
            "category": "ORATS RV",
            "compute": _rv_term_spread,
            "direction": "bearish",
            "threshold_type": "percentile_high",
        })

        def _slope_term_spread(row):
            s = row["summary"]
            return _sf(s, "rSlp2y") - _sf(s, "rSlp30")

        candidates.append({
            "name": "slope_term_spread",
            "description": "2y-30d skew slope spread",
            "category": "ORATS RV",
            "compute": _slope_term_spread,
            "direction": "neutral",
            "threshold_type": "abs_percentile_high",
        })

        # -- Borrow/funding stress --

        def _borrow_spread(row):
            s = row["summary"]
            return _sf(s, "borrow30") - _sf(s, "riskFree30")

        candidates.append({
            "name": "borrow_spread",
            "description": "Borrow rate - risk-free (funding stress)",
            "category": "ORATS Funding",
            "compute": _borrow_spread,
            "direction": "bullish",
            "threshold_type": "percentile_high",
        })

        def _borrow_term(row):
            s = row["summary"]
            b30 = _sf(s, "borrow30")
            b2y = _sf(s, "borrow2y")
            return b30 - b2y if b30 and b2y else None

        candidates.append({
            "name": "borrow_term_spread",
            "description": "30d-2y borrow rate spread (short-term stress)",
            "category": "ORATS Funding",
            "compute": _borrow_term,
            "direction": "bullish",
            "threshold_type": "percentile_high",
        })

        # -- Market-width adjustment (liquidity proxy) --

        def _mw_adj_30(row):
            return _sf(row["summary"], "mwAdj30")

        candidates.append({
            "name": "mw_adj_30",
            "description": "Market-width adjustment 30d (bid-ask width)",
            "category": "ORATS Liquidity",
            "compute": _mw_adj_30,
            "direction": "bullish",
            "threshold_type": "percentile_high",
        })

        # -- 25-delta put skew across tenors --

        def _put_skew_10d(row):
            """25d put skew at 10-day tenor."""
            s = row["summary"]
            return _sf(s, "dlt25Iv10d") - _sf(s, "dlt75Iv10d")

        candidates.append({
            "name": "put_skew_10d",
            "description": "25d-75d IV skew at 10d (short-term put demand)",
            "category": "ORATS Skew",
            "compute": _put_skew_10d,
            "direction": "bullish",
            "threshold_type": "percentile_high",
        })

        def _put_skew_60d(row):
            s = row["summary"]
            return _sf(s, "dlt25Iv60d") - _sf(s, "dlt75Iv60d")

        candidates.append({
            "name": "put_skew_60d",
            "description": "25d-75d IV skew at 60d (medium-term put demand)",
            "category": "ORATS Skew",
            "compute": _put_skew_60d,
            "direction": "bullish",
            "threshold_type": "percentile_high",
        })

        def _put_skew_term(row):
            """Short vs long skew: 10d skew / 90d skew."""
            s = row["summary"]
            s10 = _sf(s, "dlt25Iv10d") - _sf(s, "dlt75Iv10d")
            s90 = _sf(s, "dlt25Iv90d") - _sf(s, "dlt75Iv90d")
            return s10 / s90 if abs(s90) > 0.001 else None

        candidates.append({
            "name": "put_skew_term_ratio",
            "description": "10d/90d put skew ratio (panic acceleration)",
            "category": "ORATS Skew",
            "compute": _put_skew_term,
            "direction": "bullish",
            "threshold_type": "percentile_high",
        })

        # -- IV rank change (day-over-day) --

        def _iv_momentum(row):
            """iv30d change from previous day."""
            s = row["summary"]
            ps = row.get("prev_summary", {})
            iv = _sf(s, "iv30d")
            piv = _sf(ps, "iv30d")
            return iv - piv if piv > 0 else None

        candidates.append({
            "name": "iv_momentum",
            "description": "1-day IV30d change (vol acceleration)",
            "category": "ORATS Momentum",
            "compute": _iv_momentum,
            "direction": "bullish",
            "threshold_type": "percentile_high",
        })

        def _rip_change(row):
            s = row["summary"]
            ps = row.get("prev_summary", {})
            r = _sf(s, "rip")
            pr = _sf(ps, "rip")
            return r - pr if pr > 0 else None

        candidates.append({
            "name": "rip_change",
            "description": "1-day RIP change (risk premium acceleration)",
            "category": "ORATS Momentum",
            "compute": _rip_change,
            "direction": "bullish",
            "threshold_type": "percentile_high",
        })

        def _skewing_change(row):
            s = row["summary"]
            ps = row.get("prev_summary", {})
            sk = _sf(s, "skewing")
            psk = _sf(ps, "skewing")
            return sk - psk

        candidates.append({
            "name": "skewing_change",
            "description": "1-day skewing change (put demand acceleration)",
            "category": "ORATS Momentum",
            "compute": _skewing_change,
            "direction": "bullish",
            "threshold_type": "percentile_high",
        })

        def _contango_change(row):
            s = row["summary"]
            ps = row.get("prev_summary", {})
            c = _sf(s, "contango")
            pc = _sf(ps, "contango")
            return c - pc if pc else None

        candidates.append({
            "name": "contango_change",
            "description": "1-day contango change (term structure shift)",
            "category": "ORATS Momentum",
            "compute": _contango_change,
            "direction": "bullish",
            "threshold_type": "percentile_low",  # drop = fear
        })

        # -- Earnings-excluded vs normal divergence --

        def _exern_diverge_30(row):
            """exErnIv30d vs iv30d divergence."""
            s = row["summary"]
            ex = _sf(s, "exErnIv30d")
            iv = _sf(s, "iv30d")
            return ex - iv if ex and iv else None

        candidates.append({
            "name": "exern_iv30_diverge",
            "description": "Earnings-excluded vs raw IV30 (earnings vol component)",
            "category": "ORATS Earnings",
            "compute": _exern_diverge_30,
            "direction": "neutral",
            "threshold_type": "abs_percentile_high",
        })

        return candidates

    def _generate_cross_asset_candidates(self, dataset: List[Dict]) -> List[Dict]:
        """Generate candidate signals from cross-asset data."""
        candidates = []

        # Check what cross-asset data is available
        has_cross = any("cross" in row for row in dataset)
        if not has_cross:
            return candidates

        # -- VIX signals --

        def _vix_level(row):
            return row.get("cross", {}).get("vix_close")

        candidates.append({
            "name": "vix_level",
            "description": "VIX close level (fear gauge)",
            "category": "Cross-Asset VIX",
            "compute": _vix_level,
            "direction": "bullish",
            "threshold_type": "percentile_high",
        })

        def _vix_rsi(row):
            return row.get("cross", {}).get("vix_rsi")

        candidates.append({
            "name": "vix_rsi",
            "description": "VIX RSI (overbought VIX = oversold equity)",
            "category": "Cross-Asset VIX",
            "compute": _vix_rsi,
            "direction": "bullish",
            "threshold_type": "percentile_high",
        })

        def _vix_vs_sma20(row):
            return row.get("cross", {}).get("vix_vs_sma20")

        candidates.append({
            "name": "vix_vs_sma20",
            "description": "VIX / SMA20 ratio (vol spike relative)",
            "category": "Cross-Asset VIX",
            "compute": _vix_vs_sma20,
            "direction": "bullish",
            "threshold_type": "percentile_high",
        })

        def _vix_bb(row):
            return row.get("cross", {}).get("vix_bb_position")

        candidates.append({
            "name": "vix_bb_position",
            "description": "VIX Bollinger Band position (>1 = upper break)",
            "category": "Cross-Asset VIX",
            "compute": _vix_bb,
            "direction": "bullish",
            "threshold_type": "percentile_high",
        })

        # -- Credit spread --

        def _credit_ratio(row):
            return row.get("cross", {}).get("credit_ratio")

        candidates.append({
            "name": "credit_ratio",
            "description": "HYG/TLT ratio (credit risk)",
            "category": "Cross-Asset Credit",
            "compute": _credit_ratio,
            "direction": "bearish",
            "threshold_type": "percentile_low",
        })

        # -- Gold (safe haven) --

        def _gld_return(row):
            return row.get("cross", {}).get("gld_return")

        candidates.append({
            "name": "gld_return",
            "description": "Gold daily return (flight to safety)",
            "category": "Cross-Asset Gold",
            "compute": _gld_return,
            "direction": "bullish",
            "threshold_type": "percentile_high",
        })

        # -- Small cap breadth --

        def _iwm_spy(row):
            return row.get("cross", {}).get("iwm_spy_ratio")

        candidates.append({
            "name": "iwm_spy_ratio",
            "description": "IWM/SPY ratio (breadth divergence)",
            "category": "Cross-Asset Breadth",
            "compute": _iwm_spy,
            "direction": "neutral",
            "threshold_type": "percentile_low",
        })

        # -- Sector rotation --

        def _xlf_rel(row):
            return row.get("cross", {}).get("xlf_rel_return")

        candidates.append({
            "name": "xlf_rel_return",
            "description": "Financials vs SPY relative return",
            "category": "Cross-Asset Sector",
            "compute": _xlf_rel,
            "direction": "bullish",
            "threshold_type": "percentile_low",
        })

        def _xle_rel(row):
            return row.get("cross", {}).get("xle_rel_return")

        candidates.append({
            "name": "xle_rel_return",
            "description": "Energy vs SPY relative return",
            "category": "Cross-Asset Sector",
            "compute": _xle_rel,
            "direction": "neutral",
            "threshold_type": "percentile_low",
        })

        # -- Yield curve --

        def _yield_slope(row):
            return row.get("cross", {}).get("yield_curve_slope")

        candidates.append({
            "name": "yield_curve_slope",
            "description": "10Y-3M yield spread (macro stress)",
            "category": "Cross-Asset Yield",
            "compute": _yield_slope,
            "direction": "neutral",
            "threshold_type": "percentile_low",
        })

        return candidates

    # -- backtesting engine ---------------------------------------------------

    def _backtest_signal(self, dataset: List[Dict], candidate: Dict,
                         percentile_cuts: Tuple = (90, 80, 70, 10, 20, 30),
                         ) -> Dict[str, Any]:
        """Test a single candidate signal against next-day SPX returns.

        Tests at multiple percentile thresholds and reports best.
        """
        compute = candidate["compute"]
        direction = candidate["direction"]
        threshold_type = candidate["threshold_type"]

        # Compute signal values
        values = []
        for row in dataset:
            try:
                val = compute(row)
            except Exception:
                val = None
            if val is not None and not math.isnan(val):
                values.append((val, row))

        if len(values) < 20:
            return {"name": candidate["name"], "status": "insufficient_data",
                    "n_values": len(values)}

        all_vals = sorted(v[0] for v in values)
        n = len(all_vals)

        # Compute percentile thresholds
        thresholds = {}
        for p in percentile_cuts:
            idx = int(n * p / 100)
            idx = min(idx, n - 1)
            thresholds[f"P{p}"] = all_vals[idx]

        # Baseline stats (all days)
        all_returns = [v[1]["next_return"] for v in values]
        base_avg = sum(all_returns) / len(all_returns)
        base_pos = sum(1 for r in all_returns if r > 0) / len(all_returns)

        # Test each threshold
        results = []
        for label, thresh in thresholds.items():
            if "high" in threshold_type:
                signal_days = [(v, row) for v, row in values if v >= thresh]
                test_label = f">= {label}"
            elif "low" in threshold_type:
                signal_days = [(v, row) for v, row in values if v <= thresh]
                test_label = f"<= {label}"
            elif "abs" in threshold_type:
                abs_thresh = abs(thresh)
                signal_days = [(v, row) for v, row in values if abs(v) >= abs_thresh]
                test_label = f"|val| >= {label}"
            else:
                continue

            if len(signal_days) < 3:
                continue

            sig_returns = [row["next_return"] for _, row in signal_days]
            sig_avg = sum(sig_returns) / len(sig_returns)
            sig_freq = len(signal_days) / len(values)

            # Direction-aware hit rate
            if direction == "bullish":
                hits = sum(1 for r in sig_returns if r > 0)
            elif direction == "bearish":
                hits = sum(1 for r in sig_returns if r < 0)
            else:
                # neutral: predict abs move > baseline
                abs_base = sum(abs(r) for r in all_returns) / len(all_returns)
                hits = sum(1 for r in sig_returns if abs(r) > abs_base)

            hit_rate = hits / len(sig_returns)

            # Overlap with existing FEAR days
            fear_overlap = sum(
                1 for _, row in signal_days if row.get("is_fear_day")
            )
            fear_pct = fear_overlap / len(signal_days) if signal_days else 0

            results.append({
                "threshold": label,
                "threshold_value": round(thresh, 6),
                "test": test_label,
                "n_days": len(signal_days),
                "frequency": round(sig_freq, 3),
                "hit_rate": round(hit_rate, 3),
                "avg_return": round(sig_avg * 100, 4),  # in pct
                "base_avg_return": round(base_avg * 100, 4),
                "edge": round((sig_avg - base_avg) * 100, 4),
                "fear_overlap": round(fear_pct, 3),
                "independence": round(1 - fear_pct, 3),
            })

        if not results:
            return {"name": candidate["name"], "status": "no_valid_thresholds"}

        # Pick best by hit rate (among those with reasonable frequency)
        viable = [r for r in results if r["frequency"] >= 0.05
                  and r["frequency"] <= 0.40]
        if not viable:
            viable = results

        best = max(viable, key=lambda r: r["hit_rate"])

        return {
            "name": candidate["name"],
            "description": candidate["description"],
            "category": candidate["category"],
            "direction": candidate["direction"],
            "status": "ok",
            "n_total": len(values),
            "best": best,
            "all_thresholds": results,
        }

    # -- composite discovery --------------------------------------------------

    def _find_composites(self, dataset: List[Dict],
                         top_signals: List[Dict]) -> List[Dict]:
        """Test 2-signal and 3-signal combinations from top candidates."""
        if len(top_signals) < 2:
            return []

        # Pre-compute signal values for top candidates
        signal_vals: Dict[str, List[Tuple[bool, Dict]]] = {}
        for sig in top_signals:
            name = sig["name"]
            compute = sig["_compute"]
            thresh = sig["best"]["threshold_value"]
            ttype = sig["_threshold_type"]

            fires = []
            for row in dataset:
                try:
                    val = compute(row)
                except Exception:
                    val = None
                if val is None or math.isnan(val):
                    fires.append((False, row))
                    continue
                if "high" in ttype:
                    fires.append((val >= thresh, row))
                elif "low" in ttype:
                    fires.append((val <= thresh, row))
                else:
                    fires.append((abs(val) >= abs(thresh), row))
            signal_vals[name] = fires

        combos = []
        sig_names = list(signal_vals.keys())

        # Test pairs
        for i in range(len(sig_names)):
            for j in range(i + 1, len(sig_names)):
                n1, n2 = sig_names[i], sig_names[j]
                v1 = signal_vals[n1]
                v2 = signal_vals[n2]

                both_fire = []
                for (f1, row1), (f2, row2) in zip(v1, v2):
                    if f1 and f2:
                        both_fire.append(row1)

                if len(both_fire) < 3:
                    continue

                returns = [r["next_return"] for r in both_fire]
                avg_ret = sum(returns) / len(returns)
                hit_up = sum(1 for r in returns if r > 0) / len(returns)
                fear_olap = sum(1 for r in both_fire if r.get("is_fear_day")) / len(both_fire)

                combos.append({
                    "signals": [n1, n2],
                    "n_days": len(both_fire),
                    "frequency": round(len(both_fire) / len(dataset), 3),
                    "hit_rate_up": round(hit_up, 3),
                    "avg_return_pct": round(avg_ret * 100, 4),
                    "fear_overlap": round(fear_olap, 3),
                    "independence": round(1 - fear_olap, 3),
                })

        # Sort by hit rate, then edge
        combos.sort(key=lambda c: (-c["hit_rate_up"], -c["avg_return_pct"]))
        return combos[:20]

    # -- main runner ----------------------------------------------------------

    def run(self, context: Dict[str, Any]) -> AgentResult:
        """Run signal discovery. Context keys: orats, state, loader (opt)."""
        self.discover(
            cache_path=context.get("cache_path"),
            loader=context.get("loader"),
            ticker=context.get("ticker", "SPX"),
        )
        return self._result(success=True)

    def discover(self, cache_path: str = None, loader=None,
                 ticker: str = "SPX") -> Dict[str, Any]:
        """Run the full signal discovery pipeline."""
        cache = self._load_cache(cache_path)
        if not cache:
            print(f"  {C.RED}No backtest cache found. Run 0dte-backtest first.{C.RESET}")
            return {}

        print(f"\n{C.BOLD}{C.CYAN}{'=' * 78}{C.RESET}")
        print(f"  {C.BOLD}SIGNAL DISCOVERY ENGINE{C.RESET}")
        print(f"  Mining untapped ORATS fields + cross-asset data")
        print(f"{C.BOLD}{C.CYAN}{'=' * 78}{C.RESET}")

        # Build dataset
        print(f"\n  {C.DIM}Loading data...{C.RESET}", end=" ", flush=True)
        dataset = self._build_dataset(cache, ticker, loader)
        if not dataset:
            print(f"\n  {C.RED}No data available for {ticker}{C.RESET}")
            return {}

        n_days = len(dataset)
        fear_days = sum(1 for r in dataset if r.get("is_fear_day"))
        start = dataset[0]["date"]
        end = dataset[-1]["date"]
        print(f"{n_days} days ({start} to {end}), {fear_days} existing FEAR days")

        # All-day baseline stats
        all_returns = [r["next_return"] for r in dataset]
        base_avg = sum(all_returns) / len(all_returns)
        base_pos = sum(1 for r in all_returns if r > 0) / len(all_returns)
        print(f"  {C.DIM}Baseline: avg next-day return = {base_avg*100:+.3f}%, "
              f"up-day rate = {base_pos:.1%}{C.RESET}")

        # Generate candidates
        orats_candidates = self._generate_orats_candidates(dataset)
        cross_candidates = self._generate_cross_asset_candidates(dataset)
        all_candidates = orats_candidates + cross_candidates

        print(f"\n  Testing {C.BOLD}{len(all_candidates)}{C.RESET} candidate signals "
              f"({len(orats_candidates)} ORATS + {len(cross_candidates)} cross-asset)")

        # Test each candidate
        results = []
        for i, cand in enumerate(all_candidates):
            if (i + 1) % 10 == 0:
                print(f"\r  Progress: {i+1}/{len(all_candidates)}  ", end="", flush=True)
            result = self._backtest_signal(dataset, cand)
            if result.get("status") == "ok":
                result["_compute"] = cand["compute"]
                result["_threshold_type"] = cand["threshold_type"]
                results.append(result)
        print(f"\r  Tested: {len(all_candidates)} candidates → "
              f"{len(results)} with valid results      ")

        # Rank by hit rate
        results.sort(key=lambda r: -r["best"]["hit_rate"])

        # Print results
        self._print_results(results, base_avg, base_pos)

        # Find composites from top 10
        top_10 = results[:10]
        if len(top_10) >= 2:
            print(f"\n{C.BOLD}{C.CYAN}{'=' * 78}{C.RESET}")
            print(f"  {C.BOLD}COMPOSITE DISCOVERY — 2-signal combinations{C.RESET}")
            print(f"{C.BOLD}{C.CYAN}{'=' * 78}{C.RESET}")
            combos = self._find_composites(dataset, top_10)
            self._print_composites(combos, base_avg)

        return {
            "n_candidates": len(all_candidates),
            "n_valid": len(results),
            "results": [{k: v for k, v in r.items()
                        if not k.startswith("_")} for r in results],
            "composites": combos if len(top_10) >= 2 else [],
        }

    # -- terminal display -----------------------------------------------------

    def _print_results(self, results: List[Dict],
                       base_avg: float, base_pos: float) -> None:
        """Print signal discovery results."""
        print(f"\n{C.BOLD}{C.CYAN}{'=' * 78}{C.RESET}")
        print(f"  {C.BOLD}SIGNAL RANKINGS — sorted by hit rate{C.RESET}")
        print(f"  Baseline: avg return = {base_avg*100:+.3f}% | "
              f"up-day rate = {base_pos:.1%}")
        print(f"{C.BOLD}{C.CYAN}{'=' * 78}{C.RESET}")

        # Header
        print(f"\n  {'#':>2}  {'Signal':<28} {'Cat':<16} {'Dir':<7} "
              f"{'Hit%':>5} {'AvgR%':>7} {'Edge':>6} {'Freq':>5} "
              f"{'Days':>4} {'Indep':>5}")
        print(f"  {'—'*2}  {'—'*28} {'—'*16} {'—'*7} "
              f"{'—'*5} {'—'*7} {'—'*6} {'—'*5} "
              f"{'—'*4} {'—'*5}")

        for i, r in enumerate(results[:30]):
            b = r["best"]
            hit = b["hit_rate"]
            edge = b["edge"]

            # Color by hit rate
            if hit >= 0.70:
                clr = C.GREEN
            elif hit >= 0.60:
                clr = C.YELLOW
            else:
                clr = C.DIM

            # Independence color
            ind = b["independence"]
            ind_clr = C.GREEN if ind >= 0.80 else C.YELLOW if ind >= 0.50 else C.RED

            print(f"  {i+1:>2}  {clr}{r['name']:<28}{C.RESET} "
                  f"{r['category']:<16} {r['direction']:<7} "
                  f"{clr}{hit:>5.1%}{C.RESET} {b['avg_return']:>+7.3f} "
                  f"{'+'if edge>0 else ''}{edge:>5.3f} {b['frequency']:>5.1%} "
                  f"{b['n_days']:>4} {ind_clr}{ind:>5.1%}{C.RESET}")

        # Highlight best independent signals
        independent = [r for r in results if r["best"]["independence"] >= 0.70
                       and r["best"]["hit_rate"] >= 0.55]
        if independent:
            print(f"\n  {C.BOLD}{C.GREEN}TOP INDEPENDENT SIGNALS "
                  f"(>=70% independence, >=55% hit rate):{C.RESET}")
            for r in independent[:10]:
                b = r["best"]
                print(f"    {C.GREEN}★{C.RESET} {r['name']:<28} "
                      f"hit={b['hit_rate']:.1%} edge={b['edge']:+.3f}% "
                      f"indep={b['independence']:.0%} freq={b['frequency']:.1%}")

    def _print_composites(self, combos: List[Dict],
                          base_avg: float) -> None:
        """Print composite signal combinations."""
        if not combos:
            print(f"  {C.DIM}No valid composites found{C.RESET}")
            return

        print(f"\n  {'#':>2}  {'Signal Pair':<50} "
              f"{'Hit%':>5} {'AvgR%':>7} {'Freq':>5} "
              f"{'Days':>4} {'Indep':>5}")
        print(f"  {'—'*2}  {'—'*50} "
              f"{'—'*5} {'—'*7} {'—'*5} "
              f"{'—'*4} {'—'*5}")

        for i, c in enumerate(combos[:15]):
            pair = " + ".join(c["signals"])
            hit = c["hit_rate_up"]
            clr = C.GREEN if hit >= 0.75 else C.YELLOW if hit >= 0.65 else C.DIM
            ind_clr = C.GREEN if c["independence"] >= 0.80 else C.YELLOW

            print(f"  {i+1:>2}  {clr}{pair:<50}{C.RESET} "
                  f"{clr}{hit:>5.1%}{C.RESET} {c['avg_return_pct']:>+7.3f} "
                  f"{c['frequency']:>5.1%} "
                  f"{c['n_days']:>4} {ind_clr}{c['independence']:>5.1%}{C.RESET}")


# -- module-level helper (used by closures) --

def _sf(d: Dict, key: str, default: float = 0.0) -> float:
    """Safe float extraction."""
    if not d:
        return default
    v = d.get(key)
    if v is None:
        return default
    try:
        return float(v)
    except (ValueError, TypeError):
        return default
