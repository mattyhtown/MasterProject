"""
ExtendedBacktest — runs signal analysis over the full historical dataset.

Uses the HistoricalLoader to access CSV price data (SPY, SPX, VIX, HYG, TLT)
and computes available signals without requiring ORATS API.

Supports:
  - Extended signal history (24+ months)
  - Walk-forward validation (rolling train/test)
  - Per-regime performance breakdown
"""

import math
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from ..base import BaseAgent
from .regime_classifier import RegimeClassifier, Regime
from ...types import AgentResult, C


class ExtendedBacktest(BaseAgent):
    """Extended signal backtesting with historical price data."""

    def __init__(self, config=None):
        super().__init__("ExtendedBacktest", config)
        self.regime_classifier = RegimeClassifier()

    def run(self, context: Dict[str, Any]) -> AgentResult:
        action = context.get("action", "signal_history")
        loader = context.get("loader")

        if not loader:
            return self._result(success=False, errors=["No loader provided"])

        if action == "signal_history":
            return self._signal_history(loader, context.get("months", 24))
        elif action == "walk_forward":
            return self._walk_forward(
                loader,
                context.get("train_months", 12),
                context.get("test_months", 3),
                context.get("total_months", 24),
            )
        elif action == "regime":
            return self._regime_analysis(loader, context.get("months", 24))
        else:
            return self._result(success=False, errors=[f"Unknown action: {action}"])

    def _load_aligned_data(self, loader, months: int):
        """Load and align SPY, VIX, and credit spread data."""
        end = date.today().isoformat()
        start = (date.today() - timedelta(days=months * 30)).isoformat()

        spy = loader.load_daily("SPY", start, end)
        vix = loader.load_vix(start, end)
        credit = loader.load_credit_spread(start, end)

        return spy, vix, credit, start, end

    def _compute_signals(self, spy_data: List[Dict],
                         vix_data: Dict[str, float],
                         credit_data: List[Dict]) -> List[Dict]:
        """Compute available signals from price data.

        Signals computed:
          - credit_spread: HYG/TLT ratio drops > 0.5%
          - vix_spike: VIX > 20 and rising
          - vix_extreme: VIX > 30
          - rsi_oversold: RSI < 30
          - rsi_overbought: RSI > 70
          - price_below_sma200: bearish regime
          - vol_expansion: 20d vol > 50d vol by 50%+
          - bb_squeeze: price below lower Bollinger Band
        """
        credit_by_date = {c["date"]: c for c in credit_data}
        prev_vix = None

        signals = []
        for i, row in enumerate(spy_data):
            dt = row["date"]
            vix = vix_data.get(dt, 0)
            cs = credit_by_date.get(dt)

            day_signals = {}

            # Credit spread signal
            if cs and cs["spread_change"] < -0.005:
                day_signals["credit_spread"] = {
                    "fired": True,
                    "value": cs["spread_change"],
                    "level": "ACTION",
                }

            # VIX spike
            if vix > 20 and prev_vix and vix > prev_vix * 1.05:
                day_signals["vix_spike"] = {
                    "fired": True,
                    "value": vix,
                    "level": "ACTION",
                }

            # VIX extreme
            if vix > 30:
                day_signals["vix_extreme"] = {
                    "fired": True,
                    "value": vix,
                    "level": "ACTION",
                }

            # RSI oversold
            rsi = row.get("rsi", 50)
            if rsi > 0 and rsi < 30:
                day_signals["rsi_oversold"] = {
                    "fired": True,
                    "value": rsi,
                    "level": "ACTION",
                }

            # RSI overbought
            if rsi > 70:
                day_signals["rsi_overbought"] = {
                    "fired": True,
                    "value": rsi,
                    "level": "WATCH",
                }

            # Vol expansion
            v20 = row.get("volatility_20d", 0)
            v50 = row.get("volatility_50d", 0)
            if v20 > 0 and v50 > 0 and v20 > v50 * 1.5:
                day_signals["vol_expansion"] = {
                    "fired": True,
                    "value": round(v20 / v50, 2),
                    "level": "ACTION",
                }

            # BB squeeze (price below lower band)
            bb_pos = row.get("bb_position", 0.5)
            if bb_pos < 0:
                day_signals["bb_squeeze"] = {
                    "fired": True,
                    "value": bb_pos,
                    "level": "ACTION",
                }

            # Forward returns (for signal evaluation)
            fwd_1d = None
            if i + 1 < len(spy_data):
                fwd_1d = (spy_data[i + 1]["close"] - row["close"]) / row["close"]

            action_count = sum(
                1 for s in day_signals.values() if s.get("level") == "ACTION"
            )

            signals.append({
                "date": dt,
                "close": row["close"],
                "vix": vix,
                "signals": day_signals,
                "action_count": action_count,
                "fwd_1d_return": round(fwd_1d * 100, 4) if fwd_1d is not None else None,
            })

            prev_vix = vix

        return signals

    def _signal_history(self, loader, months: int) -> AgentResult:
        """Compute signals over full history and summarize."""
        spy, vix, credit, start, end = self._load_aligned_data(loader, months)
        if not spy:
            return self._result(success=False, errors=["No SPY data loaded"])

        signals = self._compute_signals(spy, vix, credit)

        # Signal days = at least 2 ACTION signals
        signal_days = [s for s in signals if s["action_count"] >= 2]
        strong_days = [s for s in signals if s["action_count"] >= 3]

        # Forward returns on signal days vs all days
        all_fwd = [s["fwd_1d_return"] for s in signals if s["fwd_1d_return"] is not None]
        sig_fwd = [s["fwd_1d_return"] for s in signal_days if s["fwd_1d_return"] is not None]
        strong_fwd = [s["fwd_1d_return"] for s in strong_days if s["fwd_1d_return"] is not None]

        # Per-signal stats
        signal_names = set()
        for s in signals:
            signal_names.update(s["signals"].keys())

        per_signal = {}
        for name in sorted(signal_names):
            fired_days = [s for s in signals if name in s["signals"]]
            fwd = [s["fwd_1d_return"] for s in fired_days if s["fwd_1d_return"] is not None]
            wins = sum(1 for r in fwd if r > 0)
            per_signal[name] = {
                "fired": len(fired_days),
                "avg_fwd_1d": round(self._mean(fwd), 4) if fwd else 0,
                "win_rate": round(wins / len(fwd) * 100, 1) if fwd else 0,
                "best": round(max(fwd), 3) if fwd else 0,
                "worst": round(min(fwd), 3) if fwd else 0,
            }

        return self._result(
            success=True,
            data={
                "period": {"start": start, "end": end},
                "total_days": len(signals),
                "signal_days": len(signal_days),
                "strong_days": len(strong_days),
                "all_days_avg_return": round(self._mean(all_fwd), 4) if all_fwd else 0,
                "signal_days_avg_return": round(self._mean(sig_fwd), 4) if sig_fwd else 0,
                "strong_days_avg_return": round(self._mean(strong_fwd), 4) if strong_fwd else 0,
                "signal_days_win_rate": round(
                    sum(1 for r in sig_fwd if r > 0) / len(sig_fwd) * 100, 1
                ) if sig_fwd else 0,
                "strong_days_win_rate": round(
                    sum(1 for r in strong_fwd if r > 0) / len(strong_fwd) * 100, 1
                ) if strong_fwd else 0,
                "per_signal": per_signal,
                "signal_day_dates": [s["date"] for s in strong_days],
            },
        )

    def _walk_forward(self, loader, train_months: int,
                      test_months: int, total_months: int) -> AgentResult:
        """Rolling train/test walk-forward validation."""
        spy, vix, credit, start, end = self._load_aligned_data(loader, total_months)
        if not spy:
            return self._result(success=False, errors=["No SPY data loaded"])

        signals = self._compute_signals(spy, vix, credit)

        # Build windows
        windows = []
        step_days = test_months * 30
        train_days = train_months * 30

        i = 0
        while i + train_days + step_days <= len(signals):
            train = signals[i:i + train_days]
            test = signals[i + train_days:i + train_days + step_days]

            # Optimize: find best signal count threshold on train
            best_thresh = 2
            best_sharpe = -999
            for thresh in [1, 2, 3]:
                t_fwd = [
                    s["fwd_1d_return"] for s in train
                    if s["action_count"] >= thresh and s["fwd_1d_return"] is not None
                ]
                if len(t_fwd) >= 5:
                    sharpe = self._sharpe(t_fwd)
                    if sharpe > best_sharpe:
                        best_sharpe = sharpe
                        best_thresh = thresh

            # Evaluate on test with optimized threshold
            train_sig = [
                s for s in train if s["action_count"] >= best_thresh
            ]
            test_sig = [
                s for s in test if s["action_count"] >= best_thresh
            ]

            train_fwd = [s["fwd_1d_return"] for s in train_sig if s["fwd_1d_return"] is not None]
            test_fwd = [s["fwd_1d_return"] for s in test_sig if s["fwd_1d_return"] is not None]

            windows.append({
                "train_start": train[0]["date"],
                "train_end": train[-1]["date"],
                "test_start": test[0]["date"],
                "test_end": test[-1]["date"],
                "threshold": best_thresh,
                "train_signals": len(train_sig),
                "test_signals": len(test_sig),
                "train_avg": round(self._mean(train_fwd), 4) if train_fwd else 0,
                "test_avg": round(self._mean(test_fwd), 4) if test_fwd else 0,
                "train_win_rate": round(
                    sum(1 for r in train_fwd if r > 0) / len(train_fwd) * 100, 1
                ) if train_fwd else 0,
                "test_win_rate": round(
                    sum(1 for r in test_fwd if r > 0) / len(test_fwd) * 100, 1
                ) if test_fwd else 0,
                "train_sharpe": round(self._sharpe(train_fwd), 3),
                "test_sharpe": round(self._sharpe(test_fwd), 3),
            })

            i += step_days

        # Stability metrics
        test_sharpes = [w["test_sharpe"] for w in windows]
        test_win_rates = [w["test_win_rate"] for w in windows]

        return self._result(
            success=True,
            data={
                "windows": windows,
                "window_count": len(windows),
                "avg_test_sharpe": round(self._mean(test_sharpes), 3) if test_sharpes else 0,
                "std_test_sharpe": round(self._std(test_sharpes), 3) if test_sharpes else 0,
                "avg_test_win_rate": round(self._mean(test_win_rates), 1) if test_win_rates else 0,
                "overfit_ratio": round(
                    self._mean([w["train_sharpe"] for w in windows]) /
                    max(self._mean(test_sharpes), 0.001), 2
                ) if test_sharpes else 0,
            },
        )

    def _regime_analysis(self, loader, months: int) -> AgentResult:
        """Delegate to RegimeClassifier with loaded data."""
        spy, vix, credit, start, end = self._load_aligned_data(loader, months)
        return self.regime_classifier.run({
            "action": "analyze",
            "daily_data": spy,
            "vix_data": vix,
            "credit_data": credit,
        })

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
    def _sharpe(returns: List[float]) -> float:
        if len(returns) < 2:
            return 0.0
        m = sum(returns) / len(returns)
        var = sum((r - m) ** 2 for r in returns) / (len(returns) - 1)
        std = math.sqrt(var) if var > 0 else 0
        return m / std if std > 0 else 0.0

    def print_report(self, result: AgentResult) -> None:
        d = result.data
        print(f"\n{C.BOLD}{'='*74}")
        print(f"  EXTENDED SIGNAL BACKTEST")
        print(f"{'='*74}{C.RESET}")

        period = d.get("period", {})
        print(f"  Period: {period.get('start', '?')} to {period.get('end', '?')}")
        print(f"  Total days: {d.get('total_days', 0)}")
        print(f"  Signal days (2+ signals): {d.get('signal_days', 0)}")
        print(f"  Strong days (3+ signals): {d.get('strong_days', 0)}")

        print(f"\n  {'Category':<25} {'Avg 1d%':>8} {'Win%':>7}")
        print(f"  {'-'*40}")
        print(f"  {'All days':<25} {d.get('all_days_avg_return', 0):>+7.4f}%")
        clr = C.GREEN if d.get("signal_days_win_rate", 0) > 50 else C.RED
        print(f"  {'Signal days (2+)':<25}"
              f" {d.get('signal_days_avg_return', 0):>+7.4f}%"
              f" {clr}{d.get('signal_days_win_rate', 0):>6.1f}%{C.RESET}")
        clr = C.GREEN if d.get("strong_days_win_rate", 0) > 50 else C.RED
        print(f"  {'Strong days (3+)':<25}"
              f" {d.get('strong_days_avg_return', 0):>+7.4f}%"
              f" {clr}{d.get('strong_days_win_rate', 0):>6.1f}%{C.RESET}")

        per_signal = d.get("per_signal", {})
        if per_signal:
            print(f"\n  {'Signal':<20} {'Fired':>6} {'Avg%':>8} {'Win%':>7}"
                  f" {'Best%':>7} {'Worst%':>8}")
            print(f"  {'-'*56}")
            for name, s in per_signal.items():
                clr = C.GREEN if s["win_rate"] > 50 else C.YELLOW
                print(f"  {name:<20} {s['fired']:>6}"
                      f" {s['avg_fwd_1d']:>+7.4f}%"
                      f" {clr}{s['win_rate']:>6.1f}%{C.RESET}"
                      f" {s['best']:>+6.3f}%"
                      f" {s['worst']:>+7.3f}%")

    def print_walk_forward(self, result: AgentResult) -> None:
        d = result.data
        print(f"\n{C.BOLD}{'='*74}")
        print(f"  WALK-FORWARD VALIDATION")
        print(f"{'='*74}{C.RESET}")
        print(f"  Windows: {d.get('window_count', 0)}")
        print(f"  Avg test Sharpe: {d.get('avg_test_sharpe', 0):.3f}"
              f" (std: {d.get('std_test_sharpe', 0):.3f})")
        print(f"  Avg test win rate: {d.get('avg_test_win_rate', 0):.1f}%")
        print(f"  Overfit ratio: {d.get('overfit_ratio', 0):.2f}"
              f" (>2.0 = likely overfit)")

        windows = d.get("windows", [])
        if windows:
            print(f"\n  {'Window':<6} {'Train':>12} {'Test':>12}"
                  f" {'Thr':>4} {'TSig':>5}"
                  f" {'TrSh':>6} {'TeSh':>6}"
                  f" {'TrW%':>6} {'TeW%':>6}")
            print(f"  {'-'*65}")
            for i, w in enumerate(windows):
                te_clr = C.GREEN if w["test_sharpe"] > 0 else C.RED
                print(f"  {i+1:<6}"
                      f" {w['train_end'][:7]:>12}"
                      f" {w['test_end'][:7]:>12}"
                      f" {w['threshold']:>4}"
                      f" {w['test_signals']:>5}"
                      f" {w['train_sharpe']:>6.3f}"
                      f" {te_clr}{w['test_sharpe']:>6.3f}{C.RESET}"
                      f" {w['train_win_rate']:>5.1f}%"
                      f" {w['test_win_rate']:>5.1f}%")
