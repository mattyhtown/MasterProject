# APEX-SHARPE Supabase Database

Complete database schema and client for the APEX-SHARPE Trading System.

## Setup Instructions

### 1. Create Supabase Project

1. Go to [https://supabase.com](https://supabase.com)
2. Create a new project
3. Note your project URL and API key

### 2. Run Schema Migration

In your Supabase project dashboard:

1. Go to **SQL Editor**
2. Copy the contents of `schema.sql`
3. Run the SQL script to create all tables, indexes, views, and functions

### 3. Configure Environment Variables

Create a `.env` file in your project root:

```bash
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-anon-key-here
```

### 4. Install Dependencies

```bash
pip install supabase python-dotenv
```

### 5. Test Connection

```python
from database.supabase_client import SupabaseClient

# Initialize client
db = SupabaseClient()

# Test connection
strategies = db.get_active_strategies()
print(f"Found {len(strategies)} active strategies")
```

## Database Schema Overview

### Core Tables

1. **strategies** - Trading strategy definitions and parameters
2. **positions** - Multi-leg options positions (iron condors, spreads, etc.)
3. **position_legs** - Individual option contracts in each position
4. **greeks_history** - Time-series tracking of Greeks evolution
5. **trades** - Individual trade executions
6. **performance_metrics** - Daily/weekly/monthly performance snapshots
7. **backtest_runs** - Historical backtest results and validation
8. **iv_rank_history** - IV rank tracking for entry signals
9. **market_conditions** - Broader market context (VIX, SPX, etc.)
10. **alerts** - Position alerts and notifications

### Materialized Views

- **open_positions_summary** - Quick overview of all open positions
- **daily_performance** - Daily P&L and metrics aggregation
- **strategy_performance_comparison** - Strategy comparison metrics

## Usage Examples

### Creating a Strategy

```python
from database.supabase_client import SupabaseClient

db = SupabaseClient()

strategy = db.create_strategy(
    name="High IV Premium Selling",
    strategy_type="IV_RANK",
    description="Sell premium when IV rank > 50%",
    parameters={
        "high_iv_threshold": 50,
        "target_dte": 35,
        "profit_target_pct": 0.50,
        "stop_loss_pct": 2.0
    }
)
print(f"Created strategy: {strategy['id']}")
```

### Opening a Position

```python
from database.supabase_client import Position, PositionLeg, SupabaseClient
from datetime import date, datetime
from decimal import Decimal

db = SupabaseClient()

# Create position
position = Position(
    symbol="SPY",
    position_type="IRON_CONDOR",
    entry_date=date.today(),
    entry_time=datetime.now(),
    entry_premium=Decimal("285.00"),  # Credit received
    entry_iv_rank=Decimal("62.5"),
    entry_dte=35,
    strategy_id=strategy['id']
)

position_record = db.create_position(position)
position_id = position_record['id']

# Add legs (iron condor = 4 legs)
legs = [
    PositionLeg(
        position_id=position_id,
        leg_index=0,
        option_type="PUT",
        strike=Decimal("545.00"),
        expiration_date=date(2024, 3, 15),
        quantity=1,
        action="BTO",
        entry_price=Decimal("1.50"),
        entry_fill_time=datetime.now(),
        entry_delta=Decimal("-0.05"),
        commission=Decimal("0.65")
    ),
    PositionLeg(
        position_id=position_id,
        leg_index=1,
        option_type="PUT",
        strike=Decimal("550.00"),
        expiration_date=date(2024, 3, 15),
        quantity=-1,
        action="STO",
        entry_price=Decimal("2.30"),
        entry_fill_time=datetime.now(),
        entry_delta=Decimal("-0.16"),
        commission=Decimal("0.65")
    ),
    # ... call side
]

for leg in legs:
    db.add_position_leg(leg)
```

### Recording Greeks History

```python
from database.supabase_client import GreeksSnapshot, SupabaseClient
from datetime import date
from decimal import Decimal

db = SupabaseClient()

snapshot = GreeksSnapshot(
    position_id=position_id,
    trade_date=date.today(),
    dte=33,
    underlying_price=Decimal("560.50"),
    portfolio_delta=Decimal("0.12"),
    portfolio_gamma=Decimal("0.002"),
    portfolio_theta=Decimal("-8.50"),
    portfolio_vega=Decimal("15.30"),
    position_value=Decimal("220.00"),
    unrealized_pnl=Decimal("65.00")
)

db.record_greeks_snapshot(snapshot)
```

### Closing a Position

```python
from decimal import Decimal

db = SupabaseClient()

# Close position at profit target
db.close_position(
    position_id=position_id,
    exit_reason="PROFIT_TARGET",
    realized_pnl=Decimal("142.50"),  # 50% of max profit
    exit_dte=24
)

# Update legs with exit prices
legs = db.get_position_legs(position_id)
for leg in legs:
    db.update_leg_exit(
        leg_id=leg['id'],
        exit_price=Decimal(leg['entry_price']) * Decimal("0.5"),
        exit_fill_time=datetime.now()
    )
```

### Recording Backtest Results

```python
from database.supabase_client import SupabaseClient
from datetime import date
from decimal import Decimal

db = SupabaseClient()

backtest = db.create_backtest_run(
    run_name="IV Rank Strategy - 2023 Backtest",
    strategy_id=strategy['id'],
    start_date=date(2023, 1, 1),
    end_date=date(2023, 12, 31),
    initial_capital=Decimal("100000.00"),
    strategy_parameters={
        "high_iv_threshold": 50,
        "target_dte": 35
    },
    results={
        "final_capital": Decimal("118500.00"),
        "total_return_pct": 18.5,
        "sharpe_ratio": 1.82,
        "max_drawdown": -0.148,
        "total_trades": 52,
        "winning_trades": 34,
        "win_rate": 65.4,
        "avg_days_in_trade": 18.5
    }
)
```

### Querying Performance

```python
from database.supabase_client import SupabaseClient
from datetime import date

db = SupabaseClient()

# Get open positions summary
open_positions = db.get_open_positions_summary()
for pos in open_positions:
    print(f"{pos['symbol']} {pos['position_type']}: ${pos['unrealized_pnl']}")

# Get daily performance
daily = db.get_daily_performance(
    start_date=date(2024, 1, 1),
    end_date=date.today()
)
for day in daily:
    print(f"{day['date']}: ${day['daily_pnl']} ({day['trades']} trades)")

# Compare strategies
comparison = db.get_strategy_performance_comparison()
for strat in comparison:
    print(f"{strat['strategy_name']}: {strat['win_rate']}% win rate, ${strat['total_pnl']} total P&L")
```

## Database Functions

### calculate_position_pnl(position_uuid)

Calculates realized P&L for a position by summing all leg P&L and subtracting commissions.

```sql
SELECT calculate_position_pnl('123e4567-e89b-12d3-a456-426614174000');
```

## Row Level Security (RLS)

To enable RLS for multi-user environments:

```sql
-- Enable RLS on all tables
ALTER TABLE strategies ENABLE ROW LEVEL SECURITY;
ALTER TABLE positions ENABLE ROW LEVEL SECURITY;
-- ... repeat for other tables

-- Create policies
CREATE POLICY "Users can view their own data"
ON positions FOR SELECT
USING (auth.uid() = user_id);

CREATE POLICY "Users can insert their own data"
ON positions FOR INSERT
WITH CHECK (auth.uid() = user_id);
```

## Indexes

All critical queries have supporting indexes:
- Position lookups by symbol, status, and date
- Greeks history by position and date
- Performance metrics by strategy and date
- Fast filtering on open positions

## Maintenance

### Archiving Old Data

```sql
-- Archive closed positions older than 2 years
CREATE TABLE positions_archive AS
SELECT * FROM positions
WHERE status = 'CLOSED'
AND exit_date < NOW() - INTERVAL '2 years';

DELETE FROM positions
WHERE id IN (SELECT id FROM positions_archive);
```

### Vacuum and Analyze

```sql
VACUUM ANALYZE positions;
VACUUM ANALYZE position_legs;
VACUUM ANALYZE greeks_history;
```

## Support

For issues or questions:
- Review the schema comments in `schema.sql`
- Check the Python client docstrings
- Consult Supabase documentation: https://supabase.com/docs
