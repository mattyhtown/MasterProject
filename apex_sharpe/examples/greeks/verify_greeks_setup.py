#!/usr/bin/env python3
"""
Setup Verification Script for APEX-SHARPE Greeks Calculator

Checks dependencies and runs basic functionality tests.
"""

import sys
from pathlib import Path


def check_dependencies():
    """Check if required dependencies are installed."""
    print("\n" + "="*60)
    print("Checking Dependencies")
    print("="*60)

    dependencies = {
        'financepy': 'FinancePy (options pricing)',
        'numpy': 'NumPy (numerical computations)',
        'decimal': 'Decimal (Python standard library)',
        'datetime': 'Datetime (Python standard library)',
    }

    missing = []
    installed = []

    for module, description in dependencies.items():
        try:
            __import__(module)
            print(f"✓ {module:<15} - {description}")
            installed.append(module)
        except ImportError:
            print(f"✗ {module:<15} - {description} [MISSING]")
            missing.append(module)

    return missing, installed


def test_imports():
    """Test that the Greeks calculator can be imported."""
    print("\n" + "="*60)
    print("Testing Module Imports")
    print("="*60)

    try:
        from greeks_calculator import (
            GreeksCalculator,
            PortfolioGreeksCalculator,
            OptionContract,
            GreeksData,
            PositionGreeks,
            PortfolioGreeksSnapshot,
            OptionType,
            OptionAction,
            calculate_option_greeks,
        )
        print("✓ All classes imported successfully")
        return True
    except Exception as e:
        print(f"✗ Import failed: {e}")
        return False


def test_basic_functionality():
    """Test basic calculator functionality."""
    print("\n" + "="*60)
    print("Testing Basic Functionality")
    print("="*60)

    try:
        from greeks_calculator import (
            GreeksCalculator,
            PortfolioGreeksCalculator,
            OptionContract,
            OptionType,
            calculate_option_greeks
        )
        from datetime import date, timedelta
        from decimal import Decimal

        # Test 1: Quick calculation
        print("\n1. Testing quick calculation function...")
        greeks = calculate_option_greeks(
            option_type='CALL',
            strike=5850,
            expiration_date=date.today() + timedelta(days=30),
            spot_price=5800,
            implied_volatility=0.18,
            quantity=10
        )
        print(f"   ✓ Position Delta: {greeks.position_delta:.2f}")
        print(f"   ✓ Position Theta: {greeks.position_theta:.2f}")
        print(f"   ✓ Position Value: ${greeks.position_value:.2f}")

        # Test 2: Calculator initialization
        print("\n2. Testing GreeksCalculator initialization...")
        calculator = GreeksCalculator(
            risk_free_rate=0.045,
            dividend_yield=0.018
        )
        print(f"   ✓ Calculator created")
        print(f"   ✓ Risk-free rate: {calculator.risk_free_rate:.2%}")

        # Test 3: Single contract calculation
        print("\n3. Testing single contract Greeks...")
        contract = OptionContract(
            option_type=OptionType.CALL,
            strike=Decimal('5850'),
            expiration_date=date.today() + timedelta(days=30),
            quantity=1,
            implied_volatility=Decimal('0.18')
        )
        greeks_data = calculator.calculate_greeks(contract, Decimal('5800'))
        print(f"   ✓ Delta: {greeks_data.delta:.4f}")
        print(f"   ✓ Gamma: {greeks_data.gamma:.6f}")
        print(f"   ✓ Theta: {greeks_data.theta:.4f}")
        print(f"   ✓ Vega: {greeks_data.vega:.4f}")

        # Test 4: Portfolio calculation
        print("\n4. Testing portfolio Greeks...")
        portfolio_calc = PortfolioGreeksCalculator(calculator)
        contracts = [
            OptionContract(
                OptionType.CALL,
                Decimal('5850'),
                date.today() + timedelta(days=30),
                10,
                Decimal('0.18')
            ),
            OptionContract(
                OptionType.PUT,
                Decimal('5750'),
                date.today() + timedelta(days=30),
                -10,
                Decimal('0.17')
            )
        ]
        snapshot = portfolio_calc.calculate_portfolio_greeks(
            contracts,
            Decimal('5800')
        )
        print(f"   ✓ Portfolio Delta: {snapshot.total_delta:.2f}")
        print(f"   ✓ Portfolio Theta: {snapshot.total_theta:.2f}")
        print(f"   ✓ Delta Neutral: {snapshot.is_delta_neutral()}")
        print(f"   ✓ Portfolio Value: ${snapshot.total_value:.2f}")

        # Test 5: Risk metrics
        print("\n5. Testing risk metrics...")
        print(f"   ✓ Delta %: {snapshot.delta_percentage():.2f}%")
        print(f"   ✓ Gamma Risk (1%): ${snapshot.risk_metrics.get('gamma_risk_1pct', 0):.2f}")

        return True

    except Exception as e:
        print(f"\n✗ Functionality test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def check_file_structure():
    """Verify all expected files are present."""
    print("\n" + "="*60)
    print("Checking File Structure")
    print("="*60)

    expected_files = [
        '__init__.py',
        'greeks_calculator.py',
        'examples.py',
        'test_greeks_calculator.py',
        'integration_example.py',
        'README.md',
        'requirements.txt',
        'IMPLEMENTATION_SUMMARY.md',
        'verify_setup.py'
    ]

    base_path = Path(__file__).parent
    missing = []
    present = []

    for filename in expected_files:
        filepath = base_path / filename
        if filepath.exists():
            size = filepath.stat().st_size
            print(f"✓ {filename:<30} ({size:>6} bytes)")
            present.append(filename)
        else:
            print(f"✗ {filename:<30} [MISSING]")
            missing.append(filename)

    return missing, present


def print_installation_instructions(missing_deps):
    """Print installation instructions for missing dependencies."""
    if not missing_deps:
        return

    print("\n" + "="*60)
    print("Installation Instructions")
    print("="*60)

    print("\nTo install missing dependencies, run:")
    print(f"\n  pip install {' '.join(missing_deps)}")

    print("\nOr install from requirements.txt:")
    print("\n  cd apex-sharpe/greeks")
    print("  pip install -r requirements.txt")


def main():
    """Run all verification checks."""
    print("\n╔" + "="*58 + "╗")
    print("║" + " "*10 + "APEX-SHARPE Greeks Calculator Setup" + " "*13 + "║")
    print("╚" + "="*58 + "╝")

    # Check file structure
    missing_files, present_files = check_file_structure()

    # Check dependencies
    missing_deps, installed_deps = check_dependencies()

    # Test imports
    imports_ok = test_imports()

    # Run functionality tests if imports work
    if imports_ok:
        functionality_ok = test_basic_functionality()
    else:
        functionality_ok = False
        print("\n⚠️  Skipping functionality tests (import failed)")

    # Summary
    print("\n" + "="*60)
    print("Verification Summary")
    print("="*60)

    print(f"\nFiles:         {len(present_files)}/{len(present_files) + len(missing_files)} present")
    print(f"Dependencies:  {len(installed_deps)}/{len(installed_deps) + len(missing_deps)} installed")
    print(f"Imports:       {'✓ OK' if imports_ok else '✗ FAILED'}")
    print(f"Functionality: {'✓ OK' if functionality_ok else '✗ FAILED or SKIPPED'}")

    if missing_files:
        print(f"\n⚠️  Missing files: {', '.join(missing_files)}")

    if missing_deps:
        print_installation_instructions(missing_deps)

    if imports_ok and functionality_ok:
        print("\n" + "="*60)
        print("✓ Setup Complete! Greeks calculator is ready to use.")
        print("="*60)
        print("\nNext steps:")
        print("  1. Run examples:     python examples.py")
        print("  2. Run tests:        pytest test_greeks_calculator.py -v")
        print("  3. Try integration:  python integration_example.py")
        print()
        return 0
    else:
        print("\n" + "="*60)
        print("⚠️  Setup incomplete - see errors above")
        print("="*60)
        print()
        return 1


if __name__ == "__main__":
    sys.exit(main())
