# APEX-SHARPE Execution Layer

Production-ready order execution and position management infrastructure for multi-leg options trading.

## Overview

The execution layer provides:

- **Options Paper Broker**: Extends CrewTrader's PaperBroker for multi-leg options execution
- **Spread Builder**: Constructs common spread types with delta-based strike selection
- **Fill Simulator**: Realistic fill simulation with bid/ask spreads and slippage
- **Position Tracker**: Multi-leg position tracking with Greeks updates and exit monitoring

## Components

### 1. OptionsPaperBroker

Extends CrewTrader's `PaperBroker` to support multi-leg options trades.

**Features:**
- Multi-leg order execution (iron condors, verticals, strangles, etc.)
- Realistic fill simulation with bid/ask spreads
- Per-contract commission calculation
- Early assignment simulation
- Position tracking with Greeks updates

**Example:**
```python
from execution import OptionsPaperBroker
from strategies.base_strategy import MultiLegSpread

# Initialize broker
broker = OptionsPaperBroker(
    initial_capital=100_000,
    commission_per_contract=0.65,
    assignment_fee=5.00
)

# Submit multi-leg spread order
order = broker.submit_spread_order(iron_condor_spread)

# Check if filled
if order.is_filled:
    print(f"Filled at {order.fill_price}")
    print(f"Commission: {order.total_commission}")

# Close spread
close_order = broker.close_spread(position_id)
```

**Key Methods:**
- `submit_spread_order(spread, order_type, limit_price)` - Submit multi-leg order
- `close_spread(position_id, order_type, limit_price)` - Close spread position
- `get_open_spread(position_id)` - Get open spread details
- `simulate_early_assignment(position_id, leg_index, price)` - Simulate early assignment
- `get_account_summary()` - Get account details with options metrics

### 2. SpreadBuilder

Constructs multi-leg options spreads with validation.

**Supported Spreads:**
- Iron Condors
- Vertical Spreads (Bull/Bear Call/Put)
- Strangles (Long/Short)
- Butterflies
- Calendars (coming soon)
- Diagonals (coming soon)

**Example:**
```python
from execution import SpreadBuilder
from decimal import Decimal

builder = SpreadBuilder()

# Build iron condor with 10-delta short strikes, 10-point wings
iron_condor = builder.build_iron_condor(
    chain=options_chain,
    put_short_delta=Decimal("0.10"),
    call_short_delta=Decimal("0.10"),
    wing_width=Decimal("10"),
    expiration_dte=45,
    quantity=1
)

print(f"Max Profit: {iron_condor.max_profit}")
print(f"Max Loss: {iron_condor.max_loss}")
print(f"Breakeven Points: {iron_condor.breakeven_points}")
print(f"Portfolio Delta: {iron_condor.portfolio_delta}")

# Validate spread structure
is_valid, error_msg = builder.validate_spread(iron_condor)
```

**Key Methods:**
- `build_iron_condor(chain, put_delta, call_delta, wing_width, dte, qty)`
- `build_vertical_spread(chain, option_type, short_strike, long_strike, dte, qty)`
- `build_strangle(chain, put_delta, call_delta, dte, qty, long_or_short)`
- `build_butterfly(chain, option_type, strikes, dte, qty)`
- `validate_spread(spread)` - Validate spread structure
- `calculate_net_premium(spread)` - Calculate net premium using mid prices

### 3. FillSimulator

Simulates realistic option order fills with market microstructure effects.

**Features:**
- Bid/ask spread modeling based on liquidity
- Slippage calculation for market orders
- Market impact for larger orders
- Time-of-day effects (wider spreads at open/close)
- Limit order fill probability estimation

**Example:**
```python
from execution import FillSimulator
from decimal import Decimal

simulator = FillSimulator(
    base_spread_pct=0.02,      # 2% base spread
    slippage_bps=5.0,          # 5 bps slippage
    market_impact_coef=0.001   # Market impact coefficient
)

# Simulate market buy order
fill_price = simulator.simulate_fill(
    order_type="MARKET",
    side="BUY",
    quantity=10,
    bid=Decimal("5.80"),
    ask=Decimal("5.90"),
    volume=1500
)

print(f"Filled at: {fill_price}")

# Estimate limit order fill probability
prob = simulator.estimate_fill_probability(
    order_type="LIMIT",
    side="BUY",
    limit_price=Decimal("5.85"),  # Mid price
    bid=Decimal("5.80"),
    ask=Decimal("5.90"),
    volume=1500
)
print(f"Fill probability: {prob:.1%}")
```

**Market Session Effects:**
| Session | Time (ET) | Spread Multiplier |
|---------|-----------|-------------------|
| Open | 9:30 - 10:00 | 2.0x (wider) |
| Mid-Morning | 10:00 - 11:30 | 1.0x (normal) |
| Lunch | 11:30 - 13:30 | 1.3x |
| Mid-Afternoon | 13:30 - 15:00 | 1.0x |
| Close | 15:00 - 16:00 | 1.5x (wider) |

### 4. PositionTracker

Tracks multi-leg positions with Greeks updates and exit monitoring.

**Features:**
- Daily Greeks updates using GreeksCalculator
- Mark-to-market P&L calculation
- Automatic exit condition checking
- Expiration and assignment handling
- Supabase persistence for position history

**Example:**
```python
from execution import PositionTracker
from greeks import GreeksCalculator
from database import SupabaseClient

# Initialize
greeks_calc = GreeksCalculator(risk_free_rate=0.045, dividend_yield=0.018)
db_client = SupabaseClient()

tracker = PositionTracker(
    greeks_calculator=greeks_calc,
    supabase_client=db_client,
    exit_profit_pct=0.50,      # Exit at 50% of max profit
    exit_loss_pct=2.00,        # Exit at 200% of max loss
    exit_dte=7,                # Exit at 7 DTE
    exit_delta_threshold=0.30  # Exit if |delta| > 0.30
)

# Open position
position_id = tracker.open_position(iron_condor_spread, strategy_id="IC_001")

# Daily update loop
tracker.update_position(position_id, current_options_chain)

# Check exit conditions
should_exit, reason = tracker.check_exit_conditions(position_id)
if should_exit:
    print(f"Exit signal: {reason}")
    closed = tracker.close_position(
        position_id,
        reason,
        exit_premium=current_spread_value
    )
    print(f"Realized P&L: {closed.realized_pnl}")

# Get portfolio Greeks across all positions
portfolio_greeks = tracker.get_portfolio_greeks()
print(f"Portfolio Delta: {portfolio_greeks['total_delta']}")
```

**Exit Conditions:**
1. **Profit Target**: Unrealized P&L >= 50% of max profit
2. **Loss Limit**: Unrealized P&L <= -200% of max loss
3. **DTE Threshold**: Days to expiration <= 7 days
4. **Delta Threshold**: Abs(portfolio_delta) > 0.30

## Integration with APEX-SHARPE

### Complete Trading Flow

```python
from execution import (
    OptionsPaperBroker,
    SpreadBuilder,
    FillSimulator,
    PositionTracker
)
from data import ORATSAdapter
from greeks import GreeksCalculator
from database import SupabaseClient
from strategies import IronCondorStrategy

# 1. Initialize components
broker = OptionsPaperBroker(initial_capital=100_000)
builder = SpreadBuilder()
simulator = FillSimulator()
greeks_calc = GreeksCalculator()
db_client = SupabaseClient()
tracker = PositionTracker(greeks_calc, db_client)

# 2. Initialize strategy
strategy = IronCondorStrategy(
    name="IC_45DTE",
    symbol="SPX",
    initial_capital=100_000,
    sharpe_threshold=1.0
)

# 3. Get market data
orats = ORATSAdapter()
chain = orats.get_options_chain("SPX")
iv_data = orats.get_iv_data("SPX")

# 4. Generate signal
signal = strategy.analyze(chain, iv_data, market_data)

# 5. Check if can trade (Sharpe filter)
if signal.action == SignalAction.ENTER and strategy.can_trade():

    # 6. Build spread
    spread = builder.build_iron_condor(
        chain,
        put_short_delta=signal.target_delta_short,
        call_short_delta=signal.target_delta_short,
        wing_width=Decimal("10"),
        expiration_dte=signal.target_dte
    )

    # 7. Submit order
    order = broker.submit_spread_order(spread)

    # 8. Track position
    if order.is_filled:
        position_id = tracker.open_position(
            spread,
            strategy_id=strategy.name
        )
        print(f"Position opened: {position_id}")

# 9. Daily management loop
for position_id in tracker.get_open_position_ids():
    # Update with current market data
    tracker.update_position(position_id, current_chain)

    # Check exit conditions
    should_exit, reason = tracker.check_exit_conditions(position_id)

    if should_exit:
        # Close position
        close_order = broker.close_spread(position_id)
        if close_order.is_filled:
            tracker.close_position(
                position_id,
                reason,
                close_order.fill_price
            )
```

## Commission and Fee Structure

### Default Fees
- **Commission per contract**: $0.65
- **Early assignment fee**: $5.00
- **Slippage**: 5 basis points (0.05%)

### Example Calculation
For an iron condor (4 legs, 1 contract each):
- Total contracts: 4
- Commission: 4 × $0.65 = $2.60 (entry)
- Commission: 4 × $0.65 = $2.60 (exit)
- **Total round-trip commission**: $5.20

## Database Persistence

Positions are automatically persisted to Supabase (if configured):

### Tables Used
- `positions` - Position header (entry/exit info)
- `position_legs` - Individual legs with Greeks
- `greeks_history` - Daily Greeks snapshots
- `performance_metrics` - Strategy performance

### Example Queries
```python
# Get all open positions
open_positions = db_client.get_open_positions(symbol="SPX")

# Get position history with legs
position = db_client.get_position_by_id(position_id)
legs = db_client.get_position_legs(position_id)

# Get Greeks history
greeks = db_client.get_greeks_history(
    position_id,
    start_date=date(2025, 1, 1),
    end_date=date(2025, 2, 1)
)
```

## Testing

### Unit Tests
```bash
# Run all execution layer tests
pytest tests/execution/ -v

# Run specific test file
pytest tests/execution/test_spread_builder.py -v

# Run with coverage
pytest tests/execution/ --cov=execution --cov-report=html
```

### Example Test Cases
```python
def test_iron_condor_builder():
    builder = SpreadBuilder()
    spread = builder.build_iron_condor(
        chain,
        Decimal("0.10"),
        Decimal("0.10"),
        Decimal("10")
    )

    assert len(spread.legs) == 4
    assert spread.spread_type == SpreadType.IRON_CONDOR
    assert spread.max_profit is not None
    assert spread.max_loss is not None

def test_fill_simulation():
    simulator = FillSimulator()
    fill = simulator.simulate_fill(
        "MARKET", "BUY", 10,
        Decimal("5.80"), Decimal("5.90"), 1500
    )

    # Should pay ask + slippage
    assert fill > Decimal("5.90")
```

## Performance Considerations

### Optimization Tips
1. **Batch Greeks Updates**: Update all positions once per day
2. **Cache Options Chains**: Reuse chain data across multiple spreads
3. **Database Writes**: Use batch inserts for Greeks snapshots
4. **Memory Management**: Close old positions to free memory

### Scalability
- **Single process**: Handle 100+ concurrent positions
- **Database**: Supabase handles millions of Greeks snapshots
- **Greeks calculation**: ~10ms per position per update

## Error Handling

```python
try:
    spread = builder.build_iron_condor(chain, ...)
    is_valid, error = builder.validate_spread(spread)

    if not is_valid:
        print(f"Invalid spread: {error}")
    else:
        order = broker.submit_spread_order(spread)

except ValueError as e:
    print(f"Strike selection failed: {e}")
except Exception as e:
    print(f"Unexpected error: {e}")
```

## API Reference

See inline docstrings in each module for detailed API documentation.

### Quick Links
- **OptionsPaperBroker**: `execution/options_broker.py`
- **SpreadBuilder**: `execution/spread_builder.py`
- **FillSimulator**: `execution/fill_simulator.py`
- **PositionTracker**: `execution/position_tracker.py`

## Future Enhancements

- [ ] Live broker integration (Interactive Brokers, TastyTrade)
- [ ] Advanced adjustment logic (rolling, hedging)
- [ ] Calendar and diagonal spread support
- [ ] Real-time Greeks streaming
- [ ] Portfolio-level risk limits
- [ ] Multi-symbol position management
- [ ] Tax lot optimization

## License

Part of the APEX-SHARPE Trading System.
