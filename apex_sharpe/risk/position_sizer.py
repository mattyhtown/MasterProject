"""
Options Position Sizer for APEX-SHARPE Trading System.

Extends CrewTrader's PositionSizer with options-specific sizing methods
based on Greeks exposure, margin requirements, and premium/risk ratios.
"""

import sys
import os
from dataclasses import dataclass
from typing import Optional, Tuple
from decimal import Decimal

# Add CrewTrader to path for PositionSizer base class
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../CrewTrader'))

from risk.position_sizer import PositionSizer

# Use relative import for internal APEX-SHARPE module
from ..strategies.base_strategy import MultiLegSpread, SpreadType


@dataclass
class PositionSizeResult:
    """
    Result of position sizing calculation.

    Attributes:
        contracts: Number of contracts to trade
        sizing_method: Method used for sizing
        capital_at_risk: Dollar amount at risk
        max_profit_potential: Maximum profit potential
        max_loss_potential: Maximum loss potential
        delta_exposure: Delta exposure from position
        vega_exposure: Vega exposure from position
        margin_requirement: Margin requirement for position
        risk_reward_ratio: Risk/reward ratio
        notes: Additional sizing notes or warnings
    """
    contracts: int
    sizing_method: str
    capital_at_risk: Decimal
    max_profit_potential: Optional[Decimal] = None
    max_loss_potential: Optional[Decimal] = None
    delta_exposure: Optional[Decimal] = None
    vega_exposure: Optional[Decimal] = None
    margin_requirement: Optional[Decimal] = None
    risk_reward_ratio: Optional[float] = None
    notes: Optional[str] = None


class OptionsPositionSizer(PositionSizer):
    """
    Options-specific position sizing extending CrewTrader's PositionSizer.

    Provides multiple sizing methods tailored for options spreads:
    1. Risk-based: Size based on maximum risk per trade
    2. Greeks-based: Size based on delta/vega exposure limits
    3. Margin-based: Size based on available buying power

    Example:
        >>> from decimal import Decimal
        >>>
        >>> sizer = OptionsPositionSizer(
        ...     risk_per_trade_pct=0.02,  # 2% risk per trade
        ...     max_delta_per_position=25,
        ...     max_vega_per_position=100
        ... )
        >>>
        >>> # Size by risk (credit spread)
        >>> result = sizer.size_by_risk(
        ...     capital=Decimal('100000'),
        ...     max_loss=Decimal('400'),  # Max loss per contract
        ...     credit_received=Decimal('100')
        ... )
        >>> print(f"Trade {result.contracts} contracts")
        >>>
        >>> # Size by delta exposure
        >>> spread = MultiLegSpread(...)
        >>> result = sizer.size_by_greeks(
        ...     spread=spread,
        ...     current_portfolio_delta=Decimal('50'),
        ...     current_portfolio_vega=Decimal('200')
        ... )
    """

    def __init__(
        self,
        risk_per_trade_pct: float = 0.02,
        max_position_value: float = 50_000.0,
        max_position_size: int = 10,
        contract_multiplier: float = 100.0,  # Standard options multiplier
        max_delta_per_position: float = 25.0,
        max_vega_per_position: float = 100.0,
        max_buying_power_pct: float = 0.25,  # Max 25% of BP per position
        min_risk_reward_ratio: float = 0.25,  # Min 1:4 risk:reward for credits
    ):
        """
        Initialize Options Position Sizer.

        Args:
            risk_per_trade_pct: Maximum risk per trade as % of capital
            max_position_value: Maximum dollar value for a position
            max_position_size: Maximum number of contracts
            contract_multiplier: Options contract multiplier (100 for standard)
            max_delta_per_position: Maximum delta per position
            max_vega_per_position: Maximum vega per position
            max_buying_power_pct: Maximum % of buying power per position
            min_risk_reward_ratio: Minimum risk/reward ratio for credit spreads
        """
        super().__init__(
            risk_per_trade_pct=risk_per_trade_pct,
            max_position_value=max_position_value,
            max_position_size=max_position_size,
            contract_multiplier=contract_multiplier,
        )

        self.max_delta_per_position = Decimal(str(max_delta_per_position))
        self.max_vega_per_position = Decimal(str(max_vega_per_position))
        self.max_buying_power_pct = Decimal(str(max_buying_power_pct))
        self.min_risk_reward_ratio = min_risk_reward_ratio

    def calculate_position_size(
        self,
        spread: MultiLegSpread,
        capital: Decimal,
        available_buying_power: Decimal,
        current_portfolio_delta: Decimal = Decimal('0'),
        current_portfolio_vega: Decimal = Decimal('0'),
        margin_per_contract: Optional[Decimal] = None,
    ) -> PositionSizeResult:
        """
        Calculate optimal position size using multiple methods.

        Takes the minimum size from all applicable methods to ensure
        all constraints are satisfied.

        Args:
            spread: The multi-leg spread to size
            capital: Total trading capital
            available_buying_power: Available buying power
            current_portfolio_delta: Current portfolio delta
            current_portfolio_vega: Current portfolio vega
            margin_per_contract: Margin requirement per contract

        Returns:
            PositionSizeResult with optimal size and details
        """
        # Ensure Greeks are calculated
        if spread.portfolio_delta is None:
            spread.calculate_portfolio_greeks()

        # Method 1: Risk-based sizing
        risk_size = self._size_by_risk_internal(
            capital=capital,
            max_loss=spread.max_loss,
            premium=spread.entry_premium,
        )

        # Method 2: Greeks-based sizing
        greeks_size = self._size_by_greeks_internal(
            spread=spread,
            current_portfolio_delta=current_portfolio_delta,
            current_portfolio_vega=current_portfolio_vega,
        )

        # Method 3: Margin-based sizing
        margin_size = self._size_by_margin_internal(
            available_buying_power=available_buying_power,
            margin_per_contract=margin_per_contract or spread.max_loss,
        )

        # Take minimum to satisfy all constraints
        sizes = [risk_size, greeks_size, margin_size]
        final_size = min(s for s in sizes if s > 0)
        final_size = min(final_size, self.max_position_size)

        # Determine which method was limiting
        if final_size == risk_size:
            method = "risk_based"
        elif final_size == greeks_size:
            method = "greeks_based"
        else:
            method = "margin_based"

        # Calculate metrics
        capital_at_risk = (spread.max_loss or Decimal('0')) * Decimal(str(final_size))
        max_profit = (spread.max_profit or Decimal('0')) * Decimal(str(final_size))
        delta_exp = (spread.portfolio_delta or Decimal('0')) * Decimal(str(final_size))
        vega_exp = (spread.portfolio_vega or Decimal('0')) * Decimal(str(final_size))
        margin_req = (margin_per_contract or spread.max_loss or Decimal('0')) * Decimal(str(final_size))

        # Calculate risk/reward ratio
        risk_reward = None
        if max_profit and capital_at_risk and capital_at_risk > 0:
            risk_reward = float(capital_at_risk / max_profit)

        # Generate notes
        notes = []
        if risk_reward and risk_reward < self.min_risk_reward_ratio:
            notes.append(f"Good risk/reward: {risk_reward:.2f}")
        if final_size < min(sizes):
            notes.append(f"Size constrained by {method}")

        return PositionSizeResult(
            contracts=final_size,
            sizing_method=method,
            capital_at_risk=capital_at_risk,
            max_profit_potential=max_profit,
            max_loss_potential=capital_at_risk,
            delta_exposure=delta_exp,
            vega_exposure=vega_exp,
            margin_requirement=margin_req,
            risk_reward_ratio=risk_reward,
            notes="; ".join(notes) if notes else None,
        )

    def size_by_risk(
        self,
        capital: Decimal,
        max_loss: Optional[Decimal],
        credit_received: Optional[Decimal] = None,
        debit_paid: Optional[Decimal] = None,
    ) -> PositionSizeResult:
        """
        Size position based on maximum risk per trade.

        For credit spreads: max_loss = spread_width - credit_received
        For debit spreads: max_loss = debit_paid

        Args:
            capital: Total trading capital
            max_loss: Maximum loss per contract
            credit_received: Credit received (for credit spreads)
            debit_paid: Debit paid (for debit spreads)

        Returns:
            PositionSizeResult with risk-based sizing
        """
        if max_loss is None or max_loss <= 0:
            return PositionSizeResult(
                contracts=0,
                sizing_method="risk_based",
                capital_at_risk=Decimal('0'),
                notes="Invalid max_loss provided",
            )

        size = self._size_by_risk_internal(capital, max_loss, credit_received or debit_paid)

        capital_at_risk = max_loss * Decimal(str(size))

        # Calculate potential profit
        max_profit = None
        risk_reward = None
        if credit_received:
            max_profit = credit_received * Decimal(str(size))
            risk_reward = float(capital_at_risk / max_profit) if max_profit > 0 else None
        elif debit_paid:
            # For debit spreads, we'd need spread width to calculate max profit
            pass

        return PositionSizeResult(
            contracts=size,
            sizing_method="risk_based",
            capital_at_risk=capital_at_risk,
            max_profit_potential=max_profit,
            max_loss_potential=capital_at_risk,
            risk_reward_ratio=risk_reward,
        )

    def size_by_greeks(
        self,
        spread: MultiLegSpread,
        current_portfolio_delta: Decimal = Decimal('0'),
        current_portfolio_vega: Decimal = Decimal('0'),
    ) -> PositionSizeResult:
        """
        Size position based on delta and vega exposure limits.

        Ensures that adding this position won't exceed Greeks limits.

        Args:
            spread: The multi-leg spread to size
            current_portfolio_delta: Current portfolio delta
            current_portfolio_vega: Current portfolio vega

        Returns:
            PositionSizeResult with Greeks-based sizing
        """
        if spread.portfolio_delta is None:
            spread.calculate_portfolio_greeks()

        size = self._size_by_greeks_internal(
            spread,
            current_portfolio_delta,
            current_portfolio_vega,
        )

        delta_exp = (spread.portfolio_delta or Decimal('0')) * Decimal(str(size))
        vega_exp = (spread.portfolio_vega or Decimal('0')) * Decimal(str(size))
        capital_at_risk = (spread.max_loss or Decimal('0')) * Decimal(str(size))

        notes = []
        if abs(delta_exp) >= self.max_delta_per_position * Decimal('0.8'):
            notes.append("Approaching delta limit")
        if abs(vega_exp) >= self.max_vega_per_position * Decimal('0.8'):
            notes.append("Approaching vega limit")

        return PositionSizeResult(
            contracts=size,
            sizing_method="greeks_based",
            capital_at_risk=capital_at_risk,
            delta_exposure=delta_exp,
            vega_exposure=vega_exp,
            notes="; ".join(notes) if notes else None,
        )

    def size_by_margin(
        self,
        available_buying_power: Decimal,
        margin_per_contract: Decimal,
    ) -> PositionSizeResult:
        """
        Size position based on available buying power and margin requirements.

        Ensures sufficient buying power remains after the trade.

        Args:
            available_buying_power: Available buying power
            margin_per_contract: Margin requirement per contract

        Returns:
            PositionSizeResult with margin-based sizing
        """
        size = self._size_by_margin_internal(
            available_buying_power,
            margin_per_contract,
        )

        margin_req = margin_per_contract * Decimal(str(size))
        bp_remaining = available_buying_power - margin_req

        return PositionSizeResult(
            contracts=size,
            sizing_method="margin_based",
            capital_at_risk=margin_req,
            margin_requirement=margin_req,
            notes=f"Buying power remaining: ${bp_remaining:,.0f}",
        )

    def _size_by_risk_internal(
        self,
        capital: Decimal,
        max_loss: Optional[Decimal],
        premium: Optional[Decimal] = None,
    ) -> int:
        """Internal risk-based sizing calculation."""
        if max_loss is None or max_loss <= 0:
            return 0

        # Calculate risk amount (% of capital)
        risk_amount = capital * Decimal(str(self.risk_per_trade_pct))

        # Calculate contracts
        contracts = int(risk_amount / max_loss)

        return max(0, min(contracts, self.max_position_size))

    def _size_by_greeks_internal(
        self,
        spread: MultiLegSpread,
        current_portfolio_delta: Decimal,
        current_portfolio_vega: Decimal,
    ) -> int:
        """Internal Greeks-based sizing calculation."""
        spread_delta = spread.portfolio_delta or Decimal('0')
        spread_vega = spread.portfolio_vega or Decimal('0')

        # Calculate max contracts based on delta limit
        delta_headroom = self.max_delta_per_position - abs(current_portfolio_delta)
        delta_contracts = 999999
        if spread_delta != 0:
            delta_contracts = int(delta_headroom / abs(spread_delta))

        # Calculate max contracts based on vega limit
        vega_headroom = self.max_vega_per_position - abs(current_portfolio_vega)
        vega_contracts = 999999
        if spread_vega != 0:
            vega_contracts = int(vega_headroom / abs(spread_vega))

        # Take minimum
        contracts = min(delta_contracts, vega_contracts, self.max_position_size)

        return max(0, contracts)

    def _size_by_margin_internal(
        self,
        available_buying_power: Decimal,
        margin_per_contract: Decimal,
    ) -> int:
        """Internal margin-based sizing calculation."""
        if margin_per_contract <= 0:
            return 0

        # Maximum buying power to use per position
        max_bp_for_position = available_buying_power * self.max_buying_power_pct

        # Calculate contracts
        contracts = int(max_bp_for_position / margin_per_contract)

        return max(0, min(contracts, self.max_position_size))

    def validate_spread_metrics(
        self,
        spread: MultiLegSpread,
        spread_type: SpreadType,
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate that a spread meets minimum quality metrics.

        Args:
            spread: The spread to validate
            spread_type: Type of spread

        Returns:
            Tuple of (is_valid, reason_if_invalid)
        """
        # For credit spreads, check risk/reward ratio
        if spread_type in (SpreadType.CREDIT_SPREAD, SpreadType.IRON_CONDOR):
            if spread.max_profit and spread.max_loss:
                risk_reward = float(abs(spread.max_loss) / spread.max_profit)
                if risk_reward > (1.0 / self.min_risk_reward_ratio):
                    return False, f"Poor risk/reward ratio: {risk_reward:.2f}"

        # Check that max_loss is defined
        if spread.max_loss is None or spread.max_loss == 0:
            return False, "Max loss not defined or zero"

        # Check that Greeks are calculated
        if spread.portfolio_delta is None:
            return False, "Portfolio Greeks not calculated"

        return True, None

    def get_sizing_parameters(self) -> dict:
        """Get current sizing parameters."""
        return {
            'risk_per_trade_pct': self.risk_per_trade_pct,
            'max_position_value': self.max_position_value,
            'max_position_size': self.max_position_size,
            'contract_multiplier': self.contract_multiplier,
            'max_delta_per_position': float(self.max_delta_per_position),
            'max_vega_per_position': float(self.max_vega_per_position),
            'max_buying_power_pct': float(self.max_buying_power_pct),
            'min_risk_reward_ratio': self.min_risk_reward_ratio,
        }
