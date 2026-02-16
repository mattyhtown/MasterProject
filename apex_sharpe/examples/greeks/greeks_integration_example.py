#!/usr/bin/env python3
"""
Integration Example: Greeks Calculator with APEX-SHARPE Database

Demonstrates how to:
1. Calculate Greeks for a position
2. Store Greeks snapshots in database
3. Track Greeks evolution over time
4. Generate risk reports
"""

import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from greeks.greeks_calculator import (
    GreeksCalculator,
    PortfolioGreeksCalculator,
    OptionContract,
    OptionType,
)
from database.supabase_client import (
    SupabaseClient,
    Position,
    PositionLeg,
    GreeksSnapshot,
)


def create_iron_condor_position(db: SupabaseClient) -> str:
    """
    Create an Iron Condor position in the database.

    Returns:
        position_id: The created position ID
    """
    print("\n=== Creating Iron Condor Position ===")

    # Create position record
    position = Position(
        symbol='SPX',
        position_type='IRON_CONDOR',
        entry_date=date.today(),
        entry_time=datetime.now(),
        entry_premium=Decimal('250.00'),  # $2.50 credit per contract
        entry_iv_rank=Decimal('45.5'),
        entry_dte=45,
        status='OPEN',
        strategy_id=None
    )

    position_record = db.create_position(position)
    position_id = position_record['id']

    print(f"Created position: {position_id}")

    # Add legs
    expiry = date.today() + timedelta(days=45)
    legs = [
        # Short put spread: 5750/5700
        PositionLeg(
            position_id=position_id,
            leg_index=0,
            option_type='PUT',
            strike=Decimal('5750'),
            expiration_date=expiry,
            quantity=-1,
            action='STO',
            entry_price=Decimal('85.50'),
            entry_fill_time=datetime.now(),
            entry_iv=Decimal('0.17'),
            commission=Decimal('0.65')
        ),
        PositionLeg(
            position_id=position_id,
            leg_index=1,
            option_type='PUT',
            strike=Decimal('5700'),
            expiration_date=expiry,
            quantity=1,
            action='BTO',
            entry_price=Decimal('62.30'),
            entry_fill_time=datetime.now(),
            entry_iv=Decimal('0.18'),
            commission=Decimal('0.65')
        ),
        # Short call spread: 5850/5900
        PositionLeg(
            position_id=position_id,
            leg_index=2,
            option_type='CALL',
            strike=Decimal('5850'),
            expiration_date=expiry,
            quantity=-1,
            action='STO',
            entry_price=Decimal('88.75'),
            entry_fill_time=datetime.now(),
            entry_iv=Decimal('0.17'),
            commission=Decimal('0.65')
        ),
        PositionLeg(
            position_id=position_id,
            leg_index=3,
            option_type='CALL',
            strike=Decimal('5900'),
            expiration_date=expiry,
            quantity=1,
            action='BTO',
            entry_price=Decimal('65.15'),
            entry_fill_time=datetime.now(),
            entry_iv=Decimal('0.18'),
            commission=Decimal('0.65')
        ),
    ]

    for leg in legs:
        db.add_position_leg(leg)
        print(f"  Added leg: {leg.action} {abs(leg.quantity)} "
              f"{leg.option_type} ${leg.strike}")

    return position_id


def calculate_and_store_greeks(
    db: SupabaseClient,
    position_id: str,
    spot_price: Decimal
) -> None:
    """
    Calculate Greeks for a position and store in database.

    Args:
        db: Database client
        position_id: Position to calculate Greeks for
        spot_price: Current underlying price
    """
    print(f"\n=== Calculating Greeks for Position {position_id} ===")

    # Get position legs from database
    legs_data = db.get_position_legs(position_id)

    if not legs_data:
        print("No legs found for position")
        return

    # Convert to OptionContract objects
    contracts = []
    for leg_data in legs_data:
        contract = OptionContract(
            option_type=OptionType[leg_data['option_type']],
            strike=Decimal(str(leg_data['strike'])),
            expiration_date=datetime.fromisoformat(
                leg_data['expiration_date']
            ).date(),
            quantity=leg_data['quantity'],
            implied_volatility=Decimal(str(leg_data['entry_iv']))
        )
        contracts.append(contract)

    # Calculate Greeks
    calculator = GreeksCalculator(
        risk_free_rate=0.045,
        dividend_yield=0.018
    )
    portfolio_calc = PortfolioGreeksCalculator(calculator)

    snapshot = portfolio_calc.calculate_portfolio_greeks(contracts, spot_price)

    # Display Greeks
    print(f"\nSpot: ${spot_price}")
    print(f"\nPortfolio Greeks:")
    print(f"  Delta:  {snapshot.total_delta:>10.2f}")
    print(f"  Gamma:  {snapshot.total_gamma:>10.4f}")
    print(f"  Theta:  {snapshot.total_theta:>10.2f} (${snapshot.total_theta:.2f}/day)")
    print(f"  Vega:   {snapshot.total_vega:>10.2f}")
    print(f"  Value:  ${snapshot.total_value:>10.2f}")

    print(f"\nRisk Metrics:")
    print(f"  Delta %:          {snapshot.delta_percentage():>8.2f}%")
    print(f"  Delta Neutral:    {snapshot.is_delta_neutral()}")
    print(f"  Gamma Risk (1%):  ${snapshot.risk_metrics.get('gamma_risk_1pct', 0):>8.2f}")

    # Get position for DTE calculation
    position_data = db.get_position_by_id(position_id)
    dte = (
        datetime.fromisoformat(legs_data[0]['expiration_date']).date()
        - date.today()
    ).days

    # Calculate unrealized P&L
    entry_premium = Decimal(str(position_data['entry_premium']))
    unrealized_pnl = snapshot.total_value - (entry_premium * 100)

    # Store in database
    greeks_snapshot = GreeksSnapshot(
        position_id=position_id,
        trade_date=date.today(),
        dte=dte,
        underlying_price=snapshot.underlying_price,
        portfolio_delta=snapshot.total_delta,
        portfolio_gamma=snapshot.total_gamma,
        portfolio_theta=snapshot.total_theta,
        portfolio_vega=snapshot.total_vega,
        position_value=snapshot.total_value,
        unrealized_pnl=unrealized_pnl
    )

    db.record_greeks_snapshot(greeks_snapshot)
    print(f"\n✓ Greeks snapshot stored in database")


def generate_risk_report(
    db: SupabaseClient,
    position_id: str
) -> None:
    """
    Generate a risk report from historical Greeks.

    Args:
        db: Database client
        position_id: Position to generate report for
    """
    print(f"\n=== Risk Report for Position {position_id} ===")

    # Get Greeks history
    history = db.get_greeks_history(position_id)

    if not history:
        print("No Greeks history found")
        return

    print(f"\nGreeks Evolution ({len(history)} snapshots):")
    print(f"\n{'Date':<12} {'DTE':<6} {'Spot':<10} {'Delta':<10} "
          f"{'Theta':<10} {'P&L':<10}")
    print("-" * 70)

    for record in history:
        print(f"{record['trade_date']:<12} "
              f"{record['dte']:<6} "
              f"${record['underlying_price']:<9.2f} "
              f"{record['portfolio_delta']:<10.2f} "
              f"{record['portfolio_theta']:<10.2f} "
              f"${record['unrealized_pnl']:<9.2f}")

    # Calculate summary statistics
    latest = history[-1]
    first = history[0]

    print(f"\nSummary:")
    print(f"  Days tracked:     {len(history)}")
    print(f"  DTE remaining:    {latest['dte']}")
    print(f"  Current Delta:    {latest['portfolio_delta']:.2f}")
    print(f"  Current Theta:    ${latest['portfolio_theta']:.2f}/day")
    print(f"  Unrealized P&L:   ${latest['unrealized_pnl']:.2f}")
    print(f"  Total Theta collected: ${sum(h['portfolio_theta'] for h in history):.2f}")


def simulate_price_scenarios(
    db: SupabaseClient,
    position_id: str,
    current_spot: Decimal
) -> None:
    """
    Simulate P&L under different price scenarios.

    Args:
        db: Database client
        position_id: Position to simulate
        current_spot: Current underlying price
    """
    print(f"\n=== P&L Scenarios for Position {position_id} ===")

    # Get position legs
    legs_data = db.get_position_legs(position_id)
    contracts = []

    for leg_data in legs_data:
        contract = OptionContract(
            option_type=OptionType[leg_data['option_type']],
            strike=Decimal(str(leg_data['strike'])),
            expiration_date=datetime.fromisoformat(
                leg_data['expiration_date']
            ).date(),
            quantity=leg_data['quantity'],
            implied_volatility=Decimal(str(leg_data['entry_iv']))
        )
        contracts.append(contract)

    # Setup calculator
    calculator = GreeksCalculator()
    portfolio_calc = PortfolioGreeksCalculator(calculator)

    # Define scenarios
    scenarios = [
        current_spot - Decimal('100'),
        current_spot - Decimal('50'),
        current_spot,
        current_spot + Decimal('50'),
        current_spot + Decimal('100'),
    ]

    # Calculate P&L for each scenario
    pnl_results = portfolio_calc.calculate_portfolio_pnl_scenarios(
        contracts,
        current_spot,
        scenarios,
        days_forward=1
    )

    print(f"\nCurrent Spot: ${current_spot}")
    print(f"Projection: 1 day forward\n")
    print(f"{'Spot':<12} {'P&L':<12} {'P&L %':<10} {'Status':<15}")
    print("-" * 60)

    position_data = db.get_position_by_id(position_id)
    entry_value = Decimal(str(position_data['entry_premium'])) * 100

    for scenario_spot, pnl in pnl_results:
        pnl_pct = (pnl / entry_value) * Decimal('100') if entry_value != 0 else Decimal('0')
        status = "✓ PROFIT" if pnl > 0 else "✗ LOSS"
        print(f"${scenario_spot:<11.2f} ${pnl:<11.2f} {pnl_pct:>7.2f}%  {status}")


def main():
    """Run integration example."""
    print("\n" + "="*70)
    print("  APEX-SHARPE Greeks Calculator - Integration Example")
    print("="*70)

    # Note: This requires SUPABASE_URL and SUPABASE_KEY environment variables
    try:
        db = SupabaseClient()
    except ValueError as e:
        print(f"\n⚠️  {e}")
        print("\nThis example requires Supabase credentials.")
        print("Set SUPABASE_URL and SUPABASE_KEY environment variables.")
        print("\nHowever, the Greeks calculator can be used standalone:")
        print("  from greeks import calculate_option_greeks")
        return

    # Example workflow
    try:
        # 1. Create a new position
        position_id = create_iron_condor_position(db)

        # 2. Calculate and store initial Greeks
        spot = Decimal('5800')
        calculate_and_store_greeks(db, position_id, spot)

        # 3. Simulate different days (would be done over time in production)
        for days_later in [1, 2, 3]:
            print(f"\n--- Day {days_later} ---")
            # Simulate spot movement
            new_spot = spot + Decimal(str(days_later * 10))
            calculate_and_store_greeks(db, position_id, new_spot)

        # 4. Generate risk report
        generate_risk_report(db, position_id)

        # 5. Run P&L scenarios
        simulate_price_scenarios(db, position_id, spot)

        print("\n" + "="*70)
        print("Integration example completed successfully!")
        print("="*70 + "\n")

    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
