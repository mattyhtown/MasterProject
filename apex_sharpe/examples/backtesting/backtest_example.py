"""
Example Usage of APEX-SHARPE Backtesting Engine.

Demonstrates basic backtest and validation workflows.
"""

from datetime import date
from decimal import Decimal
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from apex_sharpe.backtesting import (
    BacktestEngine,
    BacktestConfig,
    BacktestValidator,
    WalkForwardConfig,
    HistoricalDataManager,
    DataCache
)


def example_basic_backtest():
    """
    Example: Run a basic backtest.

    This demonstrates the simplest backtest workflow.
    """
    print("="*70)
    print("EXAMPLE: Basic Backtest")
    print("="*70)

    # NOTE: This example requires:
    # 1. MCP tools configured with ORATS access
    # 2. A strategy class (e.g., IronCondorStrategy)
    # 3. Historical data available

    # Configuration
    config = BacktestConfig(
        start_date=date(2023, 1, 1),
        end_date=date(2023, 6, 30),
        initial_capital=Decimal("100000"),
        ticker="SPY",
        commission_per_contract=Decimal("0.65"),
        track_greeks_daily=True,
        calculate_attribution=True
    )

    print(f"\nConfiguration:")
    print(f"  Period: {config.start_date} to {config.end_date}")
    print(f"  Ticker: {config.ticker}")
    print(f"  Capital: ${config.initial_capital:,.2f}")

    # NOTE: You would create these components:
    # from apex_sharpe.data import ORATSAdapter, create_data_manager
    # from apex_sharpe.strategies import IronCondorStrategy
    #
    # orats_adapter = ORATSAdapter(mcp_tools)
    # data_manager = create_data_manager(orats_adapter)
    #
    # strategy = IronCondorStrategy(
    #     name="IC_SPY",
    #     symbol="SPY",
    #     initial_capital=config.initial_capital,
    #     target_dte=45,
    #     target_delta=Decimal("0.16")
    # )
    #
    # # Run backtest
    # engine = BacktestEngine(config, strategy, data_manager)
    # results = engine.run()
    #
    # # Print results
    # print(results.summary())

    print("\n[Example - actual execution requires MCP tools and data]\n")


def example_train_test_validation():
    """
    Example: Run train/test split validation.

    This demonstrates proper out-of-sample validation.
    """
    print("="*70)
    print("EXAMPLE: Train/Test Split Validation")
    print("="*70)

    # Configuration
    config = BacktestConfig(
        start_date=date(2022, 1, 1),
        end_date=date(2023, 12, 31),
        initial_capital=Decimal("100000"),
        ticker="SPY"
    )

    print(f"\nConfiguration:")
    print(f"  Period: {config.start_date} to {config.end_date}")
    print(f"  Train/Test: 60/40 split")

    # NOTE: You would run validation:
    # validator = BacktestValidator()
    #
    # results = validator.train_test_split(
    #     config=config,
    #     strategy=strategy,
    #     data_manager=data_manager,
    #     train_ratio=0.6,
    #     sharpe_threshold=1.0
    # )
    #
    # print(results.summary())
    #
    # # Check validation
    # if results.avg_test_sharpe >= 1.0:
    #     print("\n✓ Strategy validated with Sharpe >= 1.0")
    #     if results.avg_sharpe_degradation < 30:
    #         print("✓ Train/test degradation < 30%")
    # else:
    #     print("\n✗ Strategy needs improvement")

    print("\n[Example - actual execution requires components]\n")


def example_walk_forward():
    """
    Example: Run walk-forward analysis.

    This demonstrates rolling out-of-sample testing.
    """
    print("="*70)
    print("EXAMPLE: Walk-Forward Analysis")
    print("="*70)

    # Configuration
    config = BacktestConfig(
        start_date=date(2022, 1, 1),
        end_date=date(2023, 12, 31),
        initial_capital=Decimal("100000"),
        ticker="SPY"
    )

    wf_config = WalkForwardConfig(
        train_window_days=180,  # 6 months training
        test_window_days=60,    # 2 months testing
        step_days=60,           # Move forward 2 months
        min_trades_per_period=5
    )

    print(f"\nConfiguration:")
    print(f"  Period: {config.start_date} to {config.end_date}")
    print(f"  Train Window: {wf_config.train_window_days} days")
    print(f"  Test Window: {wf_config.test_window_days} days")
    print(f"  Step: {wf_config.step_days} days")

    # NOTE: You would run walk-forward:
    # validator = BacktestValidator()
    #
    # results = validator.walk_forward(
    #     config=config,
    #     strategy=strategy,
    #     data_manager=data_manager,
    #     wf_config=wf_config,
    #     sharpe_threshold=1.0
    # )
    #
    # print(results.summary())
    #
    # # Check robustness
    # if results.robustness_score >= 70:
    #     print(f"\n✓ Robust: {results.robustness_score:.0f}% of periods succeeded")
    # else:
    #     print(f"\n✗ Not robust: {results.robustness_score:.0f}% success rate")

    print("\n[Example - actual execution requires components]\n")


def example_data_caching():
    """
    Example: Use data caching for faster backtests.

    This demonstrates efficient data management.
    """
    print("="*70)
    print("EXAMPLE: Data Caching")
    print("="*70)

    # NOTE: You would create data manager with caching:
    # from apex_sharpe.data import ORATSAdapter, create_data_manager
    #
    # orats_adapter = ORATSAdapter(mcp_tools)
    #
    # # Create manager with cache
    # data_manager = create_data_manager(
    #     orats_adapter,
    #     cache_dir=".cache/apex_sharpe",
    #     use_cache=True
    # )
    #
    # # Preload data
    # print("Preloading data...")
    # data_manager.preload_data(
    #     "SPY",
    #     date(2023, 1, 1),
    #     date(2023, 12, 31)
    # )
    #
    # # Check cache stats
    # stats = data_manager.get_cache_stats()
    # print(f"\nCache Statistics:")
    # print(f"  Cached Files: {stats['disk_cached_files']}")
    # print(f"  Cache Size: {stats['total_cache_size_mb']:.1f} MB")
    #
    # # Run multiple backtests (will use cache)
    # for i in range(5):
    #     print(f"\nBacktest {i+1} (using cache)...")
    #     engine = BacktestEngine(config, strategy, data_manager)
    #     results = engine.run()
    #
    # # Clear cache when done
    # data_manager.clear_cache("SPY")

    print("\n[Example - demonstrates caching workflow]\n")


def example_complete_workflow():
    """
    Example: Complete backtest and validation workflow.

    This demonstrates the full validation process.
    """
    print("="*70)
    print("EXAMPLE: Complete Workflow")
    print("="*70)

    print("\nWorkflow Steps:")
    print("1. Configure backtest parameters")
    print("2. Initialize data manager with caching")
    print("3. Create and configure strategy")
    print("4. Run train/test split validation")
    print("5. Run walk-forward analysis")
    print("6. Evaluate results")
    print("7. Store results in database")
    print("8. Make deployment decision")

    # Step 1: Configuration
    print("\n[Step 1] Configuration")
    config = BacktestConfig(
        start_date=date(2022, 1, 1),
        end_date=date(2023, 12, 31),
        initial_capital=Decimal("100000"),
        ticker="SPY"
    )
    print(f"  Period: {config.start_date} to {config.end_date}")

    # Step 2: Data manager
    print("\n[Step 2] Data Manager")
    print("  Cache enabled: Yes")
    print("  Cache dir: .cache/apex_sharpe")

    # Step 3: Strategy
    print("\n[Step 3] Strategy")
    print("  Strategy: IronCondorStrategy")
    print("  Target DTE: 45 days")
    print("  Target Delta: 0.16")

    # Step 4: Train/test validation
    print("\n[Step 4] Train/Test Validation")
    print("  Train Ratio: 60%")
    print("  Test Ratio: 40%")
    print("  Target Sharpe: >= 1.0")
    print("  Result: [Would show actual results]")

    # Step 5: Walk-forward
    print("\n[Step 5] Walk-Forward Analysis")
    print("  Train Window: 180 days")
    print("  Test Window: 60 days")
    print("  Result: [Would show actual results]")

    # Step 6: Evaluation
    print("\n[Step 6] Evaluation")
    print("  Criteria:")
    print("    - Out-of-sample Sharpe >= 1.0")
    print("    - Robustness score >= 70%")
    print("    - Train/test degradation < 30%")

    # Step 7: Store results
    print("\n[Step 7] Store Results")
    print("  Database: Supabase")
    print("  Table: backtest_runs")

    # Step 8: Decision
    print("\n[Step 8] Deployment Decision")
    print("  If all criteria met: ✓ Deploy to production")
    print("  If criteria not met: ✗ Continue development")

    print("\n[Example - demonstrates complete workflow]\n")


def main():
    """Run all examples."""
    print("\n" + "="*70)
    print("APEX-SHARPE BACKTESTING ENGINE - EXAMPLES")
    print("="*70 + "\n")

    example_basic_backtest()
    print("\n")

    example_train_test_validation()
    print("\n")

    example_walk_forward()
    print("\n")

    example_data_caching()
    print("\n")

    example_complete_workflow()

    print("\n" + "="*70)
    print("NOTE: These are example workflows.")
    print("Actual execution requires:")
    print("  1. MCP tools configured with ORATS access")
    print("  2. Strategy class implemented")
    print("  3. Historical data available")
    print("\nSee README.md for complete implementation details.")
    print("="*70 + "\n")


if __name__ == "__main__":
    main()
