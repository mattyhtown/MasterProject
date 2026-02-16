"""
Margin Calculator for APEX-SHARPE Trading System.

Calculates margin requirements for different options spread types,
supporting both Reg T margin and portfolio margin methodologies.
"""

from dataclasses import dataclass
from typing import Optional, List
from decimal import Decimal
from enum import Enum
# Use relative import for internal APEX-SHARPE module
from ..strategies.base_strategy import MultiLegSpread, SpreadType, SpreadLeg, OptionType


class MarginType(Enum):
    """Margin calculation methodology."""
    REG_T = "REG_T"  # Regulation T (standard)
    PORTFOLIO = "PORTFOLIO"  # Portfolio margin (risk-based)


@dataclass
class MarginRequirement:
    """
    Margin requirement calculation result.

    Attributes:
        total_requirement: Total margin/BPR required
        margin_type: Type of margin calculation used
        spread_type: Type of spread
        per_contract_requirement: Margin per contract
        maintenance_requirement: Maintenance margin (for portfolio margin)
        initial_requirement: Initial margin (for portfolio margin)
        calculation_method: Description of how margin was calculated
        max_loss: Maximum possible loss (for validation)
        components: Breakdown of margin calculation by leg/component
    """
    total_requirement: Decimal
    margin_type: MarginType
    spread_type: SpreadType
    per_contract_requirement: Decimal
    maintenance_requirement: Optional[Decimal] = None
    initial_requirement: Optional[Decimal] = None
    calculation_method: Optional[str] = None
    max_loss: Optional[Decimal] = None
    components: Optional[dict] = None


class MarginCalculator:
    """
    Calculator for options margin requirements.

    Supports multiple spread types and margin methodologies:
    - Reg T (Regulation T): Standard margin rules
    - Portfolio Margin: Risk-based margin (lower requirements)

    Example:
        >>> from decimal import Decimal
        >>>
        >>> calc = MarginCalculator(margin_type=MarginType.REG_T)
        >>>
        >>> # Iron Condor margin
        >>> margin = calc.calculate_iron_condor_margin(
        ...     put_spread_width=Decimal('5'),
        ...     call_spread_width=Decimal('5'),
        ...     contracts=10
        ... )
        >>> print(f"Margin required: ${margin.total_requirement:,.0f}")
        >>>
        >>> # For a full spread
        >>> spread = MultiLegSpread(...)
        >>> margin = calc.calculate_spread_margin(spread)
    """

    def __init__(
        self,
        margin_type: MarginType = MarginType.REG_T,
        contract_multiplier: Decimal = Decimal('100'),
        portfolio_margin_factor: Decimal = Decimal('0.15'),  # 15% of notional
    ):
        """
        Initialize Margin Calculator.

        Args:
            margin_type: Margin calculation methodology
            contract_multiplier: Options contract multiplier (100 for standard)
            portfolio_margin_factor: Factor for portfolio margin calculation
        """
        self.margin_type = margin_type
        self.contract_multiplier = contract_multiplier
        self.portfolio_margin_factor = portfolio_margin_factor

    def calculate_spread_margin(
        self,
        spread: MultiLegSpread,
        contracts: int = 1,
    ) -> MarginRequirement:
        """
        Calculate margin for a multi-leg spread.

        Automatically detects spread type and applies appropriate calculation.

        Args:
            spread: The multi-leg spread
            contracts: Number of contracts (spread sets)

        Returns:
            MarginRequirement with total and details
        """
        spread_type = spread.spread_type

        if spread_type == SpreadType.IRON_CONDOR:
            return self._calculate_iron_condor(spread, contracts)

        elif spread_type in (SpreadType.CREDIT_SPREAD, SpreadType.VERTICAL):
            return self._calculate_vertical_spread(spread, contracts)

        elif spread_type == SpreadType.DEBIT_SPREAD:
            return self._calculate_debit_spread(spread, contracts)

        elif spread_type in (SpreadType.STRADDLE, SpreadType.STRANGLE):
            return self._calculate_straddle_strangle(spread, contracts)

        elif spread_type == SpreadType.BUTTERFLY:
            return self._calculate_butterfly(spread, contracts)

        elif spread_type in (SpreadType.NAKED_PUT, SpreadType.NAKED_CALL):
            return self._calculate_naked_option(spread, contracts)

        else:
            # Generic calculation based on max loss
            return self._calculate_generic(spread, contracts)

    def calculate_iron_condor_margin(
        self,
        put_spread_width: Decimal,
        call_spread_width: Decimal,
        contracts: int = 1,
        put_credit: Optional[Decimal] = None,
        call_credit: Optional[Decimal] = None,
    ) -> MarginRequirement:
        """
        Calculate margin for an iron condor.

        Reg T: Maximum of put spread width or call spread width
        Portfolio Margin: Risk-based on maximum loss scenario

        Args:
            put_spread_width: Width of put vertical spread
            call_spread_width: Width of call vertical spread
            contracts: Number of iron condors
            put_credit: Credit received from put spread
            call_credit: Credit received from call spread

        Returns:
            MarginRequirement for the iron condor
        """
        if self.margin_type == MarginType.REG_T:
            # Margin is the wider of the two spreads
            max_spread_width = max(put_spread_width, call_spread_width)
            margin_per_contract = max_spread_width * self.contract_multiplier

            total_margin = margin_per_contract * Decimal(str(contracts))

            # Net credit reduces margin requirement
            total_credit = Decimal('0')
            if put_credit:
                total_credit += put_credit
            if call_credit:
                total_credit += call_credit

            net_margin = total_margin - (total_credit * Decimal(str(contracts)) * self.contract_multiplier)

            return MarginRequirement(
                total_requirement=net_margin,
                margin_type=MarginType.REG_T,
                spread_type=SpreadType.IRON_CONDOR,
                per_contract_requirement=margin_per_contract,
                calculation_method=f"Max spread width: ${max_spread_width}",
                max_loss=total_margin,
                components={
                    'put_spread_width': float(put_spread_width),
                    'call_spread_width': float(call_spread_width),
                    'max_spread_width': float(max_spread_width),
                    'total_credit': float(total_credit) if total_credit > 0 else 0,
                },
            )

        else:  # Portfolio Margin
            # Estimate max loss (wider spread - total credit)
            max_spread_width = max(put_spread_width, call_spread_width)
            total_credit = (put_credit or Decimal('0')) + (call_credit or Decimal('0'))

            max_loss = (max_spread_width - total_credit) * self.contract_multiplier * Decimal(str(contracts))

            # Portfolio margin is typically 15-20% of max loss
            margin = max_loss * self.portfolio_margin_factor

            return MarginRequirement(
                total_requirement=margin,
                margin_type=MarginType.PORTFOLIO,
                spread_type=SpreadType.IRON_CONDOR,
                per_contract_requirement=margin / Decimal(str(contracts)),
                maintenance_requirement=margin,
                initial_requirement=margin * Decimal('1.5'),  # 1.5x for initial
                calculation_method=f"Portfolio margin: {float(self.portfolio_margin_factor)*100}% of max loss",
                max_loss=max_loss,
            )

    def calculate_vertical_spread_margin(
        self,
        spread_width: Decimal,
        contracts: int = 1,
        credit_received: Optional[Decimal] = None,
    ) -> MarginRequirement:
        """
        Calculate margin for a vertical spread (credit or debit).

        Credit spread margin = spread width - credit received
        Debit spread margin = debit paid (max loss)

        Args:
            spread_width: Difference between strikes
            contracts: Number of spreads
            credit_received: Credit received (for credit spreads)

        Returns:
            MarginRequirement for the vertical spread
        """
        spread_width_dollars = spread_width * self.contract_multiplier

        if credit_received:
            # Credit spread
            credit_dollars = credit_received * self.contract_multiplier
            margin_per_contract = spread_width_dollars - credit_dollars
            method = "Credit spread: width - credit"
        else:
            # Debit spread (margin is the debit paid)
            margin_per_contract = spread_width_dollars
            method = "Debit spread: spread width"

        total_margin = margin_per_contract * Decimal(str(contracts))

        return MarginRequirement(
            total_requirement=total_margin,
            margin_type=self.margin_type,
            spread_type=SpreadType.VERTICAL,
            per_contract_requirement=margin_per_contract,
            calculation_method=method,
            max_loss=total_margin,
            components={
                'spread_width': float(spread_width),
                'credit_received': float(credit_received) if credit_received else 0,
            },
        )

    def calculate_buying_power_reduction(
        self,
        spread: MultiLegSpread,
        contracts: int = 1,
    ) -> Decimal:
        """
        Calculate buying power reduction (BPR) for a spread.

        This is the amount of buying power that will be tied up.

        Args:
            spread: The multi-leg spread
            contracts: Number of contracts

        Returns:
            Buying power reduction amount
        """
        margin_req = self.calculate_spread_margin(spread, contracts)
        return margin_req.total_requirement

    def simulate_portfolio_margin(
        self,
        spreads: List[MultiLegSpread],
        underlying_price: Decimal,
        price_scenarios: Optional[List[Decimal]] = None,
    ) -> MarginRequirement:
        """
        Simulate portfolio margin for multiple positions.

        Portfolio margin calculates margin based on theoretical loss
        across a range of underlying price scenarios.

        Args:
            spreads: List of open spreads
            underlying_price: Current underlying price
            price_scenarios: Price scenarios to test (defaults to +/- 15%)

        Returns:
            MarginRequirement for entire portfolio
        """
        if price_scenarios is None:
            # Default scenarios: -15%, -10%, -5%, 0%, +5%, +10%, +15%
            scenarios = [
                underlying_price * Decimal(str(1 + pct))
                for pct in [-0.15, -0.10, -0.05, 0, 0.05, 0.10, 0.15]
            ]
        else:
            scenarios = price_scenarios

        # For simplicity, use max loss across all positions
        # In real portfolio margin, would calculate P&L at each scenario
        total_max_loss = Decimal('0')
        for spread in spreads:
            if spread.max_loss:
                total_max_loss += abs(spread.max_loss)

        # Portfolio margin is percentage of max theoretical loss
        margin = total_max_loss * self.portfolio_margin_factor

        return MarginRequirement(
            total_requirement=margin,
            margin_type=MarginType.PORTFOLIO,
            spread_type=SpreadType.IRON_CONDOR,  # Generic
            per_contract_requirement=margin / Decimal(str(len(spreads))) if spreads else Decimal('0'),
            maintenance_requirement=margin,
            initial_requirement=margin * Decimal('1.5'),
            calculation_method=f"Portfolio margin across {len(scenarios)} price scenarios",
            max_loss=total_max_loss,
            components={
                'num_positions': len(spreads),
                'num_scenarios': len(scenarios),
                'underlying_price': float(underlying_price),
            },
        )

    def _calculate_iron_condor(
        self,
        spread: MultiLegSpread,
        contracts: int,
    ) -> MarginRequirement:
        """Calculate margin for iron condor from spread object."""
        # Find put and call spread widths
        legs = sorted(spread.legs, key=lambda x: x.contract.strike)

        # Assuming 4-leg iron condor: long put, short put, short call, long call
        if len(legs) != 4:
            return self._calculate_generic(spread, contracts)

        put_legs = [leg for leg in legs if leg.contract.option_type == OptionType.PUT]
        call_legs = [leg for leg in legs if leg.contract.option_type == OptionType.CALL]

        if len(put_legs) == 2 and len(call_legs) == 2:
            put_spread_width = abs(put_legs[1].contract.strike - put_legs[0].contract.strike)
            call_spread_width = abs(call_legs[1].contract.strike - call_legs[0].contract.strike)

            return self.calculate_iron_condor_margin(
                put_spread_width=put_spread_width,
                call_spread_width=call_spread_width,
                contracts=contracts,
            )

        return self._calculate_generic(spread, contracts)

    def _calculate_vertical_spread(
        self,
        spread: MultiLegSpread,
        contracts: int,
    ) -> MarginRequirement:
        """Calculate margin for vertical spread from spread object."""
        if len(spread.legs) != 2:
            return self._calculate_generic(spread, contracts)

        leg1, leg2 = spread.legs
        spread_width = abs(leg1.contract.strike - leg2.contract.strike)

        # Determine if credit or debit
        credit = spread.entry_premium if spread.entry_premium and spread.entry_premium > 0 else None

        return self.calculate_vertical_spread_margin(
            spread_width=spread_width,
            contracts=contracts,
            credit_received=credit,
        )

    def _calculate_debit_spread(
        self,
        spread: MultiLegSpread,
        contracts: int,
    ) -> MarginRequirement:
        """Calculate margin for debit spread (max loss is debit paid)."""
        # For debit spreads, margin is the debit paid
        debit = abs(spread.entry_premium) if spread.entry_premium else Decimal('0')
        margin = debit * self.contract_multiplier * Decimal(str(contracts))

        return MarginRequirement(
            total_requirement=margin,
            margin_type=self.margin_type,
            spread_type=SpreadType.DEBIT_SPREAD,
            per_contract_requirement=margin / Decimal(str(contracts)),
            calculation_method="Debit paid",
            max_loss=margin,
        )

    def _calculate_straddle_strangle(
        self,
        spread: MultiLegSpread,
        contracts: int,
    ) -> MarginRequirement:
        """Calculate margin for straddle/strangle."""
        # Simplified: use higher margin of call or put side
        # Real calculation would be more complex

        if spread.max_loss:
            margin = abs(spread.max_loss) * Decimal(str(contracts))
        else:
            # Estimate based on premium
            premium = abs(spread.entry_premium) if spread.entry_premium else Decimal('0')
            margin = premium * Decimal('20') * Decimal(str(contracts))  # 20x premium estimate

        return MarginRequirement(
            total_requirement=margin,
            margin_type=self.margin_type,
            spread_type=spread.spread_type,
            per_contract_requirement=margin / Decimal(str(contracts)),
            calculation_method="Straddle/Strangle margin estimate",
            max_loss=margin,
        )

    def _calculate_butterfly(
        self,
        spread: MultiLegSpread,
        contracts: int,
    ) -> MarginRequirement:
        """Calculate margin for butterfly spread."""
        # Butterfly max loss is typically the debit paid
        debit = abs(spread.entry_premium) if spread.entry_premium else Decimal('0')
        margin = debit * self.contract_multiplier * Decimal(str(contracts))

        return MarginRequirement(
            total_requirement=margin,
            margin_type=self.margin_type,
            spread_type=SpreadType.BUTTERFLY,
            per_contract_requirement=margin / Decimal(str(contracts)),
            calculation_method="Butterfly debit paid",
            max_loss=margin,
        )

    def _calculate_naked_option(
        self,
        spread: MultiLegSpread,
        contracts: int,
    ) -> MarginRequirement:
        """Calculate margin for naked option (very high margin requirement)."""
        # Naked options have significant margin requirements
        # Simplified calculation: 20% of underlying value
        if spread.legs:
            underlying_value = spread.underlying_price * self.contract_multiplier
            margin_per_contract = underlying_value * Decimal('0.20')
            total_margin = margin_per_contract * Decimal(str(contracts))

            return MarginRequirement(
                total_requirement=total_margin,
                margin_type=self.margin_type,
                spread_type=spread.spread_type,
                per_contract_requirement=margin_per_contract,
                calculation_method="20% of underlying value",
                max_loss=None,  # Undefined for naked options
            )

        return self._calculate_generic(spread, contracts)

    def _calculate_generic(
        self,
        spread: MultiLegSpread,
        contracts: int,
    ) -> MarginRequirement:
        """Generic margin calculation based on max loss."""
        if spread.max_loss:
            margin = abs(spread.max_loss) * Decimal(str(contracts))
        else:
            # Fallback: use premium if max_loss not defined
            premium = abs(spread.entry_premium) if spread.entry_premium else Decimal('1000')
            margin = premium * Decimal(str(contracts))

        return MarginRequirement(
            total_requirement=margin,
            margin_type=self.margin_type,
            spread_type=spread.spread_type,
            per_contract_requirement=margin / Decimal(str(contracts)) if contracts > 0 else Decimal('0'),
            calculation_method="Generic: max loss or premium",
            max_loss=margin,
        )

    def get_calculator_info(self) -> dict:
        """Get calculator configuration info."""
        return {
            'margin_type': self.margin_type.value,
            'contract_multiplier': float(self.contract_multiplier),
            'portfolio_margin_factor': float(self.portfolio_margin_factor),
        }
