# Getting Started with APEX-SHARPE Greeks Calculator

Quick start guide for using the Greeks calculator in your trading system.

## Installation

### 1. Install FinancePy

The Greeks calculator requires FinancePy:

```bash
cd /Users/mh/apex-sharpe/greeks
pip install -r requirements.txt
```

Or install individually:
```bash
pip install financepy numpy pandas pytest
```

### 2. Verify Installation

Run the verification script:

```bash
python verify_setup.py
```

You should see:
```
✓ Setup Complete! Greeks calculator is ready to use.
```

## Quick Start

### 30-Second Example

```python
from greeks import calculate_option_greeks
from datetime import date, timedelta

# Calculate Greeks for a call option
greeks = calculate_option_greeks(
    option_type='CALL',
    strike=5850,
    expiration_date=date.today() + timedelta(days=30),
    spot_price=5800,
    implied_volatility=0.18,
    quantity=10
)

print(f"Position Delta: {greeks.position_delta:.2f}")
print(f"Daily Theta: ${greeks.position_theta:.2f}")
print(f"Position Value: ${greeks.position_value:.2f}")
```

### 2-Minute Example - Iron Condor

```python
from greeks import GreeksCalculator, PortfolioGreeksCalculator, OptionContract, OptionType
from datetime import date, timedelta
from decimal import Decimal

# Setup
calculator = GreeksCalculator()
portfolio_calc = PortfolioGreeksCalculator(calculator)

# Define Iron Condor: 5700/5750/5850/5900
expiry = date.today() + timedelta(days=45)
contracts = [
    OptionContract(OptionType.PUT, Decimal('5750'), expiry, -1, Decimal('0.17')),
    OptionContract(OptionType.PUT, Decimal('5700'), expiry, 1, Decimal('0.18')),
    OptionContract(OptionType.CALL, Decimal('5850'), expiry, -1, Decimal('0.17')),
    OptionContract(OptionType.CALL, Decimal('5900'), expiry, 1, Decimal('0.18')),
]

# Calculate Greeks
snapshot = portfolio_calc.calculate_portfolio_greeks(contracts, Decimal('5800'))

print(f"Net Delta: {snapshot.total_delta:.2f}")
print(f"Net Theta: ${snapshot.total_theta:.2f}/day")
print(f"Delta Neutral: {snapshot.is_delta_neutral()}")
print(f"Portfolio Value: ${snapshot.total_value:.2f}")
```

## Running Examples

### Comprehensive Examples

```bash
python examples.py
```

This runs 6 examples demonstrating:
1. Single option Greeks
2. Iron Condor portfolio
3. Butterfly spread
4. P&L scenarios
5. Multi-strategy portfolio
6. Market curve updates

### Database Integration Example

```bash
# Set environment variables first
export SUPABASE_URL="your-supabase-url"
export SUPABASE_KEY="your-supabase-key"

python integration_example.py
```

## Running Tests

```bash
# Run all tests
pytest test_greeks_calculator.py -v

# Run specific test
pytest test_greeks_calculator.py::TestGreeksCalculator::test_calculate_call_greeks -v

# Run with coverage
pytest test_greeks_calculator.py --cov=greeks_calculator --cov-report=html
```

## Common Use Cases

### 1. Single Option Analysis

```python
from greeks import GreeksCalculator, OptionContract, OptionType
from decimal import Decimal
from datetime import date, timedelta

calculator = GreeksCalculator(
    risk_free_rate=0.045,
    dividend_yield=0.018
)

contract = OptionContract(
    option_type=OptionType.CALL,
    strike=Decimal('5850'),
    expiration_date=date.today() + timedelta(days=30),
    quantity=10,
    implied_volatility=Decimal('0.18')
)

greeks = calculator.calculate_position_greeks(contract, Decimal('5800'))
```

### 2. Portfolio Risk Monitoring

```python
from greeks import PortfolioGreeksCalculator

portfolio_calc = PortfolioGreeksCalculator(calculator)
snapshot = portfolio_calc.calculate_portfolio_greeks(contracts, current_spot)

# Check risk thresholds
if abs(snapshot.delta_percentage()) > 10:
    print("⚠️  Delta exposure exceeds 10%")

if snapshot.total_theta < -100:
    print("⚠️  Daily theta decay exceeds $100")
```

### 3. P&L Projection

```python
# Project P&L for different scenarios
spot_scenarios = [
    current_spot * Decimal('0.98'),  # -2%
    current_spot * Decimal('0.99'),  # -1%
    current_spot,                     #  0%
    current_spot * Decimal('1.01'),  # +1%
    current_spot * Decimal('1.02'),  # +2%
]

pnl_scenarios = portfolio_calc.calculate_portfolio_pnl_scenarios(
    contracts,
    current_spot,
    spot_scenarios,
    days_forward=1
)

for spot, pnl in pnl_scenarios:
    print(f"Spot ${spot}: P&L ${pnl:.2f}")
```

### 4. Database Integration

```python
from database.supabase_client import SupabaseClient, GreeksSnapshot

db = SupabaseClient()

# Record Greeks snapshot
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

# Retrieve Greeks history
history = db.get_greeks_history(position_id)
```

## Key Concepts

### Greeks Definitions

- **Delta**: Change in option price per $1 move in underlying
- **Gamma**: Change in delta per $1 move in underlying
- **Theta**: Daily time decay (change per day)
- **Vega**: Change in price per 1% change in IV
- **Rho**: Change in price per 1% change in interest rate

### Position vs Contract Greeks

- **Contract Greeks**: Per-option values (e.g., delta = 0.50)
- **Position Greeks**: Scaled by quantity × 100 multiplier
  - Example: 10 contracts with delta 0.50 → position delta = 500

### Long vs Short Positions

- **Long (quantity > 0)**:
  - Positive delta for calls
  - Negative theta (time decay hurts)
  - Positive vega (benefits from IV increase)

- **Short (quantity < 0)**:
  - Negative delta for calls
  - Positive theta (time decay helps)
  - Negative vega (benefits from IV decrease)

## Tips & Best Practices

### 1. Use Decimal for Precision

```python
# Good
strike = Decimal('5850.00')
spot = Decimal('5800.25')

# Avoid
strike = 5850.0  # Float precision issues
```

### 2. Update Curves Regularly

```python
# Update rates when they change
calculator.update_curves(
    risk_free_rate=0.035,  # New rate after Fed decision
    dividend_yield=0.020
)
```

### 3. Check Delta Neutrality

```python
# Iron Condor should be delta neutral
if not snapshot.is_delta_neutral(threshold=Decimal('5.0')):
    print("Consider adjusting position")
```

### 4. Monitor Gamma Risk

```python
# Check gamma risk for 1% move
gamma_risk = snapshot.risk_metrics.get('gamma_risk_1pct', 0)
if abs(gamma_risk) > 100:
    print("High gamma risk - delta can change significantly")
```

### 5. Track Theta Decay

```python
# Calculate days to breakeven
if 'breakeven_days' in snapshot.risk_metrics:
    days = snapshot.risk_metrics['breakeven_days']
    print(f"Position profitable after {days:.1f} days of theta decay")
```

## Troubleshooting

### ImportError: No module named 'financepy'

Install FinancePy:
```bash
pip install financepy
```

### ValueError: Implied volatility must be provided

Either set `implied_volatility` in the contract or pass it to `calculate_greeks()`:

```python
# Option 1: In contract
contract = OptionContract(..., implied_volatility=Decimal('0.18'))

# Option 2: As parameter
greeks = calculator.calculate_greeks(contract, spot, Decimal('0.18'))
```

### Greeks seem incorrect

Check:
1. Strike price and spot are in correct units
2. IV is decimal (0.18 for 18%, not 18)
3. Dates are correct (not expired)
4. Risk-free rate and dividend yield are reasonable

### Performance issues

For large portfolios:
- Calculate once and reuse results
- Consider batching calculations
- Profile with `cProfile` if needed

## Next Steps

1. **Read the README**: Full documentation in `README.md`
2. **Run Examples**: `python examples.py`
3. **Run Tests**: `pytest test_greeks_calculator.py -v`
4. **Integration**: See `integration_example.py`
5. **API Reference**: See `IMPLEMENTATION_SUMMARY.md`

## Support

For questions or issues:
1. Check `README.md` for detailed documentation
2. Review `examples.py` for usage patterns
3. See `test_greeks_calculator.py` for edge cases
4. Refer to FinancePy docs: https://github.com/domokane/FinancePy

## File Reference

```
apex-sharpe/greeks/
├── greeks_calculator.py          # Core implementation
├── examples.py                   # 6 comprehensive examples
├── test_greeks_calculator.py     # 20+ unit tests
├── integration_example.py        # Database integration
├── verify_setup.py               # Setup verification
├── README.md                     # Full documentation
├── IMPLEMENTATION_SUMMARY.md     # Technical details
├── GETTING_STARTED.md           # This file
└── requirements.txt              # Dependencies
```

## Quick Reference

```python
# Import everything
from greeks import *

# Quick calculation
greeks = calculate_option_greeks('CALL', 5850, expiry, 5800, 0.18, 10)

# Calculator
calculator = GreeksCalculator(risk_free_rate=0.045, dividend_yield=0.018)
greeks_data = calculator.calculate_greeks(contract, spot, iv)
pos_greeks = calculator.calculate_position_greeks(contract, spot, iv)

# Portfolio
portfolio_calc = PortfolioGreeksCalculator(calculator)
snapshot = portfolio_calc.calculate_portfolio_greeks(contracts, spot)
pnl = portfolio_calc.calculate_portfolio_pnl_scenarios(contracts, spot, scenarios)

# Analysis
snapshot.delta_percentage()      # Delta as %
snapshot.is_delta_neutral()      # Check delta neutral
snapshot.risk_metrics            # Additional risk metrics
```

---

**Ready to start?** Run `python verify_setup.py` to ensure everything is installed correctly!
