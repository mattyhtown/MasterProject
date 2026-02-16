# APEX-SHARPE Risk Management System

Production-ready risk management infrastructure for options trading with Greeks-based limits, position sizing, margin calculations, and real-time exposure monitoring.

## Overview

The risk management system extends CrewTrader's base risk framework with options-specific controls:

- **Greeks-based Limits**: Portfolio-level delta, vega, theta, gamma exposure limits
- **Position Sizing**: Risk-based, Greeks-based, and margin-based sizing methods
- **Margin Calculation**: Support for multiple spread types and margin methodologies
- **Exposure Monitoring**: Real-time tracking with threshold alerts and Supabase integration

## Architecture

```
risk/
├── __init__.py                  # Package exports
├── options_risk_manager.py      # Main risk manager (extends CrewTrader)
├── position_sizer.py            # Options position sizing
├── margin_calculator.py         # Margin requirements calculator
├── exposure_monitor.py          # Real-time exposure monitoring
└── README.md                    # This file
```

## Core Components

### 1. OptionsRiskManager

Extends `CrewTrader.risk.RiskManager` with Greeks-based risk controls.

**Features:**
- Portfolio Greeks limits (delta, vega, theta, gamma)
- Individual position limits
- Sharpe ratio filtering integration
- Buying power reserve management
- Trading halt capability
- Greeks history tracking

**Example:**

```python
from decimal import Decimal
from apex_sharpe.risk import OptionsRiskManager, GreeksLimits
from apex_sharpe.strategies import MultiLegSpread

# Initialize with limits
risk_mgr = OptionsRiskManager(
    max_position_value=50_000.0,
    sharpe_threshold=1.0,
    greeks_limits=GreeksLimits(
        max_portfolio_delta=Decimal('100'),
        max_portfolio_vega=Decimal('1000'),
        max_portfolio_theta=Decimal('-500'),
        max_gamma_exposure=Decimal('50'),
        max_individual_position_delta=Decimal('25'),
    ),
    max_positions=10,
    min_buying_power_reserve=20_000.0,
)

# Assess new spread
spread = MultiLegSpread(...)  # Your iron condor, etc.

assessment = risk_mgr.assess_new_spread(
    spread=spread,
    current_portfolio_delta=Decimal('50'),
    current_portfolio_vega=Decimal('300'),
    current_portfolio_theta=Decimal('-200'),
    current_portfolio_gamma=Decimal('15'),
    available_buying_power=Decimal('100000'),
    margin_requirement=Decimal('4000'),
    current_sharpe=1.5,
)

if assessment.action == RiskAction.ALLOW:
    print(f"✓ Trade approved: {assessment.suggested_contracts} contracts")
    print(f"  Delta impact: {assessment.delta_impact}")
    print(f"  Risk score: {assessment.risk_score:.2%}")
else:
    print(f"✗ Trade blocked: {assessment.reason}")
```

**Key Methods:**

- `assess_new_spread()`: Check if new position violates limits
- `assess_portfolio_risk()`: Overall portfolio risk assessment
- `update_position_count()`: Update open position count
- `record_greeks_snapshot()`: Track Greeks history
- `get_risk_status()`: Get comprehensive risk status

### 2. OptionsPositionSizer

Extends `CrewTrader.risk.PositionSizer` with options-specific sizing.

**Sizing Methods:**
1. **Risk-based**: Size based on max risk per trade (% of capital)
2. **Greeks-based**: Size based on delta/vega exposure limits
3. **Margin-based**: Size based on available buying power

**Example:**

```python
from apex_sharpe.risk import OptionsPositionSizer

sizer = OptionsPositionSizer(
    risk_per_trade_pct=0.02,      # 2% risk per trade
    max_position_size=10,          # Max 10 contracts
    max_delta_per_position=25.0,   # Max delta per position
    max_vega_per_position=100.0,   # Max vega per position
    max_buying_power_pct=0.25,     # Use max 25% of BP per position
)

# Calculate optimal position size
result = sizer.calculate_position_size(
    spread=spread,
    capital=Decimal('100000'),
    available_buying_power=Decimal('50000'),
    current_portfolio_delta=Decimal('30'),
    current_portfolio_vega=Decimal('150'),
    margin_per_contract=Decimal('400'),
)

print(f"Trade {result.contracts} contracts")
print(f"Sizing method: {result.sizing_method}")
print(f"Capital at risk: ${result.capital_at_risk:,.0f}")
print(f"Risk/Reward: {result.risk_reward_ratio:.2f}")
```

**Key Methods:**

- `calculate_position_size()`: Comprehensive sizing using all methods
- `size_by_risk()`: Risk-based sizing only
- `size_by_greeks()`: Greeks-based sizing only
- `size_by_margin()`: Margin-based sizing only
- `validate_spread_metrics()`: Check spread quality metrics

### 3. MarginCalculator

Calculates margin requirements for different spread types.

**Supported Spread Types:**
- Iron Condors
- Vertical Spreads (credit/debit)
- Butterflies
- Straddles/Strangles
- Naked options
- Generic spreads

**Margin Methodologies:**
- Reg T (Regulation T - standard margin)
- Portfolio Margin (risk-based, lower requirements)

**Example:**

```python
from apex_sharpe.risk import MarginCalculator, MarginType

# Initialize calculator
calc = MarginCalculator(
    margin_type=MarginType.REG_T,
    contract_multiplier=Decimal('100'),
)

# Calculate iron condor margin
margin = calc.calculate_iron_condor_margin(
    put_spread_width=Decimal('5'),
    call_spread_width=Decimal('5'),
    contracts=10,
    put_credit=Decimal('0.75'),
    call_credit=Decimal('0.75'),
)

print(f"Margin required: ${margin.total_requirement:,.0f}")
print(f"Per contract: ${margin.per_contract_requirement:,.0f}")
print(f"Method: {margin.calculation_method}")

# Or calculate for a full spread
spread = MultiLegSpread(...)
margin = calc.calculate_spread_margin(spread, contracts=5)

# Calculate buying power reduction
bpr = calc.calculate_buying_power_reduction(spread, contracts=5)
print(f"BPR: ${bpr:,.0f}")
```

**Key Methods:**

- `calculate_spread_margin()`: Auto-detect spread type and calculate
- `calculate_iron_condor_margin()`: Iron condor specific
- `calculate_vertical_spread_margin()`: Vertical spread specific
- `calculate_buying_power_reduction()`: BPR calculation
- `simulate_portfolio_margin()`: Portfolio margin simulation

### 4. ExposureMonitor

Real-time exposure tracking with threshold alerts.

**Features:**
- Real-time Greeks monitoring
- Multi-level alerts (INFO, WARNING, CRITICAL, BREACH)
- Historical tracking
- Supabase integration for alert storage
- Dashboard-ready metrics

**Example:**

```python
from apex_sharpe.risk import ExposureMonitor, AlertLevel
from apex_sharpe.greeks import PortfolioGreeksCalculator

# Initialize monitor
monitor = ExposureMonitor(
    delta_limit=Decimal('100'),
    vega_limit=Decimal('1000'),
    theta_limit=Decimal('-500'),
    gamma_limit=Decimal('50'),
    warning_threshold=0.80,   # Alert at 80% of limit
    critical_threshold=0.95,  # Critical at 95% of limit
)

# Optional: Connect to Supabase
from apex_sharpe.database import SupabaseClient
supabase = SupabaseClient()
monitor.set_supabase_client(supabase)

# Update with current portfolio Greeks
portfolio_greeks = greeks_calculator.calculate_portfolio_greeks(contracts, spot_price)
alerts = monitor.update(portfolio_greeks, num_positions=5)

# Process alerts
for alert in alerts:
    print(f"{alert.level.value}: {alert.message}")
    print(f"  Recommendation: {alert.recommendation}")

# Get current exposure
snapshot = monitor.get_current_exposure()
print(f"\nCurrent Exposure:")
print(f"  Delta: {snapshot.portfolio_delta:.1f} ({snapshot.delta_limit_pct:.1f}%)")
print(f"  Vega: {snapshot.portfolio_vega:.1f} ({snapshot.vega_limit_pct:.1f}%)")
print(f"  Theta: {snapshot.portfolio_theta:.1f} ({snapshot.theta_limit_pct:.1f}%)")

# Get dashboard metrics
metrics = monitor.get_dashboard_metrics()
print(f"\nStatus: {metrics['status']}")
print(f"Total alerts (24h): {metrics['alerts']['total_24h']}")
```

**Key Methods:**

- `update()`: Update with new portfolio Greeks, returns alerts
- `get_current_exposure()`: Get latest exposure snapshot
- `get_exposure_history()`: Historical exposure data
- `get_alerts()`: Get alert history with filtering
- `get_dashboard_metrics()`: Dashboard-ready metrics

## Integration with BaseStrategy

The risk system integrates seamlessly with `BaseStrategy` Sharpe filtering:

```python
from apex_sharpe.strategies import BaseStrategy
from apex_sharpe.risk import OptionsRiskManager

class MyStrategy(BaseStrategy):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # Initialize risk manager
        self.risk_manager = OptionsRiskManager(
            sharpe_threshold=self.sharpe_threshold,
            greeks_limits=GreeksLimits(...),
        )

    def analyze(self, chain, iv_data, market_data):
        # Check Sharpe ratio (BaseStrategy)
        if not self.can_trade():
            return StrategySignal(action=SignalAction.NO_SIGNAL, ...)

        # Generate signal
        signal = ...

        # Build spread
        legs = self.select_strikes(chain, signal)
        spread = MultiLegSpread(legs=legs, ...)

        # Check risk limits
        assessment = self.risk_manager.assess_new_spread(
            spread=spread,
            current_portfolio_delta=self.get_portfolio_delta(),
            current_portfolio_vega=self.get_portfolio_vega(),
            current_portfolio_theta=self.get_portfolio_theta(),
            current_portfolio_gamma=self.get_portfolio_gamma(),
            available_buying_power=self.get_buying_power(),
            margin_requirement=self.calculate_margin(spread),
            current_sharpe=self.get_current_sharpe(),
        )

        if assessment.action != RiskAction.ALLOW:
            return StrategySignal(action=SignalAction.NO_SIGNAL, ...)

        return StrategySignal(action=SignalAction.ENTER, ...)
```

## Supabase Integration

The `ExposureMonitor` can store alerts to Supabase `alerts` table:

```sql
-- Alerts table schema (should already exist in your Supabase)
CREATE TABLE alerts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    alert_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    details JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    resolved_at TIMESTAMPTZ,
    resolved_by TEXT
);

CREATE INDEX idx_alerts_type_severity ON alerts(alert_type, severity);
CREATE INDEX idx_alerts_created ON alerts(created_at DESC);
```

Alerts are automatically stored when `set_supabase_client()` is called:

```python
from apex_sharpe.database import SupabaseClient

supabase = SupabaseClient()
monitor.set_supabase_client(supabase)

# Now alerts are automatically stored
alerts = monitor.update(portfolio_greeks, num_positions=5)
```

## Risk Limits Configuration

Recommended starting limits (adjust based on your capital and risk tolerance):

```python
# Conservative (smaller accounts, lower risk)
conservative_limits = GreeksLimits(
    max_portfolio_delta=Decimal('50'),
    max_portfolio_vega=Decimal('500'),
    max_portfolio_theta=Decimal('-250'),
    max_gamma_exposure=Decimal('25'),
    max_individual_position_delta=Decimal('15'),
    warning_threshold=Decimal('0.75'),
    critical_threshold=Decimal('0.90'),
)

# Moderate (most traders)
moderate_limits = GreeksLimits(
    max_portfolio_delta=Decimal('100'),
    max_portfolio_vega=Decimal('1000'),
    max_portfolio_theta=Decimal('-500'),
    max_gamma_exposure=Decimal('50'),
    max_individual_position_delta=Decimal('25'),
    warning_threshold=Decimal('0.80'),
    critical_threshold=Decimal('0.95'),
)

# Aggressive (larger accounts, higher risk tolerance)
aggressive_limits = GreeksLimits(
    max_portfolio_delta=Decimal('200'),
    max_portfolio_vega=Decimal('2000'),
    max_portfolio_theta=Decimal('-1000'),
    max_gamma_exposure=Decimal('100'),
    max_individual_position_delta=Decimal('50'),
    warning_threshold=Decimal('0.85'),
    critical_threshold=Decimal('0.98'),
)
```

## Complete Example

Here's a complete example showing all components working together:

```python
from decimal import Decimal
from datetime import date, datetime, timedelta
from apex_sharpe.risk import (
    OptionsRiskManager,
    OptionsPositionSizer,
    MarginCalculator,
    ExposureMonitor,
    GreeksLimits,
    RiskAction,
    MarginType,
)
from apex_sharpe.strategies import MultiLegSpread, SpreadType
from apex_sharpe.greeks import PortfolioGreeksCalculator, GreeksCalculator

# 1. Initialize risk management components
risk_manager = OptionsRiskManager(
    max_position_value=50_000.0,
    sharpe_threshold=1.0,
    greeks_limits=GreeksLimits(
        max_portfolio_delta=Decimal('100'),
        max_portfolio_vega=Decimal('1000'),
        max_portfolio_theta=Decimal('-500'),
        max_gamma_exposure=Decimal('50'),
    ),
)

position_sizer = OptionsPositionSizer(
    risk_per_trade_pct=0.02,
    max_delta_per_position=25.0,
    max_vega_per_position=100.0,
)

margin_calc = MarginCalculator(margin_type=MarginType.REG_T)

exposure_monitor = ExposureMonitor(
    delta_limit=Decimal('100'),
    vega_limit=Decimal('1000'),
    theta_limit=Decimal('-500'),
    gamma_limit=Decimal('50'),
)

# 2. Build a spread (example iron condor)
spread = MultiLegSpread(
    legs=[...],  # Your spread legs
    spread_type=SpreadType.IRON_CONDOR,
    entry_time=datetime.now(),
    underlying_price=Decimal('5850'),
    max_profit=Decimal('150'),
    max_loss=Decimal('350'),
)
spread.calculate_portfolio_greeks()

# 3. Calculate margin requirement
margin = margin_calc.calculate_spread_margin(spread, contracts=1)
print(f"Margin per contract: ${margin.per_contract_requirement:,.0f}")

# 4. Calculate position size
size_result = position_sizer.calculate_position_size(
    spread=spread,
    capital=Decimal('100000'),
    available_buying_power=Decimal('50000'),
    current_portfolio_delta=Decimal('30'),
    current_portfolio_vega=Decimal('200'),
    margin_per_contract=margin.per_contract_requirement,
)
print(f"Position size: {size_result.contracts} contracts ({size_result.sizing_method})")

# 5. Assess risk
assessment = risk_manager.assess_new_spread(
    spread=spread,
    current_portfolio_delta=Decimal('30'),
    current_portfolio_vega=Decimal('200'),
    current_portfolio_theta=Decimal('-150'),
    current_portfolio_gamma=Decimal('10'),
    available_buying_power=Decimal('50000'),
    margin_requirement=margin.per_contract_requirement,
    current_sharpe=1.5,
)

if assessment.action == RiskAction.ALLOW:
    print(f"✓ Trade approved!")
    print(f"  Suggested contracts: {assessment.suggested_contracts}")
    print(f"  Risk score: {assessment.risk_score:.2%}")

    # 6. Monitor exposure (after trade)
    # Calculate portfolio Greeks
    greeks_calc = GreeksCalculator()
    portfolio_calc = PortfolioGreeksCalculator(greeks_calc)

    # Assuming we have all contracts in portfolio
    portfolio_greeks = portfolio_calc.calculate_portfolio_greeks(
        contracts=[...],
        spot_price=Decimal('5850'),
    )

    # Update monitor
    alerts = exposure_monitor.update(portfolio_greeks, num_positions=3)

    for alert in alerts:
        print(f"{alert.level.value}: {alert.message}")

else:
    print(f"✗ Trade blocked: {assessment.reason}")
```

## Testing

Run tests to verify the risk management system:

```python
# Test risk manager
from apex_sharpe.risk import OptionsRiskManager, GreeksLimits

risk_mgr = OptionsRiskManager(greeks_limits=GreeksLimits())
status = risk_mgr.get_risk_status()
print(status)

# Test position sizer
from apex_sharpe.risk import OptionsPositionSizer

sizer = OptionsPositionSizer()
params = sizer.get_sizing_parameters()
print(params)

# Test margin calculator
from apex_sharpe.risk import MarginCalculator, MarginType

calc = MarginCalculator(margin_type=MarginType.REG_T)
margin = calc.calculate_iron_condor_margin(
    put_spread_width=Decimal('5'),
    call_spread_width=Decimal('5'),
    contracts=1,
)
print(f"Margin: ${margin.total_requirement:,.0f}")

# Test exposure monitor
from apex_sharpe.risk import ExposureMonitor

monitor = ExposureMonitor(
    delta_limit=Decimal('100'),
    vega_limit=Decimal('1000'),
    theta_limit=Decimal('-500'),
    gamma_limit=Decimal('50'),
)
config = monitor.get_monitor_config()
print(config)
```

## Best Practices

1. **Set Conservative Limits Initially**: Start with conservative Greeks limits and adjust based on experience
2. **Monitor Daily**: Review exposure dashboard daily, especially before market open
3. **Respect Alerts**: Take WARNING alerts seriously, act immediately on CRITICAL/BREACH
4. **Position Sizing**: Use all three sizing methods, take the minimum
5. **Margin Safety**: Keep buying power reserve for adjustments
6. **Greeks History**: Review Greeks history to understand portfolio behavior
7. **Sharpe Integration**: Always use Sharpe filtering in conjunction with Greeks limits
8. **Risk/Reward**: Maintain minimum 1:3 risk/reward for credit spreads
9. **Regular Audits**: Periodically audit risk limits against account performance
10. **Backtest Limits**: Backtest your risk limits to ensure they work with your strategy

## Error Handling

All components handle errors gracefully:

```python
# Risk manager returns BLOCK action on errors
assessment = risk_manager.assess_new_spread(...)
if assessment.action == RiskAction.BLOCK:
    print(f"Cannot trade: {assessment.reason}")

# Position sizer returns 0 contracts on invalid inputs
result = sizer.calculate_position_size(...)
if result.contracts == 0:
    print(f"Cannot size position: {result.notes}")

# Margin calculator returns generic calculation on unknown spreads
margin = calc.calculate_spread_margin(spread)
print(f"Method: {margin.calculation_method}")

# Exposure monitor continues on Supabase errors
alerts = monitor.update(portfolio_greeks, num_positions=5)
# Alerts still generated even if Supabase storage fails
```

## Dependencies

- **CrewTrader**: Base risk management framework
- **apex-sharpe.greeks**: Greeks calculation
- **apex-sharpe.strategies**: Spread definitions
- **apex-sharpe.database**: Supabase integration (optional)
- **Python 3.8+**: Required
- **Decimal**: For precise financial calculations

## License

Part of the APEX-SHARPE Trading System.

## Support

For questions or issues:
1. Check this README
2. Review example code in `/Users/mh/apex-sharpe/strategies/`
3. Examine test files in `/Users/mh/apex-sharpe/tests/`
4. Review CrewTrader documentation for base components
