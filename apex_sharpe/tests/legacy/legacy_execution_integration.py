"""
Integration Test for APEX-SHARPE Execution Layer.

Quick smoke test to verify all components work together.
"""

import sys
import os
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from execution import (
    OptionsPaperBroker,
    SpreadBuilder,
    FillSimulator,
    PositionTracker
)


def test_imports():
    """Test that all modules import successfully."""
    print("✓ All imports successful")
    return True


def test_broker_initialization():
    """Test broker initialization."""
    broker = OptionsPaperBroker(
        initial_capital=100_000,
        commission_per_contract=0.65
    )
    assert broker.initial_capital == 100_000
    assert broker.commission_per_contract == 0.65
    print("✓ Broker initialization successful")
    return True


def test_spread_builder():
    """Test spread builder initialization."""
    builder = SpreadBuilder()
    assert builder.contract_multiplier == 100
    print("✓ Spread builder initialization successful")
    return True


def test_fill_simulator():
    """Test fill simulator."""
    simulator = FillSimulator()

    # Test basic fill
    fill = simulator.simulate_fill(
        "MARKET",
        "BUY",
        10,
        Decimal("5.80"),
        Decimal("5.90"),
        1500
    )

    assert fill >= Decimal("5.80")
    assert fill <= Decimal("6.00")
    print("✓ Fill simulator working")
    return True


def test_position_tracker():
    """Test position tracker initialization."""
    from greeks import GreeksCalculator

    greeks_calc = GreeksCalculator()
    tracker = PositionTracker(
        greeks_calculator=greeks_calc,
        supabase_client=None
    )

    assert tracker.get_position_count() == 0
    print("✓ Position tracker initialization successful")
    return True


def run_all_tests():
    """Run all integration tests."""
    print("=" * 60)
    print("APEX-SHARPE EXECUTION LAYER - INTEGRATION TEST")
    print("=" * 60 + "\n")

    tests = [
        ("Imports", test_imports),
        ("Broker", test_broker_initialization),
        ("Spread Builder", test_spread_builder),
        ("Fill Simulator", test_fill_simulator),
        ("Position Tracker", test_position_tracker),
    ]

    passed = 0
    failed = 0

    for name, test_func in tests:
        try:
            print(f"Testing {name}...", end=" ")
            if test_func():
                passed += 1
        except Exception as e:
            print(f"✗ {name} failed: {e}")
            failed += 1

    print("\n" + "=" * 60)
    print(f"Results: {passed}/{len(tests)} tests passed")
    if failed == 0:
        print("✓ All integration tests passed!")
    else:
        print(f"✗ {failed} test(s) failed")
    print("=" * 60 + "\n")

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
