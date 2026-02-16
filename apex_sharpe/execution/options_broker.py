"""
Options Paper Broker for APEX-SHARPE Trading System.

Extends CrewTrader's PaperBroker to support multi-leg options trades with:
- Multi-leg order execution
- Fill simulation with bid/ask spreads
- Per-contract commission calculation
- Early assignment simulation
- Position tracking with Greeks updates
"""

import sys
import os
from typing import Dict, List, Optional, Callable, Tuple
from datetime import datetime, date
from decimal import Decimal

# Add CrewTrader to path for PaperBroker base class
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../CrewTrader'))

from broker.paper_broker import PaperBroker
from broker.order import Order, OrderStatus, OrderSide, OrderFill, OrderType

# Use relative import for internal APEX-SHARPE module
from ..strategies.base_strategy import MultiLegSpread, SpreadLeg, OptionContract, OrderAction


class MultiLegOrder:
    """Represents a multi-leg options order."""

    def __init__(
        self,
        spread: MultiLegSpread,
        order_type: OrderType = OrderType.MARKET,
        limit_price: Optional[Decimal] = None,
        time_in_force: str = "DAY"
    ):
        """
        Initialize multi-leg order.

        Args:
            spread: MultiLegSpread containing all legs
            order_type: MARKET or LIMIT
            limit_price: Limit price for entire spread (net credit/debit)
            time_in_force: DAY, GTC, IOC, FOK
        """
        self.spread = spread
        self.order_type = order_type
        self.limit_price = limit_price
        self.time_in_force = time_in_force
        self.order_id = f"MLO_{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
        self.status = OrderStatus.PENDING
        self.created_at = datetime.now()
        self.filled_at: Optional[datetime] = None
        self.fill_price: Optional[Decimal] = None
        self.total_commission: Decimal = Decimal("0")
        self.leg_fills: List[Dict] = []

    @property
    def is_filled(self) -> bool:
        """Check if order is completely filled."""
        return self.status == OrderStatus.FILLED

    @property
    def is_active(self) -> bool:
        """Check if order is still active."""
        return self.status in (OrderStatus.PENDING, OrderStatus.SUBMITTED, OrderStatus.PARTIAL)


class OptionsPaperBroker(PaperBroker):
    """
    Paper broker for options trading with multi-leg support.

    Extends CrewTrader's PaperBroker with:
    - Multi-leg order execution
    - Options-specific fill simulation
    - Early assignment handling
    - Position tracking with Greeks

    Example:
        >>> broker = OptionsPaperBroker(
        ...     initial_capital=100_000,
        ...     commission_per_contract=0.65,
        ...     assignment_fee=5.00
        ... )
        >>> # Submit multi-leg spread order
        >>> order = broker.submit_spread_order(iron_condor_spread)
        >>> # Check if filled
        >>> if order.is_filled:
        ...     print(f"Filled at {order.fill_price}")
    """

    def __init__(
        self,
        initial_capital: float = 100_000.0,
        commission_per_contract: float = 0.65,
        assignment_fee: float = 5.00,
        slippage_bps: float = 5.0,
        min_spread_width: float = 0.01,
    ):
        """
        Initialize options paper broker.

        Args:
            initial_capital: Starting capital
            commission_per_contract: Commission per options contract
            assignment_fee: Fee per early assignment
            slippage_bps: Slippage in basis points (default 5 = 0.05%)
            min_spread_width: Minimum bid/ask spread width
        """
        super().__init__(
            initial_capital=initial_capital,
            commission_per_contract=commission_per_contract,
            slippage_pct=slippage_bps / 10000.0
        )

        self.assignment_fee = assignment_fee
        self.min_spread_width = min_spread_width

        # Multi-leg order tracking
        self._multi_leg_orders: Dict[str, MultiLegOrder] = {}
        self._open_spreads: Dict[str, MultiLegSpread] = {}  # position_id -> spread
        self._spread_fill_callback: Optional[Callable[[MultiLegOrder], None]] = None

        # Early assignment tracking
        self._assignment_history: List[Dict] = []

    def set_on_spread_fill(self, callback: Callable[[MultiLegOrder], None]) -> None:
        """
        Set callback for multi-leg order fills.

        Args:
            callback: Function to call when spread order is filled
        """
        self._spread_fill_callback = callback

    def submit_spread_order(
        self,
        spread: MultiLegSpread,
        order_type: OrderType = OrderType.MARKET,
        limit_price: Optional[Decimal] = None
    ) -> MultiLegOrder:
        """
        Submit a multi-leg spread order.

        Args:
            spread: MultiLegSpread to execute
            order_type: MARKET or LIMIT
            limit_price: Limit price for spread (net credit/debit)

        Returns:
            MultiLegOrder with fill information

        Example:
            >>> # Build iron condor
            >>> spread = spread_builder.build_iron_condor(chain, 0.10, 0.10, 10)
            >>> # Submit order
            >>> order = broker.submit_spread_order(spread, OrderType.MARKET)
        """
        # Create multi-leg order
        ml_order = MultiLegOrder(
            spread=spread,
            order_type=order_type,
            limit_price=limit_price
        )

        # Calculate net premium
        net_premium = self._calculate_spread_premium(spread)

        # Check if we can afford the trade
        max_risk = abs(spread.max_loss) if spread.max_loss else abs(net_premium)
        if max_risk > self._cash:
            ml_order.status = OrderStatus.REJECTED
            self._multi_leg_orders[ml_order.order_id] = ml_order
            return ml_order

        # Mark as submitted
        ml_order.status = OrderStatus.SUBMITTED

        # For market orders, fill immediately
        if order_type == OrderType.MARKET:
            self._execute_spread_fill(ml_order, net_premium)
        elif order_type == OrderType.LIMIT and limit_price is not None:
            # Check if limit price is acceptable
            if self._is_limit_acceptable(net_premium, limit_price, spread):
                self._execute_spread_fill(ml_order, limit_price)
            else:
                # Add to pending orders
                pass

        self._multi_leg_orders[ml_order.order_id] = ml_order
        return ml_order

    def close_spread(
        self,
        position_id: str,
        order_type: OrderType = OrderType.MARKET,
        limit_price: Optional[Decimal] = None
    ) -> Optional[MultiLegOrder]:
        """
        Close an open spread position.

        Args:
            position_id: Position ID to close
            order_type: MARKET or LIMIT
            limit_price: Limit price for closing

        Returns:
            MultiLegOrder for the closing trade, or None if position not found

        Example:
            >>> # Close position at market
            >>> close_order = broker.close_spread("POS_123")
            >>> if close_order.is_filled:
            ...     print(f"Position closed, P&L: {close_order.spread.realized_pnl}")
        """
        if position_id not in self._open_spreads:
            return None

        spread = self._open_spreads[position_id]

        # Create opposite legs to close
        closing_legs = []
        for leg in spread.legs:
            # Reverse the action
            if leg.action == OrderAction.BTO:
                closing_action = OrderAction.STC
            elif leg.action == OrderAction.STO:
                closing_action = OrderAction.BTC
            elif leg.action == OrderAction.BTC:
                closing_action = OrderAction.STO
            else:  # STC
                closing_action = OrderAction.BTO

            closing_leg = SpreadLeg(
                contract=leg.contract,
                action=closing_action,
                quantity=leg.quantity,
                leg_index=leg.leg_index
            )
            closing_legs.append(closing_leg)

        # Create closing spread
        closing_spread = MultiLegSpread(
            legs=closing_legs,
            spread_type=spread.spread_type,
            entry_time=datetime.now(),
            underlying_price=spread.underlying_price,  # Should be updated with current price
            position_id=position_id
        )

        # Submit closing order
        ml_order = self.submit_spread_order(closing_spread, order_type, limit_price)

        # If filled, remove from open positions
        if ml_order.is_filled:
            del self._open_spreads[position_id]

        return ml_order

    def get_open_spread(self, position_id: str) -> Optional[MultiLegSpread]:
        """Get open spread by position ID."""
        return self._open_spreads.get(position_id)

    def get_all_open_spreads(self) -> List[MultiLegSpread]:
        """Get all open spreads."""
        return list(self._open_spreads.values())

    def simulate_early_assignment(
        self,
        position_id: str,
        leg_index: int,
        assignment_price: Decimal
    ) -> bool:
        """
        Simulate early assignment of a short option.

        Args:
            position_id: Position ID
            leg_index: Index of leg being assigned
            assignment_price: Price at assignment

        Returns:
            True if assignment was successful

        Note:
            Early assignment typically happens on short ITM options,
            especially near expiration or ex-dividend dates.
        """
        if position_id not in self._open_spreads:
            return False

        spread = self._open_spreads[position_id]

        # Find the leg
        leg = None
        for l in spread.legs:
            if l.leg_index == leg_index:
                leg = l
                break

        if leg is None or leg.is_long:
            return False

        # Deduct assignment fee
        self._cash -= self.assignment_fee

        # Record assignment
        assignment_record = {
            'position_id': position_id,
            'leg_index': leg_index,
            'assignment_time': datetime.now(),
            'assignment_price': assignment_price,
            'contract': leg.contract,
            'fee': self.assignment_fee
        }
        self._assignment_history.append(assignment_record)

        return True

    def get_assignment_history(self) -> List[Dict]:
        """Get history of early assignments."""
        return self._assignment_history.copy()

    def calculate_spread_commission(self, spread: MultiLegSpread) -> Decimal:
        """
        Calculate total commission for a spread.

        Args:
            spread: MultiLegSpread to calculate commission for

        Returns:
            Total commission in dollars
        """
        total_contracts = sum(leg.quantity for leg in spread.legs)
        return Decimal(str(total_contracts * self.commission_per_contract))

    def _calculate_spread_premium(self, spread: MultiLegSpread) -> Decimal:
        """
        Calculate net premium for a spread using current market prices.

        For opening trades:
        - Buying (BTO): Pay the ask (negative premium)
        - Selling (STO): Receive the bid (positive premium)

        Net premium = Sum of all leg premiums
        """
        net_premium = Decimal("0")

        for leg in spread.legs:
            if leg.action == OrderAction.BTO:
                # Buying - pay the ask
                leg_premium = -leg.contract.ask * Decimal(str(leg.quantity)) * Decimal("100")
            elif leg.action == OrderAction.STO:
                # Selling - receive the bid
                leg_premium = leg.contract.bid * Decimal(str(leg.quantity)) * Decimal("100")
            elif leg.action == OrderAction.BTC:
                # Buying to close - pay the ask
                leg_premium = -leg.contract.ask * Decimal(str(leg.quantity)) * Decimal("100")
            else:  # STC
                # Selling to close - receive the bid
                leg_premium = leg.contract.bid * Decimal(str(leg.quantity)) * Decimal("100")

            net_premium += leg_premium

        return net_premium

    def _is_limit_acceptable(
        self,
        market_premium: Decimal,
        limit_price: Decimal,
        spread: MultiLegSpread
    ) -> bool:
        """
        Check if limit price is acceptable given market premium.

        For credit spreads: Want to receive at least limit_price
        For debit spreads: Want to pay at most limit_price
        """
        # Credit spread if net premium is positive
        is_credit = market_premium > 0

        if is_credit:
            # For credit spreads, want market premium >= limit price
            return market_premium >= limit_price
        else:
            # For debit spreads, want market premium <= limit price (less negative)
            return market_premium <= limit_price

    def _execute_spread_fill(
        self,
        ml_order: MultiLegOrder,
        fill_price: Decimal
    ) -> None:
        """Execute fill for multi-leg spread order."""
        spread = ml_order.spread

        # Calculate commission
        commission = self.calculate_spread_commission(spread)
        ml_order.total_commission = commission

        # Update cash
        net_cash_flow = fill_price - commission
        self._cash += float(net_cash_flow)

        # Mark order as filled
        ml_order.status = OrderStatus.FILLED
        ml_order.filled_at = datetime.now()
        ml_order.fill_price = fill_price

        # Update spread
        spread.entry_premium = fill_price
        spread.entry_time = ml_order.filled_at

        # Store in open positions if opening trade
        if spread.position_id and all(
            leg.action in (OrderAction.BTO, OrderAction.STO) for leg in spread.legs
        ):
            self._open_spreads[spread.position_id] = spread

        # Create individual leg fills for tracking
        for leg in spread.legs:
            leg_fill = {
                'leg_index': leg.leg_index,
                'contract': leg.contract,
                'action': leg.action,
                'quantity': leg.quantity,
                'price': leg.contract.mid_price,
                'fill_time': ml_order.filled_at
            }
            ml_order.leg_fills.append(leg_fill)

        # Call callback if set
        if self._spread_fill_callback:
            self._spread_fill_callback(ml_order)

    def get_account_summary(self) -> Dict:
        """
        Get detailed account summary including options positions.

        Returns:
            Dictionary with account details including:
            - cash, total_equity
            - open_positions_count
            - total_capital_at_risk
            - realized_pnl, unrealized_pnl
        """
        base_summary = self.get_account()

        # Calculate options-specific metrics
        total_risk = Decimal("0")
        unrealized_pnl = Decimal("0")

        for spread in self._open_spreads.values():
            if spread.max_loss:
                total_risk += abs(spread.max_loss)

            if spread.entry_premium:
                current_value = spread.calculate_current_value()
                pnl = spread.entry_premium - current_value
                unrealized_pnl += pnl

        base_summary['open_positions_count'] = len(self._open_spreads)
        base_summary['total_capital_at_risk'] = float(total_risk)
        base_summary['unrealized_pnl'] = float(unrealized_pnl)
        base_summary['assignments_count'] = len(self._assignment_history)

        return base_summary

    def get_multi_leg_order(self, order_id: str) -> Optional[MultiLegOrder]:
        """Get multi-leg order by ID."""
        return self._multi_leg_orders.get(order_id)

    def get_all_multi_leg_orders(self) -> List[MultiLegOrder]:
        """Get all multi-leg orders."""
        return list(self._multi_leg_orders.values())
