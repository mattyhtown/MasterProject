"""
Options Risk Manager for APEX-SHARPE Trading System.

Extends CrewTrader's RiskManager with Greeks-based limits for options trading.
Integrates with Sharpe filtering from BaseStrategy.
"""

import sys
import os
from dataclasses import dataclass
from typing import Optional, List, Dict, Any
from decimal import Decimal
from datetime import datetime

# Add CrewTrader to path for RiskManager base class
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../CrewTrader'))

from risk.risk_manager import RiskManager, RiskAction, RiskAssessment

# Use relative import for internal APEX-SHARPE module
from ..strategies.base_strategy import MultiLegSpread, SpreadType


@dataclass
class GreeksLimits:
    """
    Portfolio Greeks limits for risk management.

    All limits are absolute values (portfolio-level exposure).

    Attributes:
        max_portfolio_delta: Maximum net delta exposure (e.g., 100)
        max_portfolio_vega: Maximum vega exposure (e.g., 1000)
        max_portfolio_theta: Maximum theta decay per day (e.g., -500)
        max_gamma_exposure: Maximum gamma exposure (e.g., 50)
        max_individual_position_delta: Max delta for single position (e.g., 25)
    """
    max_portfolio_delta: Decimal = Decimal('100')
    max_portfolio_vega: Decimal = Decimal('1000')
    max_portfolio_theta: Decimal = Decimal('-500')  # Negative = decay
    max_gamma_exposure: Decimal = Decimal('50')
    max_individual_position_delta: Decimal = Decimal('25')

    # Risk thresholds as percentage of limit
    warning_threshold: Decimal = Decimal('0.80')  # 80% of limit
    critical_threshold: Decimal = Decimal('0.95')  # 95% of limit


@dataclass
class OptionsRiskAssessment:
    """
    Risk assessment result for options positions.

    Extends base RiskAssessment with Greeks-specific information.
    """
    action: RiskAction
    reason: str

    # Suggested position sizing
    suggested_contracts: Optional[int] = None
    max_contracts: Optional[int] = None

    # Greeks impact
    delta_impact: Optional[Decimal] = None
    vega_impact: Optional[Decimal] = None
    theta_impact: Optional[Decimal] = None
    gamma_impact: Optional[Decimal] = None

    # Margin/capital requirements
    margin_requirement: Optional[Decimal] = None
    buying_power_effect: Optional[Decimal] = None

    # Portfolio state after position
    portfolio_delta_after: Optional[Decimal] = None
    portfolio_vega_after: Optional[Decimal] = None

    # Risk metrics
    risk_score: Optional[float] = None  # 0.0 to 1.0
    limit_utilization: Optional[Dict[str, float]] = None


class OptionsRiskManager(RiskManager):
    """
    Options-specific risk manager extending CrewTrader's RiskManager.

    Adds Greeks-based limits and options-specific risk controls on top of
    base equity risk management.

    Example:
        >>> from decimal import Decimal
        >>>
        >>> # Initialize with Greeks limits
        >>> risk_mgr = OptionsRiskManager(
        ...     max_position_value=50_000.0,
        ...     greeks_limits=GreeksLimits(
        ...         max_portfolio_delta=Decimal('100'),
        ...         max_portfolio_vega=Decimal('1000')
        ...     )
        ... )
        >>>
        >>> # Assess new spread
        >>> spread = MultiLegSpread(...)  # Your spread
        >>> current_portfolio = get_portfolio_greeks()
        >>>
        >>> assessment = risk_mgr.assess_new_spread(
        ...     spread=spread,
        ...     current_portfolio_delta=current_portfolio.total_delta,
        ...     current_portfolio_vega=current_portfolio.total_vega,
        ...     available_buying_power=Decimal('50000')
        ... )
        >>>
        >>> if assessment.action == RiskAction.ALLOW:
        ...     print(f"Trade approved: {assessment.suggested_contracts} contracts")
        >>> else:
        ...     print(f"Trade blocked: {assessment.reason}")
    """

    def __init__(
        self,
        max_position_value: float = 50_000.0,
        max_total_exposure: float = 200_000.0,
        max_drawdown_pct: float = 0.20,
        risk_per_trade_pct: float = 0.02,
        max_correlation: float = 0.7,
        sharpe_threshold: float = 1.0,
        greeks_limits: Optional[GreeksLimits] = None,
        max_positions: int = 10,
        min_buying_power_reserve: float = 20_000.0,
    ):
        """
        Initialize Options Risk Manager.

        Args:
            max_position_value: Maximum value for a single position
            max_total_exposure: Maximum total portfolio exposure
            max_drawdown_pct: Maximum allowed drawdown before halting
            risk_per_trade_pct: Maximum risk per trade as % of capital
            max_correlation: Maximum correlation between positions
            sharpe_threshold: Minimum Sharpe ratio to allow trading
            greeks_limits: Greeks exposure limits
            max_positions: Maximum number of concurrent positions
            min_buying_power_reserve: Minimum buying power to maintain
        """
        # Initialize base RiskManager
        super().__init__(
            max_position_value=max_position_value,
            max_total_exposure=max_total_exposure,
            max_drawdown_pct=max_drawdown_pct,
            risk_per_trade_pct=risk_per_trade_pct,
            max_correlation=max_correlation,
            sharpe_threshold=sharpe_threshold,
        )

        # Options-specific limits
        self.greeks_limits = greeks_limits or GreeksLimits()
        self.max_positions = max_positions
        self.min_buying_power_reserve = min_buying_power_reserve

        # Tracking
        self._current_position_count = 0
        self._greeks_history: List[Dict[str, Any]] = []

    def assess_new_spread(
        self,
        spread: MultiLegSpread,
        current_portfolio_delta: Decimal,
        current_portfolio_vega: Decimal,
        current_portfolio_theta: Decimal,
        current_portfolio_gamma: Decimal,
        available_buying_power: Decimal,
        margin_requirement: Decimal,
        current_sharpe: Optional[float] = None,
    ) -> OptionsRiskAssessment:
        """
        Assess risk of a new options spread.

        Checks:
        1. Trading halt status
        2. Sharpe ratio threshold (from BaseStrategy)
        3. Position count limits
        4. Greeks exposure limits (delta, vega, theta, gamma)
        5. Buying power requirements
        6. Drawdown limits

        Args:
            spread: The proposed multi-leg spread
            current_portfolio_delta: Current portfolio delta
            current_portfolio_vega: Current portfolio vega
            current_portfolio_theta: Current portfolio theta
            current_portfolio_gamma: Current portfolio gamma
            available_buying_power: Available buying power
            margin_requirement: Margin required for the spread
            current_sharpe: Current rolling Sharpe ratio

        Returns:
            OptionsRiskAssessment with action and details
        """
        # Check if trading is halted
        if self._trading_halted:
            return OptionsRiskAssessment(
                action=RiskAction.BLOCK,
                reason=f"Trading halted: {self._halt_reason}",
                risk_score=1.0,
            )

        # Check drawdown
        if self.drawdown_monitor.is_breached():
            self._halt_trading("Maximum drawdown exceeded")
            return OptionsRiskAssessment(
                action=RiskAction.CLOSE_ALL,
                reason="Maximum drawdown exceeded - close all positions",
                risk_score=1.0,
            )

        # Check Sharpe ratio (integration with BaseStrategy)
        if current_sharpe is not None and current_sharpe < self.sharpe_threshold:
            return OptionsRiskAssessment(
                action=RiskAction.BLOCK,
                reason=f"Sharpe ratio ({current_sharpe:.2f}) below threshold ({self.sharpe_threshold})",
                risk_score=0.8,
            )

        # Check position count limit
        if self._current_position_count >= self.max_positions:
            return OptionsRiskAssessment(
                action=RiskAction.BLOCK,
                reason=f"Maximum positions ({self.max_positions}) reached",
                risk_score=0.7,
            )

        # Calculate spread Greeks
        if spread.portfolio_delta is None:
            spread.calculate_portfolio_greeks()

        spread_delta = spread.portfolio_delta or Decimal('0')
        spread_vega = spread.portfolio_vega or Decimal('0')
        spread_theta = spread.portfolio_theta or Decimal('0')
        spread_gamma = spread.portfolio_gamma or Decimal('0')

        # Check individual position delta limit
        if abs(spread_delta) > self.greeks_limits.max_individual_position_delta:
            return OptionsRiskAssessment(
                action=RiskAction.BLOCK,
                reason=f"Individual position delta ({abs(spread_delta):.1f}) exceeds limit ({self.greeks_limits.max_individual_position_delta})",
                delta_impact=spread_delta,
                risk_score=0.9,
            )

        # Calculate portfolio Greeks after adding spread
        portfolio_delta_after = current_portfolio_delta + spread_delta
        portfolio_vega_after = current_portfolio_vega + spread_vega
        portfolio_theta_after = current_portfolio_theta + spread_theta
        portfolio_gamma_after = current_portfolio_gamma + spread_gamma

        # Check portfolio delta limit
        if abs(portfolio_delta_after) > self.greeks_limits.max_portfolio_delta:
            return OptionsRiskAssessment(
                action=RiskAction.BLOCK,
                reason=f"Portfolio delta ({abs(portfolio_delta_after):.1f}) would exceed limit ({self.greeks_limits.max_portfolio_delta})",
                delta_impact=spread_delta,
                portfolio_delta_after=portfolio_delta_after,
                risk_score=0.9,
            )

        # Check portfolio vega limit
        if abs(portfolio_vega_after) > self.greeks_limits.max_portfolio_vega:
            return OptionsRiskAssessment(
                action=RiskAction.BLOCK,
                reason=f"Portfolio vega ({abs(portfolio_vega_after):.1f}) would exceed limit ({self.greeks_limits.max_portfolio_vega})",
                vega_impact=spread_vega,
                portfolio_vega_after=portfolio_vega_after,
                risk_score=0.85,
            )

        # Check portfolio theta limit (theta is typically negative)
        if portfolio_theta_after < self.greeks_limits.max_portfolio_theta:
            return OptionsRiskAssessment(
                action=RiskAction.BLOCK,
                reason=f"Portfolio theta ({portfolio_theta_after:.1f}) would exceed decay limit ({self.greeks_limits.max_portfolio_theta})",
                theta_impact=spread_theta,
                risk_score=0.75,
            )

        # Check portfolio gamma limit
        if abs(portfolio_gamma_after) > self.greeks_limits.max_gamma_exposure:
            return OptionsRiskAssessment(
                action=RiskAction.BLOCK,
                reason=f"Portfolio gamma ({abs(portfolio_gamma_after):.1f}) would exceed limit ({self.greeks_limits.max_gamma_exposure})",
                gamma_impact=spread_gamma,
                risk_score=0.80,
            )

        # Check buying power
        buying_power_after = available_buying_power - margin_requirement
        if buying_power_after < Decimal(str(self.min_buying_power_reserve)):
            return OptionsRiskAssessment(
                action=RiskAction.BLOCK,
                reason=f"Insufficient buying power (reserve: ${self.min_buying_power_reserve:,.0f})",
                margin_requirement=margin_requirement,
                buying_power_effect=margin_requirement,
                risk_score=0.85,
            )

        # Calculate risk score and limit utilization
        limit_util = self._calculate_limit_utilization(
            portfolio_delta_after,
            portfolio_vega_after,
            portfolio_theta_after,
            portfolio_gamma_after,
        )

        risk_score = max(limit_util.values()) if limit_util else 0.0

        # Check if approaching warning threshold
        if risk_score >= float(self.greeks_limits.warning_threshold):
            warning_greeks = [k for k, v in limit_util.items() if v >= float(self.greeks_limits.warning_threshold)]
            reason = f"Trade approved with warning: {', '.join(warning_greeks)} approaching limits"
        else:
            reason = "Trade approved - within all risk limits"

        return OptionsRiskAssessment(
            action=RiskAction.ALLOW,
            reason=reason,
            suggested_contracts=1,  # For multi-leg spreads, typically 1 set
            delta_impact=spread_delta,
            vega_impact=spread_vega,
            theta_impact=spread_theta,
            gamma_impact=spread_gamma,
            margin_requirement=margin_requirement,
            buying_power_effect=margin_requirement,
            portfolio_delta_after=portfolio_delta_after,
            portfolio_vega_after=portfolio_vega_after,
            risk_score=risk_score,
            limit_utilization=limit_util,
        )

    def assess_portfolio_risk(
        self,
        portfolio_delta: Decimal,
        portfolio_vega: Decimal,
        portfolio_theta: Decimal,
        portfolio_gamma: Decimal,
        total_positions: int,
        total_margin_used: Decimal,
        total_buying_power: Decimal,
    ) -> OptionsRiskAssessment:
        """
        Assess overall portfolio risk.

        This should be called periodically to monitor portfolio health.

        Args:
            portfolio_delta: Current total portfolio delta
            portfolio_vega: Current total portfolio vega
            portfolio_theta: Current total portfolio theta
            portfolio_gamma: Current total portfolio gamma
            total_positions: Number of open positions
            total_margin_used: Total margin in use
            total_buying_power: Total available buying power

        Returns:
            OptionsRiskAssessment with portfolio risk status
        """
        issues = []
        risk_actions = []

        # Check Greeks limits
        if abs(portfolio_delta) > self.greeks_limits.max_portfolio_delta:
            issues.append(f"Portfolio delta ({abs(portfolio_delta):.1f}) exceeds limit")
            risk_actions.append(RiskAction.REDUCE_SIZE)

        if abs(portfolio_vega) > self.greeks_limits.max_portfolio_vega:
            issues.append(f"Portfolio vega ({abs(portfolio_vega):.1f}) exceeds limit")
            risk_actions.append(RiskAction.REDUCE_SIZE)

        if portfolio_theta < self.greeks_limits.max_portfolio_theta:
            issues.append(f"Portfolio theta ({portfolio_theta:.1f}) exceeds decay limit")
            risk_actions.append(RiskAction.REDUCE_SIZE)

        if abs(portfolio_gamma) > self.greeks_limits.max_gamma_exposure:
            issues.append(f"Portfolio gamma ({abs(portfolio_gamma):.1f}) exceeds limit")
            risk_actions.append(RiskAction.REDUCE_SIZE)

        # Check position count
        if total_positions > self.max_positions:
            issues.append(f"Position count ({total_positions}) exceeds limit ({self.max_positions})")
            risk_actions.append(RiskAction.CLOSE_POSITION)

        # Check buying power reserve
        available_bp = total_buying_power - total_margin_used
        if available_bp < Decimal(str(self.min_buying_power_reserve)):
            issues.append(f"Below minimum buying power reserve (${available_bp:,.0f})")
            risk_actions.append(RiskAction.REDUCE_SIZE)

        # Calculate limit utilization
        limit_util = self._calculate_limit_utilization(
            portfolio_delta,
            portfolio_vega,
            portfolio_theta,
            portfolio_gamma,
        )

        risk_score = max(limit_util.values()) if limit_util else 0.0

        # Determine action
        if issues:
            action = RiskAction.REDUCE_SIZE if RiskAction.REDUCE_SIZE in risk_actions else RiskAction.CLOSE_POSITION
            reason = "; ".join(issues)
        elif risk_score >= float(self.greeks_limits.critical_threshold):
            action = RiskAction.REDUCE_SIZE
            reason = "Portfolio approaching critical risk limits"
        elif risk_score >= float(self.greeks_limits.warning_threshold):
            action = RiskAction.ALLOW
            reason = "Portfolio risk elevated but within limits"
        else:
            action = RiskAction.ALLOW
            reason = "Portfolio risk within normal limits"

        return OptionsRiskAssessment(
            action=action,
            reason=reason,
            portfolio_delta_after=portfolio_delta,
            portfolio_vega_after=portfolio_vega,
            risk_score=risk_score,
            limit_utilization=limit_util,
            margin_requirement=total_margin_used,
            buying_power_effect=available_bp,
        )

    def _calculate_limit_utilization(
        self,
        portfolio_delta: Decimal,
        portfolio_vega: Decimal,
        portfolio_theta: Decimal,
        portfolio_gamma: Decimal,
    ) -> Dict[str, float]:
        """
        Calculate utilization percentage of each Greeks limit.

        Returns:
            Dictionary mapping Greek name to utilization (0.0 to 1.0+)
        """
        return {
            'delta': float(abs(portfolio_delta) / self.greeks_limits.max_portfolio_delta),
            'vega': float(abs(portfolio_vega) / self.greeks_limits.max_portfolio_vega),
            'theta': float(abs(portfolio_theta) / abs(self.greeks_limits.max_portfolio_theta)),
            'gamma': float(abs(portfolio_gamma) / self.greeks_limits.max_gamma_exposure),
        }

    def update_position_count(self, count: int) -> None:
        """Update the current position count."""
        self._current_position_count = count

    def record_greeks_snapshot(
        self,
        timestamp: datetime,
        portfolio_delta: Decimal,
        portfolio_vega: Decimal,
        portfolio_theta: Decimal,
        portfolio_gamma: Decimal,
    ) -> None:
        """
        Record a snapshot of portfolio Greeks for history tracking.

        Args:
            timestamp: Snapshot timestamp
            portfolio_delta: Total portfolio delta
            portfolio_vega: Total portfolio vega
            portfolio_theta: Total portfolio theta
            portfolio_gamma: Total portfolio gamma
        """
        snapshot = {
            'timestamp': timestamp,
            'portfolio_delta': float(portfolio_delta),
            'portfolio_vega': float(portfolio_vega),
            'portfolio_theta': float(portfolio_theta),
            'portfolio_gamma': float(portfolio_gamma),
            'limit_utilization': self._calculate_limit_utilization(
                portfolio_delta,
                portfolio_vega,
                portfolio_theta,
                portfolio_gamma,
            ),
        }

        self._greeks_history.append(snapshot)

        # Keep only last 1000 snapshots
        if len(self._greeks_history) > 1000:
            self._greeks_history = self._greeks_history[-1000:]

    def get_greeks_history(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Get historical Greeks snapshots.

        Args:
            limit: Maximum number of snapshots to return (most recent)

        Returns:
            List of Greeks snapshots
        """
        if limit:
            return self._greeks_history[-limit:]
        return self._greeks_history

    def get_risk_status(self) -> Dict[str, Any]:
        """
        Get comprehensive risk status including base and Greeks limits.

        Returns:
            Dictionary with complete risk status
        """
        base_status = self.get_status()

        greeks_status = {
            'greeks_limits': {
                'max_portfolio_delta': float(self.greeks_limits.max_portfolio_delta),
                'max_portfolio_vega': float(self.greeks_limits.max_portfolio_vega),
                'max_portfolio_theta': float(self.greeks_limits.max_portfolio_theta),
                'max_gamma_exposure': float(self.greeks_limits.max_gamma_exposure),
                'max_individual_position_delta': float(self.greeks_limits.max_individual_position_delta),
            },
            'current_position_count': self._current_position_count,
            'max_positions': self.max_positions,
            'min_buying_power_reserve': self.min_buying_power_reserve,
            'greeks_history_size': len(self._greeks_history),
        }

        return {**base_status, **greeks_status}
