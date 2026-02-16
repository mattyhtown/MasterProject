"""
Greeks Calculator for APEX-SHARPE Trading System.

Integrates FinancePy for options pricing and Greeks calculations.
Provides both single-option and portfolio-level Greeks analysis.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import List, Optional, Tuple, Dict
from enum import Enum

from financepy.utils.date import Date
from financepy.products.equity import EquityVanillaOption, OptionTypes
from financepy.models.black_scholes import BlackScholes
from financepy.market.curves import DiscountCurveFlat


class OptionType(Enum):
    """Option type enumeration."""
    CALL = "CALL"
    PUT = "PUT"


class OptionAction(Enum):
    """Option action enumeration."""
    BUY = "BUY"
    SELL = "SELL"
    BTO = "BTO"  # Buy to Open
    STO = "STO"  # Sell to Open
    BTC = "BTC"  # Buy to Close
    STC = "STC"  # Sell to Close


@dataclass
class OptionContract:
    """
    Represents a single option contract.

    Attributes:
        option_type: Call or Put
        strike: Strike price
        expiration_date: Option expiration date
        quantity: Number of contracts (positive for long, negative for short)
        implied_volatility: Implied volatility (as decimal, e.g., 0.20 for 20%)
    """
    option_type: OptionType
    strike: Decimal
    expiration_date: date
    quantity: int
    implied_volatility: Optional[Decimal] = None


@dataclass
class GreeksData:
    """
    Greeks values for a single option contract.

    All Greeks are per-contract values:
    - Delta: Change in option price per $1 change in underlying
    - Gamma: Change in delta per $1 change in underlying
    - Theta: Change in option price per day
    - Vega: Change in option price per 1% change in IV
    - Rho: Change in option price per 1% change in interest rate
    """
    delta: Decimal
    gamma: Decimal
    theta: Decimal
    vega: Decimal
    rho: Decimal
    option_price: Decimal
    underlying_price: Decimal
    strike: Decimal
    time_to_expiry: Decimal  # In years
    implied_volatility: Decimal


@dataclass
class PositionGreeks:
    """
    Greeks for a single position (contract * quantity).

    Accounts for long/short position direction.
    """
    contract: OptionContract
    greeks_data: GreeksData
    position_delta: Decimal
    position_gamma: Decimal
    position_theta: Decimal
    position_vega: Decimal
    position_rho: Decimal
    position_value: Decimal

    @classmethod
    def from_contract_greeks(
        cls,
        contract: OptionContract,
        greeks_data: GreeksData
    ) -> 'PositionGreeks':
        """
        Create position Greeks from contract Greeks and quantity.

        Args:
            contract: The option contract
            greeks_data: Per-contract Greeks

        Returns:
            PositionGreeks with scaled values
        """
        quantity = contract.quantity
        multiplier = 100  # Standard options contract multiplier

        return cls(
            contract=contract,
            greeks_data=greeks_data,
            position_delta=Decimal(str(greeks_data.delta * quantity * multiplier)),
            position_gamma=Decimal(str(greeks_data.gamma * quantity * multiplier)),
            position_theta=Decimal(str(greeks_data.theta * quantity * multiplier)),
            position_vega=Decimal(str(greeks_data.vega * quantity * multiplier)),
            position_rho=Decimal(str(greeks_data.rho * quantity * multiplier)),
            position_value=Decimal(str(greeks_data.option_price * quantity * multiplier))
        )


@dataclass
class PortfolioGreeksSnapshot:
    """
    Aggregated Greeks for an entire portfolio.

    Attributes:
        timestamp: Calculation timestamp
        underlying_price: Current underlying price
        total_delta: Net delta across all positions
        total_gamma: Net gamma across all positions
        total_theta: Net theta across all positions (daily P&L decay)
        total_vega: Net vega across all positions
        total_rho: Net rho across all positions
        total_value: Total portfolio value
        positions: Individual position Greeks
        risk_metrics: Additional risk calculations
    """
    timestamp: datetime
    underlying_price: Decimal
    total_delta: Decimal
    total_gamma: Decimal
    total_theta: Decimal
    total_vega: Decimal
    total_rho: Decimal
    total_value: Decimal
    positions: List[PositionGreeks] = field(default_factory=list)
    risk_metrics: Dict[str, Decimal] = field(default_factory=dict)

    def delta_percentage(self) -> Decimal:
        """Calculate delta as percentage of underlying price exposure."""
        if self.underlying_price == 0:
            return Decimal('0')
        return (self.total_delta / self.underlying_price) * Decimal('100')

    def is_delta_neutral(self, threshold: Decimal = Decimal('0.1')) -> bool:
        """
        Check if portfolio is delta neutral.

        Args:
            threshold: Maximum delta percentage to consider neutral

        Returns:
            True if abs(delta_percentage) <= threshold
        """
        return abs(self.delta_percentage()) <= threshold


class GreeksCalculator:
    """
    Calculator for options Greeks using FinancePy.

    Manages market data curves and provides methods for calculating
    Greeks for individual options and entire portfolios.
    """

    def __init__(
        self,
        risk_free_rate: float = 0.045,
        dividend_yield: float = 0.018,
        valuation_date: Optional[date] = None
    ):
        """
        Initialize Greeks calculator.

        Args:
            risk_free_rate: Annual risk-free interest rate (e.g., 0.045 for 4.5%)
            dividend_yield: Annual dividend yield (e.g., 0.018 for 1.8%)
            valuation_date: Valuation date (defaults to today)
        """
        self.risk_free_rate = risk_free_rate
        self.dividend_yield = dividend_yield
        self.valuation_date = valuation_date or date.today()

        # Initialize curves
        self._update_curves()

    def _update_curves(self) -> None:
        """Update discount and dividend curves with current rates."""
        financepy_date = Date(
            self.valuation_date.day,
            self.valuation_date.month,
            self.valuation_date.year
        )

        self.discount_curve = DiscountCurveFlat(
            financepy_date,
            self.risk_free_rate
        )

        self.dividend_curve = DiscountCurveFlat(
            financepy_date,
            self.dividend_yield
        )

    def update_curves(
        self,
        risk_free_rate: Optional[float] = None,
        dividend_yield: Optional[float] = None
    ) -> None:
        """
        Update market curves with new rates.

        Args:
            risk_free_rate: New risk-free rate (if provided)
            dividend_yield: New dividend yield (if provided)
        """
        if risk_free_rate is not None:
            self.risk_free_rate = risk_free_rate

        if dividend_yield is not None:
            self.dividend_yield = dividend_yield

        self._update_curves()

    def _python_date_to_financepy(self, python_date: date) -> Date:
        """Convert Python date to FinancePy Date."""
        return Date(python_date.day, python_date.month, python_date.year)

    def _create_financepy_option(
        self,
        contract: OptionContract
    ) -> EquityVanillaOption:
        """
        Create FinancePy option object from contract.

        Args:
            contract: Option contract specification

        Returns:
            FinancePy EquityVanillaOption object
        """
        expiry_date = self._python_date_to_financepy(contract.expiration_date)
        strike_price = float(contract.strike)

        option_type = (
            OptionTypes.EUROPEAN_CALL
            if contract.option_type == OptionType.CALL
            else OptionTypes.EUROPEAN_PUT
        )

        return EquityVanillaOption(expiry_date, strike_price, option_type)

    def calculate_greeks(
        self,
        contract: OptionContract,
        spot_price: Decimal,
        implied_volatility: Optional[Decimal] = None
    ) -> GreeksData:
        """
        Calculate Greeks for a single option contract.

        Args:
            contract: Option contract specification
            spot_price: Current underlying price
            implied_volatility: IV override (uses contract.implied_volatility if not provided)

        Returns:
            GreeksData with all Greeks values

        Raises:
            ValueError: If implied volatility is not provided
        """
        # Determine IV to use
        iv = implied_volatility or contract.implied_volatility
        if iv is None:
            raise ValueError(
                "Implied volatility must be provided either in contract or as parameter"
            )

        # Create FinancePy objects
        option = self._create_financepy_option(contract)
        model = BlackScholes(float(iv))

        # Get valuation date
        val_date = self._python_date_to_financepy(self.valuation_date)
        spot = float(spot_price)

        # Calculate option price
        option_price = option.value(
            val_date,
            spot,
            self.discount_curve,
            self.dividend_curve,
            model
        )

        # Calculate Greeks
        delta = option.delta(
            val_date,
            spot,
            self.discount_curve,
            self.dividend_curve,
            model
        )

        gamma = option.gamma(
            val_date,
            spot,
            self.discount_curve,
            self.dividend_curve,
            model
        )

        theta = option.theta(
            val_date,
            spot,
            self.discount_curve,
            self.dividend_curve,
            model
        )

        vega = option.vega(
            val_date,
            spot,
            self.discount_curve,
            self.dividend_curve,
            model
        )

        rho = option.rho(
            val_date,
            spot,
            self.discount_curve,
            self.dividend_curve,
            model
        )

        # Calculate time to expiry
        days_to_expiry = (contract.expiration_date - self.valuation_date).days
        time_to_expiry = Decimal(str(days_to_expiry / 365.0))

        return GreeksData(
            delta=Decimal(str(delta)),
            gamma=Decimal(str(gamma)),
            theta=Decimal(str(theta)),
            vega=Decimal(str(vega)),
            rho=Decimal(str(rho)),
            option_price=Decimal(str(option_price)),
            underlying_price=spot_price,
            strike=contract.strike,
            time_to_expiry=time_to_expiry,
            implied_volatility=iv
        )

    def calculate_position_greeks(
        self,
        contract: OptionContract,
        spot_price: Decimal,
        implied_volatility: Optional[Decimal] = None
    ) -> PositionGreeks:
        """
        Calculate Greeks for a position (accounting for quantity).

        Args:
            contract: Option contract with quantity
            spot_price: Current underlying price
            implied_volatility: IV override

        Returns:
            PositionGreeks with scaled values
        """
        greeks_data = self.calculate_greeks(contract, spot_price, implied_volatility)
        return PositionGreeks.from_contract_greeks(contract, greeks_data)


class PortfolioGreeksCalculator:
    """
    Calculator for portfolio-level Greeks aggregation.

    Handles multiple positions and provides risk analytics.
    """

    def __init__(self, greeks_calculator: GreeksCalculator):
        """
        Initialize portfolio calculator.

        Args:
            greeks_calculator: Underlying Greeks calculator instance
        """
        self.calculator = greeks_calculator

    def calculate_portfolio_greeks(
        self,
        contracts: List[OptionContract],
        spot_price: Decimal,
        implied_volatilities: Optional[Dict[int, Decimal]] = None
    ) -> PortfolioGreeksSnapshot:
        """
        Calculate aggregated Greeks for a portfolio of options.

        Args:
            contracts: List of option contracts
            spot_price: Current underlying price
            implied_volatilities: Optional dict mapping contract index to IV

        Returns:
            PortfolioGreeksSnapshot with aggregated Greeks
        """
        position_greeks_list: List[PositionGreeks] = []

        total_delta = Decimal('0')
        total_gamma = Decimal('0')
        total_theta = Decimal('0')
        total_vega = Decimal('0')
        total_rho = Decimal('0')
        total_value = Decimal('0')

        # Calculate Greeks for each position
        for idx, contract in enumerate(contracts):
            # Get IV for this contract
            iv = None
            if implied_volatilities and idx in implied_volatilities:
                iv = implied_volatilities[idx]

            # Calculate position Greeks
            pos_greeks = self.calculator.calculate_position_greeks(
                contract,
                spot_price,
                iv
            )

            position_greeks_list.append(pos_greeks)

            # Aggregate
            total_delta += pos_greeks.position_delta
            total_gamma += pos_greeks.position_gamma
            total_theta += pos_greeks.position_theta
            total_vega += pos_greeks.position_vega
            total_rho += pos_greeks.position_rho
            total_value += pos_greeks.position_value

        # Calculate risk metrics
        risk_metrics = self._calculate_risk_metrics(
            position_greeks_list,
            spot_price,
            total_delta,
            total_gamma,
            total_theta
        )

        return PortfolioGreeksSnapshot(
            timestamp=datetime.now(),
            underlying_price=spot_price,
            total_delta=total_delta,
            total_gamma=total_gamma,
            total_theta=total_theta,
            total_vega=total_vega,
            total_rho=total_rho,
            total_value=total_value,
            positions=position_greeks_list,
            risk_metrics=risk_metrics
        )

    def _calculate_risk_metrics(
        self,
        positions: List[PositionGreeks],
        spot_price: Decimal,
        total_delta: Decimal,
        total_gamma: Decimal,
        total_theta: Decimal
    ) -> Dict[str, Decimal]:
        """
        Calculate additional risk metrics.

        Args:
            positions: List of position Greeks
            spot_price: Current underlying price
            total_delta: Portfolio delta
            total_gamma: Portfolio gamma
            total_theta: Portfolio theta

        Returns:
            Dictionary of risk metrics
        """
        metrics: Dict[str, Decimal] = {}

        # Delta as percentage of notional
        notional = spot_price * sum(
            abs(Decimal(str(pos.contract.quantity))) for pos in positions
        ) * Decimal('100')

        if notional > 0:
            metrics['delta_percentage'] = (total_delta / spot_price) * Decimal('100')
            metrics['notional_exposure'] = notional

        # Gamma risk (potential delta change for 1% move)
        one_percent_move = spot_price * Decimal('0.01')
        metrics['gamma_risk_1pct'] = total_gamma * one_percent_move

        # Theta as percentage of portfolio value
        total_value = sum(abs(pos.position_value) for pos in positions)
        if total_value > 0:
            metrics['theta_percentage'] = (total_theta / total_value) * Decimal('100')

        # Breakeven days (assuming theta decay only)
        if total_theta != 0:
            metrics['breakeven_days'] = abs(total_value / total_theta)

        return metrics

    def calculate_portfolio_pnl_scenarios(
        self,
        contracts: List[OptionContract],
        current_spot: Decimal,
        spot_scenarios: List[Decimal],
        implied_volatilities: Optional[Dict[int, Decimal]] = None,
        days_forward: int = 1
    ) -> List[Tuple[Decimal, Decimal]]:
        """
        Calculate P&L scenarios for different spot prices.

        Args:
            contracts: List of option contracts
            current_spot: Current underlying price
            spot_scenarios: List of spot prices to evaluate
            implied_volatilities: Optional IV overrides
            days_forward: Number of days forward to project

        Returns:
            List of (spot_price, pnl) tuples
        """
        # Calculate current portfolio value
        current_snapshot = self.calculate_portfolio_greeks(
            contracts,
            current_spot,
            implied_volatilities
        )
        current_value = current_snapshot.total_value

        # Adjust valuation date forward
        original_date = self.calculator.valuation_date
        forward_date = original_date + timedelta(days=days_forward)
        self.calculator.valuation_date = forward_date
        self.calculator._update_curves()

        pnl_scenarios: List[Tuple[Decimal, Decimal]] = []

        try:
            for scenario_spot in spot_scenarios:
                # Calculate portfolio value at scenario spot
                scenario_snapshot = self.calculate_portfolio_greeks(
                    contracts,
                    scenario_spot,
                    implied_volatilities
                )

                # Calculate P&L
                pnl = scenario_snapshot.total_value - current_value
                pnl_scenarios.append((scenario_spot, pnl))

        finally:
            # Restore original valuation date
            self.calculator.valuation_date = original_date
            self.calculator._update_curves()

        return pnl_scenarios


# Convenience function for quick calculations
def calculate_option_greeks(
    option_type: str,
    strike: float,
    expiration_date: date,
    spot_price: float,
    implied_volatility: float,
    quantity: int = 1,
    risk_free_rate: float = 0.045,
    dividend_yield: float = 0.018
) -> PositionGreeks:
    """
    Convenience function for quick Greeks calculation.

    Args:
        option_type: 'CALL' or 'PUT'
        strike: Strike price
        expiration_date: Option expiration
        spot_price: Current underlying price
        implied_volatility: Implied volatility (e.g., 0.20 for 20%)
        quantity: Number of contracts
        risk_free_rate: Risk-free rate
        dividend_yield: Dividend yield

    Returns:
        PositionGreeks with calculated values

    Example:
        >>> from datetime import date, timedelta
        >>> greeks = calculate_option_greeks(
        ...     option_type='CALL',
        ...     strike=5800,
        ...     expiration_date=date.today() + timedelta(days=30),
        ...     spot_price=5850,
        ...     implied_volatility=0.18,
        ...     quantity=10
        ... )
        >>> print(f"Position Delta: {greeks.position_delta}")
    """
    calculator = GreeksCalculator(
        risk_free_rate=risk_free_rate,
        dividend_yield=dividend_yield
    )

    contract = OptionContract(
        option_type=OptionType[option_type.upper()],
        strike=Decimal(str(strike)),
        expiration_date=expiration_date,
        quantity=quantity,
        implied_volatility=Decimal(str(implied_volatility))
    )

    return calculator.calculate_position_greeks(
        contract,
        Decimal(str(spot_price))
    )
