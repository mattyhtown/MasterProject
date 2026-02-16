"""
Position Tracker for APEX-SHARPE Trading System.

Tracks multi-leg options positions with:
- Daily Greeks updates
- Mark-to-market P&L
- Exit condition monitoring
- Expiration and assignment handling
- Supabase persistence
"""

import sys
import os
from typing import Dict, List, Optional, Tuple
from datetime import datetime, date
from decimal import Decimal
from dataclasses import asdict

# Use relative imports for internal APEX-SHARPE modules
from ..strategies.base_strategy import MultiLegSpread, SpreadLeg, OptionContract, OptionsChain
from ..greeks.greeks_calculator import GreeksCalculator, OptionContract as GreeksOptionContract, OptionType
from ..database.supabase_client import (
    SupabaseClient,
    Position,
    PositionLeg,
    GreeksSnapshot
)


class PositionTracker:
    """
    Tracks and manages multi-leg options positions.

    Provides:
    - Position storage and retrieval
    - Daily Greeks updates
    - Mark-to-market P&L calculation
    - Exit condition checking
    - Expiration handling
    - Supabase persistence

    Example:
        >>> tracker = PositionTracker(
        ...     greeks_calculator=greeks_calc,
        ...     supabase_client=db_client
        ... )
        >>> # Open a new position
        >>> position_id = tracker.open_position(iron_condor_spread, strategy_id="IC_001")
        >>> # Update position with new market data
        >>> tracker.update_position(position_id, current_chain)
        >>> # Check if should exit
        >>> should_exit, reason = tracker.check_exit_conditions(position_id)
    """

    def __init__(
        self,
        greeks_calculator: GreeksCalculator,
        supabase_client: Optional[SupabaseClient] = None,
        exit_profit_pct: float = 0.50,      # Exit at 50% max profit
        exit_loss_pct: float = 2.00,        # Exit at 200% max loss
        exit_dte: int = 7,                   # Exit at 7 DTE
        exit_delta_threshold: Decimal = Decimal("0.30")  # Exit if position delta > 0.30
    ):
        """
        Initialize position tracker.

        Args:
            greeks_calculator: Calculator for Greeks updates
            supabase_client: Database client for persistence (optional)
            exit_profit_pct: Exit at this % of max profit (e.g., 0.50 = 50%)
            exit_loss_pct: Exit at this % of max loss (e.g., 2.00 = 200%)
            exit_dte: Exit when DTE reaches this level
            exit_delta_threshold: Exit if abs(portfolio_delta) exceeds this
        """
        self.greeks_calculator = greeks_calculator
        self.supabase_client = supabase_client
        self.exit_profit_pct = exit_profit_pct
        self.exit_loss_pct = exit_loss_pct
        self.exit_dte = exit_dte
        self.exit_delta_threshold = exit_delta_threshold

        # In-memory position storage
        self._positions: Dict[str, MultiLegSpread] = {}

    def open_position(
        self,
        spread: MultiLegSpread,
        strategy_id: Optional[str] = None,
        persist: bool = True
    ) -> str:
        """
        Open a new position.

        Args:
            spread: MultiLegSpread to open
            strategy_id: Strategy identifier
            persist: Whether to persist to database

        Returns:
            Position ID

        Example:
            >>> position_id = tracker.open_position(
            ...     iron_condor_spread,
            ...     strategy_id="IC_001"
            ... )
        """
        # Generate position ID if not set
        if not spread.position_id:
            spread.position_id = f"POS_{datetime.now().strftime('%Y%m%d%H%M%S%f')}"

        position_id = spread.position_id

        # Store in memory
        self._positions[position_id] = spread

        # Persist to database if client available
        if persist and self.supabase_client:
            self._persist_position(spread, strategy_id)

        return position_id

    def close_position(
        self,
        position_id: str,
        exit_reason: str,
        exit_premium: Decimal,
        persist: bool = True
    ) -> Optional[MultiLegSpread]:
        """
        Close a position.

        Args:
            position_id: Position to close
            exit_reason: Reason for exit
            exit_premium: Exit value/premium
            persist: Whether to persist closure to database

        Returns:
            Closed MultiLegSpread, or None if not found

        Example:
            >>> closed = tracker.close_position(
            ...     "POS_123",
            ...     "PROFIT_TARGET",
            ...     Decimal("150.00")
            ... )
        """
        if position_id not in self._positions:
            return None

        spread = self._positions[position_id]

        # Update exit information
        spread.exit_time = datetime.now()
        spread.exit_reason = exit_reason
        spread.exit_premium = exit_premium

        # Calculate realized P&L
        if spread.entry_premium:
            spread.realized_pnl = spread.entry_premium - exit_premium

        # Persist to database
        if persist and self.supabase_client and spread.entry_premium:
            dte = self._calculate_dte(spread)
            self.supabase_client.close_position(
                position_id,
                exit_reason,
                spread.realized_pnl or Decimal("0"),
                dte
            )

        # Remove from open positions
        del self._positions[position_id]

        return spread

    def update_position(
        self,
        position_id: str,
        current_chain: OptionsChain,
        persist_greeks: bool = True
    ) -> bool:
        """
        Update position with current market data.

        Updates:
        - Contract prices from chain
        - Portfolio Greeks
        - Unrealized P&L

        Args:
            position_id: Position to update
            current_chain: Current options chain
            persist_greeks: Whether to save Greeks snapshot to database

        Returns:
            True if successful

        Example:
            >>> # Daily update loop
            >>> for position_id in tracker.get_open_position_ids():
            ...     tracker.update_position(position_id, current_chain)
        """
        if position_id not in self._positions:
            return False

        spread = self._positions[position_id]

        # Update contract prices from chain
        for leg in spread.legs:
            updated_contract = self._find_contract_in_chain(
                current_chain,
                leg.contract
            )
            if updated_contract:
                leg.contract = updated_contract

        # Recalculate portfolio Greeks
        self._update_greeks(spread, current_chain.underlying_price)

        # Calculate current value and P&L
        spread.calculate_current_value()
        spread.calculate_unrealized_pnl()

        # Persist Greeks snapshot
        if persist_greeks and self.supabase_client:
            self._persist_greeks_snapshot(spread, current_chain.underlying_price)

        return True

    def check_exit_conditions(
        self,
        position_id: str
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if position should be exited.

        Exit conditions:
        1. Profit target reached (50% of max profit)
        2. Loss limit exceeded (200% of max loss)
        3. DTE threshold reached (7 DTE)
        4. Delta threshold exceeded (|delta| > 0.30)

        Args:
            position_id: Position to check

        Returns:
            Tuple of (should_exit, exit_reason)

        Example:
            >>> should_exit, reason = tracker.check_exit_conditions("POS_123")
            >>> if should_exit:
            ...     print(f"Exit position: {reason}")
        """
        if position_id not in self._positions:
            return False, None

        spread = self._positions[position_id]

        # Check DTE
        dte = self._calculate_dte(spread)
        if dte <= self.exit_dte:
            return True, f"DTE_THRESHOLD (DTE={dte})"

        # Check profit target
        if spread.max_profit and spread.entry_premium:
            target_pnl = spread.max_profit * Decimal(str(self.exit_profit_pct))
            current_pnl = spread.calculate_unrealized_pnl()

            if current_pnl and current_pnl >= target_pnl:
                return True, f"PROFIT_TARGET (P&L={current_pnl:.2f}, Target={target_pnl:.2f})"

        # Check loss limit
        if spread.max_loss and spread.entry_premium:
            loss_limit = abs(spread.max_loss) * Decimal(str(self.exit_loss_pct))
            current_pnl = spread.calculate_unrealized_pnl()

            if current_pnl and current_pnl <= -loss_limit:
                return True, f"LOSS_LIMIT (P&L={current_pnl:.2f}, Limit={-loss_limit:.2f})"

        # Check delta threshold
        if spread.portfolio_delta:
            if abs(spread.portfolio_delta) > self.exit_delta_threshold:
                return True, f"DELTA_THRESHOLD (Delta={spread.portfolio_delta:.3f})"

        return False, None

    def get_position(self, position_id: str) -> Optional[MultiLegSpread]:
        """Get position by ID."""
        return self._positions.get(position_id)

    def get_open_position_ids(self) -> List[str]:
        """Get all open position IDs."""
        return list(self._positions.keys())

    def get_all_open_positions(self) -> List[MultiLegSpread]:
        """Get all open positions."""
        return list(self._positions.values())

    def get_position_count(self) -> int:
        """Get count of open positions."""
        return len(self._positions)

    def get_total_capital_at_risk(self) -> Decimal:
        """Calculate total capital at risk across all positions."""
        total_risk = Decimal("0")
        for spread in self._positions.values():
            if spread.max_loss:
                total_risk += abs(spread.max_loss)
        return total_risk

    def get_portfolio_greeks(self) -> Dict[str, Decimal]:
        """
        Get aggregated portfolio Greeks across all positions.

        Returns:
            Dictionary with total_delta, total_gamma, total_theta, total_vega, total_rho
        """
        totals = {
            'total_delta': Decimal("0"),
            'total_gamma': Decimal("0"),
            'total_theta': Decimal("0"),
            'total_vega': Decimal("0"),
            'total_rho': Decimal("0"),
        }

        for spread in self._positions.values():
            if spread.portfolio_delta:
                totals['total_delta'] += spread.portfolio_delta
            if spread.portfolio_gamma:
                totals['total_gamma'] += spread.portfolio_gamma
            if spread.portfolio_theta:
                totals['total_theta'] += spread.portfolio_theta
            if spread.portfolio_vega:
                totals['total_vega'] += spread.portfolio_vega
            if spread.portfolio_rho:
                totals['total_rho'] += spread.portfolio_rho

        return totals

    def handle_expiration(
        self,
        position_id: str,
        underlying_price: Decimal
    ) -> Optional[MultiLegSpread]:
        """
        Handle position expiration.

        Automatically closes position at expiration value.

        Args:
            position_id: Position reaching expiration
            underlying_price: Underlying price at expiration

        Returns:
            Closed spread, or None if not found

        Example:
            >>> # On expiration Friday
            >>> for pos_id in tracker.get_expiring_positions(date.today()):
            ...     tracker.handle_expiration(pos_id, current_price)
        """
        if position_id not in self._positions:
            return None

        spread = self._positions[position_id]

        # Calculate expiration value
        expiration_value = self._calculate_expiration_value(spread, underlying_price)

        return self.close_position(
            position_id,
            "EXPIRATION",
            expiration_value
        )

    def get_expiring_positions(self, expiration_date: date) -> List[str]:
        """
        Get positions expiring on a specific date.

        Args:
            expiration_date: Expiration date to check

        Returns:
            List of position IDs expiring on that date
        """
        expiring = []

        for position_id, spread in self._positions.items():
            if spread.legs and spread.legs[0].contract.expiration == expiration_date:
                expiring.append(position_id)

        return expiring

    def _update_greeks(self, spread: MultiLegSpread, underlying_price: Decimal) -> None:
        """Update portfolio Greeks for a spread."""
        # Convert spread legs to Greeks calculator format
        contracts = []

        for leg in spread.legs:
            # Determine quantity with sign
            quantity = leg.quantity if leg.is_long else -leg.quantity

            greeks_contract = GreeksOptionContract(
                option_type=OptionType.CALL if leg.contract.option_type.value == "CALL" else OptionType.PUT,
                strike=leg.contract.strike,
                expiration_date=leg.contract.expiration,
                quantity=quantity,
                implied_volatility=leg.contract.implied_volatility
            )
            contracts.append(greeks_contract)

        # Calculate position Greeks
        try:
            for i, leg in enumerate(spread.legs):
                greeks_data = self.greeks_calculator.calculate_greeks(
                    contracts[i],
                    underlying_price
                )

                # Update contract Greeks
                leg.contract.delta = greeks_data.delta
                leg.contract.gamma = greeks_data.gamma
                leg.contract.theta = greeks_data.theta
                leg.contract.vega = greeks_data.vega
                leg.contract.rho = greeks_data.rho

            # Recalculate portfolio Greeks
            spread.calculate_portfolio_greeks()

        except Exception as e:
            # Log error but don't fail
            print(f"Error updating Greeks: {e}")

    def _calculate_dte(self, spread: MultiLegSpread) -> int:
        """Calculate days to expiration."""
        if not spread.legs:
            return 0

        expiration = spread.legs[0].contract.expiration
        today = date.today()

        return (expiration - today).days

    def _calculate_expiration_value(
        self,
        spread: MultiLegSpread,
        underlying_price: Decimal
    ) -> Decimal:
        """
        Calculate intrinsic value at expiration.

        Each option worth max(0, intrinsic_value) * quantity * multiplier
        """
        total_value = Decimal("0")

        for leg in spread.legs:
            contract = leg.contract
            strike = contract.strike

            if contract.option_type.value == "CALL":
                intrinsic = max(Decimal("0"), underlying_price - strike)
            else:  # PUT
                intrinsic = max(Decimal("0"), strike - underlying_price)

            # Value = intrinsic * quantity * multiplier
            leg_value = intrinsic * Decimal(str(leg.quantity)) * Decimal("100")

            # Short positions are negative value, long are positive
            if leg.is_short:
                total_value -= leg_value
            else:
                total_value += leg_value

        return total_value

    def _find_contract_in_chain(
        self,
        chain: OptionsChain,
        target_contract: OptionContract
    ) -> Optional[OptionContract]:
        """Find matching contract in options chain."""
        contracts = chain.get_contracts_by_expiration(
            target_contract.expiration,
            target_contract.option_type
        )

        for contract in contracts:
            if contract.strike == target_contract.strike:
                return contract

        return None

    def _persist_position(self, spread: MultiLegSpread, strategy_id: Optional[str]) -> None:
        """Persist position to Supabase."""
        if not self.supabase_client:
            return

        # Create position record
        position = Position(
            symbol=spread.legs[0].contract.symbol if spread.legs else "UNKNOWN",
            position_type=spread.spread_type.value,
            entry_date=spread.entry_time.date(),
            entry_time=spread.entry_time,
            entry_premium=spread.entry_premium or Decimal("0"),
            entry_dte=self._calculate_dte(spread),
            strategy_id=strategy_id
        )

        db_position = self.supabase_client.create_position(position)

        if db_position:
            position_db_id = db_position['id']

            # Add all legs
            for leg in spread.legs:
                position_leg = PositionLeg(
                    position_id=position_db_id,
                    leg_index=leg.leg_index,
                    option_type=leg.contract.option_type.value,
                    strike=leg.contract.strike,
                    expiration_date=leg.contract.expiration,
                    quantity=leg.quantity,
                    action=leg.action.value,
                    entry_price=leg.contract.mid_price,
                    entry_fill_time=spread.entry_time,
                    entry_delta=leg.contract.delta,
                    entry_gamma=leg.contract.gamma,
                    entry_theta=leg.contract.theta,
                    entry_vega=leg.contract.vega,
                    entry_iv=leg.contract.implied_volatility
                )
                self.supabase_client.add_position_leg(position_leg)

    def _persist_greeks_snapshot(
        self,
        spread: MultiLegSpread,
        underlying_price: Decimal
    ) -> None:
        """Persist Greeks snapshot to Supabase."""
        if not self.supabase_client or not spread.position_id:
            return

        snapshot = GreeksSnapshot(
            position_id=spread.position_id,
            trade_date=date.today(),
            dte=self._calculate_dte(spread),
            underlying_price=underlying_price,
            portfolio_delta=spread.portfolio_delta or Decimal("0"),
            portfolio_gamma=spread.portfolio_gamma or Decimal("0"),
            portfolio_theta=spread.portfolio_theta or Decimal("0"),
            portfolio_vega=spread.portfolio_vega or Decimal("0"),
            position_value=spread.current_value or Decimal("0"),
            unrealized_pnl=spread.calculate_unrealized_pnl() or Decimal("0")
        )

        self.supabase_client.record_greeks_snapshot(snapshot)
