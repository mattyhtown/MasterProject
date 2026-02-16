"""
StrategyDevAgent — develops and validates new trading strategies from data.

Combines pattern analysis, regime classification, and signal research to:
  - Propose new signal combinations
  - Backtest candidate strategies
  - Compare against baselines
  - Report statistical significance
"""

import math
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from ..base import BaseAgent
from ...types import AgentResult, C


class StrategyDevAgent(BaseAgent):
    """Develops and validates new trading strategies."""

    def __init__(self, config=None):
        super().__init__("StrategyDev", config)

    def run(self, context: Dict[str, Any]) -> AgentResult:
        action = context.get("action", "scan_strategies")
        loader = context.get("loader")

        if not loader:
            return self._result(success=False, errors=["No loader provided"])

        if action == "scan_strategies":
            return self._scan_strategies(
                loader,
                context.get("ticker", "SPY"),
                context.get("start", ""),
                context.get("end", ""),
            )
        elif action == "test_strategy":
            return self._test_strategy(
                loader,
                context.get("strategy", {}),
                context.get("ticker", "SPY"),
                context.get("start", ""),
                context.get("end", ""),
            )
        elif action == "compare":
            return self._compare_strategies(
                loader,
                context.get("strategies", []),
                context.get("ticker", "SPY"),
                context.get("start", ""),
                context.get("end", ""),
            )
        elif action == "multi_asset":
            return self._multi_asset_strategy(
                loader,
                context.get("tickers", ["SPY", "QQQ", "IWM"]),
                context.get("start", ""),
                context.get("end", ""),
            )
        else:
            return self._result(success=False, errors=[f"Unknown action: {action}"])

    def _scan_strategies(self, loader, ticker: str,
                         start: str, end: str) -> AgentResult:
        """Scan for effective signal combinations on a ticker."""
        daily = loader.load_daily(ticker, start, end)
        vix = loader.load_vix(start, end)

        if not daily or len(daily) < 120:
            return self._result(success=False,
                                errors=[f"Need 120+ days of data for {ticker}"])

        # Define candidate signals
        signal_funcs = {
            "rsi_oversold": lambda r, v: r.get("rsi", 50) < 30 and r.get("rsi", 50) > 0,
            "rsi_overbought": lambda r, v: r.get("rsi", 50) > 70,
            "bb_below": lambda r, v: r.get("bb_position", 0.5) < 0,
            "bb_above": lambda r, v: r.get("bb_position", 0.5) > 1,
            "price_above_sma200": lambda r, v: r["close"] > r.get("sma_200", 0) > 0,
            "price_below_sma200": lambda r, v: r["close"] < r.get("sma_200", 0) if r.get("sma_200", 0) > 0 else False,
            "sma20_above_sma50": lambda r, v: r.get("sma_20", 0) > r.get("sma_50", 0) > 0,
            "high_vix": lambda r, v: v > 25,
            "low_vix": lambda r, v: 0 < v < 15,
            "macd_positive": lambda r, v: r.get("macd", 0) > r.get("macd_signal", 0) and r.get("macd", 0) != 0,
            "vol_expansion": lambda r, v: r.get("volatility_20d", 0) > r.get("volatility_50d", 0) * 1.5 if r.get("volatility_50d", 0) > 0 else False,
            "down_3d": lambda r, v: r.get("_down_streak", 0) >= 3,
        }

        # Pre-compute streaks
        streak = 0
        for r in daily:
            if r["daily_return"] < 0:
                streak += 1
            else:
                streak = 0
            r["_down_streak"] = streak

        # Test each individual signal
        single_results = {}
        for sig_name, sig_func in signal_funcs.items():
            entries = []
            for i, r in enumerate(daily):
                v = vix.get(r["date"], 0)
                if sig_func(r, v):
                    if i + 1 < len(daily):
                        fwd_1d = (daily[i+1]["close"] - r["close"]) / r["close"]
                        entries.append(fwd_1d)

            if len(entries) >= 10:
                wins = sum(1 for e in entries if e > 0)
                single_results[sig_name] = {
                    "count": len(entries),
                    "avg_pct": round(self._mean(entries) * 100, 4),
                    "win_rate": round(wins / len(entries) * 100, 1),
                    "sharpe": round(self._sharpe(entries), 3),
                }

        # Test pairwise combinations (top signals only)
        top_signals = sorted(
            single_results.items(),
            key=lambda x: x[1]["sharpe"],
            reverse=True
        )[:6]

        combo_results = {}
        sig_names = [s[0] for s in top_signals]

        for i, s1 in enumerate(sig_names):
            for s2 in sig_names[i+1:]:
                entries = []
                for idx, r in enumerate(daily):
                    v = vix.get(r["date"], 0)
                    if signal_funcs[s1](r, v) and signal_funcs[s2](r, v):
                        if idx + 1 < len(daily):
                            fwd = (daily[idx+1]["close"] - r["close"]) / r["close"]
                            entries.append(fwd)

                if len(entries) >= 5:
                    wins = sum(1 for e in entries if e > 0)
                    combo_results[f"{s1}+{s2}"] = {
                        "count": len(entries),
                        "avg_pct": round(self._mean(entries) * 100, 4),
                        "win_rate": round(wins / len(entries) * 100, 1),
                        "sharpe": round(self._sharpe(entries), 3),
                    }

        # Rank all strategies
        all_strats = {}
        for k, v in single_results.items():
            all_strats[k] = {**v, "type": "single"}
        for k, v in combo_results.items():
            all_strats[k] = {**v, "type": "combo"}

        ranked = sorted(
            all_strats.items(),
            key=lambda x: x[1]["sharpe"],
            reverse=True
        )

        # Baseline: buy and hold
        all_rets = [r["daily_return"] for r in daily if r["daily_return"] != 0]
        baseline = {
            "avg_pct": round(self._mean(all_rets) * 100, 4),
            "win_rate": round(
                sum(1 for r in all_rets if r > 0) / len(all_rets) * 100, 1
            ) if all_rets else 0,
            "sharpe": round(self._sharpe(all_rets), 3),
        }

        return self._result(
            success=True,
            data={
                "ticker": ticker,
                "total_days": len(daily),
                "baseline": baseline,
                "single_signals": single_results,
                "combo_signals": combo_results,
                "ranked": [{
                    "name": name,
                    **stats,
                } for name, stats in ranked[:15]],
            },
        )

    def _test_strategy(self, loader, strategy: Dict,
                       ticker: str, start: str, end: str) -> AgentResult:
        """Backtest a specific strategy definition."""
        daily = loader.load_daily(ticker, start, end)
        vix = loader.load_vix(start, end)

        if not daily:
            return self._result(success=False, errors=[f"No data for {ticker}"])

        rules = strategy.get("rules", {})
        hold_days = strategy.get("hold_days", 1)

        # Build signal function from rules
        entries = []
        for i, r in enumerate(daily):
            v = vix.get(r["date"], 0)
            triggered = True

            for rule, value in rules.items():
                if rule == "rsi_below" and (r.get("rsi", 50) >= value or r.get("rsi", 0) == 0):
                    triggered = False
                elif rule == "rsi_above" and r.get("rsi", 50) <= value:
                    triggered = False
                elif rule == "vix_above" and v <= value:
                    triggered = False
                elif rule == "vix_below" and v >= value:
                    triggered = False
                elif rule == "bb_below" and r.get("bb_position", 0.5) >= value:
                    triggered = False
                elif rule == "above_sma200" and value and r.get("sma_200", 0) > 0 and r["close"] <= r["sma_200"]:
                    triggered = False
                elif rule == "below_sma200" and value and r.get("sma_200", 0) > 0 and r["close"] >= r["sma_200"]:
                    triggered = False

            if triggered and i + hold_days < len(daily):
                fwd = (daily[i + hold_days]["close"] - r["close"]) / r["close"]
                entries.append({
                    "date": r["date"],
                    "entry_price": r["close"],
                    "exit_price": daily[i + hold_days]["close"],
                    "return_pct": round(fwd * 100, 4),
                })

        if not entries:
            return self._result(success=True, data={
                "ticker": ticker,
                "strategy": strategy,
                "trades": 0,
                "message": "No entries triggered",
            })

        returns = [e["return_pct"] / 100 for e in entries]
        wins = sum(1 for r in returns if r > 0)
        losses = len(returns) - wins

        # Equity curve
        equity = [1.0]
        for r in returns:
            equity.append(equity[-1] * (1 + r))

        # Max drawdown of equity curve
        peak = equity[0]
        max_dd = 0
        for e in equity:
            if e > peak:
                peak = e
            dd = (e - peak) / peak
            if dd < max_dd:
                max_dd = dd

        return self._result(
            success=True,
            data={
                "ticker": ticker,
                "strategy": strategy,
                "trades": len(entries),
                "wins": wins,
                "losses": losses,
                "win_rate": round(wins / len(entries) * 100, 1),
                "avg_return_pct": round(self._mean(returns) * 100, 4),
                "total_return_pct": round((equity[-1] - 1) * 100, 2),
                "sharpe": round(self._sharpe(returns), 3),
                "max_drawdown_pct": round(max_dd * 100, 2),
                "profit_factor": round(
                    sum(r for r in returns if r > 0) / abs(sum(r for r in returns if r < 0)), 2
                ) if any(r < 0 for r in returns) else float('inf'),
                "best_trade_pct": round(max(returns) * 100, 3),
                "worst_trade_pct": round(min(returns) * 100, 3),
                "recent_trades": entries[-5:],
            },
        )

    def _compare_strategies(self, loader, strategies: List[Dict],
                            ticker: str, start: str, end: str) -> AgentResult:
        """Compare multiple strategy definitions side-by-side."""
        results = []

        for strat in strategies:
            result = self._test_strategy(loader, strat, ticker, start, end)
            if result.success and result.data.get("trades", 0) > 0:
                results.append({
                    "name": strat.get("name", "unnamed"),
                    "trades": result.data["trades"],
                    "win_rate": result.data["win_rate"],
                    "avg_return_pct": result.data["avg_return_pct"],
                    "total_return_pct": result.data["total_return_pct"],
                    "sharpe": result.data["sharpe"],
                    "max_drawdown_pct": result.data["max_drawdown_pct"],
                    "profit_factor": result.data["profit_factor"],
                })

        results.sort(key=lambda x: x["sharpe"], reverse=True)

        return self._result(
            success=True,
            data={
                "ticker": ticker,
                "strategies": results,
                "best": results[0]["name"] if results else "",
            },
        )

    def _multi_asset_strategy(self, loader, tickers: List[str],
                              start: str, end: str) -> AgentResult:
        """Test a rotation/momentum strategy across multiple assets."""
        # Monthly rotation: buy top N by recent momentum
        data_by_ticker = {}
        for ticker in tickers:
            daily = loader.load_daily(ticker, start, end)
            if daily:
                data_by_ticker[ticker] = {r["date"]: r for r in daily}

        if len(data_by_ticker) < 2:
            return self._result(success=False,
                                errors=["Need at least 2 tickers with data"])

        # Get all unique months
        all_dates = set()
        for ticker_data in data_by_ticker.values():
            all_dates.update(ticker_data.keys())
        all_dates = sorted(all_dates)

        if len(all_dates) < 60:
            return self._result(success=False, errors=["Need 60+ days"])

        # Monthly rebalance: pick top ticker by 20-day momentum
        lookback = 20
        hold_period = 20
        trades = []

        i = lookback
        while i + hold_period < len(all_dates):
            entry_date = all_dates[i]
            exit_date = all_dates[min(i + hold_period, len(all_dates) - 1)]

            # Rank tickers by recent momentum
            rankings = []
            for ticker, dates_data in data_by_ticker.items():
                if entry_date in dates_data and all_dates[i - lookback] in dates_data:
                    entry_price = dates_data[entry_date]["close"]
                    lookback_price = dates_data[all_dates[i - lookback]]["close"]
                    if lookback_price > 0:
                        momentum = (entry_price / lookback_price - 1)
                        rankings.append((ticker, momentum, entry_price))

            if not rankings:
                i += hold_period
                continue

            rankings.sort(key=lambda x: x[1], reverse=True)
            best_ticker, momentum, entry_price = rankings[0]

            # Forward return
            if exit_date in data_by_ticker[best_ticker]:
                exit_price = data_by_ticker[best_ticker][exit_date]["close"]
                ret = (exit_price - entry_price) / entry_price

                trades.append({
                    "entry_date": entry_date,
                    "exit_date": exit_date,
                    "ticker": best_ticker,
                    "momentum": round(momentum * 100, 2),
                    "return_pct": round(ret * 100, 3),
                })

            i += hold_period

        if not trades:
            return self._result(success=True, data={
                "tickers": tickers,
                "trades": 0,
                "message": "No rotation trades generated",
            })

        returns = [t["return_pct"] / 100 for t in trades]
        wins = sum(1 for r in returns if r > 0)

        # Ticker frequency
        freq = defaultdict(int)
        for t in trades:
            freq[t["ticker"]] += 1

        return self._result(
            success=True,
            data={
                "tickers": tickers,
                "trades": len(trades),
                "wins": wins,
                "win_rate": round(wins / len(trades) * 100, 1),
                "avg_return_pct": round(self._mean(returns) * 100, 3),
                "total_return_pct": round(
                    (math.prod(1 + r for r in returns) - 1) * 100, 2
                ),
                "sharpe": round(self._sharpe(returns), 3),
                "ticker_frequency": dict(freq),
                "recent_trades": trades[-5:],
            },
        )

    @staticmethod
    def _mean(values: List[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    @staticmethod
    def _sharpe(returns: List[float]) -> float:
        if len(returns) < 2:
            return 0.0
        m = sum(returns) / len(returns)
        var = sum((r - m) ** 2 for r in returns) / (len(returns) - 1)
        std = math.sqrt(var) if var > 0 else 0
        return m / std if std > 0 else 0.0

    def print_scan(self, result: AgentResult) -> None:
        d = result.data
        print(f"\n{C.BOLD}{'='*74}")
        print(f"  STRATEGY SCAN: {d.get('ticker', '?')}")
        print(f"{'='*74}{C.RESET}")
        print(f"  Total days: {d.get('total_days', 0):,}")

        baseline = d.get("baseline", {})
        print(f"\n  {C.DIM}Baseline (buy & hold):{C.RESET}"
              f" avg {baseline.get('avg_pct', 0):+.4f}%"
              f" win {baseline.get('win_rate', 0):.1f}%"
              f" sharpe {baseline.get('sharpe', 0):.3f}")

        ranked = d.get("ranked", [])
        if ranked:
            print(f"\n  {'Strategy':<30} {'Type':>6} {'N':>5}"
                  f" {'Avg%':>8} {'Win%':>6} {'Sharpe':>7}")
            print(f"  {'-'*62}")
            for s in ranked:
                sh_clr = C.GREEN if s["sharpe"] > baseline.get("sharpe", 0) else C.YELLOW
                print(f"  {s['name']:<30} {s['type']:>6} {s['count']:>5}"
                      f" {s['avg_pct']:>+7.4f}%"
                      f" {s['win_rate']:>5.1f}%"
                      f" {sh_clr}{s['sharpe']:>7.3f}{C.RESET}")
        print()

    def print_test(self, result: AgentResult) -> None:
        d = result.data
        print(f"\n{C.BOLD}{'='*74}")
        print(f"  STRATEGY BACKTEST: {d.get('ticker', '?')}")
        print(f"{'='*74}{C.RESET}")
        print(f"  Trades: {d.get('trades', 0)}")
        print(f"  Win rate: {d.get('win_rate', 0):.1f}%"
              f" ({d.get('wins', 0)}W / {d.get('losses', 0)}L)")
        print(f"  Avg return: {d.get('avg_return_pct', 0):+.4f}%")
        print(f"  Total return: {d.get('total_return_pct', 0):+.2f}%")
        print(f"  Sharpe: {d.get('sharpe', 0):.3f}")
        print(f"  Max drawdown: {d.get('max_drawdown_pct', 0):.2f}%")
        print(f"  Profit factor: {d.get('profit_factor', 0):.2f}")
        print(f"  Best trade: {d.get('best_trade_pct', 0):+.3f}%")
        print(f"  Worst trade: {d.get('worst_trade_pct', 0):+.3f}%")
        print()
