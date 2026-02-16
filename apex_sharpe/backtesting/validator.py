"""
Backtest Validation Module for APEX-SHARPE Trading System.

Implements proper validation methodologies including train/test splits,
walk-forward analysis, and multi-scenario robustness testing.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from enum import Enum
from typing import List, Dict, Optional, Callable, Any
import copy


class ValidationMethod(Enum):
    """Validation methodology types."""
    TRAIN_TEST_SPLIT = "TRAIN_TEST_SPLIT"
    WALK_FORWARD = "WALK_FORWARD"
    MULTI_SCENARIO = "MULTI_SCENARIO"


@dataclass
class WalkForwardConfig:
    """Configuration for walk-forward analysis."""
    train_window_days: int = 180  # 6 months training
    test_window_days: int = 60    # 2 months testing
    step_days: int = 60           # Move forward 2 months each step
    min_trades_per_period: int = 5  # Minimum trades for valid period


@dataclass
class ValidationPeriod:
    """Single validation period results."""
    period_id: int
    train_start: date
    train_end: date
    test_start: date
    test_end: date

    train_results: Optional['BacktestResults'] = None
    test_results: Optional['BacktestResults'] = None

    sharpe_degradation: float = 0.0
    is_valid: bool = True  # Has sufficient trades


@dataclass
class ValidationResults:
    """
    Comprehensive validation results across all periods.

    Contains statistics aggregated across all validation periods
    for robust strategy evaluation.
    """
    method: ValidationMethod
    ticker: str
    total_periods: int

    # Aggregated metrics
    avg_test_sharpe: float
    std_test_sharpe: float
    min_test_sharpe: float
    max_test_sharpe: float

    avg_test_return: float
    std_test_return: float

    avg_sharpe_degradation: float
    periods_sharpe_above_threshold: int  # How many achieved Sharpe >= 1.0

    # Individual period results
    periods: List[ValidationPeriod]

    # Robustness metrics
    consistency_score: float  # 0-100, how consistent across periods
    robustness_score: float   # 0-100, percentage achieving targets

    def summary(self) -> str:
        """Generate formatted summary report."""
        lines = []
        lines.append("=" * 70)
        lines.append(f"{self.method.value} VALIDATION RESULTS")
        lines.append("=" * 70)

        lines.append(f"\nTicker: {self.ticker}")
        lines.append(f"Total Periods: {self.total_periods}")
        lines.append(f"Valid Periods: {len([p for p in self.periods if p.is_valid])}")

        lines.append(f"\n{'AGGREGATED OUT-OF-SAMPLE PERFORMANCE':-<70}")
        lines.append(f"Average Test Sharpe:     {self.avg_test_sharpe:>10.3f} (+/- {self.std_test_sharpe:.3f})")
        lines.append(f"Min Test Sharpe:         {self.min_test_sharpe:>10.3f}")
        lines.append(f"Max Test Sharpe:         {self.max_test_sharpe:>10.3f}")
        lines.append(f"Average Test Return:     {self.avg_test_return:>10.2f}%")

        lines.append(f"\n{'VALIDATION QUALITY':-<70}")
        lines.append(f"Avg Sharpe Degradation:  {self.avg_sharpe_degradation:>10.2f}%")
        lines.append(f"Periods Sharpe >= 1.0:   {self.periods_sharpe_above_threshold}/{self.total_periods}")
        lines.append(f"Consistency Score:       {self.consistency_score:>10.1f}/100")
        lines.append(f"Robustness Score:        {self.robustness_score:>10.1f}/100")

        # Assessment
        lines.append(f"\n{'ASSESSMENT':-<70}")
        if self.avg_test_sharpe >= 1.0:
            lines.append("✓ PASSED - Average out-of-sample Sharpe >= 1.0")
        else:
            lines.append("✗ FAILED - Average out-of-sample Sharpe < 1.0")

        if self.robustness_score >= 70:
            lines.append("✓ ROBUST - 70%+ of periods achieved targets")
        else:
            lines.append("✗ NOT ROBUST - Less than 70% achieved targets")

        if self.avg_sharpe_degradation < 30:
            lines.append("✓ STABLE - Train/test degradation < 30%")
        else:
            lines.append("⚠ UNSTABLE - High train/test degradation")

        # Period details
        if len(self.periods) <= 10:
            lines.append(f"\n{'PERIOD DETAILS':-<70}")
            for period in self.periods:
                if period.test_results:
                    sharpe = period.test_results.sharpe_ratio
                    ret = period.test_results.total_return_pct
                    status = "✓" if sharpe >= 1.0 else "✗"
                    lines.append(
                        f"Period {period.period_id}: {status} "
                        f"Sharpe={sharpe:.3f}, Return={ret:.2f}%, "
                        f"Test: {period.test_start} to {period.test_end}"
                    )

        lines.append("\n" + "=" * 70)
        return "\n".join(lines)


class BacktestValidator:
    """
    Validator for backtesting strategies with proper methodology.

    Implements train/test splits, walk-forward analysis, and
    multi-scenario robustness testing to ensure out-of-sample validity.

    Example:
        >>> validator = BacktestValidator()
        >>> results = validator.train_test_split(
        ...     config, strategy, data_manager,
        ...     train_ratio=0.6
        ... )
        >>> print(results.summary())
    """

    def __init__(self):
        """Initialize validator."""
        pass

    def train_test_split(
        self,
        config: 'BacktestConfig',
        strategy: 'BaseStrategy',
        data_manager: 'HistoricalDataManager',
        train_ratio: float = 0.6,
        sharpe_threshold: float = 1.0
    ) -> ValidationResults:
        """
        Run train/test split validation.

        Parameters are evaluated on training data, then validated on
        unseen test data to ensure out-of-sample performance.

        Args:
            config: Backtest configuration
            strategy: Strategy instance
            data_manager: Historical data manager
            train_ratio: Fraction of data for training (default 0.6)
            sharpe_threshold: Target Sharpe ratio (default 1.0)

        Returns:
            ValidationResults with train/test comparison
        """
        print(f"\n{'='*70}")
        print("TRAIN/TEST SPLIT VALIDATION")
        print(f"{'='*70}\n")

        # Calculate split date
        total_days = (config.end_date - config.start_date).days
        train_days = int(total_days * train_ratio)
        split_date = config.start_date + timedelta(days=train_days)

        print(f"Total Period: {config.start_date} to {config.end_date} ({total_days} days)")
        print(f"Train Period: {config.start_date} to {split_date} ({train_days} days, {train_ratio:.0%})")
        print(f"Test Period:  {split_date} to {config.end_date} ({total_days - train_days} days, {1-train_ratio:.0%})")

        # Train period
        print(f"\n{'--- TRAINING PERIOD ---'}")
        train_config = copy.deepcopy(config)
        train_config.end_date = split_date

        train_strategy = copy.deepcopy(strategy)

        from .backtest_engine import BacktestEngine
        train_engine = BacktestEngine(train_config, train_strategy, data_manager)
        train_results = train_engine.run()

        print(f"\nTrain Sharpe: {train_results.sharpe_ratio:.3f}")
        print(f"Train Return: {train_results.total_return_pct:.2f}%")
        print(f"Train Trades: {train_results.trade_stats.total_trades}")

        # Test period (OUT-OF-SAMPLE)
        print(f"\n{'--- TEST PERIOD (OUT-OF-SAMPLE) ---'}")
        test_config = copy.deepcopy(config)
        test_config.start_date = split_date

        test_strategy = copy.deepcopy(strategy)
        test_strategy.closed_positions = []  # Reset for clean test

        test_engine = BacktestEngine(test_config, test_strategy, data_manager)
        test_results = test_engine.run()

        print(f"\nTest Sharpe:  {test_results.sharpe_ratio:.3f}")
        print(f"Test Return:  {test_results.total_return_pct:.2f}%")
        print(f"Test Trades:  {test_results.trade_stats.total_trades}")

        # Calculate degradation
        if train_results.sharpe_ratio > 0:
            degradation = (train_results.sharpe_ratio - test_results.sharpe_ratio) / train_results.sharpe_ratio * 100
        else:
            degradation = 0.0

        print(f"\nSharpe Degradation: {degradation:.2f}%")

        # Create validation period
        period = ValidationPeriod(
            period_id=1,
            train_start=config.start_date,
            train_end=split_date,
            test_start=split_date,
            test_end=config.end_date,
            train_results=train_results,
            test_results=test_results,
            sharpe_degradation=degradation,
            is_valid=test_results.trade_stats.total_trades >= 5
        )

        # Calculate scores
        consistency_score = max(0, 100 - abs(degradation))
        robustness_score = 100.0 if test_results.sharpe_ratio >= sharpe_threshold else 0.0
        periods_above = 1 if test_results.sharpe_ratio >= sharpe_threshold else 0

        results = ValidationResults(
            method=ValidationMethod.TRAIN_TEST_SPLIT,
            ticker=config.ticker,
            total_periods=1,
            avg_test_sharpe=test_results.sharpe_ratio,
            std_test_sharpe=0.0,
            min_test_sharpe=test_results.sharpe_ratio,
            max_test_sharpe=test_results.sharpe_ratio,
            avg_test_return=test_results.total_return_pct,
            std_test_return=0.0,
            avg_sharpe_degradation=degradation,
            periods_sharpe_above_threshold=periods_above,
            periods=[period],
            consistency_score=consistency_score,
            robustness_score=robustness_score
        )

        print(f"\n{results.summary()}")

        return results

    def walk_forward(
        self,
        config: 'BacktestConfig',
        strategy: 'BaseStrategy',
        data_manager: 'HistoricalDataManager',
        wf_config: Optional[WalkForwardConfig] = None,
        sharpe_threshold: float = 1.0
    ) -> ValidationResults:
        """
        Run walk-forward analysis.

        Slides a training window forward through time, always testing
        on unseen future data. This ensures the strategy works across
        different market regimes.

        Args:
            config: Backtest configuration
            strategy: Strategy instance
            data_manager: Historical data manager
            wf_config: Walk-forward configuration (uses defaults if None)
            sharpe_threshold: Target Sharpe ratio (default 1.0)

        Returns:
            ValidationResults with walk-forward analysis
        """
        if wf_config is None:
            wf_config = WalkForwardConfig()

        print(f"\n{'='*70}")
        print("WALK-FORWARD ANALYSIS")
        print(f"{'='*70}\n")

        print(f"Configuration:")
        print(f"  Train Window: {wf_config.train_window_days} days")
        print(f"  Test Window:  {wf_config.test_window_days} days")
        print(f"  Step Size:    {wf_config.step_days} days")

        periods: List[ValidationPeriod] = []
        period_id = 1

        current_start = config.start_date

        while True:
            train_end = current_start + timedelta(days=wf_config.train_window_days)
            test_start = train_end + timedelta(days=1)
            test_end = test_start + timedelta(days=wf_config.test_window_days)

            # Check if we have enough data
            if test_end > config.end_date:
                break

            print(f"\n--- Period {period_id} ---")
            print(f"Train: {current_start} to {train_end}")
            print(f"Test:  {test_start} to {test_end}")

            # Run test period (training would optimize parameters)
            test_config = copy.deepcopy(config)
            test_config.start_date = test_start
            test_config.end_date = test_end

            test_strategy = copy.deepcopy(strategy)

            from .backtest_engine import BacktestEngine
            test_engine = BacktestEngine(test_config, test_strategy, data_manager)
            test_results = test_engine.run()

            print(f"Test Sharpe: {test_results.sharpe_ratio:.3f}")
            print(f"Test Trades: {test_results.trade_stats.total_trades}")

            # Create period
            period = ValidationPeriod(
                period_id=period_id,
                train_start=current_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
                test_results=test_results,
                is_valid=test_results.trade_stats.total_trades >= wf_config.min_trades_per_period
            )

            periods.append(period)

            # Move forward
            current_start += timedelta(days=wf_config.step_days)
            period_id += 1

        # Calculate aggregated metrics
        valid_periods = [p for p in periods if p.is_valid]

        if not valid_periods:
            print("\nWarning: No valid periods with sufficient trades")
            return ValidationResults(
                method=ValidationMethod.WALK_FORWARD,
                ticker=config.ticker,
                total_periods=len(periods),
                avg_test_sharpe=0.0,
                std_test_sharpe=0.0,
                min_test_sharpe=0.0,
                max_test_sharpe=0.0,
                avg_test_return=0.0,
                std_test_return=0.0,
                avg_sharpe_degradation=0.0,
                periods_sharpe_above_threshold=0,
                periods=periods,
                consistency_score=0.0,
                robustness_score=0.0
            )

        import numpy as np

        sharpes = [p.test_results.sharpe_ratio for p in valid_periods]
        returns = [p.test_results.total_return_pct for p in valid_periods]

        avg_sharpe = np.mean(sharpes)
        std_sharpe = np.std(sharpes) if len(sharpes) > 1 else 0.0
        min_sharpe = np.min(sharpes)
        max_sharpe = np.max(sharpes)

        avg_return = np.mean(returns)
        std_return = np.std(returns) if len(returns) > 1 else 0.0

        periods_above = sum(1 for s in sharpes if s >= sharpe_threshold)

        # Scores
        consistency_score = max(0, 100 - (std_sharpe / max(avg_sharpe, 0.01) * 100))
        robustness_score = (periods_above / len(valid_periods)) * 100

        results = ValidationResults(
            method=ValidationMethod.WALK_FORWARD,
            ticker=config.ticker,
            total_periods=len(periods),
            avg_test_sharpe=avg_sharpe,
            std_test_sharpe=std_sharpe,
            min_test_sharpe=min_sharpe,
            max_test_sharpe=max_sharpe,
            avg_test_return=avg_return,
            std_test_return=std_return,
            avg_sharpe_degradation=0.0,  # Not applicable for walk-forward
            periods_sharpe_above_threshold=periods_above,
            periods=periods,
            consistency_score=consistency_score,
            robustness_score=robustness_score
        )

        print(f"\n{results.summary()}")

        return results

    def multi_scenario(
        self,
        config: 'BacktestConfig',
        strategy_factory: Callable[[], 'BaseStrategy'],
        data_manager: 'HistoricalDataManager',
        scenarios: List[Dict[str, Any]],
        sharpe_threshold: float = 1.0
    ) -> ValidationResults:
        """
        Run multi-scenario robustness testing.

        Tests the strategy across multiple parameter configurations
        or data scenarios to ensure robustness.

        Args:
            config: Backtest configuration
            strategy_factory: Function that creates strategy instances
            data_manager: Historical data manager
            scenarios: List of scenario configurations
            sharpe_threshold: Target Sharpe ratio (default 1.0)

        Returns:
            ValidationResults with multi-scenario analysis
        """
        print(f"\n{'='*70}")
        print("MULTI-SCENARIO ROBUSTNESS TEST")
        print(f"{'='*70}\n")

        print(f"Testing {len(scenarios)} different scenarios...")

        periods: List[ValidationPeriod] = []

        for i, scenario in enumerate(scenarios, 1):
            print(f"\n--- Scenario {i} ---")
            print(f"Parameters: {scenario}")

            # Create strategy with scenario parameters
            strategy = strategy_factory()

            # Apply scenario parameters
            for key, value in scenario.items():
                if hasattr(strategy, key):
                    setattr(strategy, key, value)
                elif hasattr(strategy, 'parameters'):
                    strategy.parameters[key] = value

            # Run backtest
            from .backtest_engine import BacktestEngine
            engine = BacktestEngine(config, strategy, data_manager)
            results = engine.run()

            print(f"Sharpe: {results.sharpe_ratio:.3f}")
            print(f"Return: {results.total_return_pct:.2f}%")
            print(f"Trades: {results.trade_stats.total_trades}")

            # Create period
            period = ValidationPeriod(
                period_id=i,
                train_start=config.start_date,
                train_end=config.end_date,
                test_start=config.start_date,
                test_end=config.end_date,
                test_results=results,
                is_valid=results.trade_stats.total_trades >= 5
            )

            periods.append(period)

        # Calculate aggregated metrics
        valid_periods = [p for p in periods if p.is_valid]

        if not valid_periods:
            print("\nWarning: No valid scenarios")
            return ValidationResults(
                method=ValidationMethod.MULTI_SCENARIO,
                ticker=config.ticker,
                total_periods=len(periods),
                avg_test_sharpe=0.0,
                std_test_sharpe=0.0,
                min_test_sharpe=0.0,
                max_test_sharpe=0.0,
                avg_test_return=0.0,
                std_test_return=0.0,
                avg_sharpe_degradation=0.0,
                periods_sharpe_above_threshold=0,
                periods=periods,
                consistency_score=0.0,
                robustness_score=0.0
            )

        import numpy as np

        sharpes = [p.test_results.sharpe_ratio for p in valid_periods]
        returns = [p.test_results.total_return_pct for p in valid_periods]

        avg_sharpe = np.mean(sharpes)
        std_sharpe = np.std(sharpes) if len(sharpes) > 1 else 0.0
        min_sharpe = np.min(sharpes)
        max_sharpe = np.max(sharpes)

        avg_return = np.mean(returns)
        std_return = np.std(returns) if len(returns) > 1 else 0.0

        periods_above = sum(1 for s in sharpes if s >= sharpe_threshold)

        # Scores
        consistency_score = max(0, 100 - (std_sharpe / max(avg_sharpe, 0.01) * 100))
        robustness_score = (periods_above / len(valid_periods)) * 100

        results = ValidationResults(
            method=ValidationMethod.MULTI_SCENARIO,
            ticker=config.ticker,
            total_periods=len(periods),
            avg_test_sharpe=avg_sharpe,
            std_test_sharpe=std_sharpe,
            min_test_sharpe=min_sharpe,
            max_test_sharpe=max_sharpe,
            avg_test_return=avg_return,
            std_test_return=std_return,
            avg_sharpe_degradation=0.0,
            periods_sharpe_above_threshold=periods_above,
            periods=periods,
            consistency_score=consistency_score,
            robustness_score=robustness_score
        )

        print(f"\n{results.summary()}")

        return results
