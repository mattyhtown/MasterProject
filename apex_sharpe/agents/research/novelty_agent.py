"""
NoveltyAgent — discovers new data sources, anomalies, and untested patterns.

Hunts for alpha by:
  - Scanning the archive for under-analyzed tickers
  - Detecting anomalies (unusual returns, correlation breaks, regime shifts)
  - Testing cross-asset lead/lag relationships
  - Finding untested indicator combos that predict forward returns
  - Flagging structural breaks in time series
"""

import math
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from ..base import BaseAgent
from ...types import AgentResult, C


class NoveltyAgent(BaseAgent):
    """Discovers new datasets, anomalies, and untested trading patterns."""

    def __init__(self, config=None):
        super().__init__("Novelty", config)

    def run(self, context: Dict[str, Any]) -> AgentResult:
        action = context.get("action", "scan")
        loader = context.get("loader")

        if not loader:
            return self._result(success=False, errors=["No loader provided"])

        if action == "scan":
            return self._full_scan(loader)
        elif action == "anomalies":
            return self._anomaly_scan(
                loader,
                context.get("ticker", "SPY"),
                context.get("start", ""),
                context.get("end", ""),
            )
        elif action == "lead_lag":
            return self._lead_lag(
                loader,
                context.get("target", "SPY"),
                context.get("candidates", []),
                context.get("start", ""),
                context.get("end", ""),
            )
        elif action == "regime_breaks":
            return self._regime_breaks(
                loader,
                context.get("ticker", "SPY"),
                context.get("start", ""),
                context.get("end", ""),
            )
        elif action == "hidden_factors":
            return self._hidden_factors(
                loader,
                context.get("target", "SPY"),
                context.get("start", ""),
                context.get("end", ""),
            )
        elif action == "underexplored":
            return self._underexplored_tickers(loader)
        else:
            return self._result(success=False, errors=[f"Unknown action: {action}"])

    # ------------------------------------------------------------------
    # Full novelty scan
    # ------------------------------------------------------------------

    def _full_scan(self, loader) -> AgentResult:
        """Run all novelty detectors and surface the best findings."""
        findings = []

        # 1. Find underexplored tickers with high Sharpe
        under_result = self._underexplored_tickers(loader)
        if under_result.success:
            gems = under_result.data.get("gems", [])
            for g in gems[:5]:
                findings.append({
                    "type": "underexplored_gem",
                    "priority": "HIGH" if g["sharpe"] > 0.8 else "MEDIUM",
                    "description": (
                        f"{g['ticker']} ({g['asset_class']}): "
                        f"Sharpe {g['sharpe']:.3f}, {g['return_pct']:+.1f}% total, "
                        f"{g['days']} days — not in any existing strategy"
                    ),
                    "data": g,
                })

        # 2. Cross-asset lead/lag with SPY
        lag_candidates = ["GLD", "TLT", "VIX", "HYG", "EURUSD", "USDJPY",
                          "BTC_USD", "CL", "GC", "YIELD_TNX"]
        lag_result = self._lead_lag(loader, "SPY", lag_candidates, "", "")
        if lag_result.success:
            for sig in lag_result.data.get("signals", []):
                if abs(sig.get("predictive_corr", 0)) > 0.08:
                    direction = "predicts UP" if sig["predictive_corr"] > 0 else "predicts DOWN"
                    findings.append({
                        "type": "lead_lag",
                        "priority": "HIGH" if abs(sig["predictive_corr"]) > 0.12 else "MEDIUM",
                        "description": (
                            f"{sig['ticker']} {direction} for SPY "
                            f"(lag-{sig['best_lag']}d corr: {sig['predictive_corr']:+.4f}, "
                            f"win rate: {sig.get('win_rate', 0):.1f}%)"
                        ),
                        "data": sig,
                    })

        # 3. Anomalies in SPY
        anomaly_result = self._anomaly_scan(loader, "SPY", "", "")
        if anomaly_result.success:
            for a in anomaly_result.data.get("anomalies", [])[:5]:
                findings.append({
                    "type": "anomaly",
                    "priority": a.get("severity", "MEDIUM"),
                    "description": a["description"],
                    "data": a,
                })

        # 4. Regime breaks
        break_result = self._regime_breaks(loader, "SPY", "", "")
        if break_result.success:
            for b in break_result.data.get("breaks", [])[-3:]:
                findings.append({
                    "type": "regime_break",
                    "priority": "HIGH",
                    "description": (
                        f"Regime break at {b['date']}: "
                        f"vol shifted {b['vol_before']:.1f}% → {b['vol_after']:.1f}%, "
                        f"mean shifted {b['mean_before']:+.3f}% → {b['mean_after']:+.3f}%"
                    ),
                    "data": b,
                })

        # Sort by priority
        priority_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        findings.sort(key=lambda f: priority_order.get(f["priority"], 2))

        return self._result(
            success=True,
            data={
                "findings": findings,
                "total": len(findings),
                "high_priority": sum(1 for f in findings if f["priority"] == "HIGH"),
            },
        )

    # ------------------------------------------------------------------
    # Underexplored tickers — find hidden gems in the archive
    # ------------------------------------------------------------------

    def _underexplored_tickers(self, loader) -> AgentResult:
        """Find tickers with strong risk-adjusted returns that aren't in any strategy."""
        # Tickers already used in the system
        known = {"SPY", "SPX", "QQQ", "IWM", "TLT", "HYG", "GLD", "SLV",
                 "VIX", "DIA", "EFA", "FXI", "EWZ", "USO", "AGG", "BND"}

        available = loader.available_tickers()
        gems = []

        for asset_class, tickers in available.items():
            for ticker in tickers:
                if ticker.upper() in known:
                    continue
                # Strip IDX_ prefix for comparison
                clean = ticker.replace("IDX_", "")
                if clean in known:
                    continue

                daily = loader.load_daily(ticker)
                if len(daily) < 252:  # Need at least 1 year
                    continue

                closes = [r["close"] for r in daily if r["close"] > 0]
                returns = [r["daily_return"] for r in daily if r["daily_return"] != 0]

                if len(returns) < 100:
                    continue

                sharpe = self._annualized_sharpe(returns)
                total_ret = (closes[-1] / closes[0] - 1) * 100 if len(closes) > 1 else 0

                # Only surface gems with meaningful Sharpe
                if sharpe > 0.4:
                    # Check if it has low correlation to SPY
                    spy_data = loader.load_daily("SPY")
                    spy_by_date = {r["date"]: r["daily_return"] for r in spy_data}
                    common_rets_spy = []
                    common_rets_ticker = []
                    for r in daily:
                        if r["date"] in spy_by_date and r["daily_return"] != 0:
                            common_rets_spy.append(spy_by_date[r["date"]])
                            common_rets_ticker.append(r["daily_return"])

                    spy_corr = self._pearson(common_rets_spy, common_rets_ticker) if len(common_rets_spy) > 30 else 0

                    gems.append({
                        "ticker": ticker,
                        "asset_class": asset_class,
                        "days": len(daily),
                        "start": daily[0]["date"],
                        "end": daily[-1]["date"],
                        "return_pct": round(total_ret, 2),
                        "sharpe": round(sharpe, 3),
                        "spy_correlation": round(spy_corr, 4),
                        "diversification_value": "HIGH" if abs(spy_corr) < 0.3 else "MEDIUM" if abs(spy_corr) < 0.6 else "LOW",
                    })

        gems.sort(key=lambda x: x["sharpe"], reverse=True)

        return self._result(
            success=True,
            data={
                "gems": gems[:30],
                "total_scanned": sum(len(t) for t in available.values()),
                "gems_found": len(gems),
            },
        )

    # ------------------------------------------------------------------
    # Anomaly detection
    # ------------------------------------------------------------------

    def _anomaly_scan(self, loader, ticker: str,
                      start: str, end: str) -> AgentResult:
        """Detect statistical anomalies in a ticker's behavior."""
        daily = loader.load_daily(ticker, start, end)
        if len(daily) < 60:
            return self._result(success=False, errors=["Need 60+ days"])

        anomalies = []
        returns = [r["daily_return"] for r in daily if r["daily_return"] != 0]
        mean_ret = self._mean(returns)
        std_ret = self._std(returns)

        if std_ret == 0:
            return self._result(success=True, data={"anomalies": [], "ticker": ticker})

        # 1. Z-score outliers (|z| > 3)
        for r in daily:
            if r["daily_return"] == 0:
                continue
            z = (r["daily_return"] - mean_ret) / std_ret
            if abs(z) > 3:
                anomalies.append({
                    "type": "return_outlier",
                    "date": r["date"],
                    "value": round(r["daily_return"] * 100, 3),
                    "z_score": round(z, 2),
                    "severity": "HIGH" if abs(z) > 4 else "MEDIUM",
                    "description": (
                        f"{r['date']}: {r['daily_return']*100:+.2f}% return "
                        f"(z={z:.1f}, {abs(z):.0f} sigma event)"
                    ),
                })

        # 2. Volatility regime shifts (rolling 20d vol vs 60d vol)
        for i in range(60, len(daily)):
            recent_rets = [daily[j]["daily_return"] for j in range(i-20, i) if daily[j]["daily_return"] != 0]
            longer_rets = [daily[j]["daily_return"] for j in range(i-60, i) if daily[j]["daily_return"] != 0]

            if len(recent_rets) < 10 or len(longer_rets) < 30:
                continue

            vol_20 = self._std(recent_rets)
            vol_60 = self._std(longer_rets)

            if vol_60 > 0 and vol_20 / vol_60 > 2.5:
                anomalies.append({
                    "type": "vol_regime_shift",
                    "date": daily[i]["date"],
                    "value": round(vol_20 / vol_60, 2),
                    "severity": "HIGH" if vol_20 / vol_60 > 3 else "MEDIUM",
                    "description": (
                        f"{daily[i]['date']}: Vol spike — 20d vol "
                        f"{vol_20*100:.2f}% vs 60d avg {vol_60*100:.2f}% "
                        f"({vol_20/vol_60:.1f}x expansion)"
                    ),
                })

        # 3. Correlation breaks with VIX
        vix_data = loader.load_vix(start, end)
        if vix_data:
            # Rolling 60d correlation between returns and VIX changes
            prev_vix = None
            vix_changes = {}
            for dt, v in sorted(vix_data.items()):
                if prev_vix is not None and prev_vix > 0:
                    vix_changes[dt] = (v - prev_vix) / prev_vix
                prev_vix = v

            for i in range(60, len(daily)):
                window_rets = []
                window_vix = []
                for j in range(i-60, i):
                    dt = daily[j]["date"]
                    if dt in vix_changes and daily[j]["daily_return"] != 0:
                        window_rets.append(daily[j]["daily_return"])
                        window_vix.append(vix_changes[dt])

                if len(window_rets) < 30:
                    continue

                corr = self._pearson(window_rets, window_vix)
                # Normal: SPY and VIX changes are negatively correlated (~-0.7)
                if corr > -0.2:
                    anomalies.append({
                        "type": "vix_decorrelation",
                        "date": daily[i]["date"],
                        "value": round(corr, 4),
                        "severity": "HIGH" if corr > 0 else "MEDIUM",
                        "description": (
                            f"{daily[i]['date']}: {ticker}/VIX correlation "
                            f"breakdown — 60d corr = {corr:+.3f} "
                            f"(normal: -0.6 to -0.8)"
                        ),
                    })

        # Deduplicate nearby dates (keep highest severity)
        anomalies = self._deduplicate_anomalies(anomalies, gap_days=5)
        anomalies.sort(key=lambda a: a["date"], reverse=True)

        return self._result(
            success=True,
            data={
                "ticker": ticker,
                "anomalies": anomalies[:20],
                "total_found": len(anomalies),
            },
        )

    # ------------------------------------------------------------------
    # Lead/lag discovery
    # ------------------------------------------------------------------

    def _lead_lag(self, loader, target: str,
                  candidates: List[str],
                  start: str, end: str) -> AgentResult:
        """Find assets that lead/predict the target's returns."""
        target_data = loader.load_daily(target, start, end)
        if not target_data:
            return self._result(success=False, errors=[f"No data for {target}"])

        target_by_date = {r["date"]: r for r in target_data}
        target_dates = [r["date"] for r in target_data]

        if not candidates:
            # Default: scan diverse set
            candidates = ["GLD", "TLT", "HYG", "VIX", "EURUSD", "USDJPY",
                          "BTC_USD", "CL", "GC", "YIELD_TNX", "EWZ", "FXI",
                          "SLV", "QQQ", "IWM"]

        signals = []
        for ticker in candidates:
            cand_data = loader.load_daily(ticker, start, end)
            if len(cand_data) < 120:
                continue

            cand_by_date = {r["date"]: r["daily_return"] for r in cand_data}

            # Test lags 1-5: does candidate's return on day T predict
            # target's return on day T+lag?
            best_lag = 0
            best_corr = 0
            best_win_rate = 0

            for lag in range(1, 6):
                cand_rets = []
                target_fwd = []

                for i in range(len(target_dates) - lag):
                    dt = target_dates[i]
                    fwd_dt = target_dates[i + lag]
                    if dt in cand_by_date and fwd_dt in target_by_date:
                        cr = cand_by_date[dt]
                        tr = target_by_date[fwd_dt]["daily_return"]
                        if cr != 0 and tr != 0:
                            cand_rets.append(cr)
                            target_fwd.append(tr)

                if len(cand_rets) < 60:
                    continue

                corr = self._pearson(cand_rets, target_fwd)

                # Also check: when candidate has big move, what happens to target?
                # (thresholded signal, not just linear correlation)
                threshold = sorted([abs(r) for r in cand_rets])[int(len(cand_rets) * 0.8)]
                big_moves = [(c, t) for c, t in zip(cand_rets, target_fwd) if abs(c) > threshold]

                if big_moves:
                    # When candidate drops big, does target follow?
                    down_moves = [t for c, t in big_moves if c < 0]
                    up_moves = [t for c, t in big_moves if c > 0]
                    win_rate = 0
                    if down_moves:
                        # If candidate drops and we expect target to drop too (positive corr)
                        # or bounce (negative corr)
                        if corr > 0:
                            win_rate = sum(1 for t in down_moves if t < 0) / len(down_moves) * 100
                        else:
                            win_rate = sum(1 for t in down_moves if t > 0) / len(down_moves) * 100
                else:
                    win_rate = 0

                if abs(corr) > abs(best_corr):
                    best_corr = corr
                    best_lag = lag
                    best_win_rate = win_rate

            if abs(best_corr) > 0.03:
                signals.append({
                    "ticker": ticker,
                    "best_lag": best_lag,
                    "predictive_corr": round(best_corr, 4),
                    "win_rate": round(best_win_rate, 1),
                    "strength": (
                        "STRONG" if abs(best_corr) > 0.12
                        else "MODERATE" if abs(best_corr) > 0.06
                        else "WEAK"
                    ),
                })

        signals.sort(key=lambda s: abs(s["predictive_corr"]), reverse=True)

        return self._result(
            success=True,
            data={
                "target": target,
                "signals": signals,
                "actionable": [s for s in signals if s["strength"] != "WEAK"],
            },
        )

    # ------------------------------------------------------------------
    # Regime / structural breaks
    # ------------------------------------------------------------------

    def _regime_breaks(self, loader, ticker: str,
                       start: str, end: str) -> AgentResult:
        """Detect structural breaks in the time series."""
        daily = loader.load_daily(ticker, start, end)
        if len(daily) < 120:
            return self._result(success=False, errors=["Need 120+ days"])

        returns = [r["daily_return"] for r in daily]
        window = 60  # 60-day windows
        breaks = []

        for i in range(window, len(returns) - window):
            before = [r for r in returns[i - window:i] if r != 0]
            after = [r for r in returns[i:i + window] if r != 0]

            if len(before) < 30 or len(after) < 30:
                continue

            mean_b = self._mean(before)
            mean_a = self._mean(after)
            vol_b = self._std(before) * math.sqrt(252) * 100
            vol_a = self._std(after) * math.sqrt(252) * 100

            # Detect significant shifts in mean or volatility
            mean_shift = abs(mean_a - mean_b)
            vol_shift = abs(vol_a - vol_b)

            # Use pooled std for significance
            pooled_std = self._std(before + after)
            if pooled_std == 0:
                continue

            t_stat = (mean_a - mean_b) / (pooled_std * math.sqrt(2 / window))
            vol_ratio = vol_a / vol_b if vol_b > 0 else 1

            if abs(t_stat) > 2.5 or vol_ratio > 2.0 or vol_ratio < 0.5:
                breaks.append({
                    "date": daily[i]["date"],
                    "t_stat": round(t_stat, 2),
                    "mean_before": round(mean_b * 100, 3),
                    "mean_after": round(mean_a * 100, 3),
                    "vol_before": round(vol_b, 1),
                    "vol_after": round(vol_a, 1),
                    "vol_ratio": round(vol_ratio, 2),
                    "break_type": (
                        "MEAN+VOL" if abs(t_stat) > 2.5 and (vol_ratio > 2 or vol_ratio < 0.5)
                        else "MEAN" if abs(t_stat) > 2.5
                        else "VOL"
                    ),
                })

        # Deduplicate nearby breaks
        breaks = self._deduplicate_breaks(breaks, gap_days=30)

        return self._result(
            success=True,
            data={
                "ticker": ticker,
                "breaks": breaks,
                "total": len(breaks),
            },
        )

    # ------------------------------------------------------------------
    # Hidden factor discovery
    # ------------------------------------------------------------------

    def _hidden_factors(self, loader, target: str,
                        start: str, end: str) -> AgentResult:
        """Find which assets explain residual variance in target returns."""
        target_data = loader.load_daily(target, start, end)
        if not target_data:
            return self._result(success=False, errors=[f"No data for {target}"])

        target_by_date = {r["date"]: r["daily_return"] for r in target_data
                          if r["daily_return"] != 0}

        # Test a broad set of potential factors
        factor_tickers = {
            "Bonds": "TLT",
            "Gold": "GLD",
            "Oil": "CL",
            "Dollar/Yen": "USDJPY",
            "Euro/Dollar": "EURUSD",
            "High Yield": "HYG",
            "Small Cap": "IWM",
            "Tech": "QQQ",
            "China": "FXI",
            "Volatility": "VIX",
            "Silver": "SLV",
            "Bitcoin": "BTC_USD",
            "Brazil": "EWZ",
            "10Y Yield": "YIELD_TNX",
            "Copper": "HG",
        }

        factors = {}
        for label, ticker in factor_tickers.items():
            fdata = loader.load_daily(ticker, start, end)
            if len(fdata) < 120:
                continue

            factor_by_date = {r["date"]: r["daily_return"] for r in fdata
                              if r["daily_return"] != 0}

            # Align
            common = sorted(set(target_by_date) & set(factor_by_date))
            if len(common) < 60:
                continue

            target_rets = [target_by_date[d] for d in common]
            factor_rets = [factor_by_date[d] for d in common]

            corr = self._pearson(target_rets, factor_rets)

            # Beta (slope of regression)
            beta = self._beta(factor_rets, target_rets)

            # R-squared (how much variance does this factor explain?)
            r_sq = corr ** 2

            factors[label] = {
                "ticker": ticker,
                "correlation": round(corr, 4),
                "beta": round(beta, 4),
                "r_squared": round(r_sq, 4),
                "variance_explained_pct": round(r_sq * 100, 1),
                "common_days": len(common),
            }

        # Sort by R-squared
        ranked = sorted(factors.items(), key=lambda x: x[1]["r_squared"], reverse=True)

        # Find factors that explain residual (after removing top factor)
        top_factor = ranked[0] if ranked else None
        residual_factors = []
        if top_factor:
            top_label, top_data = top_factor
            top_ticker = top_data["ticker"]
            top_fdata = loader.load_daily(top_ticker, start, end)
            top_by_date = {r["date"]: r["daily_return"] for r in top_fdata
                           if r["daily_return"] != 0}

            # Compute residuals: target - beta * top_factor
            common = sorted(set(target_by_date) & set(top_by_date))
            if len(common) > 60:
                residuals = {}
                for d in common:
                    predicted = top_data["beta"] * top_by_date[d]
                    residuals[d] = target_by_date[d] - predicted

                # Now find what explains residuals
                for label, finfo in factors.items():
                    if label == top_label:
                        continue
                    fticker = finfo["ticker"]
                    ff = loader.load_daily(fticker, start, end)
                    ff_by_date = {r["date"]: r["daily_return"] for r in ff
                                  if r["daily_return"] != 0}

                    res_common = sorted(set(residuals) & set(ff_by_date))
                    if len(res_common) < 60:
                        continue

                    res_rets = [residuals[d] for d in res_common]
                    ff_rets = [ff_by_date[d] for d in res_common]
                    res_corr = self._pearson(res_rets, ff_rets)

                    if abs(res_corr) > 0.05:
                        residual_factors.append({
                            "label": label,
                            "ticker": fticker,
                            "residual_corr": round(res_corr, 4),
                            "additional_r_sq": round(res_corr ** 2 * 100, 1),
                        })

                residual_factors.sort(key=lambda x: abs(x["residual_corr"]), reverse=True)

        return self._result(
            success=True,
            data={
                "target": target,
                "factors": dict(ranked),
                "top_factor": top_factor[0] if top_factor else None,
                "residual_factors": residual_factors[:5],
                "total_variance_explained": round(
                    sum(f["r_squared"] for _, f in ranked[:3]) * 100, 1
                ) if ranked else 0,
            },
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _deduplicate_anomalies(self, anomalies: List[Dict],
                                gap_days: int) -> List[Dict]:
        """Keep only the most severe anomaly within gap_days."""
        if not anomalies:
            return []
        severity_rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        anomalies.sort(key=lambda a: (a["date"], severity_rank.get(a.get("severity", "LOW"), 2)))

        result = [anomalies[0]]
        for a in anomalies[1:]:
            prev_date = datetime.strptime(result[-1]["date"], "%Y-%m-%d")
            curr_date = datetime.strptime(a["date"], "%Y-%m-%d")
            if (curr_date - prev_date).days >= gap_days:
                result.append(a)
            elif severity_rank.get(a.get("severity"), 2) < severity_rank.get(result[-1].get("severity"), 2):
                result[-1] = a

        return result

    def _deduplicate_breaks(self, breaks: List[Dict],
                             gap_days: int) -> List[Dict]:
        """Keep only the strongest break within gap_days."""
        if not breaks:
            return []
        result = [breaks[0]]
        for b in breaks[1:]:
            prev_date = datetime.strptime(result[-1]["date"], "%Y-%m-%d")
            curr_date = datetime.strptime(b["date"], "%Y-%m-%d")
            if (curr_date - prev_date).days >= gap_days:
                result.append(b)
            elif abs(b["t_stat"]) > abs(result[-1]["t_stat"]):
                result[-1] = b
        return result

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
    def _pearson(x: List[float], y: List[float]) -> float:
        n = min(len(x), len(y))
        if n < 2:
            return 0.0
        mx = sum(x[:n]) / n
        my = sum(y[:n]) / n
        cov = sum((x[i] - mx) * (y[i] - my) for i in range(n))
        sx = math.sqrt(sum((xi - mx) ** 2 for xi in x[:n]))
        sy = math.sqrt(sum((yi - my) ** 2 for yi in y[:n]))
        if sx == 0 or sy == 0:
            return 0.0
        return cov / (sx * sy)

    @staticmethod
    def _beta(x: List[float], y: List[float]) -> float:
        """Slope of y = alpha + beta * x."""
        n = min(len(x), len(y))
        if n < 2:
            return 0.0
        mx = sum(x[:n]) / n
        my = sum(y[:n]) / n
        cov = sum((x[i] - mx) * (y[i] - my) for i in range(n))
        var_x = sum((xi - mx) ** 2 for xi in x[:n])
        if var_x == 0:
            return 0.0
        return cov / var_x

    @staticmethod
    def _annualized_sharpe(daily_returns: List[float]) -> float:
        if len(daily_returns) < 20:
            return 0.0
        m = sum(daily_returns) / len(daily_returns)
        var = sum((r - m) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
        std = math.sqrt(var) if var > 0 else 0
        if std == 0:
            return 0.0
        return (m / std) * math.sqrt(252)

    def print_scan(self, result: AgentResult) -> None:
        d = result.data
        findings = d.get("findings", [])

        print(f"\n{C.BOLD}{'='*74}")
        print(f"  NOVELTY SCAN")
        print(f"{'='*74}{C.RESET}")
        print(f"  Findings: {d.get('total', 0)}"
              f" ({d.get('high_priority', 0)} high priority)")

        for f in findings:
            clr = C.RED if f["priority"] == "HIGH" else C.YELLOW if f["priority"] == "MEDIUM" else C.DIM
            icon = f["type"].upper()
            print(f"\n  {clr}[{f['priority']}]{C.RESET} {C.CYAN}{icon}{C.RESET}")
            print(f"    {f['description']}")

        print()

    def print_lead_lag(self, result: AgentResult) -> None:
        d = result.data
        print(f"\n{C.BOLD}{'='*74}")
        print(f"  LEAD/LAG ANALYSIS: {d.get('target', '?')}")
        print(f"{'='*74}{C.RESET}")

        signals = d.get("signals", [])
        print(f"\n  {'Ticker':<12} {'Lag':>4} {'Corr':>8} {'Win%':>6} {'Strength'}")
        print(f"  {'-'*42}")
        for s in signals:
            clr = C.GREEN if s["strength"] == "STRONG" else C.YELLOW if s["strength"] == "MODERATE" else C.DIM
            print(f"  {s['ticker']:<12} {s['best_lag']:>3}d"
                  f" {s['predictive_corr']:>+7.4f}"
                  f" {s['win_rate']:>5.1f}%"
                  f" {clr}{s['strength']}{C.RESET}")
        print()

    def print_factors(self, result: AgentResult) -> None:
        d = result.data
        print(f"\n{C.BOLD}{'='*74}")
        print(f"  HIDDEN FACTORS: {d.get('target', '?')}")
        print(f"{'='*74}{C.RESET}")

        factors = d.get("factors", {})
        print(f"\n  {'Factor':<16} {'Ticker':>8} {'Corr':>8} {'Beta':>8} {'R2%':>6}")
        print(f"  {'-'*46}")
        for label, f in factors.items():
            clr = C.GREEN if f["r_squared"] > 0.1 else C.YELLOW if f["r_squared"] > 0.02 else C.DIM
            print(f"  {label:<16} {f['ticker']:>8}"
                  f" {f['correlation']:>+7.4f}"
                  f" {f['beta']:>+7.4f}"
                  f" {clr}{f['variance_explained_pct']:>5.1f}%{C.RESET}")

        resid = d.get("residual_factors", [])
        if resid:
            print(f"\n  {C.CYAN}Residual factors (after removing {d.get('top_factor', '?')}):{C.RESET}")
            for r in resid:
                print(f"    {r['label']:<16} corr={r['residual_corr']:+.4f}"
                      f"  +{r['additional_r_sq']:.1f}% variance")
        print()
