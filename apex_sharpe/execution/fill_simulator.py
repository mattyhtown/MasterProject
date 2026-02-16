"""
Fill Simulator for APEX-SHARPE Trading System.

Provides realistic fill simulation for options orders with:
- Bid/ask spread modeling
- Slippage calculation
- Market impact
- Time-of-day effects
- Liquidity considerations
"""

from typing import Dict, Optional
from decimal import Decimal
from datetime import datetime, time
from enum import Enum


class MarketSession(Enum):
    """Market trading session periods."""
    OPEN = "OPEN"           # First 30 minutes
    MID_MORNING = "MID_MORNING"  # 10:00 - 11:30
    LUNCH = "LUNCH"         # 11:30 - 13:30
    MID_AFTERNOON = "MID_AFTERNOON"  # 13:30 - 15:00
    CLOSE = "CLOSE"         # Last 60 minutes
    AFTER_HOURS = "AFTER_HOURS"


class FillSimulator:
    """
    Simulates realistic option order fills.

    Models market microstructure effects:
    - Bid/ask spreads based on liquidity
    - Slippage from market orders
    - Market impact for larger orders
    - Wider spreads at market open/close
    - Liquidity-based fill quality

    Example:
        >>> simulator = FillSimulator(
        ...     base_spread_pct=0.02,
        ...     slippage_bps=5.0
        ... )
        >>> # Simulate market order fill
        >>> fill_price = simulator.simulate_fill(
        ...     order_type="MARKET",
        ...     side="BUY",
        ...     quantity=10,
        ...     bid=5.80,
        ...     ask=5.90,
        ...     volume=1500
        ... )
        >>> print(f"Filled at: {fill_price}")
    """

    def __init__(
        self,
        base_spread_pct: float = 0.02,      # 2% base bid/ask spread
        slippage_bps: float = 5.0,          # 5 bps slippage
        market_impact_coef: float = 0.001,  # Market impact coefficient
        open_spread_mult: float = 2.0,      # Open spread multiplier
        close_spread_mult: float = 1.5,     # Close spread multiplier
        lunch_spread_mult: float = 1.3,     # Lunch spread multiplier
        min_spread_width: float = 0.01,     # Minimum $0.01 spread
        adverse_selection_prob: float = 0.1 # Probability of worse fill
    ):
        """
        Initialize fill simulator.

        Args:
            base_spread_pct: Base bid/ask spread as percentage of mid
            slippage_bps: Slippage in basis points
            market_impact_coef: Coefficient for market impact calculation
            open_spread_mult: Spread multiplier for market open
            close_spread_mult: Spread multiplier for market close
            lunch_spread_mult: Spread multiplier for lunch session
            min_spread_width: Minimum absolute spread width
            adverse_selection_prob: Probability of adverse selection (worse fill)
        """
        self.base_spread_pct = base_spread_pct
        self.slippage_bps = slippage_bps
        self.market_impact_coef = market_impact_coef
        self.open_spread_mult = open_spread_mult
        self.close_spread_mult = close_spread_mult
        self.lunch_spread_mult = lunch_spread_mult
        self.min_spread_width = min_spread_width
        self.adverse_selection_prob = adverse_selection_prob

    def simulate_fill(
        self,
        order_type: str,
        side: str,
        quantity: int,
        bid: Decimal,
        ask: Decimal,
        volume: int,
        limit_price: Optional[Decimal] = None,
        current_time: Optional[datetime] = None
    ) -> Decimal:
        """
        Simulate order fill with realistic market conditions.

        Args:
            order_type: MARKET, LIMIT
            side: BUY or SELL
            quantity: Number of contracts
            bid: Current bid price
            ask: Current ask price
            volume: Option volume (daily)
            limit_price: Limit price (if LIMIT order)
            current_time: Time of order (for session effects)

        Returns:
            Fill price as Decimal

        Example:
            >>> # Market buy order
            >>> fill = simulator.simulate_fill(
            ...     "MARKET", "BUY", 10,
            ...     Decimal("5.80"), Decimal("5.90"), 1500
            ... )
        """
        mid = (bid + ask) / Decimal("2")
        spread = ask - bid

        # Adjust spread for market session
        if current_time:
            session = self._get_market_session(current_time)
            spread = self._adjust_spread_for_session(spread, session)

        # Adjust spread for liquidity
        spread = self._adjust_spread_for_liquidity(spread, volume, mid)

        # Ensure minimum spread
        spread = max(spread, Decimal(str(self.min_spread_width)))

        # Recalculate bid/ask with adjusted spread
        adjusted_bid = mid - spread / Decimal("2")
        adjusted_ask = mid + spread / Decimal("2")

        if order_type == "MARKET":
            return self._simulate_market_fill(
                side, quantity, adjusted_bid, adjusted_ask, volume, mid
            )
        elif order_type == "LIMIT":
            return self._simulate_limit_fill(
                side, quantity, adjusted_bid, adjusted_ask, limit_price
            )
        else:
            # Default to mid for other order types
            return mid

    def simulate_multi_leg_fill(
        self,
        legs: list,
        order_type: str = "MARKET",
        limit_price: Optional[Decimal] = None,
        current_time: Optional[datetime] = None
    ) -> Dict[int, Decimal]:
        """
        Simulate fill for multi-leg spread order.

        Args:
            legs: List of leg dictionaries with keys:
                  - leg_index, side, quantity, bid, ask, volume
            order_type: MARKET or LIMIT
            limit_price: Net limit price for entire spread
            current_time: Time of order

        Returns:
            Dictionary mapping leg_index to fill_price

        Note:
            For multi-leg orders, legs typically fill simultaneously
            but may have correlated slippage.
        """
        fills = {}

        for leg in legs:
            leg_fill = self.simulate_fill(
                order_type=order_type,
                side=leg['side'],
                quantity=leg['quantity'],
                bid=leg['bid'],
                ask=leg['ask'],
                volume=leg['volume'],
                limit_price=leg.get('limit_price'),
                current_time=current_time
            )
            fills[leg['leg_index']] = leg_fill

        return fills

    def estimate_fill_probability(
        self,
        order_type: str,
        side: str,
        limit_price: Decimal,
        bid: Decimal,
        ask: Decimal,
        volume: int
    ) -> float:
        """
        Estimate probability of limit order fill.

        Args:
            order_type: Order type
            side: BUY or SELL
            limit_price: Limit price
            bid: Current bid
            ask: Current ask
            volume: Option volume

        Returns:
            Fill probability between 0.0 and 1.0

        Example:
            >>> # Aggressive limit buy (at ask)
            >>> prob = simulator.estimate_fill_probability(
            ...     "LIMIT", "BUY", Decimal("5.90"),
            ...     Decimal("5.80"), Decimal("5.90"), 1000
            ... )
            >>> print(f"Fill probability: {prob:.1%}")
        """
        mid = (bid + ask) / Decimal("2")

        if order_type != "LIMIT":
            return 1.0  # Market orders always fill

        if side == "BUY":
            # Better than ask = high probability
            if limit_price >= ask:
                return 0.95
            # At mid = moderate probability
            elif limit_price >= mid:
                return 0.50
            # Better than bid = low probability
            elif limit_price >= bid:
                return 0.20
            else:
                return 0.05
        else:  # SELL
            # Better than bid = high probability
            if limit_price <= bid:
                return 0.95
            # At mid = moderate probability
            elif limit_price <= mid:
                return 0.50
            # Better than ask = low probability
            elif limit_price <= ask:
                return 0.20
            else:
                return 0.05

    def _simulate_market_fill(
        self,
        side: str,
        quantity: int,
        bid: Decimal,
        ask: Decimal,
        volume: int,
        mid: Decimal
    ) -> Decimal:
        """Simulate market order fill."""
        # Base fill at bid/ask
        if side == "BUY":
            fill_price = ask
        else:
            fill_price = bid

        # Add slippage
        slippage = mid * Decimal(str(self.slippage_bps / 10000.0))
        if side == "BUY":
            fill_price += slippage
        else:
            fill_price -= slippage

        # Add market impact for larger orders
        if volume > 0:
            impact = self._calculate_market_impact(quantity, volume, mid)
            if side == "BUY":
                fill_price += impact
            else:
                fill_price -= impact

        return fill_price

    def _simulate_limit_fill(
        self,
        side: str,
        quantity: int,
        bid: Decimal,
        ask: Decimal,
        limit_price: Optional[Decimal]
    ) -> Decimal:
        """Simulate limit order fill."""
        if limit_price is None:
            return (bid + ask) / Decimal("2")

        mid = (bid + ask) / Decimal("2")

        # Check if limit is executable
        if side == "BUY":
            if limit_price >= ask:
                # Aggressive limit - fill at ask or better
                return min(limit_price, ask)
            elif limit_price >= mid:
                # At mid - fill at limit
                return limit_price
            else:
                # Passive limit - may not fill, but if it does, at limit
                return limit_price
        else:  # SELL
            if limit_price <= bid:
                # Aggressive limit - fill at bid or better
                return max(limit_price, bid)
            elif limit_price <= mid:
                # At mid - fill at limit
                return limit_price
            else:
                # Passive limit - may not fill, but if it does, at limit
                return limit_price

    def _calculate_market_impact(
        self,
        quantity: int,
        volume: int,
        mid: Decimal
    ) -> Decimal:
        """
        Calculate market impact for order size.

        Impact increases with order size relative to daily volume.
        """
        if volume <= 0:
            return Decimal("0")

        # Order size as fraction of daily volume
        size_ratio = quantity / volume

        # Impact is proportional to square root of size ratio
        impact_pct = self.market_impact_coef * (size_ratio ** 0.5)

        return mid * Decimal(str(impact_pct))

    def _adjust_spread_for_session(
        self,
        spread: Decimal,
        session: MarketSession
    ) -> Decimal:
        """Adjust spread based on market session."""
        if session == MarketSession.OPEN:
            return spread * Decimal(str(self.open_spread_mult))
        elif session == MarketSession.CLOSE:
            return spread * Decimal(str(self.close_spread_mult))
        elif session == MarketSession.LUNCH:
            return spread * Decimal(str(self.lunch_spread_mult))
        else:
            return spread

    def _adjust_spread_for_liquidity(
        self,
        spread: Decimal,
        volume: int,
        mid: Decimal
    ) -> Decimal:
        """
        Adjust spread based on option liquidity.

        Lower volume = wider spreads
        """
        if volume <= 0:
            # Very illiquid - wide spread
            return spread * Decimal("3.0")
        elif volume < 100:
            # Low liquidity
            return spread * Decimal("2.0")
        elif volume < 500:
            # Moderate liquidity
            return spread * Decimal("1.5")
        else:
            # Good liquidity
            return spread

    def _get_market_session(self, current_time: datetime) -> MarketSession:
        """Determine market session from time."""
        t = current_time.time()

        # Market hours: 9:30 - 16:00 ET
        open_time = time(9, 30)
        open_end = time(10, 0)
        mid_morning_end = time(11, 30)
        lunch_end = time(13, 30)
        mid_afternoon_end = time(15, 0)
        close_time = time(16, 0)

        if t < open_time or t >= close_time:
            return MarketSession.AFTER_HOURS
        elif t < open_end:
            return MarketSession.OPEN
        elif t < mid_morning_end:
            return MarketSession.MID_MORNING
        elif t < lunch_end:
            return MarketSession.LUNCH
        elif t < mid_afternoon_end:
            return MarketSession.MID_AFTERNOON
        else:
            return MarketSession.CLOSE

    def get_effective_spread(
        self,
        bid: Decimal,
        ask: Decimal,
        volume: int,
        current_time: Optional[datetime] = None
    ) -> Decimal:
        """
        Calculate effective spread after all adjustments.

        Args:
            bid: Bid price
            ask: Ask price
            volume: Option volume
            current_time: Time of calculation

        Returns:
            Effective spread width
        """
        mid = (bid + ask) / Decimal("2")
        spread = ask - bid

        if current_time:
            session = self._get_market_session(current_time)
            spread = self._adjust_spread_for_session(spread, session)

        spread = self._adjust_spread_for_liquidity(spread, volume, mid)
        spread = max(spread, Decimal(str(self.min_spread_width)))

        return spread
