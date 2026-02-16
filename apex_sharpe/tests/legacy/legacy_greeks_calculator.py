#!/usr/bin/env python3
"""
Unit tests for APEX-SHARPE Greeks Calculator.

Tests all major functionality including:
- Single option Greeks calculation
- Portfolio Greeks aggregation
- Risk metrics
- P&L scenarios
- Market curve updates
"""

import pytest
from datetime import date, timedelta
from decimal import Decimal
from greeks_calculator import (
    GreeksCalculator,
    PortfolioGreeksCalculator,
    OptionContract,
    GreeksData,
    PositionGreeks,
    PortfolioGreeksSnapshot,
    OptionType,
    calculate_option_greeks,
)


class TestGreeksCalculator:
    """Test suite for GreeksCalculator."""

    @pytest.fixture
    def calculator(self):
        """Create a GreeksCalculator instance."""
        return GreeksCalculator(
            risk_free_rate=0.045,
            dividend_yield=0.018
        )

    @pytest.fixture
    def call_contract(self):
        """Create a sample call option contract."""
        return OptionContract(
            option_type=OptionType.CALL,
            strike=Decimal('5850'),
            expiration_date=date.today() + timedelta(days=30),
            quantity=1,
            implied_volatility=Decimal('0.18')
        )

    @pytest.fixture
    def put_contract(self):
        """Create a sample put option contract."""
        return OptionContract(
            option_type=OptionType.PUT,
            strike=Decimal('5750'),
            expiration_date=date.today() + timedelta(days=30),
            quantity=1,
            implied_volatility=Decimal('0.18')
        )

    def test_calculator_initialization(self, calculator):
        """Test calculator initializes correctly."""
        assert calculator.risk_free_rate == 0.045
        assert calculator.dividend_yield == 0.018
        assert calculator.valuation_date == date.today()
        assert calculator.discount_curve is not None
        assert calculator.dividend_curve is not None

    def test_calculate_call_greeks(self, calculator, call_contract):
        """Test calculating Greeks for a call option."""
        spot = Decimal('5800')
        greeks = calculator.calculate_greeks(call_contract, spot)

        # Verify Greeks object is returned
        assert isinstance(greeks, GreeksData)

        # Verify Greeks have reasonable values for OTM call
        assert 0 < greeks.delta < 1  # Call delta between 0 and 1
        assert greeks.gamma > 0  # Gamma always positive
        assert greeks.theta < 0  # Long option has negative theta
        assert greeks.vega > 0  # Vega always positive for long options
        assert greeks.option_price > 0  # Price must be positive

    def test_calculate_put_greeks(self, calculator, put_contract):
        """Test calculating Greeks for a put option."""
        spot = Decimal('5800')
        greeks = calculator.calculate_greeks(put_contract, spot)

        # Verify Greeks have reasonable values for OTM put
        assert -1 < greeks.delta < 0  # Put delta between -1 and 0
        assert greeks.gamma > 0  # Gamma always positive
        assert greeks.theta < 0  # Long option has negative theta
        assert greeks.vega > 0  # Vega always positive
        assert greeks.option_price > 0

    def test_atm_option_greeks(self, calculator):
        """Test Greeks for ATM option have expected properties."""
        spot = Decimal('5800')
        atm_call = OptionContract(
            option_type=OptionType.CALL,
            strike=spot,
            expiration_date=date.today() + timedelta(days=30),
            quantity=1,
            implied_volatility=Decimal('0.18')
        )

        greeks = calculator.calculate_greeks(atm_call, spot)

        # ATM call should have delta around 0.5
        assert 0.4 < greeks.delta < 0.6
        # ATM options have highest gamma
        assert greeks.gamma > 0

    def test_position_greeks_long(self, calculator, call_contract):
        """Test position Greeks for long position."""
        spot = Decimal('5800')
        call_contract.quantity = 10

        pos_greeks = calculator.calculate_position_greeks(call_contract, spot)

        assert isinstance(pos_greeks, PositionGreeks)
        # Position Greeks should be scaled by quantity * 100
        assert pos_greeks.position_delta == pos_greeks.greeks_data.delta * 10 * 100
        assert pos_greeks.position_value > 0

    def test_position_greeks_short(self, calculator, call_contract):
        """Test position Greeks for short position."""
        spot = Decimal('5800')
        call_contract.quantity = -10

        pos_greeks = calculator.calculate_position_greeks(call_contract, spot)

        # Short position should have negative delta
        assert pos_greeks.position_delta < 0
        # Short position should have positive theta
        assert pos_greeks.position_theta > 0

    def test_update_curves(self, calculator, call_contract):
        """Test updating market curves changes Greeks."""
        spot = Decimal('5800')

        # Long-dated option to see rho effect
        call_contract.expiration_date = date.today() + timedelta(days=365)

        greeks_before = calculator.calculate_greeks(call_contract, spot)

        # Update risk-free rate
        calculator.update_curves(risk_free_rate=0.055)

        greeks_after = calculator.calculate_greeks(call_contract, spot)

        # Rho should show the rate change affected price
        assert greeks_before.option_price != greeks_after.option_price

    def test_implied_volatility_override(self, calculator, call_contract):
        """Test overriding implied volatility."""
        spot = Decimal('5800')

        # Calculate with contract IV
        greeks1 = calculator.calculate_greeks(call_contract, spot)

        # Calculate with higher IV override
        greeks2 = calculator.calculate_greeks(
            call_contract,
            spot,
            implied_volatility=Decimal('0.25')
        )

        # Higher IV should increase option price and vega
        assert greeks2.option_price > greeks1.option_price

    def test_missing_iv_raises_error(self, calculator):
        """Test that missing IV raises ValueError."""
        contract = OptionContract(
            option_type=OptionType.CALL,
            strike=Decimal('5800'),
            expiration_date=date.today() + timedelta(days=30),
            quantity=1,
            implied_volatility=None  # No IV provided
        )

        with pytest.raises(ValueError):
            calculator.calculate_greeks(contract, Decimal('5800'))


class TestPortfolioGreeksCalculator:
    """Test suite for PortfolioGreeksCalculator."""

    @pytest.fixture
    def calculator(self):
        """Create a GreeksCalculator instance."""
        return GreeksCalculator()

    @pytest.fixture
    def portfolio_calc(self, calculator):
        """Create a PortfolioGreeksCalculator instance."""
        return PortfolioGreeksCalculator(calculator)

    @pytest.fixture
    def iron_condor_contracts(self):
        """Create Iron Condor contracts."""
        expiry = date.today() + timedelta(days=45)
        return [
            # Put spread
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
            # Call spread
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

    def test_portfolio_greeks_calculation(
        self,
        portfolio_calc,
        iron_condor_contracts
    ):
        """Test portfolio Greeks calculation."""
        spot = Decimal('5800')
        snapshot = portfolio_calc.calculate_portfolio_greeks(
            iron_condor_contracts,
            spot
        )

        assert isinstance(snapshot, PortfolioGreeksSnapshot)
        assert snapshot.underlying_price == spot
        assert len(snapshot.positions) == 4
        assert snapshot.timestamp is not None

    def test_iron_condor_delta_neutral(
        self,
        portfolio_calc,
        iron_condor_contracts
    ):
        """Test that Iron Condor is approximately delta neutral."""
        spot = Decimal('5800')
        snapshot = portfolio_calc.calculate_portfolio_greeks(
            iron_condor_contracts,
            spot
        )

        # Iron Condor should be approximately delta neutral
        assert abs(snapshot.delta_percentage()) < 5  # Within 5%

    def test_iron_condor_positive_theta(
        self,
        portfolio_calc,
        iron_condor_contracts
    ):
        """Test that Iron Condor has positive theta."""
        spot = Decimal('5800')
        snapshot = portfolio_calc.calculate_portfolio_greeks(
            iron_condor_contracts,
            spot
        )

        # Short Iron Condor should collect theta
        assert snapshot.total_theta > 0

    def test_straddle_greeks(self, portfolio_calc):
        """Test Greeks for a straddle position."""
        spot = Decimal('5800')
        expiry = date.today() + timedelta(days=30)

        contracts = [
            OptionContract(
                option_type=OptionType.CALL,
                strike=spot,
                expiration_date=expiry,
                quantity=1,
                implied_volatility=Decimal('0.18')
            ),
            OptionContract(
                option_type=OptionType.PUT,
                strike=spot,
                expiration_date=expiry,
                quantity=1,
                implied_volatility=Decimal('0.18')
            ),
        ]

        snapshot = portfolio_calc.calculate_portfolio_greeks(contracts, spot)

        # Long straddle should be approximately delta neutral
        assert abs(snapshot.delta_percentage()) < 5
        # Long straddle should have negative theta
        assert snapshot.total_theta < 0
        # Long straddle should have positive vega
        assert snapshot.total_vega > 0

    def test_risk_metrics_calculated(
        self,
        portfolio_calc,
        iron_condor_contracts
    ):
        """Test that risk metrics are calculated."""
        spot = Decimal('5800')
        snapshot = portfolio_calc.calculate_portfolio_greeks(
            iron_condor_contracts,
            spot
        )

        assert 'gamma_risk_1pct' in snapshot.risk_metrics
        assert 'theta_percentage' in snapshot.risk_metrics
        assert 'notional_exposure' in snapshot.risk_metrics

    def test_pnl_scenarios(self, portfolio_calc):
        """Test P&L scenario calculation."""
        spot = Decimal('5800')
        expiry = date.today() + timedelta(days=30)

        contracts = [
            OptionContract(
                option_type=OptionType.CALL,
                strike=spot,
                expiration_date=expiry,
                quantity=-1,
                implied_volatility=Decimal('0.18')
            ),
        ]

        spot_scenarios = [
            spot - Decimal('50'),
            spot,
            spot + Decimal('50'),
        ]

        pnl_scenarios = portfolio_calc.calculate_portfolio_pnl_scenarios(
            contracts,
            spot,
            spot_scenarios,
            days_forward=1
        )

        assert len(pnl_scenarios) == 3
        # Each scenario returns (spot, pnl) tuple
        for scenario_spot, pnl in pnl_scenarios:
            assert isinstance(scenario_spot, Decimal)
            assert isinstance(pnl, Decimal)

    def test_is_delta_neutral(self, portfolio_calc):
        """Test delta neutrality check."""
        spot = Decimal('5800')
        expiry = date.today() + timedelta(days=30)

        # Create delta-neutral position (ATM straddle)
        contracts = [
            OptionContract(
                option_type=OptionType.CALL,
                strike=spot,
                expiration_date=expiry,
                quantity=1,
                implied_volatility=Decimal('0.18')
            ),
            OptionContract(
                option_type=OptionType.PUT,
                strike=spot,
                expiration_date=expiry,
                quantity=1,
                implied_volatility=Decimal('0.18')
            ),
        ]

        snapshot = portfolio_calc.calculate_portfolio_greeks(contracts, spot)
        assert snapshot.is_delta_neutral()


class TestConvenienceFunction:
    """Test convenience function for quick calculations."""

    def test_calculate_option_greeks(self):
        """Test convenience function works correctly."""
        greeks = calculate_option_greeks(
            option_type='CALL',
            strike=5850,
            expiration_date=date.today() + timedelta(days=30),
            spot_price=5800,
            implied_volatility=0.18,
            quantity=10
        )

        assert isinstance(greeks, PositionGreeks)
        assert greeks.contract.option_type == OptionType.CALL
        assert greeks.contract.quantity == 10

    def test_calculate_option_greeks_put(self):
        """Test convenience function with put option."""
        greeks = calculate_option_greeks(
            option_type='put',  # Test case-insensitive
            strike=5750,
            expiration_date=date.today() + timedelta(days=30),
            spot_price=5800,
            implied_volatility=0.18,
            quantity=-5  # Short position
        )

        assert greeks.contract.option_type == OptionType.PUT
        assert greeks.contract.quantity == -5
        # Short put should have positive delta
        assert greeks.position_delta > 0


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_zero_quantity_position(self):
        """Test handling of zero quantity."""
        calculator = GreeksCalculator()

        contract = OptionContract(
            option_type=OptionType.CALL,
            strike=Decimal('5800'),
            expiration_date=date.today() + timedelta(days=30),
            quantity=0,
            implied_volatility=Decimal('0.18')
        )

        pos_greeks = calculator.calculate_position_greeks(
            contract,
            Decimal('5800')
        )

        # All position Greeks should be zero
        assert pos_greeks.position_delta == 0
        assert pos_greeks.position_gamma == 0
        assert pos_greeks.position_value == 0

    def test_deep_itm_call(self):
        """Test deep ITM call option."""
        calculator = GreeksCalculator()

        contract = OptionContract(
            option_type=OptionType.CALL,
            strike=Decimal('5500'),
            expiration_date=date.today() + timedelta(days=30),
            quantity=1,
            implied_volatility=Decimal('0.18')
        )

        greeks = calculator.calculate_greeks(contract, Decimal('5800'))

        # Deep ITM call should have delta close to 1
        assert greeks.delta > 0.9

    def test_deep_otm_put(self):
        """Test deep OTM put option."""
        calculator = GreeksCalculator()

        contract = OptionContract(
            option_type=OptionType.PUT,
            strike=Decimal('5500'),
            expiration_date=date.today() + timedelta(days=30),
            quantity=1,
            implied_volatility=Decimal('0.18')
        )

        greeks = calculator.calculate_greeks(contract, Decimal('5800'))

        # Deep OTM put should have delta close to 0
        assert abs(greeks.delta) < 0.1

    def test_near_expiration(self):
        """Test option near expiration."""
        calculator = GreeksCalculator()

        contract = OptionContract(
            option_type=OptionType.CALL,
            strike=Decimal('5800'),
            expiration_date=date.today() + timedelta(days=1),
            quantity=1,
            implied_volatility=Decimal('0.18')
        )

        greeks = calculator.calculate_greeks(contract, Decimal('5800'))

        # Near expiration should have high theta (in absolute terms)
        assert abs(greeks.theta) > 0


def run_tests():
    """Run all tests with pytest."""
    pytest.main([__file__, '-v', '--tb=short'])


if __name__ == '__main__':
    run_tests()
