"""
MacroAgent — cross-asset macro analysis using historical data.

Analyzes:
  - Inter-market correlations (equities, bonds, commodities, FX, crypto)
  - Risk-on / risk-off regime detection
  - Yield curve dynamics (from bond yield data)
  - Currency / commodity impact on equities
  - Global market leadership rotation
"""

import math
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..base import BaseAgent
from ...types import AgentResult, C


class MacroAgent(BaseAgent):
    """Cross-asset macro analysis and regime detection."""

    def __init__(self, config=None):
        super().__init__("Macro", config)

    def run(self, context: Dict[str, Any]) -> AgentResult:
        action = context.get("action", "dashboard")
        loader = context.get("loader")

        if not loader:
            return self._result(success=False, errors=["No loader provided"])

        if action == "dashboard":
            return self._macro_dashboard(
                loader,
                context.get("start", ""),
                context.get("end", ""),
            )
        elif action == "risk_regime":
            return self._risk_regime(
                loader,
                context.get("start", ""),
                context.get("end", ""),
            )
        elif action == "yield_curve":
            return self._yield_curve(
                loader,
                context.get("start", ""),
                context.get("end", ""),
            )
        elif action == "rotation":
            return self._sector_rotation(
                loader,
                context.get("start", ""),
                context.get("end", ""),
                context.get("lookback_days", 60),
            )
        elif action == "cross_asset":
            return self._cross_asset_signals(
                loader,
                context.get("start", ""),
                context.get("end", ""),
            )
        else:
            return self._result(success=False, errors=[f"Unknown action: {action}"])

    def _macro_dashboard(self, loader, start: str, end: str) -> AgentResult:
        """High-level macro dashboard across asset classes."""
        # Key tickers for macro view
        macro_tickers = {
            "equities": ["SPY", "QQQ", "IWM", "DIA", "EFA", "FXI", "EWZ"],
            "bonds": ["TLT", "AGG", "BND"],
            "commodities": ["GLD", "SLV", "USO"],
            "crypto": ["BTC_USD", "ETH_USD"],
            "forex": ["EURUSD", "USDJPY", "GBPUSD"],
        }

        dashboard = {}
        for asset_class, tickers in macro_tickers.items():
            class_data = []
            for ticker in tickers:
                daily = loader.load_daily(ticker, start, end)
                if not daily or len(daily) < 20:
                    continue

                closes = [r["close"] for r in daily if r["close"] > 0]
                returns = [r["daily_return"] for r in daily if r["daily_return"] != 0]

                if len(closes) < 2:
                    continue

                # Recent performance
                last_20 = closes[-20:] if len(closes) >= 20 else closes
                ret_20d = (last_20[-1] / last_20[0] - 1) * 100 if len(last_20) > 1 else 0

                last_60 = closes[-60:] if len(closes) >= 60 else closes
                ret_60d = (last_60[-1] / last_60[0] - 1) * 100 if len(last_60) > 1 else 0

                total_ret = (closes[-1] / closes[0] - 1) * 100

                class_data.append({
                    "ticker": ticker,
                    "last_close": round(closes[-1], 2),
                    "return_20d_pct": round(ret_20d, 2),
                    "return_60d_pct": round(ret_60d, 2),
                    "total_return_pct": round(total_ret, 2),
                    "volatility_pct": round(
                        self._std(returns) * math.sqrt(252) * 100, 2
                    ) if len(returns) > 1 else 0,
                    "days": len(daily),
                })

            if class_data:
                dashboard[asset_class] = class_data

        return self._result(
            success=True,
            data={"dashboard": dashboard},
        )

    def _risk_regime(self, loader, start: str, end: str) -> AgentResult:
        """Detect risk-on / risk-off regimes from cross-asset signals."""
        # Load key indicators
        spy_data = loader.load_daily("SPY", start, end)
        tlt_data = loader.load_daily("TLT", start, end)
        gld_data = loader.load_daily("GLD", start, end)
        vix_data = loader.load_vix(start, end)

        if not spy_data:
            return self._result(success=False, errors=["No SPY data"])

        # Build daily risk regime
        spy_by_date = {r["date"]: r for r in spy_data}
        tlt_by_date = {r["date"]: r for r in tlt_data} if tlt_data else {}
        gld_by_date = {r["date"]: r for r in gld_data} if gld_data else {}

        regimes = []
        for r in spy_data:
            dt = r["date"]
            vix = vix_data.get(dt, 0)
            tlt = tlt_by_date.get(dt)
            gld = gld_by_date.get(dt)

            # Risk score: higher = more risk-on
            risk_score = 0

            # SPY return positive = risk-on
            if r["daily_return"] > 0:
                risk_score += 1
            elif r["daily_return"] < -0.01:
                risk_score -= 2

            # VIX < 18 = risk-on, > 25 = risk-off
            if vix > 0:
                if vix < 18:
                    risk_score += 1
                elif vix > 25:
                    risk_score -= 2
                elif vix > 20:
                    risk_score -= 1

            # TLT down (bonds selling off) = risk-on (money into equities)
            if tlt and tlt["daily_return"] < -0.003:
                risk_score += 1
            elif tlt and tlt["daily_return"] > 0.005:
                risk_score -= 1

            # Gold up = risk-off (flight to safety)
            if gld and gld["daily_return"] > 0.005:
                risk_score -= 1

            # SPY above SMA200 = risk-on
            sma200 = r.get("sma_200", 0)
            if sma200 > 0:
                if r["close"] > sma200:
                    risk_score += 1
                else:
                    risk_score -= 1

            if risk_score >= 2:
                regime = "RISK_ON"
            elif risk_score <= -2:
                regime = "RISK_OFF"
            else:
                regime = "NEUTRAL"

            regimes.append({
                "date": dt,
                "regime": regime,
                "risk_score": risk_score,
                "vix": vix,
            })

        # Stats per regime
        regime_stats = defaultdict(list)
        for i, r in enumerate(regimes):
            if i + 1 < len(spy_data):
                fwd = spy_data[i + 1]["daily_return"]
                regime_stats[r["regime"]].append(fwd)

        summary = {}
        for regime, fwd_rets in regime_stats.items():
            summary[regime] = {
                "days": len(fwd_rets),
                "pct": round(len(fwd_rets) / len(regimes) * 100, 1),
                "avg_fwd_1d_pct": round(self._mean(fwd_rets) * 100, 4),
                "win_rate": round(
                    sum(1 for r in fwd_rets if r > 0) / len(fwd_rets) * 100, 1
                ) if fwd_rets else 0,
            }

        # Current regime
        current = regimes[-1] if regimes else {"regime": "UNKNOWN", "risk_score": 0}

        return self._result(
            success=True,
            data={
                "current_regime": current["regime"],
                "current_score": current["risk_score"],
                "regime_stats": summary,
                "total_days": len(regimes),
            },
        )

    def _yield_curve(self, loader, start: str, end: str) -> AgentResult:
        """Yield curve analysis from bond yield data."""
        # Load yield data
        yields = {}
        yield_tickers = {
            "3m": "YIELD_IRX",   # 13-week T-bill
            "5y": "YIELD_FVX",   # 5-year Treasury
            "10y": "YIELD_TNX",  # 10-year Treasury
            "30y": "YIELD_TYX",  # 30-year Treasury
        }

        for label, ticker in yield_tickers.items():
            data = loader.load_daily(ticker, start, end)
            if data:
                yields[label] = {r["date"]: r["close"] for r in data}

        if not yields:
            return self._result(success=False,
                                errors=["No yield data found"])

        # Find common dates
        all_date_sets = [set(v.keys()) for v in yields.values()]
        common_dates = sorted(set.intersection(*all_date_sets)) if all_date_sets else []

        if not common_dates:
            return self._result(success=False,
                                errors=["No overlapping yield dates"])

        # Compute spreads
        spreads = []
        for dt in common_dates:
            entry = {"date": dt}
            for label in yields:
                entry[label] = yields[label][dt]

            # 10y-3m spread (classic inversion indicator)
            if "10y" in entry and "3m" in entry:
                entry["spread_10y_3m"] = round(entry["10y"] - entry["3m"], 4)

            # 30y-5y spread
            if "30y" in entry and "5y" in entry:
                entry["spread_30y_5y"] = round(entry["30y"] - entry["5y"], 4)

            spreads.append(entry)

        # Inversion analysis
        inverted_days = [s for s in spreads
                         if s.get("spread_10y_3m", 1) < 0]

        # Current state
        current = spreads[-1] if spreads else {}

        return self._result(
            success=True,
            data={
                "current": current,
                "total_days": len(spreads),
                "inverted_days": len(inverted_days),
                "inversion_pct": round(
                    len(inverted_days) / len(spreads) * 100, 1
                ) if spreads else 0,
                "available_tenors": list(yields.keys()),
                "period": {
                    "start": common_dates[0],
                    "end": common_dates[-1],
                },
            },
        )

    def _sector_rotation(self, loader, start: str, end: str,
                         lookback_days: int) -> AgentResult:
        """Sector/region rotation analysis."""
        # ETFs representing different sectors/regions
        rotation_tickers = {
            "US Large Cap": "SPY",
            "US Tech": "QQQ",
            "US Small Cap": "IWM",
            "Europe": "EFA",
            "Japan": "EWJ",
            "China": "FXI",
            "Brazil": "EWZ",
            "Gold": "GLD",
            "Bonds": "TLT",
        }

        performances = []
        for label, ticker in rotation_tickers.items():
            daily = loader.load_daily(ticker, start, end)
            if not daily or len(daily) < lookback_days:
                continue

            # Recent lookback period
            recent = daily[-lookback_days:]
            closes = [r["close"] for r in recent if r["close"] > 0]

            if len(closes) < 2:
                continue

            ret = (closes[-1] / closes[0] - 1) * 100
            returns = [r["daily_return"] for r in recent if r["daily_return"] != 0]

            performances.append({
                "label": label,
                "ticker": ticker,
                "return_pct": round(ret, 2),
                "volatility_pct": round(
                    self._std(returns) * math.sqrt(252) * 100, 2
                ) if len(returns) > 1 else 0,
                "sharpe": round(self._annualized_sharpe(returns), 3),
            })

        performances.sort(key=lambda x: x["return_pct"], reverse=True)

        # Leadership: top performer vs bottom
        leadership = ""
        if len(performances) >= 2:
            top = performances[0]
            bottom = performances[-1]
            leadership = (
                f"{top['label']} ({top['ticker']}) leads at "
                f"{top['return_pct']:+.2f}%, "
                f"{bottom['label']} ({bottom['ticker']}) lags at "
                f"{bottom['return_pct']:+.2f}%"
            )

        return self._result(
            success=True,
            data={
                "lookback_days": lookback_days,
                "performances": performances,
                "leadership": leadership,
            },
        )

    def _cross_asset_signals(self, loader, start: str,
                             end: str) -> AgentResult:
        """Cross-asset divergence/convergence signals."""
        pairs = [
            ("SPY", "TLT", "Equity/Bond"),
            ("SPY", "GLD", "Equity/Gold"),
            ("GLD", "SLV", "Gold/Silver ratio"),
            ("QQQ", "IWM", "Growth/Value proxy"),
            ("SPY", "EFA", "US/International"),
        ]

        signals = []
        for t1, t2, label in pairs:
            d1 = loader.load_daily(t1, start, end)
            d2 = loader.load_daily(t2, start, end)

            if not d1 or not d2:
                continue

            # Build ratio
            by_date_1 = {r["date"]: r["close"] for r in d1 if r["close"] > 0}
            by_date_2 = {r["date"]: r["close"] for r in d2 if r["close"] > 0}
            common = sorted(set(by_date_1) & set(by_date_2))

            if len(common) < 60:
                continue

            ratios = [by_date_1[d] / by_date_2[d] for d in common]

            # Current ratio vs 60-day average
            recent_ratio = ratios[-1]
            avg_60 = self._mean(ratios[-60:])
            std_60 = self._std(ratios[-60:]) if len(ratios) >= 60 else 0

            z_score = (recent_ratio - avg_60) / std_60 if std_60 > 0 else 0

            # Correlation of returns
            rets_1 = []
            rets_2 = []
            for i in range(1, len(common)):
                r1 = (by_date_1[common[i]] - by_date_1[common[i-1]]) / by_date_1[common[i-1]]
                r2 = (by_date_2[common[i]] - by_date_2[common[i-1]]) / by_date_2[common[i-1]]
                rets_1.append(r1)
                rets_2.append(r2)

            # Recent 60d correlation vs full correlation
            full_corr = self._pearson(rets_1, rets_2)
            recent_corr = self._pearson(rets_1[-60:], rets_2[-60:]) if len(rets_1) >= 60 else full_corr

            signal = "NORMAL"
            if abs(z_score) > 2:
                signal = "EXTREME_DIVERGENCE"
            elif abs(z_score) > 1.5:
                signal = "DIVERGENCE"
            elif abs(recent_corr - full_corr) > 0.3:
                signal = "CORRELATION_BREAK"

            signals.append({
                "pair": label,
                "tickers": f"{t1}/{t2}",
                "ratio": round(recent_ratio, 4),
                "ratio_z_score": round(z_score, 2),
                "full_correlation": round(full_corr, 4),
                "recent_correlation": round(recent_corr, 4),
                "signal": signal,
            })

        return self._result(
            success=True,
            data={"signals": signals},
        )

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
    def _annualized_sharpe(daily_returns: List[float]) -> float:
        if len(daily_returns) < 20:
            return 0.0
        m = sum(daily_returns) / len(daily_returns)
        var = sum((r - m) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
        std = math.sqrt(var) if var > 0 else 0
        if std == 0:
            return 0.0
        return (m / std) * math.sqrt(252)

    def print_dashboard(self, result: AgentResult) -> None:
        d = result.data
        dashboard = d.get("dashboard", {})

        print(f"\n{C.BOLD}{'='*74}")
        print(f"  MACRO DASHBOARD")
        print(f"{'='*74}{C.RESET}")

        for asset_class, tickers in dashboard.items():
            print(f"\n  {C.CYAN}{asset_class.upper()}{C.RESET}")
            print(f"  {'Ticker':<12} {'Last':>10} {'20d%':>8} {'60d%':>8}"
                  f" {'Total%':>9} {'Vol%':>7}")
            print(f"  {'-'*54}")
            for t in tickers:
                ret_clr = C.GREEN if t["return_20d_pct"] > 0 else C.RED
                print(f"  {t['ticker']:<12}"
                      f" {t['last_close']:>10,.2f}"
                      f" {ret_clr}{t['return_20d_pct']:>+7.2f}%{C.RESET}"
                      f" {t['return_60d_pct']:>+7.2f}%"
                      f" {t['total_return_pct']:>+8.2f}%"
                      f" {t['volatility_pct']:>6.2f}%")
        print()

    def print_risk_regime(self, result: AgentResult) -> None:
        d = result.data
        regime = d.get("current_regime", "?")
        regime_clr = (C.GREEN if regime == "RISK_ON"
                      else C.RED if regime == "RISK_OFF"
                      else C.YELLOW)

        print(f"\n{C.BOLD}{'='*74}")
        print(f"  RISK REGIME ANALYSIS")
        print(f"{'='*74}{C.RESET}")
        print(f"  Current: {regime_clr}{regime}{C.RESET}"
              f" (score: {d.get('current_score', 0)})")
        print(f"  Total days: {d.get('total_days', 0):,}")

        stats = d.get("regime_stats", {})
        print(f"\n  {'Regime':<12} {'Days':>6} {'%':>6} {'Avg1d%':>8} {'Win%':>6}")
        print(f"  {'-'*38}")
        for regime, s in stats.items():
            clr = (C.GREEN if regime == "RISK_ON"
                   else C.RED if regime == "RISK_OFF"
                   else C.YELLOW)
            print(f"  {clr}{regime:<12}{C.RESET}"
                  f" {s['days']:>6} {s['pct']:>5.1f}%"
                  f" {s['avg_fwd_1d_pct']:>+7.4f}%"
                  f" {s['win_rate']:>5.1f}%")
        print()
