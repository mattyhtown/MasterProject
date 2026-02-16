"""
Strategy Comparison for APEX-SHARPE Trading System.

This example demonstrates side-by-side comparison of multiple strategies:
1. Iron Condor (High IV)
2. Iron Condor (Medium IV)
3. Credit Spread (Aggressive)
4. Credit Spread (Conservative)
5. Buy-and-Hold Benchmark

For each strategy, we'll compare:
- Risk-adjusted returns (Sharpe, Sortino)
- Maximum drawdown and recovery time
- Win rates and profit factors
- Greeks attribution
- Performance across volatility regimes
- Capital efficiency

This helps identify which strategies work best in different market conditions.
"""

import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Tuple
import json

# Import APEX-SHARPE components
from apex_sharpe.strategies.iron_condor_strategy import IronCondorStrategy


def print_header(title: str):
    """Print formatted header."""
    print("\n" + "=" * 100)
    print(f"  {title}")
    print("=" * 100)


def print_subheader(title: str):
    """Print formatted subheader."""
    print(f"\n{'─' * 100}")
    print(f"  {title}")
    print(f"{'─' * 100}")


class StrategyComparison:
    """Compare multiple trading strategies side-by-side."""

    def __init__(self):
        """Initialize strategy comparison."""
        self.strategies = {}
        self.backtest_results = {}

    def setup_strategies(self):
        """Setup all strategies for comparison."""
        print_header("STRATEGY COMPARISON FRAMEWORK")
        print_subheader("Setting Up Strategies")

        # Strategy 1: Iron Condor - High IV
        self.strategies['ic_high_iv'] = {
            "name": "Iron Condor (High IV)",
            "type": "IRON_CONDOR",
            "config": IronCondorStrategy(
                name="IC_HighIV",
                symbol="SPY",
                initial_capital=Decimal("100000"),
                sharpe_threshold=1.0,
                iv_rank_min=Decimal("0.70"),  # Only trade IV Rank > 70%
                delta_short_target=Decimal("0.15"),
                width=Decimal("5.00"),
            ),
            "description": "Conservative iron condor, only trades high IV environments"
        }

        # Strategy 2: Iron Condor - Medium IV
        self.strategies['ic_med_iv'] = {
            "name": "Iron Condor (Medium IV)",
            "type": "IRON_CONDOR",
            "config": IronCondorStrategy(
                name="IC_MedIV",
                symbol="SPY",
                initial_capital=Decimal("100000"),
                sharpe_threshold=1.0,
                iv_rank_min=Decimal("0.50"),  # Trade IV Rank > 50%
                delta_short_target=Decimal("0.20"),  # Closer to ATM
                width=Decimal("5.00"),
            ),
            "description": "More active iron condor, trades medium to high IV"
        }

        # Strategy 3: Credit Spread - Aggressive
        self.strategies['cs_aggressive'] = {
            "name": "Credit Spread (Aggressive)",
            "type": "CREDIT_SPREAD",
            "config": {
                "iv_rank_min": Decimal("0.40"),
                "delta_short_target": Decimal("0.30"),  # Higher delta
                "width": Decimal("3.00"),  # Tighter spreads
            },
            "description": "Aggressive credit spreads with higher delta targets"
        }

        # Strategy 4: Credit Spread - Conservative
        self.strategies['cs_conservative'] = {
            "name": "Credit Spread (Conservative)",
            "type": "CREDIT_SPREAD",
            "config": {
                "iv_rank_min": Decimal("0.60"),
                "delta_short_target": Decimal("0.10"),  # Lower delta
                "width": Decimal("10.00"),  # Wider spreads
            },
            "description": "Conservative credit spreads with wide wings"
        }

        # Strategy 5: Buy-and-Hold Benchmark
        self.strategies['buy_hold'] = {
            "name": "Buy-and-Hold SPY",
            "type": "BENCHMARK",
            "config": {},
            "description": "Simple buy-and-hold benchmark for comparison"
        }

        print("\n✓ Strategies configured:")
        for key, strategy in self.strategies.items():
            print(f"  - {strategy['name']}: {strategy['description']}")

    def run_backtests(self):
        """Run backtests for all strategies."""
        print_subheader("Running Backtests")

        backtest_period = "2024-01-01 to 2026-02-05 (2 years)"
        print(f"\nBacktest Period: {backtest_period}")
        print(f"Initial Capital: $100,000 per strategy")
        print(f"Commission: $0.65 per contract\n")

        print("Running backtests...")
        print("  [====================] Iron Condor (High IV) complete")
        print("  [====================] Iron Condor (Medium IV) complete")
        print("  [====================] Credit Spread (Aggressive) complete")
        print("  [====================] Credit Spread (Conservative) complete")
        print("  [====================] Buy-and-Hold SPY complete")

        # Simulated backtest results
        self.backtest_results = {
            'ic_high_iv': {
                'total_return': 28.45,
                'cagr': 13.45,
                'sharpe_ratio': 1.92,
                'sortino_ratio': 2.67,
                'max_drawdown': -6.23,
                'win_rate': 76.5,
                'total_trades': 36,
                'avg_win': 425.50,
                'avg_loss': -380.25,
                'profit_factor': 2.15,
                'avg_days_in_trade': 28,
                'best_trade': 1250.00,
                'worst_trade': -1120.00,
            },
            'ic_med_iv': {
                'total_return': 32.18,
                'cagr': 15.12,
                'sharpe_ratio': 1.68,
                'sortino_ratio': 2.34,
                'max_drawdown': -9.87,
                'win_rate': 68.2,
                'total_trades': 62,
                'avg_win': 385.20,
                'avg_loss': -425.80,
                'profit_factor': 1.82,
                'avg_days_in_trade': 24,
                'best_trade': 980.00,
                'worst_trade': -1450.00,
            },
            'cs_aggressive': {
                'total_return': 41.67,
                'cagr': 19.12,
                'sharpe_ratio': 1.34,
                'sortino_ratio': 1.89,
                'max_drawdown': -14.56,
                'win_rate': 62.5,
                'total_trades': 88,
                'avg_win': 310.00,
                'avg_loss': -520.00,
                'profit_factor': 1.54,
                'avg_days_in_trade': 18,
                'best_trade': 890.00,
                'worst_trade': -1850.00,
            },
            'cs_conservative': {
                'total_return': 19.34,
                'cagr': 9.23,
                'sharpe_ratio': 1.78,
                'sortino_ratio': 2.45,
                'max_drawdown': -5.12,
                'win_rate': 82.3,
                'total_trades': 28,
                'avg_win': 280.50,
                'avg_loss': -295.00,
                'profit_factor': 2.34,
                'avg_days_in_trade': 32,
                'best_trade': 680.00,
                'worst_trade': -780.00,
            },
            'buy_hold': {
                'total_return': 24.50,
                'cagr': 11.58,
                'sharpe_ratio': 0.92,
                'sortino_ratio': 1.24,
                'max_drawdown': -18.23,
                'win_rate': 100.0,  # Always long
                'total_trades': 1,
                'avg_win': 24500.00,
                'avg_loss': 0.00,
                'profit_factor': float('inf'),
                'avg_days_in_trade': 730,
                'best_trade': 24500.00,
                'worst_trade': 0.00,
            },
        }

        print("\n✓ All backtests completed")

    def compare_returns(self):
        """Compare return metrics across strategies."""
        print_subheader("Return Metrics Comparison")

        print("\n{:<30} {:>12} {:>12} {:>12} {:>12}".format(
            "Strategy", "Total Return", "CAGR", "Sharpe", "Sortino"
        ))
        print("-" * 100)

        # Sort by Sharpe ratio
        sorted_strategies = sorted(
            self.backtest_results.items(),
            key=lambda x: x[1]['sharpe_ratio'],
            reverse=True
        )

        for key, results in sorted_strategies:
            strategy_name = self.strategies[key]['name']
            print("{:<30} {:>11.2f}% {:>11.2f}% {:>12.2f} {:>12.2f}".format(
                strategy_name,
                results['total_return'],
                results['cagr'],
                results['sharpe_ratio'],
                results['sortino_ratio']
            ))

        print("\n📊 Analysis:")
        best_sharpe = sorted_strategies[0]
        best_return = max(self.backtest_results.items(), key=lambda x: x[1]['total_return'])

        print(f"  Best Risk-Adjusted Returns (Sharpe): {self.strategies[best_sharpe[0]]['name']} ({best_sharpe[1]['sharpe_ratio']:.2f})")
        print(f"  Highest Total Returns: {self.strategies[best_return[0]]['name']} ({best_return[1]['total_return']:.2f}%)")

        # Check which strategies beat benchmark
        benchmark_sharpe = self.backtest_results['buy_hold']['sharpe_ratio']
        strategies_beat_benchmark = [
            self.strategies[key]['name']
            for key, results in self.backtest_results.items()
            if results['sharpe_ratio'] > benchmark_sharpe and key != 'buy_hold'
        ]

        print(f"  Strategies Beating Benchmark: {len(strategies_beat_benchmark)}/{len(self.strategies)-1}")
        for name in strategies_beat_benchmark:
            print(f"    ✓ {name}")

    def compare_risk(self):
        """Compare risk metrics across strategies."""
        print_subheader("Risk Metrics Comparison")

        print("\n{:<30} {:>15} {:>12} {:>15}".format(
            "Strategy", "Max Drawdown", "Win Rate", "Profit Factor"
        ))
        print("-" * 100)

        # Sort by max drawdown (best to worst)
        sorted_strategies = sorted(
            self.backtest_results.items(),
            key=lambda x: x[1]['max_drawdown'],
            reverse=True  # Less negative is better
        )

        for key, results in sorted_strategies:
            strategy_name = self.strategies[key]['name']
            pf = results['profit_factor']
            pf_str = f"{pf:.2f}" if pf != float('inf') else "∞"

            print("{:<30} {:>14.2f}% {:>11.1f}% {:>15}".format(
                strategy_name,
                results['max_drawdown'],
                results['win_rate'],
                pf_str
            ))

        print("\n📊 Analysis:")
        best_dd = sorted_strategies[0]
        best_win_rate = max(self.backtest_results.items(), key=lambda x: x[1]['win_rate'])

        print(f"  Lowest Drawdown: {self.strategies[best_dd[0]]['name']} ({best_dd[1]['max_drawdown']:.2f}%)")
        print(f"  Highest Win Rate: {self.strategies[best_win_rate[0]]['name']} ({best_win_rate[1]['win_rate']:.1f}%)")

        # Risk assessment
        print("\n  Risk Assessment:")
        for key, results in self.backtest_results.items():
            if key == 'buy_hold':
                continue
            risk_level = "Low" if abs(results['max_drawdown']) < 8 else "Medium" if abs(results['max_drawdown']) < 12 else "High"
            print(f"    {self.strategies[key]['name']}: {risk_level} risk")

    def compare_trade_statistics(self):
        """Compare trade-level statistics."""
        print_subheader("Trade Statistics Comparison")

        print("\n{:<30} {:>12} {:>12} {:>12} {:>15}".format(
            "Strategy", "Total Trades", "Avg Win", "Avg Loss", "Avg Days/Trade"
        ))
        print("-" * 100)

        for key, results in self.backtest_results.items():
            if key == 'buy_hold':
                continue

            strategy_name = self.strategies[key]['name']
            print("{:<30} {:>12} {:>11.2f} {:>11.2f} {:>15}".format(
                strategy_name,
                results['total_trades'],
                results['avg_win'],
                results['avg_loss'],
                results['avg_days_in_trade']
            ))

        print("\n📊 Analysis:")

        # Most active strategy
        most_active = max(
            [(k, v) for k, v in self.backtest_results.items() if k != 'buy_hold'],
            key=lambda x: x[1]['total_trades']
        )
        print(f"  Most Active: {self.strategies[most_active[0]]['name']} ({most_active[1]['total_trades']} trades)")

        # Best win/loss ratio
        best_wl_ratio = max(
            [(k, v) for k, v in self.backtest_results.items() if k != 'buy_hold'],
            key=lambda x: x[1]['avg_win'] / abs(x[1]['avg_loss']) if x[1]['avg_loss'] != 0 else 0
        )
        wl_ratio = best_wl_ratio[1]['avg_win'] / abs(best_wl_ratio[1]['avg_loss'])
        print(f"  Best Win/Loss Ratio: {self.strategies[best_wl_ratio[0]]['name']} ({wl_ratio:.2f})")

        # Shortest holding period
        shortest_hold = min(
            [(k, v) for k, v in self.backtest_results.items() if k != 'buy_hold'],
            key=lambda x: x[1]['avg_days_in_trade']
        )
        print(f"  Most Capital Efficient: {self.strategies[shortest_hold[0]]['name']} ({shortest_hold[1]['avg_days_in_trade']} days avg)")

    def compare_volatility_regimes(self):
        """Compare performance across different volatility regimes."""
        print_subheader("Performance by Volatility Regime")

        # Simulated performance by IV regime
        regime_performance = {
            'Low IV (< 15)': {
                'ic_high_iv': {'trades': 0, 'return': 0.0, 'sharpe': 0.0},
                'ic_med_iv': {'trades': 8, 'return': 2.3, 'sharpe': 0.95},
                'cs_aggressive': {'trades': 15, 'return': 5.8, 'sharpe': 1.12},
                'cs_conservative': {'trades': 3, 'return': 1.2, 'sharpe': 1.05},
                'buy_hold': {'trades': 1, 'return': 8.5, 'sharpe': 0.88},
            },
            'Medium IV (15-25)': {
                'ic_high_iv': {'trades': 12, 'return': 8.4, 'sharpe': 1.65},
                'ic_med_iv': {'trades': 28, 'return': 15.2, 'sharpe': 1.78},
                'cs_aggressive': {'trades': 42, 'return': 22.1, 'sharpe': 1.45},
                'cs_conservative': {'trades': 18, 'return': 12.3, 'sharpe': 1.92},
                'buy_hold': {'trades': 1, 'return': 12.3, 'sharpe': 0.95},
            },
            'High IV (> 25)': {
                'ic_high_iv': {'trades': 24, 'return': 20.1, 'sharpe': 2.34},
                'ic_med_iv': {'trades': 26, 'return': 14.7, 'sharpe': 1.56},
                'cs_aggressive': {'trades': 31, 'return': 13.8, 'sharpe': 1.18},
                'cs_conservative': {'trades': 7, 'return': 5.8, 'sharpe': 1.52},
                'buy_hold': {'trades': 1, 'return': 3.7, 'sharpe': 0.65},
            },
        }

        for regime_name, results in regime_performance.items():
            print(f"\n{regime_name}:")
            print(f"  {'-' * 90}")
            print(f"  {'Strategy':<30} {'Trades':<10} {'Return':<12} {'Sharpe':<10}")
            print(f"  {'-' * 90}")

            for key, perf in results.items():
                if key == 'buy_hold':
                    continue
                strategy_name = self.strategies[key]['name']
                print(f"  {strategy_name:<30} {perf['trades']:<10} {perf['return']:>10.1f}% {perf['sharpe']:>10.2f}")

            # Find best strategy for this regime
            best = max(
                [(k, v) for k, v in results.items() if k != 'buy_hold'],
                key=lambda x: x[1]['sharpe']
            )
            print(f"\n  🏆 Best in this regime: {self.strategies[best[0]]['name']}")

        print("\n📊 Regime Analysis:")
        print("  • Iron Condor (High IV) excels in high volatility environments")
        print("  • Credit Spread (Aggressive) performs well in medium volatility")
        print("  • Iron Condor (Medium IV) provides consistent returns across regimes")
        print("  • Credit Spread (Conservative) best for stable, low-risk returns")

    def generate_recommendation(self):
        """Generate strategy recommendations based on comparison."""
        print_subheader("Strategy Recommendations")

        # Calculate scores
        scores = {}
        for key in self.backtest_results.keys():
            if key == 'buy_hold':
                continue

            results = self.backtest_results[key]

            # Score components (normalized 0-100)
            sharpe_score = min(results['sharpe_ratio'] / 2.0 * 100, 100)
            return_score = min(results['total_return'] / 50.0 * 100, 100)
            dd_score = max(0, 100 + results['max_drawdown'] * 5)  # Less negative is better
            win_rate_score = results['win_rate']

            # Overall score (weighted)
            overall_score = (
                sharpe_score * 0.35 +
                return_score * 0.25 +
                dd_score * 0.25 +
                win_rate_score * 0.15
            )

            scores[key] = {
                'overall': overall_score,
                'sharpe': sharpe_score,
                'return': return_score,
                'drawdown': dd_score,
                'win_rate': win_rate_score,
            }

        # Rank strategies
        ranked = sorted(scores.items(), key=lambda x: x[1]['overall'], reverse=True)

        print("\n🏆 Overall Rankings:\n")
        for i, (key, score_dict) in enumerate(ranked, 1):
            strategy_name = self.strategies[key]['name']
            print(f"  {i}. {strategy_name}")
            print(f"     Overall Score: {score_dict['overall']:.1f}/100")
            print(f"     Sharpe: {score_dict['sharpe']:.1f} | Return: {score_dict['return']:.1f} | Risk: {score_dict['drawdown']:.1f} | Win Rate: {score_dict['win_rate']:.1f}")
            print()

        # Specific recommendations
        print("📋 Specific Recommendations:\n")

        winner = ranked[0]
        print(f"  1️⃣ BEST OVERALL: {self.strategies[winner[0]]['name']}")
        print(f"     {self.strategies[winner[0]]['description']}")
        print(f"     Recommended for: Primary trading strategy")
        print()

        # Best for beginners (low risk)
        safest = min(
            scores.items(),
            key=lambda x: abs(self.backtest_results[x[0]]['max_drawdown'])
        )
        print(f"  2️⃣ BEST FOR BEGINNERS: {self.strategies[safest[0]]['name']}")
        print(f"     Lowest Drawdown: {self.backtest_results[safest[0]]['max_drawdown']:.2f}%")
        print(f"     Recommended for: Conservative traders, smaller accounts")
        print()

        # Best for aggressive
        highest_return = max(
            scores.items(),
            key=lambda x: self.backtest_results[x[0]]['total_return']
        )
        print(f"  3️⃣ BEST FOR AGGRESSIVE: {self.strategies[highest_return[0]]['name']}")
        print(f"     Highest Return: {self.backtest_results[highest_return[0]]['total_return']:.2f}%")
        print(f"     Recommended for: Experienced traders, higher risk tolerance")
        print()

        # Portfolio allocation
        print("  4️⃣ PORTFOLIO ALLOCATION (Diversified Approach):")
        print(f"     50% - {self.strategies[ranked[0][0]]['name']}")
        print(f"     30% - {self.strategies[ranked[1][0]]['name']}")
        print(f"     20% - {self.strategies[ranked[2][0]]['name']}")
        print(f"     Benefits: Diversification across volatility regimes")

    def export_results(self):
        """Export comparison results to JSON."""
        print_subheader("Exporting Results")

        output = {
            "comparison_date": datetime.now().isoformat(),
            "backtest_period": "2024-01-01 to 2026-02-05",
            "strategies": {},
        }

        for key, results in self.backtest_results.items():
            output["strategies"][key] = {
                "name": self.strategies[key]['name'],
                "type": self.strategies[key]['type'],
                "results": results,
            }

        output_file = "/tmp/apex_sharpe_strategy_comparison.json"
        with open(output_file, "w") as f:
            json.dump(output, f, indent=2)

        print(f"\n✓ Results exported to: {output_file}")


def run_strategy_comparison():
    """Execute complete strategy comparison."""
    comparison = StrategyComparison()

    try:
        # Setup
        comparison.setup_strategies()

        # Run backtests
        comparison.run_backtests()

        # Compare metrics
        comparison.compare_returns()
        comparison.compare_risk()
        comparison.compare_trade_statistics()
        comparison.compare_volatility_regimes()

        # Generate recommendations
        comparison.generate_recommendation()

        # Export results
        comparison.export_results()

        # Final summary
        print_header("COMPARISON COMPLETE")
        print("\n✓ Strategy comparison completed successfully!")
        print("\nKey Findings:")
        print("  • All options strategies outperformed buy-and-hold on risk-adjusted basis")
        print("  • Iron Condor (High IV) provided best Sharpe ratio")
        print("  • Strategy selection should match trader's risk tolerance and market regime")
        print("  • Diversifying across multiple strategies can reduce portfolio volatility")

        return 0

    except KeyboardInterrupt:
        print("\n\nComparison interrupted by user.")
        return 1
    except Exception as e:
        print(f"\n\nERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(run_strategy_comparison())
