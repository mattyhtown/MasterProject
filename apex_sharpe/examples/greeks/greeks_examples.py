#!/usr/bin/env python3
"""
Examples demonstrating the APEX-SHARPE Greeks Calculator.

Shows various use cases including:
- Single option Greeks calculation
- Multi-leg strategy Greeks (Iron Condor, Butterfly, etc.)
- Portfolio risk analysis
- P&L scenario analysis
"""

from datetime import date, timedelta
from decimal import Decimal
from greeks_calculator import (
    GreeksCalculator,
    PortfolioGreeksCalculator,
    OptionContract,
    OptionType,
    calculate_option_greeks,
)


def example_single_option():
    """Example: Calculate Greeks for a single option."""
    print("\n" + "="*70)
    print("EXAMPLE 1: Single Option Greeks")
    print("="*70)

    # Quick calculation using convenience function
    greeks = calculate_option_greeks(
        option_type='CALL',
        strike=5850,
        expiration_date=date.today() + timedelta(days=30),
        spot_price=5800,
        implied_volatility=0.18,
        quantity=10
    )

    print(f"\n10 SPX 5850 Calls, 30 DTE")
    print(f"Spot: $5,800")
    print(f"IV: 18%")
    print(f"\nPer-Contract Greeks:")
    print(f"  Delta:  {greeks.greeks_data.delta:>8.4f}")
    print(f"  Gamma:  {greeks.greeks_data.gamma:>8.6f}")
    print(f"  Theta:  {greeks.greeks_data.theta:>8.4f}")
    print(f"  Vega:   {greeks.greeks_data.vega:>8.4f}")
    print(f"  Rho:    {greeks.greeks_data.rho:>8.4f}")
    print(f"  Price:  ${greeks.greeks_data.option_price:>8.2f}")

    print(f"\nPosition Greeks (10 contracts):")
    print(f"  Delta:  {greeks.position_delta:>10.2f}")
    print(f"  Gamma:  {greeks.position_gamma:>10.4f}")
    print(f"  Theta:  {greeks.position_theta:>10.2f}")
    print(f"  Vega:   {greeks.position_vega:>10.2f}")
    print(f"  Value:  ${greeks.position_value:>10.2f}")


def example_iron_condor():
    """Example: Calculate Greeks for an Iron Condor."""
    print("\n" + "="*70)
    print("EXAMPLE 2: Iron Condor Greeks")
    print("="*70)

    spot = Decimal('5800')
    expiry = date.today() + timedelta(days=45)

    # Iron Condor: Short 5750/5700 Put Spread, Short 5850/5900 Call Spread
    contracts = [
        # Put side
        OptionContract(
            option_type=OptionType.PUT,
            strike=Decimal('5750'),
            expiration_date=expiry,
            quantity=-1,  # Short
            implied_volatility=Decimal('0.17')
        ),
        OptionContract(
            option_type=OptionType.PUT,
            strike=Decimal('5700'),
            expiration_date=expiry,
            quantity=1,  # Long
            implied_volatility=Decimal('0.18')
        ),
        # Call side
        OptionContract(
            option_type=OptionType.CALL,
            strike=Decimal('5850'),
            expiration_date=expiry,
            quantity=-1,  # Short
            implied_volatility=Decimal('0.17')
        ),
        OptionContract(
            option_type=OptionType.CALL,
            strike=Decimal('5900'),
            expiration_date=expiry,
            quantity=1,  # Long
            implied_volatility=Decimal('0.18')
        ),
    ]

    # Calculate portfolio Greeks
    calculator = GreeksCalculator()
    portfolio_calc = PortfolioGreeksCalculator(calculator)
    snapshot = portfolio_calc.calculate_portfolio_greeks(contracts, spot)

    print(f"\nIron Condor: 5700/5750/5850/5900")
    print(f"Spot: ${spot}")
    print(f"DTE: 45")
    print(f"\nPortfolio Greeks:")
    print(f"  Net Delta:  {snapshot.total_delta:>10.2f}")
    print(f"  Net Gamma:  {snapshot.total_gamma:>10.4f}")
    print(f"  Net Theta:  {snapshot.total_theta:>10.2f} (${snapshot.total_theta:.2f}/day)")
    print(f"  Net Vega:   {snapshot.total_vega:>10.2f}")
    print(f"  Net Value:  ${snapshot.total_value:>10.2f}")

    print(f"\nRisk Metrics:")
    print(f"  Delta %:          {snapshot.delta_percentage():>8.2f}%")
    print(f"  Delta Neutral:    {snapshot.is_delta_neutral()}")
    print(f"  Gamma Risk (1%):  ${snapshot.risk_metrics.get('gamma_risk_1pct', 0):>8.2f}")
    print(f"  Theta %:          {snapshot.risk_metrics.get('theta_percentage', 0):>8.4f}%")
    print(f"  Breakeven Days:   {snapshot.risk_metrics.get('breakeven_days', 0):>8.1f}")

    print(f"\nIndividual Legs:")
    for i, pos in enumerate(snapshot.positions):
        action = "Short" if pos.contract.quantity < 0 else "Long"
        print(f"  Leg {i+1}: {action} {abs(pos.contract.quantity)} "
              f"{pos.contract.option_type.value} ${pos.contract.strike} "
              f"- Delta: {pos.position_delta:>8.2f}")


def example_butterfly():
    """Example: Calculate Greeks for a Call Butterfly."""
    print("\n" + "="*70)
    print("EXAMPLE 3: Call Butterfly Greeks")
    print("="*70)

    spot = Decimal('5800')
    expiry = date.today() + timedelta(days=30)

    # Call Butterfly: Buy 1 5750, Sell 2 5800, Buy 1 5850
    contracts = [
        OptionContract(
            option_type=OptionType.CALL,
            strike=Decimal('5750'),
            expiration_date=expiry,
            quantity=1,
            implied_volatility=Decimal('0.18')
        ),
        OptionContract(
            option_type=OptionType.CALL,
            strike=Decimal('5800'),
            expiration_date=expiry,
            quantity=-2,
            implied_volatility=Decimal('0.17')
        ),
        OptionContract(
            option_type=OptionType.CALL,
            strike=Decimal('5850'),
            expiration_date=expiry,
            quantity=1,
            implied_volatility=Decimal('0.18')
        ),
    ]

    calculator = GreeksCalculator()
    portfolio_calc = PortfolioGreeksCalculator(calculator)
    snapshot = portfolio_calc.calculate_portfolio_greeks(contracts, spot)

    print(f"\nCall Butterfly: 5750/5800/5850")
    print(f"Spot: ${spot} (ATM)")
    print(f"DTE: 30")
    print(f"\nPortfolio Greeks:")
    print(f"  Net Delta:  {snapshot.total_delta:>10.2f}")
    print(f"  Net Gamma:  {snapshot.total_gamma:>10.4f}")
    print(f"  Net Theta:  {snapshot.total_theta:>10.2f}")
    print(f"  Net Vega:   {snapshot.total_vega:>10.2f}")
    print(f"  Net Value:  ${snapshot.total_value:>10.2f}")


def example_pnl_scenarios():
    """Example: Calculate P&L scenarios for a position."""
    print("\n" + "="*70)
    print("EXAMPLE 4: P&L Scenario Analysis")
    print("="*70)

    spot = Decimal('5800')
    expiry = date.today() + timedelta(days=30)

    # Short Straddle: Sell 1 5800 Call, Sell 1 5800 Put
    contracts = [
        OptionContract(
            option_type=OptionType.CALL,
            strike=Decimal('5800'),
            expiration_date=expiry,
            quantity=-1,
            implied_volatility=Decimal('0.18')
        ),
        OptionContract(
            option_type=OptionType.PUT,
            strike=Decimal('5800'),
            expiration_date=expiry,
            quantity=-1,
            implied_volatility=Decimal('0.18')
        ),
    ]

    calculator = GreeksCalculator()
    portfolio_calc = PortfolioGreeksCalculator(calculator)

    # Generate spot scenarios
    spot_scenarios = [
        spot - Decimal('100'),
        spot - Decimal('50'),
        spot,
        spot + Decimal('50'),
        spot + Decimal('100'),
    ]

    print(f"\nShort Straddle: Sell 5800 Call + 5800 Put")
    print(f"Current Spot: ${spot}")
    print(f"DTE: 30")

    # Calculate P&L for 1 day forward
    pnl_scenarios = portfolio_calc.calculate_portfolio_pnl_scenarios(
        contracts,
        spot,
        spot_scenarios,
        days_forward=1
    )

    print(f"\nP&L Scenarios (1 day forward):")
    print(f"{'Spot Price':<15} {'P&L':<15} {'P&L %':<10}")
    print("-" * 40)

    current_snapshot = portfolio_calc.calculate_portfolio_greeks(contracts, spot)
    current_value = current_snapshot.total_value

    for scenario_spot, pnl in pnl_scenarios:
        pnl_pct = (pnl / abs(current_value)) * Decimal('100') if current_value != 0 else Decimal('0')
        print(f"${scenario_spot:<14.2f} ${pnl:<14.2f} {pnl_pct:>8.2f}%")


def example_multi_strategy_portfolio():
    """Example: Portfolio with multiple strategies."""
    print("\n" + "="*70)
    print("EXAMPLE 5: Multi-Strategy Portfolio")
    print("="*70)

    spot = Decimal('5800')

    # Multiple positions with different expirations
    contracts = [
        # Short-term Iron Condor (30 DTE)
        OptionContract(
            option_type=OptionType.PUT,
            strike=Decimal('5750'),
            expiration_date=date.today() + timedelta(days=30),
            quantity=-2,
            implied_volatility=Decimal('0.17')
        ),
        OptionContract(
            option_type=OptionType.PUT,
            strike=Decimal('5700'),
            expiration_date=date.today() + timedelta(days=30),
            quantity=2,
            implied_volatility=Decimal('0.18')
        ),
        OptionContract(
            option_type=OptionType.CALL,
            strike=Decimal('5850'),
            expiration_date=date.today() + timedelta(days=30),
            quantity=-2,
            implied_volatility=Decimal('0.17')
        ),
        OptionContract(
            option_type=OptionType.CALL,
            strike=Decimal('5900'),
            expiration_date=date.today() + timedelta(days=30),
            quantity=2,
            implied_volatility=Decimal('0.18')
        ),
        # Long-term diagonal spread (60 DTE)
        OptionContract(
            option_type=OptionType.CALL,
            strike=Decimal('5800'),
            expiration_date=date.today() + timedelta(days=60),
            quantity=5,
            implied_volatility=Decimal('0.19')
        ),
        OptionContract(
            option_type=OptionType.CALL,
            strike=Decimal('5900'),
            expiration_date=date.today() + timedelta(days=90),
            quantity=-5,
            implied_volatility=Decimal('0.20')
        ),
    ]

    calculator = GreeksCalculator()
    portfolio_calc = PortfolioGreeksCalculator(calculator)
    snapshot = portfolio_calc.calculate_portfolio_greeks(contracts, spot)

    print(f"\nMulti-Strategy Portfolio")
    print(f"Spot: ${spot}")
    print(f"\nAggregate Portfolio Greeks:")
    print(f"  Total Delta:    {snapshot.total_delta:>10.2f}")
    print(f"  Total Gamma:    {snapshot.total_gamma:>10.4f}")
    print(f"  Total Theta:    {snapshot.total_theta:>10.2f}")
    print(f"  Total Vega:     {snapshot.total_vega:>10.2f}")
    print(f"  Portfolio Value: ${snapshot.total_value:>10.2f}")

    print(f"\nRisk Analysis:")
    print(f"  Delta Percentage:     {snapshot.delta_percentage():>8.2f}%")
    print(f"  Is Delta Neutral:     {snapshot.is_delta_neutral()}")
    print(f"  Notional Exposure:    ${snapshot.risk_metrics.get('notional_exposure', 0):>10.2f}")
    print(f"  Daily Theta Decay:    ${snapshot.total_theta:>10.2f}")


def example_update_curves():
    """Example: Updating market curves."""
    print("\n" + "="*70)
    print("EXAMPLE 6: Updating Market Curves")
    print("="*70)

    calculator = GreeksCalculator(
        risk_free_rate=0.045,
        dividend_yield=0.018
    )

    contract = OptionContract(
        option_type=OptionType.CALL,
        strike=Decimal('5800'),
        expiration_date=date.today() + timedelta(days=365),
        quantity=1,
        implied_volatility=Decimal('0.18')
    )

    spot = Decimal('5800')

    print("\nLong-term Call (1 year DTE)")
    print(f"\nInitial Rates:")
    print(f"  Risk-free rate: {calculator.risk_free_rate:.2%}")
    print(f"  Dividend yield: {calculator.dividend_yield:.2%}")

    greeks1 = calculator.calculate_greeks(contract, spot)
    print(f"\nGreeks with initial rates:")
    print(f"  Rho: {greeks1.rho:>8.4f}")

    # Update rates (Fed rate cut scenario)
    calculator.update_curves(risk_free_rate=0.035)

    print(f"\nAfter rate cut to 3.5%:")
    greeks2 = calculator.calculate_greeks(contract, spot)
    print(f"  Rho: {greeks2.rho:>8.4f}")
    print(f"  Change: {greeks2.rho - greeks1.rho:>8.4f}")


def main():
    """Run all examples."""
    print("\n")
    print("╔" + "="*68 + "╗")
    print("║" + " "*10 + "APEX-SHARPE Greeks Calculator Examples" + " "*19 + "║")
    print("╚" + "="*68 + "╝")

    try:
        example_single_option()
        example_iron_condor()
        example_butterfly()
        example_pnl_scenarios()
        example_multi_strategy_portfolio()
        example_update_curves()

        print("\n" + "="*70)
        print("All examples completed successfully!")
        print("="*70 + "\n")

    except Exception as e:
        print(f"\nError running examples: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
