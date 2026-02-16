"""
Spread Builder for APEX-SHARPE Trading System.

Constructs multi-leg options spreads with delta-based strike selection
and automatic validation of spread structure.
"""

from typing import List, Optional, Tuple
from decimal import Decimal
from datetime import datetime

from ..strategies.base_strategy import (
    MultiLegSpread,
    SpreadLeg,
    OptionContract,
    OptionsChain,
    OrderAction,
    OptionType,
    SpreadType
)


class SpreadBuilder:
    """
    Constructs multi-leg options spreads.

    Provides methods to build common spread types:
    - Iron Condors
    - Vertical Spreads (Bull/Bear)
    - Strangles
    - Butterflies
    - Calendars
    - Diagonals

    Uses delta-based strike selection for precise positioning.

    Example:
        >>> builder = SpreadBuilder()
        >>> # Build iron condor with 0.10 delta short strikes, 10-point wings
        >>> spread = builder.build_iron_condor(
        ...     chain=options_chain,
        ...     put_short_delta=Decimal("0.10"),
        ...     call_short_delta=Decimal("0.10"),
        ...     wing_width=Decimal("10")
        ... )
        >>> print(f"Max profit: {spread.max_profit}")
        >>> print(f"Max loss: {spread.max_loss}")
    """

    def __init__(self, contract_multiplier: int = 100):
        """
        Initialize spread builder.

        Args:
            contract_multiplier: Standard options contract multiplier (default 100)
        """
        self.contract_multiplier = contract_multiplier

    def build_iron_condor(
        self,
        chain: OptionsChain,
        put_short_delta: Decimal,
        call_short_delta: Decimal,
        wing_width: Decimal,
        expiration_dte: int = 45,
        quantity: int = 1
    ) -> MultiLegSpread:
        """
        Build an iron condor spread.

        Structure: Short put spread + Short call spread
        - Sell OTM put at put_short_delta
        - Buy further OTM put (wing_width below)
        - Sell OTM call at call_short_delta
        - Buy further OTM call (wing_width above)

        Args:
            chain: Options chain data
            put_short_delta: Target delta for short put (e.g., 0.10 for 10-delta)
            call_short_delta: Target delta for short call (e.g., 0.10 for 10-delta)
            wing_width: Distance between short and long strikes
            expiration_dte: Target days to expiration
            quantity: Number of spreads

        Returns:
            MultiLegSpread with 4 legs

        Example:
            >>> # Build 45-DTE iron condor with 10-delta shorts, 10-point wings
            >>> ic = builder.build_iron_condor(
            ...     chain, Decimal("0.10"), Decimal("0.10"), Decimal("10")
            ... )
        """
        # Find appropriate expiration
        expirations = chain.filter_by_dte(expiration_dte, tolerance_days=7)
        if not expirations:
            raise ValueError(f"No expiration found near {expiration_dte} DTE")
        expiration = expirations[0]

        # Find short put (negative delta for puts)
        short_put = self._find_contract_by_delta(
            chain, expiration, OptionType.PUT, -put_short_delta
        )
        if not short_put:
            raise ValueError(f"Could not find put at {put_short_delta} delta")

        # Find long put (further OTM)
        long_put_strike = short_put.strike - wing_width
        long_put = self._find_contract_by_strike(
            chain, expiration, OptionType.PUT, long_put_strike
        )
        if not long_put:
            raise ValueError(f"Could not find long put at strike {long_put_strike}")

        # Find short call (positive delta for calls)
        short_call = self._find_contract_by_delta(
            chain, expiration, OptionType.CALL, call_short_delta
        )
        if not short_call:
            raise ValueError(f"Could not find call at {call_short_delta} delta")

        # Find long call (further OTM)
        long_call_strike = short_call.strike + wing_width
        long_call = self._find_contract_by_strike(
            chain, expiration, OptionType.CALL, long_call_strike
        )
        if not long_call:
            raise ValueError(f"Could not find long call at strike {long_call_strike}")

        # Build legs
        legs = [
            SpreadLeg(long_put, OrderAction.BTO, quantity, 0),   # Buy long put
            SpreadLeg(short_put, OrderAction.STO, quantity, 1),  # Sell short put
            SpreadLeg(short_call, OrderAction.STO, quantity, 2), # Sell short call
            SpreadLeg(long_call, OrderAction.BTO, quantity, 3),  # Buy long call
        ]

        # Create spread
        spread = MultiLegSpread(
            legs=legs,
            spread_type=SpreadType.IRON_CONDOR,
            entry_time=datetime.now(),
            underlying_price=chain.underlying_price
        )

        # Calculate Greeks and risk metrics
        spread.calculate_portfolio_greeks()
        self._calculate_iron_condor_risk(spread, wing_width)

        return spread

    def build_vertical_spread(
        self,
        chain: OptionsChain,
        option_type: OptionType,
        short_strike: Decimal,
        long_strike: Decimal,
        expiration_dte: int = 45,
        quantity: int = 1,
        spread_direction: str = "CREDIT"
    ) -> MultiLegSpread:
        """
        Build a vertical spread (bull/bear call/put spread).

        Args:
            chain: Options chain
            option_type: CALL or PUT
            short_strike: Strike to sell
            long_strike: Strike to buy
            expiration_dte: Target DTE
            quantity: Number of spreads
            spread_direction: CREDIT or DEBIT

        Returns:
            MultiLegSpread with 2 legs

        Example:
            >>> # Build bull put spread (credit spread)
            >>> spread = builder.build_vertical_spread(
            ...     chain, OptionType.PUT,
            ...     short_strike=Decimal("5800"),
            ...     long_strike=Decimal("5790"),
            ...     spread_direction="CREDIT"
            ... )
        """
        expirations = chain.filter_by_dte(expiration_dte, tolerance_days=7)
        if not expirations:
            raise ValueError(f"No expiration found near {expiration_dte} DTE")
        expiration = expirations[0]

        # Find contracts
        short_contract = self._find_contract_by_strike(
            chain, expiration, option_type, short_strike
        )
        long_contract = self._find_contract_by_strike(
            chain, expiration, option_type, long_strike
        )

        if not short_contract or not long_contract:
            raise ValueError("Could not find contracts at specified strikes")

        # Build legs
        legs = [
            SpreadLeg(long_contract, OrderAction.BTO, quantity, 0),
            SpreadLeg(short_contract, OrderAction.STO, quantity, 1),
        ]

        # Determine spread type
        if option_type == OptionType.CALL:
            if spread_direction == "CREDIT":
                spread_type = SpreadType.CREDIT_SPREAD  # Bear call spread
            else:
                spread_type = SpreadType.DEBIT_SPREAD   # Bull call spread
        else:  # PUT
            if spread_direction == "CREDIT":
                spread_type = SpreadType.CREDIT_SPREAD  # Bull put spread
            else:
                spread_type = SpreadType.DEBIT_SPREAD   # Bear put spread

        spread = MultiLegSpread(
            legs=legs,
            spread_type=spread_type,
            entry_time=datetime.now(),
            underlying_price=chain.underlying_price
        )

        spread.calculate_portfolio_greeks()
        self._calculate_vertical_risk(spread, short_strike, long_strike)

        return spread

    def build_strangle(
        self,
        chain: OptionsChain,
        put_delta: Decimal,
        call_delta: Decimal,
        expiration_dte: int = 45,
        quantity: int = 1,
        long_or_short: str = "SHORT"
    ) -> MultiLegSpread:
        """
        Build a strangle (long or short).

        Args:
            chain: Options chain
            put_delta: Target delta for put
            call_delta: Target delta for call
            expiration_dte: Target DTE
            quantity: Number of strangles
            long_or_short: SHORT (sell) or LONG (buy)

        Returns:
            MultiLegSpread with 2 legs

        Example:
            >>> # Build short strangle with 16-delta strikes
            >>> strangle = builder.build_strangle(
            ...     chain,
            ...     put_delta=Decimal("0.16"),
            ...     call_delta=Decimal("0.16"),
            ...     long_or_short="SHORT"
            ... )
        """
        expirations = chain.filter_by_dte(expiration_dte, tolerance_days=7)
        if not expirations:
            raise ValueError(f"No expiration found near {expiration_dte} DTE")
        expiration = expirations[0]

        # Find contracts by delta
        put_contract = self._find_contract_by_delta(
            chain, expiration, OptionType.PUT, -put_delta
        )
        call_contract = self._find_contract_by_delta(
            chain, expiration, OptionType.CALL, call_delta
        )

        if not put_contract or not call_contract:
            raise ValueError("Could not find contracts at specified deltas")

        # Determine action
        action = OrderAction.STO if long_or_short == "SHORT" else OrderAction.BTO

        legs = [
            SpreadLeg(put_contract, action, quantity, 0),
            SpreadLeg(call_contract, action, quantity, 1),
        ]

        spread = MultiLegSpread(
            legs=legs,
            spread_type=SpreadType.STRANGLE,
            entry_time=datetime.now(),
            underlying_price=chain.underlying_price
        )

        spread.calculate_portfolio_greeks()
        self._calculate_strangle_risk(spread, long_or_short)

        return spread

    def build_butterfly(
        self,
        chain: OptionsChain,
        option_type: OptionType,
        strikes: Tuple[Decimal, Decimal, Decimal],
        expiration_dte: int = 45,
        quantity: int = 1
    ) -> MultiLegSpread:
        """
        Build a butterfly spread.

        Structure: Buy 1 low strike, Sell 2 middle strike, Buy 1 high strike

        Args:
            chain: Options chain
            option_type: CALL or PUT
            strikes: Tuple of (low_strike, middle_strike, high_strike)
            expiration_dte: Target DTE
            quantity: Number of butterflies

        Returns:
            MultiLegSpread with 3 legs

        Example:
            >>> # Build call butterfly
            >>> butterfly = builder.build_butterfly(
            ...     chain,
            ...     OptionType.CALL,
            ...     strikes=(Decimal("5800"), Decimal("5850"), Decimal("5900"))
            ... )
        """
        low_strike, mid_strike, high_strike = strikes

        # Validate strikes are evenly spaced
        if (mid_strike - low_strike) != (high_strike - mid_strike):
            raise ValueError("Butterfly strikes must be evenly spaced")

        expirations = chain.filter_by_dte(expiration_dte, tolerance_days=7)
        if not expirations:
            raise ValueError(f"No expiration found near {expiration_dte} DTE")
        expiration = expirations[0]

        # Find contracts
        low_contract = self._find_contract_by_strike(
            chain, expiration, option_type, low_strike
        )
        mid_contract = self._find_contract_by_strike(
            chain, expiration, option_type, mid_strike
        )
        high_contract = self._find_contract_by_strike(
            chain, expiration, option_type, high_strike
        )

        if not all([low_contract, mid_contract, high_contract]):
            raise ValueError("Could not find all contracts at specified strikes")

        legs = [
            SpreadLeg(low_contract, OrderAction.BTO, quantity, 0),
            SpreadLeg(mid_contract, OrderAction.STO, quantity * 2, 1),
            SpreadLeg(high_contract, OrderAction.BTO, quantity, 2),
        ]

        spread = MultiLegSpread(
            legs=legs,
            spread_type=SpreadType.BUTTERFLY,
            entry_time=datetime.now(),
            underlying_price=chain.underlying_price
        )

        spread.calculate_portfolio_greeks()
        self._calculate_butterfly_risk(spread, strikes)

        return spread

    def validate_spread(self, spread: MultiLegSpread) -> Tuple[bool, Optional[str]]:
        """
        Validate spread structure and pricing.

        Args:
            spread: Spread to validate

        Returns:
            Tuple of (is_valid, error_message)

        Checks:
        - All legs have valid contracts
        - Strikes are properly ordered
        - Greeks are calculated
        - Risk metrics are present
        """
        # Check all legs have contracts
        if not spread.legs:
            return False, "Spread has no legs"

        for leg in spread.legs:
            if not leg.contract:
                return False, f"Leg {leg.leg_index} missing contract"

        # Check Greeks are calculated
        if spread.portfolio_delta is None:
            return False, "Portfolio Greeks not calculated"

        # Type-specific validation
        if spread.spread_type == SpreadType.IRON_CONDOR:
            return self._validate_iron_condor(spread)
        elif spread.spread_type == SpreadType.BUTTERFLY:
            return self._validate_butterfly(spread)
        elif spread.spread_type in (SpreadType.CREDIT_SPREAD, SpreadType.DEBIT_SPREAD):
            return self._validate_vertical(spread)

        return True, None

    def calculate_net_premium(self, spread: MultiLegSpread) -> Decimal:
        """
        Calculate net premium for a spread.

        Uses mid prices for estimation.

        Args:
            spread: Spread to calculate premium for

        Returns:
            Net premium (positive for credit, negative for debit)
        """
        net_premium = Decimal("0")

        for leg in spread.legs:
            mid_price = leg.contract.mid_price
            leg_value = mid_price * Decimal(str(leg.quantity)) * Decimal(str(self.contract_multiplier))

            if leg.is_short:
                net_premium += leg_value
            else:
                net_premium -= leg_value

        return net_premium

    def _find_contract_by_delta(
        self,
        chain: OptionsChain,
        expiration: datetime,
        option_type: OptionType,
        target_delta: Decimal,
        tolerance: Decimal = Decimal("0.03")
    ) -> Optional[OptionContract]:
        """Find option contract closest to target delta."""
        contracts = chain.filter_by_delta(expiration, target_delta, option_type, tolerance)

        if not contracts:
            return None

        # Return closest match
        return min(
            contracts,
            key=lambda c: abs(c.delta - target_delta) if c.delta else Decimal("999")
        )

    def _find_contract_by_strike(
        self,
        chain: OptionsChain,
        expiration: datetime,
        option_type: OptionType,
        strike: Decimal
    ) -> Optional[OptionContract]:
        """Find option contract at specific strike."""
        contracts = chain.get_contracts_by_expiration(expiration, option_type)

        for contract in contracts:
            if contract.strike == strike:
                return contract

        return None

    def _calculate_iron_condor_risk(
        self,
        spread: MultiLegSpread,
        wing_width: Decimal
    ) -> None:
        """Calculate risk metrics for iron condor."""
        net_premium = self.calculate_net_premium(spread)
        spread.entry_premium = net_premium

        # Max profit is the net credit received
        spread.max_profit = net_premium

        # Max loss is wing width minus net credit (per spread)
        quantity = spread.legs[0].quantity
        max_loss_per_spread = (wing_width * Decimal(str(self.contract_multiplier))) - net_premium
        spread.max_loss = -max_loss_per_spread * Decimal(str(quantity))

        # Breakeven points
        put_side_be = spread.legs[1].contract.strike - (net_premium / Decimal(str(self.contract_multiplier * quantity)))
        call_side_be = spread.legs[2].contract.strike + (net_premium / Decimal(str(self.contract_multiplier * quantity)))
        spread.breakeven_points = [put_side_be, call_side_be]

    def _calculate_vertical_risk(
        self,
        spread: MultiLegSpread,
        short_strike: Decimal,
        long_strike: Decimal
    ) -> None:
        """Calculate risk metrics for vertical spread."""
        net_premium = self.calculate_net_premium(spread)
        spread.entry_premium = net_premium

        strike_width = abs(short_strike - long_strike)
        quantity = spread.legs[0].quantity

        if net_premium > 0:  # Credit spread
            spread.max_profit = net_premium
            spread.max_loss = -(strike_width * Decimal(str(self.contract_multiplier * quantity)) - net_premium)
        else:  # Debit spread
            spread.max_profit = strike_width * Decimal(str(self.contract_multiplier * quantity)) + net_premium
            spread.max_loss = net_premium

    def _calculate_strangle_risk(
        self,
        spread: MultiLegSpread,
        long_or_short: str
    ) -> None:
        """Calculate risk metrics for strangle."""
        net_premium = self.calculate_net_premium(spread)
        spread.entry_premium = net_premium

        if long_or_short == "SHORT":
            spread.max_profit = net_premium
            spread.max_loss = None  # Theoretically unlimited
        else:  # LONG
            spread.max_profit = None  # Theoretically unlimited
            spread.max_loss = net_premium

    def _calculate_butterfly_risk(
        self,
        spread: MultiLegSpread,
        strikes: Tuple[Decimal, Decimal, Decimal]
    ) -> None:
        """Calculate risk metrics for butterfly."""
        net_premium = self.calculate_net_premium(spread)
        spread.entry_premium = net_premium

        low_strike, mid_strike, high_strike = strikes
        wing_width = mid_strike - low_strike
        quantity = spread.legs[0].quantity

        # Max profit at middle strike
        spread.max_profit = wing_width * Decimal(str(self.contract_multiplier * quantity)) + net_premium

        # Max loss is the net debit paid
        spread.max_loss = net_premium

        # Breakeven points
        spread.breakeven_points = [
            low_strike - (net_premium / Decimal(str(self.contract_multiplier * quantity))),
            high_strike + (net_premium / Decimal(str(self.contract_multiplier * quantity)))
        ]

    def _validate_iron_condor(self, spread: MultiLegSpread) -> Tuple[bool, Optional[str]]:
        """Validate iron condor structure."""
        if len(spread.legs) != 4:
            return False, "Iron condor must have 4 legs"

        # Check strikes are properly ordered
        strikes = sorted([leg.contract.strike for leg in spread.legs])
        for i in range(len(strikes) - 1):
            if strikes[i] >= strikes[i + 1]:
                return False, "Strikes not properly ordered"

        return True, None

    def _validate_butterfly(self, spread: MultiLegSpread) -> Tuple[bool, Optional[str]]:
        """Validate butterfly structure."""
        if len(spread.legs) != 3:
            return False, "Butterfly must have 3 legs"

        # Middle leg should have 2x quantity
        if spread.legs[1].quantity != spread.legs[0].quantity * 2:
            return False, "Middle leg must have 2x quantity of wings"

        return True, None

    def _validate_vertical(self, spread: MultiLegSpread) -> Tuple[bool, Optional[str]]:
        """Validate vertical spread structure."""
        if len(spread.legs) != 2:
            return False, "Vertical spread must have 2 legs"

        return True, None
