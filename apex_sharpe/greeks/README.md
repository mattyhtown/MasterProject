# APEX-SHARPE Greeks Calculator

Professional-grade options Greeks calculator built on FinancePy's Black-Scholes implementation.

## Features

- **Single Option Greeks**: Calculate all Greeks (delta, gamma, theta, vega, rho) for individual options
- **Portfolio Greeks**: Aggregate Greeks across multi-leg strategies
- **Risk Analytics**: Advanced risk metrics including gamma risk, theta decay, and delta neutrality
- **P&L Scenarios**: Project P&L across different underlying price scenarios
- **Market Curves**: Configurable discount and dividend curves
- **Type-Safe**: Full type hints and dataclasses for production use

## Installation

Requires FinancePy:

```bash
pip install financepy
```

## Quick Start

### Single Option Greeks

```python
from greeks_calculator import calculate_option_greeks
from datetime import date, timedelta

# Calculate Greeks for a single call option
greeks = calculate_option_greeks(
    option_type='CALL',
    strike=5850,
    expiration_date=date.today() + timedelta(days=30),
    spot_price=5800,
    implied_volatility=0.18,
    quantity=10
)

print(f"Position Delta: {greeks.position_delta}")
print(f"Position Theta: {greeks.position_theta}")
print(f"Position Value: ${greeks.position_value}")
```

### Portfolio Greeks (Iron Condor)

```python
from greeks_calculator import (
    GreeksCalculator,
    PortfolioGreeksCalculator,
    OptionContract,
    OptionType
)
from datetime import date, timedelta
from decimal import Decimal

# Setup calculator
calculator = GreeksCalculator(
    risk_free_rate=0.045,
    dividend_yield=0.018
)
portfolio_calc = PortfolioGreeksCalculator(calculator)

# Define Iron Condor legs
spot = Decimal('5800')
expiry = date.today() + timedelta(days=45)

contracts = [
    # Short put spread
    OptionContract(
        option_type=OptionType.PUT,
        strike=Decimal('5750'),
        expiration_date=expiry,
        quantity=-1,
        implied_volatility=Decimal('0.17')
    ),
    OptionContract(
        option_type=OptionType.PUT,
        strike=Decimal('5700'),
        expiration_date=expiry,
        quantity=1,
        implied_volatility=Decimal('0.18')
    ),
    # Short call spread
    OptionContract(
        option_type=OptionType.CALL,
        strike=Decimal('5850'),
        expiration_date=expiry,
        quantity=-1,
        implied_volatility=Decimal('0.17')
    ),
    OptionContract(
        option_type=OptionType.CALL,
        strike=Decimal('5900'),
        expiration_date=expiry,
        quantity=1,
        implied_volatility=Decimal('0.18')
    ),
]

# Calculate portfolio Greeks
snapshot = portfolio_calc.calculate_portfolio_greeks(contracts, spot)

print(f"Net Delta: {snapshot.total_delta}")
print(f"Net Theta: {snapshot.total_theta} (${snapshot.total_theta}/day)")
print(f"Delta Neutral: {snapshot.is_delta_neutral()}")
print(f"Portfolio Value: ${snapshot.total_value}")
```

## Architecture

### Core Classes

#### `GreeksCalculator`
Main calculator using FinancePy's Black-Scholes model.

```python
calculator = GreeksCalculator(
    risk_free_rate=0.045,      # 4.5% annual
    dividend_yield=0.018,       # 1.8% annual
    valuation_date=date.today()
)

# Calculate Greeks for single contract
greeks_data = calculator.calculate_greeks(
    contract=option_contract,
    spot_price=Decimal('5800'),
    implied_volatility=Decimal('0.18')
)

# Calculate position Greeks (accounts for quantity)
position_greeks = calculator.calculate_position_greeks(
    contract=option_contract,
    spot_price=Decimal('5800')
)

# Update market rates
calculator.update_curves(
    risk_free_rate=0.035,  # Rate cut scenario
    dividend_yield=0.020
)
```

#### `PortfolioGreeksCalculator`
Aggregates Greeks across multiple positions.

```python
portfolio_calc = PortfolioGreeksCalculator(calculator)

# Calculate portfolio Greeks
snapshot = portfolio_calc.calculate_portfolio_greeks(
    contracts=[contract1, contract2, contract3],
    spot_price=Decimal('5800')
)

# P&L scenario analysis
pnl_scenarios = portfolio_calc.calculate_portfolio_pnl_scenarios(
    contracts=contracts,
    current_spot=Decimal('5800'),
    spot_scenarios=[Decimal('5700'), Decimal('5800'), Decimal('5900')],
    days_forward=1
)
```

### Data Classes

#### `OptionContract`
Represents a single option position.

```python
contract = OptionContract(
    option_type=OptionType.CALL,
    strike=Decimal('5850'),
    expiration_date=date(2025, 3, 15),
    quantity=10,  # Positive for long, negative for short
    implied_volatility=Decimal('0.18')
)
```

#### `GreeksData`
Per-contract Greeks values.

- `delta`: Change in option price per $1 change in underlying
- `gamma`: Change in delta per $1 change in underlying
- `theta`: Change in option price per day
- `vega`: Change in option price per 1% change in IV
- `rho`: Change in option price per 1% change in interest rate

#### `PositionGreeks`
Greeks scaled by quantity (accounts for contract multiplier of 100).

#### `PortfolioGreeksSnapshot`
Aggregated portfolio Greeks with risk metrics.

```python
snapshot.total_delta          # Net delta
snapshot.total_gamma          # Net gamma
snapshot.total_theta          # Daily P&L decay
snapshot.total_vega           # IV sensitivity
snapshot.delta_percentage()   # Delta as % of underlying
snapshot.is_delta_neutral()   # Check if delta-neutral
snapshot.risk_metrics         # Additional risk calculations
```

## Greeks Definitions

### Delta
- **Definition**: Rate of change of option price with respect to underlying price
- **Range**:
  - Calls: 0 to 1 (long), 0 to -1 (short)
  - Puts: -1 to 0 (long), 0 to 1 (short)
- **Interpretation**:
  - Delta of 0.50 means option moves $0.50 for every $1 move in underlying
  - Portfolio delta shows directional exposure

### Gamma
- **Definition**: Rate of change of delta with respect to underlying price
- **Interpretation**:
  - High gamma = delta changes rapidly with spot movement
  - Maximum at ATM options
  - Risks: Sharp delta changes can create large directional exposure

### Theta
- **Definition**: Rate of change of option price with respect to time (time decay)
- **Units**: Change per day
- **Interpretation**:
  - Negative theta = position loses value with time (long options)
  - Positive theta = position gains value with time (short options)
  - Accelerates as expiration approaches

### Vega
- **Definition**: Rate of change of option price with respect to implied volatility
- **Units**: Change per 1% change in IV (e.g., from 20% to 21%)
- **Interpretation**:
  - Positive vega = benefits from IV increase (long options)
  - Negative vega = benefits from IV decrease (short options)

### Rho
- **Definition**: Rate of change of option price with respect to interest rate
- **Units**: Change per 1% change in risk-free rate
- **Interpretation**:
  - Generally smallest Greek
  - More significant for long-dated options

## Risk Metrics

The calculator provides additional risk metrics:

- **Delta Percentage**: Delta as percentage of notional exposure
- **Gamma Risk (1%)**: Potential delta change for 1% underlying move
- **Theta Percentage**: Daily decay as percentage of position value
- **Breakeven Days**: Days until theta erases current profit
- **Notional Exposure**: Total underlying exposure across positions

## Integration with APEX-SHARPE Database

The Greeks calculator integrates with the APEX-SHARPE database schema:

```python
from apex_sharpe.database.supabase_client import (
    SupabaseClient,
    GreeksSnapshot
)
from apex_sharpe.greeks import GreeksCalculator, PortfolioGreeksCalculator

# Calculate Greeks
snapshot = portfolio_calc.calculate_portfolio_greeks(contracts, spot)

# Record to database
db = SupabaseClient()
db.record_greeks_snapshot(GreeksSnapshot(
    position_id=position_id,
    trade_date=date.today(),
    dte=days_to_expiry,
    underlying_price=snapshot.underlying_price,
    portfolio_delta=snapshot.total_delta,
    portfolio_gamma=snapshot.total_gamma,
    portfolio_theta=snapshot.total_theta,
    portfolio_vega=snapshot.total_vega,
    position_value=snapshot.total_value,
    unrealized_pnl=unrealized_pnl
))
```

## Examples

Run the comprehensive examples:

```bash
python examples.py
```

Examples include:
1. Single option Greeks calculation
2. Iron Condor portfolio Greeks
3. Butterfly spread Greeks
4. P&L scenario analysis
5. Multi-strategy portfolio
6. Updating market curves

## Technical Notes

### FinancePy Integration

The calculator uses FinancePy's implementation of the Black-Scholes model:

- `EquityVanillaOption`: European-style options
- `BlackScholes`: Black-Scholes-Merton pricing model
- `DiscountCurveFlat`: Flat discount and dividend curves

### Precision

- Uses Python `Decimal` for financial calculations
- Converts to/from float only at FinancePy boundary
- 100x contract multiplier applied to position Greeks

### Date Handling

- Python `date` objects for inputs
- Automatically converts to FinancePy `Date` objects
- Time to expiry calculated in years (days / 365)

## API Reference

### GreeksCalculator

```python
__init__(
    risk_free_rate: float = 0.045,
    dividend_yield: float = 0.018,
    valuation_date: Optional[date] = None
)

calculate_greeks(
    contract: OptionContract,
    spot_price: Decimal,
    implied_volatility: Optional[Decimal] = None
) -> GreeksData

calculate_position_greeks(
    contract: OptionContract,
    spot_price: Decimal,
    implied_volatility: Optional[Decimal] = None
) -> PositionGreeks

update_curves(
    risk_free_rate: Optional[float] = None,
    dividend_yield: Optional[float] = None
) -> None
```

### PortfolioGreeksCalculator

```python
__init__(greeks_calculator: GreeksCalculator)

calculate_portfolio_greeks(
    contracts: List[OptionContract],
    spot_price: Decimal,
    implied_volatilities: Optional[Dict[int, Decimal]] = None
) -> PortfolioGreeksSnapshot

calculate_portfolio_pnl_scenarios(
    contracts: List[OptionContract],
    current_spot: Decimal,
    spot_scenarios: List[Decimal],
    implied_volatilities: Optional[Dict[int, Decimal]] = None,
    days_forward: int = 1
) -> List[Tuple[Decimal, Decimal]]
```

## Testing

Run tests with pytest:

```bash
pytest tests/test_greeks_calculator.py -v
```

## License

Part of the APEX-SHARPE Trading System.

## References

- FinancePy Documentation: https://github.com/domokane/FinancePy
- Black-Scholes Model: https://en.wikipedia.org/wiki/Black%E2%80%93Scholes_model
- Options Greeks: https://www.investopedia.com/trading/using-the-greeks-to-understand-options/
