"""
Example Usage of APEX-SHARPE Execution Layer.

Demonstrates:
1. Building spreads with SpreadBuilder
2. Submitting orders with OptionsPaperBroker
3. Simulating fills with FillSimulator
4. Tracking positions with PositionTracker
"""

import sys
import os
from datetime import datetime, date, timedelta
from decimal import Decimal

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from execution import (
    OptionsPaperBroker,
    SpreadBuilder,
    FillSimulator,
    PositionTracker
)
from strategies.base_strategy import (
    OptionsChain,
    OptionContract,
    OptionType,
    MarketData,
    IVData
)
from greeks import GreeksCalculator


def create_sample_options_chain() -> OptionsChain:
    """Create a sample options chain for demonstration."""
    underlying_price = Decimal("5850.00")
    expiration = date.today() + timedelta(days=45)

    # Create sample contracts around current price
    calls = []
    puts = []

    # Wide range of strikes to capture all deltas (5 point increments for SPX)
    strikes = []
    for strike_val in range(5650, 6050, 5):
        strikes.append(Decimal(str(strike_val)))

    for strike in strikes:
        # Calculate theoretical deltas based on distance from ATM
        distance = float(strike - underlying_price)

        # Simple delta model: closer to ATM = higher delta magnitude
        # Calls: delta decreases as strike moves higher
        # Puts: delta increases (more negative) as strike moves lower

        if distance > 0:  # OTM calls, ITM puts
            # OTM call delta (decreases with distance)
            call_delta = Decimal("0.50") * Decimal(str(max(0.02, 1 - abs(distance) / 200)))
            # ITM put delta (very negative)
            put_delta = -Decimal("0.50") - Decimal(str(min(0.48, abs(distance) / 200)))
        elif distance < 0:  # ITM calls, OTM puts
            # ITM call delta (very high)
            call_delta = Decimal("0.50") + Decimal(str(min(0.48, abs(distance) / 200)))
            # OTM put delta (decreases with distance)
            put_delta = -Decimal("0.50") * Decimal(str(max(0.02, 1 - abs(distance) / 200)))
        else:  # ATM
            call_delta = Decimal("0.50")
            put_delta = Decimal("-0.50")

        # Create call
        call = OptionContract(
            symbol="SPX",
            strike=strike,
            expiration=expiration,
            option_type=OptionType.CALL,
            bid=Decimal("15.80"),
            ask=Decimal("16.20"),
            last=Decimal("16.00"),
            volume=1000,
            open_interest=5000,
            implied_volatility=Decimal("0.18"),
            delta=max(Decimal("0.01"), min(Decimal("0.99"), call_delta)),
            gamma=Decimal("0.002"),
            theta=Decimal("-0.15"),
            vega=Decimal("0.35"),
            dte=45
        )
        calls.append(call)

        # Create put
        put = OptionContract(
            symbol="SPX",
            strike=strike,
            expiration=expiration,
            option_type=OptionType.PUT,
            bid=Decimal("14.60"),
            ask=Decimal("15.00"),
            last=Decimal("14.80"),
            volume=1200,
            open_interest=6000,
            implied_volatility=Decimal("0.19"),
            delta=max(Decimal("-0.99"), min(Decimal("-0.01"), put_delta)),
            gamma=Decimal("0.002"),
            theta=Decimal("-0.14"),
            vega=Decimal("0.33"),
            dte=45
        )
        puts.append(put)

    # Create chain
    chain = OptionsChain(
        symbol="SPX",
        timestamp=datetime.now(),
        underlying_price=underlying_price,
        expirations=[expiration]
    )
    chain.calls[expiration] = calls
    chain.puts[expiration] = puts

    return chain


def example_1_build_iron_condor():
    """Example 1: Build an iron condor spread."""
    print("=" * 80)
    print("EXAMPLE 1: Building an Iron Condor")
    print("=" * 80)

    # Create sample chain
    chain = create_sample_options_chain()
    builder = SpreadBuilder()

    # Build iron condor with 10-delta short strikes, 10-point wings
    print("\nBuilding iron condor:")
    print("  - Short strikes: 10-delta")
    print("  - Wing width: $10")
    print("  - Expiration: 45 DTE")

    spread = builder.build_iron_condor(
        chain=chain,
        put_short_delta=Decimal("0.10"),
        call_short_delta=Decimal("0.10"),
        wing_width=Decimal("10"),
        expiration_dte=45,
        quantity=1
    )

    print("\nIron Condor Details:")
    print(f"  Spread Type: {spread.spread_type.value}")
    print(f"  Number of Legs: {len(spread.legs)}")

    for leg in spread.legs:
        print(f"\n  Leg {leg.leg_index}: {leg.action.value}")
        print(f"    Strike: ${leg.contract.strike}")
        print(f"    Type: {leg.contract.option_type.value}")
        print(f"    Delta: {leg.contract.delta:.3f}")
        print(f"    Quantity: {leg.quantity}")

    print(f"\nPortfolio Greeks:")
    print(f"  Delta: {spread.portfolio_delta:.3f}")
    print(f"  Gamma: {spread.portfolio_gamma:.5f}")
    print(f"  Theta: {spread.portfolio_theta:.3f}")
    print(f"  Vega: {spread.portfolio_vega:.3f}")

    print(f"\nRisk Metrics:")
    print(f"  Net Premium: ${spread.entry_premium:.2f}")
    print(f"  Max Profit: ${spread.max_profit:.2f}")
    print(f"  Max Loss: ${spread.max_loss:.2f}")
    print(f"  Breakeven Points: {[f'${bp:.2f}' for bp in spread.breakeven_points]}")

    # Validate spread
    is_valid, error = builder.validate_spread(spread)
    print(f"\nSpread Validation: {'VALID' if is_valid else f'INVALID - {error}'}")

    return spread


def example_2_submit_and_fill():
    """Example 2: Submit order and simulate fill."""
    print("\n" + "=" * 80)
    print("EXAMPLE 2: Submitting Order and Fill Simulation")
    print("=" * 80)

    # Build spread
    chain = create_sample_options_chain()
    builder = SpreadBuilder()
    spread = builder.build_iron_condor(
        chain, Decimal("0.10"), Decimal("0.10"), Decimal("10")
    )

    # Initialize broker
    broker = OptionsPaperBroker(
        initial_capital=100_000,
        commission_per_contract=0.65
    )

    print("\nAccount before trade:")
    account = broker.get_account()
    print(f"  Cash: ${account['cash']:,.2f}")
    print(f"  Total Equity: ${account['total_equity']:,.2f}")

    # Submit order
    print("\nSubmitting market order for iron condor...")
    order = broker.submit_spread_order(spread)

    print(f"\nOrder Status: {order.status.value}")
    print(f"Order ID: {order.order_id}")

    if order.is_filled:
        print(f"Fill Price: ${order.fill_price:.2f}")
        print(f"Fill Time: {order.filled_at.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Commission: ${order.total_commission:.2f}")

        print(f"\nLeg Fills:")
        for leg_fill in order.leg_fills:
            print(f"  Leg {leg_fill['leg_index']}: {leg_fill['action'].value}")
            print(f"    Strike: ${leg_fill['contract'].strike}")
            print(f"    Price: ${leg_fill['price']:.2f}")

        # Check account after
        print("\nAccount after trade:")
        account = broker.get_account_summary()
        print(f"  Cash: ${account['cash']:,.2f}")
        print(f"  Open Positions: {account['open_positions_count']}")
        print(f"  Capital at Risk: ${account['total_capital_at_risk']:,.2f}")

    return broker, order


def example_3_fill_simulator():
    """Example 3: Demonstrate fill simulator."""
    print("\n" + "=" * 80)
    print("EXAMPLE 3: Fill Simulator")
    print("=" * 80)

    simulator = FillSimulator(
        base_spread_pct=0.02,
        slippage_bps=5.0
    )

    # Test different order types
    bid = Decimal("5.80")
    ask = Decimal("5.90")
    mid = (bid + ask) / Decimal("2")

    print(f"\nMarket Data:")
    print(f"  Bid: ${bid:.2f}")
    print(f"  Ask: ${ask:.2f}")
    print(f"  Mid: ${mid:.2f}")
    print(f"  Volume: 1,500 contracts")

    # Market buy
    print("\n1. Market Buy Order (10 contracts):")
    fill = simulator.simulate_fill("MARKET", "BUY", 10, bid, ask, 1500)
    print(f"   Fill Price: ${fill:.2f}")
    print(f"   Cost vs Ask: ${(fill - ask):.2f} ({((fill - ask) / ask * 100):.2f}%)")

    # Market sell
    print("\n2. Market Sell Order (10 contracts):")
    fill = simulator.simulate_fill("MARKET", "SELL", 10, bid, ask, 1500)
    print(f"   Fill Price: ${fill:.2f}")
    print(f"   Cost vs Bid: ${(bid - fill):.2f} ({((bid - fill) / bid * 100):.2f}%)")

    # Limit buy at mid
    print("\n3. Limit Buy Order at Mid (10 contracts):")
    fill = simulator.simulate_fill("LIMIT", "BUY", 10, bid, ask, 1500, limit_price=mid)
    print(f"   Fill Price: ${fill:.2f}")

    # Estimate fill probabilities
    print("\n4. Fill Probability Estimates:")
    scenarios = [
        ("Aggressive buy (at ask)", "BUY", ask),
        ("Mid buy", "BUY", mid),
        ("Passive buy (at bid)", "BUY", bid),
        ("Aggressive sell (at bid)", "SELL", bid),
        ("Mid sell", "SELL", mid),
    ]

    for name, side, limit_price in scenarios:
        prob = simulator.estimate_fill_probability(
            "LIMIT", side, limit_price, bid, ask, 1500
        )
        print(f"   {name}: {prob:.1%}")


def example_4_position_tracking():
    """Example 4: Position tracking with exit monitoring."""
    print("\n" + "=" * 80)
    print("EXAMPLE 4: Position Tracking")
    print("=" * 80)

    # Setup
    chain = create_sample_options_chain()
    builder = SpreadBuilder()
    greeks_calc = GreeksCalculator(risk_free_rate=0.045, dividend_yield=0.018)

    # Build and open position
    spread = builder.build_iron_condor(
        chain, Decimal("0.10"), Decimal("0.10"), Decimal("10")
    )

    # Initialize tracker
    tracker = PositionTracker(
        greeks_calculator=greeks_calc,
        supabase_client=None,  # No database for demo
        exit_profit_pct=0.50,
        exit_loss_pct=2.00,
        exit_dte=7
    )

    print("\nOpening position...")
    position_id = tracker.open_position(spread)
    print(f"Position ID: {position_id}")

    # Simulate daily updates
    print("\nSimulating 5 days of market movement...")

    for day in range(1, 6):
        print(f"\nDay {day}:")

        # Update underlying price (simulate movement)
        price_change = Decimal(str((-1) ** day * 10 * day))  # Oscillating movement
        new_price = chain.underlying_price + price_change
        chain.underlying_price = new_price

        print(f"  Underlying: ${new_price:.2f} ({price_change:+.2f})")

        # Update position
        tracker.update_position(position_id, chain, persist_greeks=False)

        # Get updated spread
        spread = tracker.get_position(position_id)

        print(f"  Portfolio Delta: {spread.portfolio_delta:.3f}")
        print(f"  Portfolio Theta: {spread.portfolio_theta:.3f}")

        pnl = spread.calculate_unrealized_pnl()
        print(f"  Unrealized P&L: ${pnl:.2f}")

        # Check exit conditions
        should_exit, reason = tracker.check_exit_conditions(position_id)
        if should_exit:
            print(f"  ⚠️  EXIT SIGNAL: {reason}")

    # Portfolio summary
    print("\nPortfolio Summary:")
    print(f"  Open Positions: {tracker.get_position_count()}")
    print(f"  Total Capital at Risk: ${tracker.get_total_capital_at_risk():,.2f}")

    portfolio_greeks = tracker.get_portfolio_greeks()
    print(f"\nPortfolio Greeks:")
    print(f"  Total Delta: {portfolio_greeks['total_delta']:.3f}")
    print(f"  Total Gamma: {portfolio_greeks['total_gamma']:.5f}")
    print(f"  Total Theta: {portfolio_greeks['total_theta']:.3f}")
    print(f"  Total Vega: {portfolio_greeks['total_vega']:.3f}")


def example_5_complete_workflow():
    """Example 5: Complete trading workflow."""
    print("\n" + "=" * 80)
    print("EXAMPLE 5: Complete Trading Workflow")
    print("=" * 80)

    # Initialize all components
    broker = OptionsPaperBroker(initial_capital=100_000)
    builder = SpreadBuilder()
    simulator = FillSimulator()
    greeks_calc = GreeksCalculator()
    tracker = PositionTracker(greeks_calc, None)

    chain = create_sample_options_chain()

    print("\nStep 1: Build spread")
    spread = builder.build_iron_condor(
        chain, Decimal("0.10"), Decimal("0.10"), Decimal("10")
    )
    print(f"  Built {spread.spread_type.value} with ${spread.max_profit:.2f} max profit")

    print("\nStep 2: Submit order")
    order = broker.submit_spread_order(spread)
    print(f"  Order {order.order_id}: {order.status.value}")

    if order.is_filled:
        print(f"  Filled at ${order.fill_price:.2f}")

        print("\nStep 3: Track position")
        position_id = tracker.open_position(spread)
        print(f"  Tracking position {position_id}")

        print("\nStep 4: Monitor (simulating 3 days)...")
        for day in [1, 2, 3]:
            # Update prices
            tracker.update_position(position_id, chain, persist_greeks=False)

            # Check exit
            should_exit, reason = tracker.check_exit_conditions(position_id)

            pos = tracker.get_position(position_id)
            pnl = pos.calculate_unrealized_pnl()

            print(f"  Day {day}: P&L=${pnl:.2f}, Exit={should_exit}")

        print("\nStep 5: Close position")
        close_order = broker.close_spread(position_id)

        if close_order and close_order.is_filled:
            closed = tracker.close_position(
                position_id,
                "MANUAL_CLOSE",
                close_order.fill_price
            )
            print(f"  Position closed")
            print(f"  Realized P&L: ${closed.realized_pnl:.2f}")

        # Final account summary
        print("\nFinal Account Summary:")
        account = broker.get_account_summary()
        print(f"  Total Equity: ${account['total_equity']:,.2f}")
        print(f"  Realized P&L: ${account['realized_pnl']:,.2f}")


def main():
    """Run all examples."""
    print("\n" + "=" * 80)
    print("APEX-SHARPE EXECUTION LAYER - EXAMPLE USAGE")
    print("=" * 80)

    try:
        # Run examples
        example_1_build_iron_condor()
        example_2_submit_and_fill()
        example_3_fill_simulator()
        example_4_position_tracking()
        example_5_complete_workflow()

        print("\n" + "=" * 80)
        print("All examples completed successfully!")
        print("=" * 80 + "\n")

    except Exception as e:
        print(f"\n❌ Error running examples: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
