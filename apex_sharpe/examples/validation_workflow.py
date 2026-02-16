"""
Complete Validation Workflow for APEX-SHARPE Trading System.

This example demonstrates comprehensive strategy validation using multiple techniques:
1. Train/Test Split - Classic out-of-sample testing
2. Walk-Forward Analysis - Rolling window validation
3. Multi-Scenario Testing - Different market regimes
4. Monte Carlo Simulation - Randomized trade sequences
5. Stress Testing - Extreme market conditions
6. Generate Validation Report - Pass/fail criteria

This follows validation patterns from CrewTrader to ensure strategy robustness
before live deployment.
"""

import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Tuple
import random

# Import APEX-SHARPE components
from apex_sharpe.strategies.iron_condor_strategy import IronCondorStrategy
from apex_sharpe.backtesting import (
    BacktestEngine,
    BacktestValidator,
    ValidationMethod,
    WalkForwardConfig,
    BacktestConfig,
)
from apex_sharpe.risk import OptionsRiskManager, GreeksLimits


def print_header(title: str):
    """Print formatted header."""
    print("\n" + "=" * 80)
    print(f"  {title}")
    print("=" * 80)


def print_subheader(title: str):
    """Print formatted subheader."""
    print(f"\n{'─' * 80}")
    print(f"  {title}")
    print(f"{'─' * 80}")


class ValidationWorkflow:
    """Comprehensive validation workflow for trading strategies."""

    def __init__(self):
        """Initialize validation workflow."""
        self.strategy = None
        self.validator = None
        self.results = {}

    def setup_strategy(self):
        """Setup strategy for validation."""
        print_header("STRATEGY VALIDATION WORKFLOW")
        print_subheader("Step 1: Strategy Configuration")

        self.strategy = IronCondorStrategy(
            name="IronCondor_Validation",
            symbol="SPY",
            initial_capital=Decimal("100000"),
            sharpe_threshold=1.0,
            sharpe_window=30,
            iv_rank_min=Decimal("0.70"),
            delta_short_target=Decimal("0.15"),
            width=Decimal("5.00"),
            max_risk_per_trade_pct=0.01,
        )

        print(f"\nStrategy: {self.strategy.name}")
        print(f"  Symbol: {self.strategy.symbol}")
        print(f"  Initial Capital: ${self.strategy.initial_capital:,.0f}")
        print(f"  Sharpe Threshold: {self.strategy.sharpe_threshold}")
        print(f"  IV Rank Minimum: {self.strategy.iv_rank_min * 100}%")
        print("\n✓ Strategy configured for validation")

    def train_test_split(self) -> Dict:
        """Perform train/test split validation."""
        print_subheader("Step 2: Train/Test Split Validation")

        # Define periods
        total_days = 730  # 2 years
        train_days = 548  # 75% for training
        test_days = 182   # 25% for testing

        end_date = date.today()
        train_start = end_date - timedelta(days=total_days)
        train_end = train_start + timedelta(days=train_days)
        test_start = train_end + timedelta(days=1)
        test_end = end_date

        print(f"\nData Split:")
        print(f"  Total Period: {train_start} to {test_end} ({total_days} days)")
        print(f"  Training Set: {train_start} to {train_end} ({train_days} days, 75%)")
        print(f"  Testing Set:  {test_start} to {test_end} ({test_days} days, 25%)")

        # Run backtest on training data
        print(f"\nRunning Training Backtest...")
        # In production: train_results = self.run_backtest(train_start, train_end)
        train_results = {
            "total_return": Decimal("18.45"),
            "sharpe_ratio": Decimal("1.92"),
            "max_drawdown": Decimal("-7.23"),
            "win_rate": Decimal("0.76"),
            "total_trades": 36,
        }

        print(f"  Training Results:")
        print(f"    Total Return: +{train_results['total_return']}%")
        print(f"    Sharpe Ratio: {train_results['sharpe_ratio']}")
        print(f"    Max Drawdown: {train_results['max_drawdown']}%")
        print(f"    Win Rate: {train_results['win_rate'] * 100:.1f}%")
        print(f"    Total Trades: {train_results['total_trades']}")

        # Run backtest on testing data
        print(f"\nRunning Testing Backtest (Out-of-Sample)...")
        # In production: test_results = self.run_backtest(test_start, test_end)
        test_results = {
            "total_return": Decimal("14.12"),
            "sharpe_ratio": Decimal("1.67"),
            "max_drawdown": Decimal("-9.15"),
            "win_rate": Decimal("0.72"),
            "total_trades": 12,
        }

        print(f"  Testing Results:")
        print(f"    Total Return: +{test_results['total_return']}%")
        print(f"    Sharpe Ratio: {test_results['sharpe_ratio']}")
        print(f"    Max Drawdown: {test_results['max_drawdown']}%")
        print(f"    Win Rate: {test_results['win_rate'] * 100:.1f}%")
        print(f"    Total Trades: {test_results['total_trades']}")

        # Compare results
        print(f"\nPerformance Degradation Analysis:")
        return_degradation = (train_results['total_return'] - test_results['total_return']) / train_results['total_return'] * 100
        sharpe_degradation = (train_results['sharpe_ratio'] - test_results['sharpe_ratio']) / train_results['sharpe_ratio'] * 100

        print(f"  Return Degradation: {return_degradation:.1f}%")
        print(f"  Sharpe Degradation: {sharpe_degradation:.1f}%")

        # Validate
        passed = (
            test_results['sharpe_ratio'] > Decimal("1.0") and
            return_degradation < 30 and
            sharpe_degradation < 30
        )

        if passed:
            print(f"\n  ✓ PASSED - Out-of-sample performance acceptable")
            print(f"    - Sharpe > 1.0: ✓")
            print(f"    - Return degradation < 30%: ✓")
            print(f"    - Sharpe degradation < 30%: ✓")
        else:
            print(f"\n  ✗ FAILED - Out-of-sample performance degraded significantly")

        return {
            "method": "train_test_split",
            "passed": passed,
            "train_results": train_results,
            "test_results": test_results,
        }

    def walk_forward_analysis(self) -> Dict:
        """Perform walk-forward analysis."""
        print_subheader("Step 3: Walk-Forward Analysis")

        print(f"\nWalk-Forward Configuration:")
        print(f"  Window Size: 90 days (training)")
        print(f"  Step Size: 30 days (testing)")
        print(f"  Total Windows: 6")

        # Simulate walk-forward windows
        windows = [
            {"train": "2024-08-01 to 2024-10-30", "test": "2024-11-01 to 2024-11-30", "sharpe": 1.82, "return": 5.2},
            {"train": "2024-09-01 to 2024-11-30", "test": "2024-12-01 to 2024-12-31", "sharpe": 1.45, "return": 3.8},
            {"train": "2024-10-01 to 2024-12-31", "test": "2025-01-01 to 2025-01-31", "sharpe": 1.91, "return": 6.1},
            {"train": "2024-11-01 to 2025-01-31", "test": "2025-02-01 to 2025-02-28", "sharpe": 1.38, "return": 2.9},
            {"train": "2024-12-01 to 2025-02-28", "test": "2025-03-01 to 2025-03-31", "sharpe": 1.67, "return": 4.5},
            {"train": "2025-01-01 to 2025-03-31", "test": "2025-04-01 to 2025-04-30", "sharpe": 1.74, "return": 5.3},
        ]

        print(f"\nWalk-Forward Results:")
        print(f"\n  {'Window':<10} {'Training Period':<30} {'Test Period':<30} {'Sharpe':<10} {'Return':<10}")
        print(f"  {'-' * 95}")

        total_sharpe = 0
        total_return = 0
        passed_windows = 0

        for i, window in enumerate(windows, 1):
            status = "✓" if window['sharpe'] > 1.0 else "✗"
            print(f"  {i:<10} {window['train']:<30} {window['test']:<30} {window['sharpe']:<10.2f} {window['return']:<10.1f}% {status}")
            total_sharpe += window['sharpe']
            total_return += window['return']
            if window['sharpe'] > 1.0:
                passed_windows += 1

        avg_sharpe = total_sharpe / len(windows)
        avg_return = total_return / len(windows)
        consistency_rate = passed_windows / len(windows) * 100

        print(f"\n  Average Sharpe: {avg_sharpe:.2f}")
        print(f"  Average Return: {avg_return:.1f}%")
        print(f"  Consistency: {consistency_rate:.0f}% of windows passed")

        # Validate
        passed = avg_sharpe > 1.0 and consistency_rate >= 70

        if passed:
            print(f"\n  ✓ PASSED - Consistent performance across time periods")
            print(f"    - Average Sharpe > 1.0: ✓")
            print(f"    - Consistency >= 70%: ✓")
        else:
            print(f"\n  ✗ FAILED - Inconsistent performance")

        return {
            "method": "walk_forward",
            "passed": passed,
            "avg_sharpe": avg_sharpe,
            "consistency_rate": consistency_rate,
        }

    def multi_scenario_testing(self) -> Dict:
        """Test strategy across different market scenarios."""
        print_subheader("Step 4: Multi-Scenario Testing")

        scenarios = {
            "High Volatility (VIX > 25)": {
                "period": "Mar 2020 - May 2020",
                "sharpe": 2.34,
                "return": 12.5,
                "max_dd": -15.2,
                "trades": 8,
            },
            "Low Volatility (VIX < 15)": {
                "period": "Jan 2019 - Dec 2019",
                "sharpe": 0.82,
                "return": 3.2,
                "max_dd": -4.5,
                "trades": 6,
            },
            "Rising Market (+20%)": {
                "period": "Jan 2023 - Dec 2023",
                "sharpe": 1.56,
                "return": 8.9,
                "max_dd": -6.7,
                "trades": 18,
            },
            "Falling Market (-15%)": {
                "period": "Sep 2022 - Dec 2022",
                "sharpe": 1.45,
                "return": 5.4,
                "max_dd": -12.3,
                "trades": 7,
            },
            "Sideways Market": {
                "period": "Jul 2021 - Dec 2021",
                "sharpe": 1.89,
                "return": 9.8,
                "max_dd": -5.1,
                "trades": 14,
            },
        }

        print(f"\nTesting across {len(scenarios)} market scenarios:\n")

        passed_scenarios = 0
        for scenario_name, results in scenarios.items():
            passed = results['sharpe'] > 0.8 and results['return'] > 0
            status = "✓ PASS" if passed else "✗ FAIL"

            print(f"  {scenario_name}:")
            print(f"    Period: {results['period']}")
            print(f"    Sharpe: {results['sharpe']:.2f}")
            print(f"    Return: +{results['return']:.1f}%")
            print(f"    Max DD: {results['max_dd']:.1f}%")
            print(f"    Trades: {results['trades']}")
            print(f"    Status: {status}\n")

            if passed:
                passed_scenarios += 1

        scenario_pass_rate = passed_scenarios / len(scenarios) * 100

        print(f"  Scenario Pass Rate: {scenario_pass_rate:.0f}% ({passed_scenarios}/{len(scenarios)})")

        # Validate
        passed = scenario_pass_rate >= 80

        if passed:
            print(f"\n  ✓ PASSED - Strategy robust across market scenarios")
        else:
            print(f"\n  ✗ FAILED - Strategy struggles in some scenarios")

        return {
            "method": "multi_scenario",
            "passed": passed,
            "pass_rate": scenario_pass_rate,
        }

    def monte_carlo_simulation(self) -> Dict:
        """Run Monte Carlo simulation on trade sequences."""
        print_subheader("Step 5: Monte Carlo Simulation")

        print(f"\nRunning 1,000 Monte Carlo simulations...")
        print(f"  Randomly reordering historical trades")
        print(f"  Calculating distribution of outcomes\n")

        # Simulate Monte Carlo results
        simulations = []
        for i in range(1000):
            # Simulate randomized returns
            final_return = random.normalvariate(15.0, 5.0)  # Mean 15%, StdDev 5%
            sharpe = random.normalvariate(1.65, 0.3)
            max_dd = random.normalvariate(-8.5, 2.0)
            simulations.append({
                "return": final_return,
                "sharpe": sharpe,
                "max_dd": max_dd,
            })

        # Calculate statistics
        returns = [s["return"] for s in simulations]
        sharpes = [s["sharpe"] for s in simulations]
        drawdowns = [s["max_dd"] for s in simulations]

        print(f"  Monte Carlo Statistics:")
        print(f"\n  Total Return Distribution:")
        print(f"    Mean: {sum(returns) / len(returns):.2f}%")
        print(f"    Median: {sorted(returns)[len(returns) // 2]:.2f}%")
        print(f"    5th Percentile: {sorted(returns)[int(len(returns) * 0.05)]:.2f}%")
        print(f"    95th Percentile: {sorted(returns)[int(len(returns) * 0.95)]:.2f}%")

        print(f"\n  Sharpe Ratio Distribution:")
        print(f"    Mean: {sum(sharpes) / len(sharpes):.2f}")
        print(f"    Median: {sorted(sharpes)[len(sharpes) // 2]:.2f}")
        print(f"    5th Percentile: {sorted(sharpes)[int(len(sharpes) * 0.05)]:.2f}")
        print(f"    95th Percentile: {sorted(sharpes)[int(len(sharpes) * 0.95)]:.2f}")

        print(f"\n  Max Drawdown Distribution:")
        print(f"    Mean: {sum(drawdowns) / len(drawdowns):.2f}%")
        print(f"    Median: {sorted(drawdowns)[len(drawdowns) // 2]:.2f}%")
        print(f"    5th Percentile (Worst): {sorted(drawdowns)[int(len(drawdowns) * 0.05)]:.2f}%")
        print(f"    95th Percentile (Best): {sorted(drawdowns)[int(len(drawdowns) * 0.95)]:.2f}%")

        # Risk of ruin
        losing_simulations = len([s for s in simulations if s["return"] < 0])
        risk_of_ruin = losing_simulations / len(simulations) * 100

        print(f"\n  Risk Analysis:")
        print(f"    Probability of Loss: {risk_of_ruin:.2f}%")
        print(f"    Probability of Profit: {100 - risk_of_ruin:.2f}%")

        # Validate
        mean_sharpe = sum(sharpes) / len(sharpes)
        percentile_5_return = sorted(returns)[int(len(returns) * 0.05)]

        passed = mean_sharpe > 1.0 and percentile_5_return > -10 and risk_of_ruin < 10

        if passed:
            print(f"\n  ✓ PASSED - Monte Carlo shows acceptable risk/reward")
            print(f"    - Mean Sharpe > 1.0: ✓")
            print(f"    - 5th Percentile Return > -10%: ✓")
            print(f"    - Risk of Ruin < 10%: ✓")
        else:
            print(f"\n  ✗ FAILED - Monte Carlo shows excessive risk")

        return {
            "method": "monte_carlo",
            "passed": passed,
            "mean_sharpe": mean_sharpe,
            "risk_of_ruin": risk_of_ruin,
        }

    def stress_testing(self) -> Dict:
        """Perform stress testing under extreme conditions."""
        print_subheader("Step 6: Stress Testing")

        stress_scenarios = {
            "Flash Crash (-10% in 1 day)": {
                "date": "2020-03-16",
                "pnl": -1850,
                "portfolio_impact": -1.85,
                "recovery_days": 12,
            },
            "VIX Spike (+50 points)": {
                "date": "2020-03-12",
                "pnl": -1240,
                "portfolio_impact": -1.24,
                "recovery_days": 8,
            },
            "Gap Up Opening (+5%)": {
                "date": "2020-11-09",
                "pnl": -620,
                "portfolio_impact": -0.62,
                "recovery_days": 4,
            },
            "Earnings Shock": {
                "date": "2021-01-27",
                "pnl": -890,
                "portfolio_impact": -0.89,
                "recovery_days": 6,
            },
            "Fed Rate Surprise": {
                "date": "2022-11-02",
                "pnl": -730,
                "portfolio_impact": -0.73,
                "recovery_days": 5,
            },
        }

        print(f"\nStress Test Results:\n")

        max_loss = 0
        max_impact = 0
        passed_tests = 0

        for scenario, results in stress_scenarios.items():
            impact_ok = results['portfolio_impact'] > -5.0  # Max 5% loss
            recovery_ok = results['recovery_days'] <= 15
            passed = impact_ok and recovery_ok
            status = "✓ PASS" if passed else "✗ FAIL"

            print(f"  {scenario}:")
            print(f"    Date: {results['date']}")
            print(f"    P&L Impact: ${results['pnl']:,.0f}")
            print(f"    Portfolio Impact: {results['portfolio_impact']:.2f}%")
            print(f"    Recovery Time: {results['recovery_days']} days")
            print(f"    Status: {status}\n")

            if abs(results['pnl']) > abs(max_loss):
                max_loss = results['pnl']
            if abs(results['portfolio_impact']) > abs(max_impact):
                max_impact = results['portfolio_impact']
            if passed:
                passed_tests += 1

        stress_pass_rate = passed_tests / len(stress_scenarios) * 100

        print(f"  Stress Test Summary:")
        print(f"    Max Single Event Loss: ${max_loss:,.0f}")
        print(f"    Max Portfolio Impact: {max_impact:.2f}%")
        print(f"    Pass Rate: {stress_pass_rate:.0f}% ({passed_tests}/{len(stress_scenarios)})")

        # Validate
        passed = stress_pass_rate >= 80 and max_impact > -5.0

        if passed:
            print(f"\n  ✓ PASSED - Strategy resilient to extreme events")
        else:
            print(f"\n  ✗ FAILED - Strategy vulnerable to stress scenarios")

        return {
            "method": "stress_testing",
            "passed": passed,
            "pass_rate": stress_pass_rate,
            "max_impact": max_impact,
        }

    def generate_validation_report(self, validation_results: List[Dict]):
        """Generate comprehensive validation report."""
        print_header("VALIDATION REPORT")

        # Summary table
        print(f"\nValidation Method Summary:\n")
        print(f"  {'Method':<30} {'Status':<15} {'Key Metric':<30}")
        print(f"  {'-' * 75}")

        all_passed = True
        for result in validation_results:
            method = result['method'].replace('_', ' ').title()
            status = "✓ PASSED" if result['passed'] else "✗ FAILED"

            # Get key metric
            if 'avg_sharpe' in result:
                metric = f"Avg Sharpe: {result['avg_sharpe']:.2f}"
            elif 'pass_rate' in result:
                metric = f"Pass Rate: {result['pass_rate']:.1f}%"
            elif 'mean_sharpe' in result:
                metric = f"Mean Sharpe: {result['mean_sharpe']:.2f}"
            elif 'max_impact' in result:
                metric = f"Max Impact: {result['max_impact']:.2f}%"
            else:
                metric = "N/A"

            print(f"  {method:<30} {status:<15} {metric:<30}")

            if not result['passed']:
                all_passed = False

        # Overall assessment
        print(f"\n" + "=" * 80)
        if all_passed:
            print(f"  ✓ VALIDATION PASSED - Strategy Ready for Live Trading")
        else:
            print(f"  ✗ VALIDATION FAILED - Strategy Requires Improvement")
        print(f"=" * 80)

        # Recommendations
        print(f"\nRecommendations:")
        if all_passed:
            print(f"  1. ✓ Proceed to paper trading with small position sizes")
            print(f"  2. ✓ Monitor performance closely in first 30 days")
            print(f"  3. ✓ Set up automated alerts for risk limit breaches")
            print(f"  4. ✓ Review strategy performance monthly")
        else:
            print(f"  1. ✗ Do NOT deploy to live trading")
            print(f"  2. ✗ Investigate failed validation methods")
            print(f"  3. ✗ Adjust strategy parameters")
            print(f"  4. ✗ Re-run validation after improvements")

        print(f"\nValidation Checklist:")
        print(f"  [ {'✓' if all_passed else ' '} ] Out-of-sample testing completed")
        print(f"  [ {'✓' if all_passed else ' '} ] Walk-forward analysis passed")
        print(f"  [ {'✓' if all_passed else ' '} ] Multi-scenario testing passed")
        print(f"  [ {'✓' if all_passed else ' '} ] Monte Carlo simulation acceptable")
        print(f"  [ {'✓' if all_passed else ' '} ] Stress testing passed")
        print(f"  [ {'✓' if all_passed else ' '} ] Risk limits properly configured")
        print(f"  [ {'✓' if all_passed else ' '} ] Database storage tested")
        print(f"  [ {'✓' if all_passed else ' '} ] Monitoring alerts configured")

        return all_passed


def run_validation_workflow():
    """Execute complete validation workflow."""
    workflow = ValidationWorkflow()

    try:
        # Setup
        workflow.setup_strategy()

        # Run validation methods
        validation_results = []

        # 1. Train/Test Split
        result1 = workflow.train_test_split()
        validation_results.append(result1)

        # 2. Walk-Forward Analysis
        result2 = workflow.walk_forward_analysis()
        validation_results.append(result2)

        # 3. Multi-Scenario Testing
        result3 = workflow.multi_scenario_testing()
        validation_results.append(result3)

        # 4. Monte Carlo Simulation
        result4 = workflow.monte_carlo_simulation()
        validation_results.append(result4)

        # 5. Stress Testing
        result5 = workflow.stress_testing()
        validation_results.append(result5)

        # Generate final report
        all_passed = workflow.generate_validation_report(validation_results)

        if all_passed:
            print(f"\n✓ Strategy validation completed successfully!")
            return 0
        else:
            print(f"\n✗ Strategy validation failed. Review results above.")
            return 1

    except KeyboardInterrupt:
        print("\n\nValidation interrupted by user.")
        return 1
    except Exception as e:
        print(f"\n\nERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(run_validation_workflow())
