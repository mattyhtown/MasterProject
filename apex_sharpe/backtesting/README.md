# APEX-SHARPE Backtesting Engine

Event-driven backtesting infrastructure for options trading strategies with comprehensive performance analysis and validation.

## Overview

The APEX-SHARPE backtesting engine provides:

- **Event-Driven Architecture**: Market data, expiration, and signal events
- **Position Lifecycle Management**: Entry, tracking, exit, and Greeks calculation
- **Historical Data Management**: Caching and efficient retrieval from ORATS
- **Performance Analysis**: Options-specific metrics, Greeks attribution, risk-adjusted returns
- **Validation Methodology**: Train/test splits, walk-forward analysis, multi-scenario testing

## Architecture

```
backtesting/
├── __init__.py                      # Module exports
├── backtest_engine.py               # Core event-driven engine
├── historical_data_manager.py       # Data loading and caching
├── performance_analyzer.py          # Performance metrics
├── validator.py                     # Validation methodologies
└── README.md                        # This file
```

## Quick Start

### Basic Backtest

```python
from datetime import date
from decimal import Decimal
from apex_sharpe.backtesting import BacktestEngine, BacktestConfig
from apex_sharpe.strategies import IronCondorStrategy
from apex_sharpe.data import ORATSAdapter, create_data_manager

# Configure backtest
config = BacktestConfig(
    start_date=date(2023, 1, 1),
    end_date=date(2023, 12, 31),
    initial_capital=Decimal("100000"),
    ticker="SPY",
    commission_per_contract=Decimal("0.65"),
    slippage_pct=Decimal("0.0005")
)

# Initialize components
orats_adapter = ORATSAdapter(mcp_tools)
data_manager = create_data_manager(orats_adapter)

strategy = IronCondorStrategy(
    name="IC_Test",
    symbol="SPY",
    initial_capital=Decimal("100000"),
    target_dte=45,
    target_delta=Decimal("0.16")
)

# Run backtest
engine = BacktestEngine(config, strategy, data_manager)
results = engine.run()

# Print results
print(results.summary())
```

### Train/Test Split Validation

```python
from apex_sharpe.backtesting import BacktestValidator

validator = BacktestValidator()

# 60/40 train/test split
validation_results = validator.train_test_split(
    config=config,
    strategy=strategy,
    data_manager=data_manager,
    train_ratio=0.6,
    sharpe_threshold=1.0
)

print(validation_results.summary())

# Check if strategy passed validation
if validation_results.avg_test_sharpe >= 1.0:
    print("✓ Strategy validated with Sharpe >= 1.0")
else:
    print("✗ Strategy needs improvement")
```

### Walk-Forward Analysis

```python
from apex_sharpe.backtesting import WalkForwardConfig

# Configure walk-forward
wf_config = WalkForwardConfig(
    train_window_days=180,  # 6 months training
    test_window_days=60,    # 2 months testing
    step_days=60            # Move forward 2 months
)

# Run walk-forward
results = validator.walk_forward(
    config=config,
    strategy=strategy,
    data_manager=data_manager,
    wf_config=wf_config
)

print(results.summary())
```

## Components

### BacktestEngine

The core backtesting engine with event-driven architecture.

**Features:**
- Event queue processing (priority-based)
- Position tracking with daily Greeks updates
- Commission and slippage modeling
- Integration with strategy base classes
- Daily performance tracking

**Events:**
- `MarketDataEvent`: New market data available
- `ExpirationEvent`: Options expiring
- `SignalEvent`: Trading signal from strategy

**Example:**
```python
engine = BacktestEngine(config, strategy, data_manager)
results = engine.run()

print(f"Final Capital: ${results.final_capital:,.2f}")
print(f"Sharpe Ratio: {results.sharpe_ratio:.3f}")
print(f"Max Drawdown: {results.max_drawdown:.2f}%")
```

### HistoricalDataManager

Manages loading and caching of historical options data.

**Features:**
- Local disk caching (pickle)
- In-memory caching
- Date range queries
- Cache management and statistics

**Example:**
```python
from apex_sharpe.backtesting import DataCache

cache_config = DataCache(
    cache_dir=".cache/apex_sharpe",
    use_cache=True,
    cache_ttl_days=30
)

manager = HistoricalDataManager(orats_adapter, cache_config)

# Preload data for faster backtesting
manager.preload_data("SPY", start_date, end_date)

# Get date range
chains = manager.get_date_range("SPY", start_date, end_date)

# Clear cache
manager.clear_cache("SPY")
```

### PerformanceAnalyzer

Calculates comprehensive performance metrics.

**Metrics:**
- Risk-adjusted: Sharpe, Sortino, Calmar
- Risk: Max drawdown, volatility, downside deviation
- Trade statistics: Win rate, profit factor, expectancy
- Options-specific: Avg DTE at entry/exit, holding period
- Greeks attribution: Theta, delta, vega, gamma P&L

**Example:**
```python
from apex_sharpe.backtesting import PerformanceAnalyzer

analyzer = PerformanceAnalyzer(config)
results = analyzer.analyze(
    closed_positions=engine.closed_positions,
    equity_curve=engine.equity_curve,
    equity_dates=engine.equity_dates,
    daily_stats=engine.daily_stats
)

# Access specific metrics
print(f"Sharpe Ratio: {results.sharpe_ratio:.3f}")
print(f"Win Rate: {results.trade_stats.win_rate:.2f}%")
print(f"Max Drawdown: {results.max_drawdown:.2f}%")

# Greeks attribution (if enabled)
if results.greeks_attribution:
    ga = results.greeks_attribution
    print(f"Theta P&L: ${ga.theta_pnl:,.2f} ({ga.theta_pct:.1f}%)")
    print(f"Delta P&L: ${ga.delta_pnl:,.2f} ({ga.delta_pct:.1f}%)")
```

### BacktestValidator

Implements proper validation methodologies.

**Methods:**
1. **Train/Test Split**: Evaluate parameters on training data, validate on test data
2. **Walk-Forward**: Rolling out-of-sample testing across time
3. **Multi-Scenario**: Robustness testing across parameter sets

**Validation Criteria:**
- Out-of-sample Sharpe >= 1.0
- Train/test degradation < 30%
- Robustness score >= 70% (percentage achieving targets)

**Example:**
```python
validator = BacktestValidator()

# Train/test split
results = validator.train_test_split(
    config, strategy, data_manager,
    train_ratio=0.6
)

# Walk-forward
results = validator.walk_forward(
    config, strategy, data_manager,
    wf_config=WalkForwardConfig()
)

# Multi-scenario
scenarios = [
    {'target_dte': 30, 'target_delta': 0.10},
    {'target_dte': 45, 'target_delta': 0.16},
    {'target_dte': 60, 'target_delta': 0.20},
]

results = validator.multi_scenario(
    config,
    lambda: IronCondorStrategy(...),
    data_manager,
    scenarios
)
```

## Configuration

### BacktestConfig

```python
from apex_sharpe.backtesting import BacktestConfig
from datetime import date
from decimal import Decimal

config = BacktestConfig(
    # Required
    start_date=date(2023, 1, 1),
    end_date=date(2023, 12, 31),
    initial_capital=Decimal("100000"),
    ticker="SPY",

    # Costs
    commission_per_contract=Decimal("0.65"),  # Per contract
    slippage_pct=Decimal("0.0005"),           # 5 basis points

    # Risk
    max_positions=5,
    max_capital_per_trade=Decimal("0.20"),    # 20% max per trade

    # Greeks
    risk_free_rate=0.045,                     # 4.5%
    dividend_yield=0.018,                     # 1.8%

    # Tracking
    track_greeks_daily=True,
    calculate_attribution=True
)
```

### WalkForwardConfig

```python
from apex_sharpe.backtesting import WalkForwardConfig

wf_config = WalkForwardConfig(
    train_window_days=180,      # 6 months training
    test_window_days=60,        # 2 months testing
    step_days=60,               # Move forward 2 months
    min_trades_per_period=5     # Minimum trades for valid period
)
```

## Results and Reporting

### BacktestResults

The main results object contains:

```python
# Access metrics
results.sharpe_ratio           # Annualized Sharpe
results.total_return_pct       # Total return percentage
results.max_drawdown          # Maximum drawdown percentage
results.trade_stats.win_rate  # Win rate percentage

# Print formatted summary
print(results.summary())

# Access equity curve
for date, equity in zip(results.equity_dates, results.equity_curve):
    print(f"{date}: ${equity:,.2f}")

# Trade statistics
stats = results.trade_stats
print(f"Total Trades: {stats.total_trades}")
print(f"Win Rate: {stats.win_rate:.2f}%")
print(f"Profit Factor: {stats.profit_factor:.3f}")
print(f"Avg Holding Days: {stats.avg_holding_days:.1f}")
```

### ValidationResults

```python
# Access validation metrics
validation.avg_test_sharpe           # Average out-of-sample Sharpe
validation.std_test_sharpe           # Standard deviation
validation.robustness_score          # 0-100, % achieving targets
validation.consistency_score         # 0-100, consistency across periods

# Print summary
print(validation.summary())

# Access individual periods
for period in validation.periods:
    if period.test_results:
        print(f"Period {period.period_id}: "
              f"Sharpe={period.test_results.sharpe_ratio:.3f}")
```

## Best Practices

### 1. Always Use Validation

Never rely on a single backtest. Always validate with:
- Train/test split (minimum 60/40)
- Walk-forward analysis (5+ periods)
- Out-of-sample testing

```python
# Good: Proper validation
validator = BacktestValidator()
results = validator.train_test_split(config, strategy, data_manager)

if results.avg_test_sharpe >= 1.0 and results.robustness_score >= 70:
    print("Strategy validated for production")

# Bad: Single backtest without validation
engine = BacktestEngine(config, strategy, data_manager)
results = engine.run()
# Don't deploy based on this alone!
```

### 2. Check Data Quality

```python
# Estimate data size before loading
estimate = data_manager.estimate_data_size("SPY", start_date, end_date)
print(f"Trading Days: {estimate['trading_days']}")
print(f"Estimated Size: {estimate['estimated_mb']:.1f} MB")

# Preload for faster backtesting
data_manager.preload_data("SPY", start_date, end_date)

# Check cache stats
stats = data_manager.get_cache_stats()
print(f"Cached Files: {stats['disk_cached_files']}")
print(f"Cache Size: {stats['total_cache_size_mb']:.1f} MB")
```

### 3. Monitor Performance During Development

```python
# Track multiple metrics
results = engine.run()

print(f"Sharpe: {results.sharpe_ratio:.3f} (target >= 1.0)")
print(f"Max DD: {results.max_drawdown:.2f}% (target < 20%)")
print(f"Win Rate: {results.trade_stats.win_rate:.2f}% (target > 50%)")
print(f"Trades: {results.trade_stats.total_trades} (target > 20)")

# Greeks attribution
if results.greeks_attribution:
    ga = results.greeks_attribution
    print(f"\nTheta contributed {ga.theta_pct:.1f}% of P&L")
```

### 4. Store Results in Database

```python
from apex_sharpe.database import SupabaseClient

client = SupabaseClient()

# Store backtest run
client.create_backtest_run(
    run_name=f"IC_SPY_{date.today()}",
    strategy_id=strategy_id,
    start_date=config.start_date,
    end_date=config.end_date,
    initial_capital=config.initial_capital,
    strategy_parameters=strategy.parameters,
    results={
        'sharpe_ratio': results.sharpe_ratio,
        'total_return_pct': results.total_return_pct,
        'max_drawdown': results.max_drawdown,
        'total_trades': results.trade_stats.total_trades,
        'win_rate': results.trade_stats.win_rate
    }
)
```

## Integration with APEX-SHARPE

### Strategy Integration

The backtesting engine seamlessly integrates with strategies that inherit from `BaseStrategy`:

```python
from apex_sharpe.strategies import BaseStrategy

class MyStrategy(BaseStrategy):
    def analyze(self, chain, iv_data, market_data):
        # Generate signals
        pass

    def select_strikes(self, chain, signal):
        # Select specific strikes
        pass

    def should_exit(self, position, current_chain):
        # Exit logic
        pass

    def should_adjust(self, position, current_chain):
        # Adjustment logic
        pass

# Use in backtest
strategy = MyStrategy(...)
engine = BacktestEngine(config, strategy, data_manager)
results = engine.run()
```

### Database Integration

Results are stored in the `backtest_runs` table:

```sql
-- Query backtest history
SELECT
    run_name,
    sharpe_ratio,
    total_return_pct,
    max_drawdown,
    total_trades,
    run_at
FROM backtest_runs
WHERE strategy_id = 'your-strategy-id'
ORDER BY run_at DESC;
```

## Performance Optimization

### Caching

```python
# Enable caching for repeated backtests
cache_config = DataCache(
    cache_dir=".cache/apex_sharpe",
    use_cache=True,
    cache_ttl_days=30
)

manager = HistoricalDataManager(orats_adapter, cache_config)

# First run: loads from ORATS
chains = manager.get_date_range("SPY", start_date, end_date)

# Subsequent runs: loads from cache (much faster)
chains = manager.get_date_range("SPY", start_date, end_date)
```

### Parallel Validation

For walk-forward or multi-scenario testing, consider parallel execution:

```python
from concurrent.futures import ProcessPoolExecutor

def run_scenario(scenario_params):
    # Run single scenario
    strategy = create_strategy(**scenario_params)
    engine = BacktestEngine(config, strategy, data_manager)
    return engine.run()

# Parallel execution
scenarios = [...]
with ProcessPoolExecutor() as executor:
    results = list(executor.map(run_scenario, scenarios))
```

## Troubleshooting

### Issue: Slow data loading

**Solution**: Enable caching and preload data
```python
manager.preload_data("SPY", start_date, end_date)
```

### Issue: Not enough trades in validation period

**Solution**: Adjust walk-forward window or check strategy parameters
```python
wf_config = WalkForwardConfig(
    train_window_days=180,
    test_window_days=90,  # Increase test window
    min_trades_per_period=3  # Lower threshold
)
```

### Issue: High memory usage

**Solution**: Clear cache between runs
```python
manager.clear_cache()
```

### Issue: Inconsistent validation results

**Solution**: Check for look-ahead bias, ensure proper train/test separation
```python
# Verify no data leakage
assert test_config.start_date > train_config.end_date
```

## Example: Complete Backtest Workflow

```python
from datetime import date
from decimal import Decimal
from apex_sharpe.backtesting import *
from apex_sharpe.strategies import IronCondorStrategy
from apex_sharpe.data import ORATSAdapter, create_data_manager

# 1. Configuration
config = BacktestConfig(
    start_date=date(2023, 1, 1),
    end_date=date(2023, 12, 31),
    initial_capital=Decimal("100000"),
    ticker="SPY"
)

# 2. Initialize
orats_adapter = ORATSAdapter(mcp_tools)
data_manager = create_data_manager(orats_adapter)

strategy = IronCondorStrategy(
    name="IC_SPY",
    symbol="SPY",
    initial_capital=Decimal("100000")
)

# 3. Run validation
validator = BacktestValidator()

# Train/test split
tt_results = validator.train_test_split(
    config, strategy, data_manager,
    train_ratio=0.6
)

# Walk-forward
wf_results = validator.walk_forward(
    config, strategy, data_manager,
    wf_config=WalkForwardConfig()
)

# 4. Evaluate
print("\nTrain/Test Results:")
print(tt_results.summary())

print("\nWalk-Forward Results:")
print(wf_results.summary())

# 5. Make decision
if (tt_results.avg_test_sharpe >= 1.0 and
    wf_results.avg_test_sharpe >= 1.0 and
    wf_results.robustness_score >= 70):
    print("\n✓ Strategy validated for production deployment")
else:
    print("\n✗ Strategy needs further development")

# 6. Store results
from apex_sharpe.database import SupabaseClient

client = SupabaseClient()
client.create_backtest_run(
    run_name=f"IC_SPY_{date.today()}",
    strategy_id="ic-strategy",
    start_date=config.start_date,
    end_date=config.end_date,
    initial_capital=config.initial_capital,
    strategy_parameters=strategy.parameters,
    results={
        'sharpe_ratio': tt_results.avg_test_sharpe,
        'total_return_pct': tt_results.avg_test_return,
        'robustness_score': wf_results.robustness_score
    }
)
```

## References

- **CrewTrader Math Utils**: `/Users/mh/CrewTrader/utils/math_utils.py`
- **Base Strategy**: `/Users/mh/apex-sharpe/strategies/base_strategy.py`
- **ORATS Adapter**: `/Users/mh/apex-sharpe/data/orats_adapter.py`
- **Greeks Calculator**: `/Users/mh/apex-sharpe/greeks/greeks_calculator.py`
- **Supabase Client**: `/Users/mh/apex-sharpe/database/supabase_client.py`

## License

Part of the APEX-SHARPE Trading System.
