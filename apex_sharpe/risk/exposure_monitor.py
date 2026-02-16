"""
Exposure Monitor for APEX-SHARPE Trading System.

Real-time tracking and alerting for portfolio Greeks exposure.
Integrates with Supabase for alert storage and historical tracking.
"""

from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Any
from datetime import datetime, date
from decimal import Decimal
from enum import Enum
# Use relative import for internal APEX-SHARPE module
from ..greeks.greeks_calculator import PortfolioGreeksSnapshot


class AlertLevel(Enum):
    """Alert severity levels."""
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    BREACH = "BREACH"


@dataclass
class ExposureSnapshot:
    """
    Snapshot of current portfolio exposure.

    Attributes:
        timestamp: Snapshot time
        portfolio_delta: Total portfolio delta
        portfolio_gamma: Total portfolio gamma
        portfolio_theta: Total portfolio theta (daily decay)
        portfolio_vega: Total portfolio vega
        portfolio_value: Total portfolio value
        num_positions: Number of open positions
        underlying_price: Current underlying price
        delta_limit_pct: Delta as % of limit
        vega_limit_pct: Vega as % of limit
        theta_limit_pct: Theta as % of limit
        gamma_limit_pct: Gamma as % of limit
    """
    timestamp: datetime
    portfolio_delta: Decimal
    portfolio_gamma: Decimal
    portfolio_theta: Decimal
    portfolio_vega: Decimal
    portfolio_value: Decimal
    num_positions: int
    underlying_price: Decimal

    # Limit utilization percentages (0-100+)
    delta_limit_pct: Decimal = Decimal('0')
    vega_limit_pct: Decimal = Decimal('0')
    theta_limit_pct: Decimal = Decimal('0')
    gamma_limit_pct: Decimal = Decimal('0')

    @classmethod
    def from_portfolio_greeks(
        cls,
        greeks: PortfolioGreeksSnapshot,
        num_positions: int,
        delta_limit: Decimal,
        vega_limit: Decimal,
        theta_limit: Decimal,
        gamma_limit: Decimal,
    ) -> 'ExposureSnapshot':
        """
        Create snapshot from PortfolioGreeksSnapshot.

        Args:
            greeks: Portfolio Greeks snapshot
            num_positions: Number of positions
            delta_limit: Delta limit for percentage calculation
            vega_limit: Vega limit
            theta_limit: Theta limit (negative)
            gamma_limit: Gamma limit

        Returns:
            ExposureSnapshot with calculated limit percentages
        """
        delta_pct = (abs(greeks.total_delta) / delta_limit * Decimal('100')) if delta_limit > 0 else Decimal('0')
        vega_pct = (abs(greeks.total_vega) / vega_limit * Decimal('100')) if vega_limit > 0 else Decimal('0')
        theta_pct = (abs(greeks.total_theta) / abs(theta_limit) * Decimal('100')) if theta_limit != 0 else Decimal('0')
        gamma_pct = (abs(greeks.total_gamma) / gamma_limit * Decimal('100')) if gamma_limit > 0 else Decimal('0')

        return cls(
            timestamp=greeks.timestamp,
            portfolio_delta=greeks.total_delta,
            portfolio_gamma=greeks.total_gamma,
            portfolio_theta=greeks.total_theta,
            portfolio_vega=greeks.total_vega,
            portfolio_value=greeks.total_value,
            num_positions=num_positions,
            underlying_price=greeks.underlying_price,
            delta_limit_pct=delta_pct,
            vega_limit_pct=vega_pct,
            theta_limit_pct=theta_pct,
            gamma_limit_pct=gamma_pct,
        )


@dataclass
class ExposureAlert:
    """
    Alert for exposure threshold breach.

    Attributes:
        alert_id: Unique alert identifier
        timestamp: Alert generation time
        level: Alert severity level
        greek_type: Which Greek triggered the alert
        current_value: Current value of the Greek
        limit_value: Limit that was approached/breached
        utilization_pct: Utilization percentage
        message: Human-readable alert message
        recommendation: Suggested action
        position_count: Number of positions contributing
    """
    alert_id: str
    timestamp: datetime
    level: AlertLevel
    greek_type: str
    current_value: Decimal
    limit_value: Decimal
    utilization_pct: Decimal
    message: str
    recommendation: str
    position_count: int

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage/serialization."""
        return {
            'alert_id': self.alert_id,
            'timestamp': self.timestamp.isoformat(),
            'level': self.level.value,
            'greek_type': self.greek_type,
            'current_value': float(self.current_value),
            'limit_value': float(self.limit_value),
            'utilization_pct': float(self.utilization_pct),
            'message': self.message,
            'recommendation': self.recommendation,
            'position_count': self.position_count,
        }


class ExposureMonitor:
    """
    Real-time exposure monitoring and alerting.

    Tracks portfolio Greeks exposure and generates alerts when
    thresholds are approached or breached.

    Example:
        >>> from decimal import Decimal
        >>> from greeks.greeks_calculator import PortfolioGreeksSnapshot
        >>>
        >>> # Initialize monitor
        >>> monitor = ExposureMonitor(
        ...     delta_limit=Decimal('100'),
        ...     vega_limit=Decimal('1000'),
        ...     theta_limit=Decimal('-500'),
        ...     gamma_limit=Decimal('50'),
        ...     warning_threshold=0.80,  # Alert at 80%
        ...     critical_threshold=0.95   # Critical at 95%
        ... )
        >>>
        >>> # Update with current portfolio Greeks
        >>> greeks = PortfolioGreeksSnapshot(...)
        >>> alerts = monitor.update(greeks, num_positions=5)
        >>>
        >>> # Check for alerts
        >>> for alert in alerts:
        ...     print(f"{alert.level.value}: {alert.message}")
        >>>
        >>> # Get current exposure
        >>> snapshot = monitor.get_current_exposure()
        >>> print(f"Delta: {snapshot.portfolio_delta} ({snapshot.delta_limit_pct}%)")
    """

    def __init__(
        self,
        delta_limit: Decimal,
        vega_limit: Decimal,
        theta_limit: Decimal,
        gamma_limit: Decimal,
        warning_threshold: float = 0.80,
        critical_threshold: float = 0.95,
        store_alerts: bool = True,
        max_history_size: int = 1000,
    ):
        """
        Initialize Exposure Monitor.

        Args:
            delta_limit: Maximum portfolio delta
            vega_limit: Maximum portfolio vega
            theta_limit: Maximum portfolio theta (negative)
            gamma_limit: Maximum portfolio gamma
            warning_threshold: Threshold for warning alerts (0-1)
            critical_threshold: Threshold for critical alerts (0-1)
            store_alerts: Whether to store alerts in history
            max_history_size: Maximum exposure snapshots to retain
        """
        self.delta_limit = delta_limit
        self.vega_limit = vega_limit
        self.theta_limit = theta_limit
        self.gamma_limit = gamma_limit
        self.warning_threshold = Decimal(str(warning_threshold))
        self.critical_threshold = Decimal(str(critical_threshold))
        self.store_alerts = store_alerts
        self.max_history_size = max_history_size

        # State
        self._current_snapshot: Optional[ExposureSnapshot] = None
        self._exposure_history: List[ExposureSnapshot] = []
        self._alert_history: List[ExposureAlert] = []
        self._alert_counter = 0

        # Supabase client (optional, initialized externally)
        self._supabase_client = None

    def set_supabase_client(self, client) -> None:
        """
        Set Supabase client for alert storage.

        Args:
            client: SupabaseClient instance from database.supabase_client
        """
        self._supabase_client = client

    def update(
        self,
        portfolio_greeks: PortfolioGreeksSnapshot,
        num_positions: int,
    ) -> List[ExposureAlert]:
        """
        Update monitor with new portfolio Greeks and check for alerts.

        Args:
            portfolio_greeks: Current portfolio Greeks
            num_positions: Number of open positions

        Returns:
            List of alerts generated (empty if none)
        """
        # Create snapshot
        snapshot = ExposureSnapshot.from_portfolio_greeks(
            greeks=portfolio_greeks,
            num_positions=num_positions,
            delta_limit=self.delta_limit,
            vega_limit=self.vega_limit,
            theta_limit=self.theta_limit,
            gamma_limit=self.gamma_limit,
        )

        self._current_snapshot = snapshot

        # Add to history
        self._exposure_history.append(snapshot)
        if len(self._exposure_history) > self.max_history_size:
            self._exposure_history = self._exposure_history[-self.max_history_size:]

        # Check for alerts
        alerts = self._check_thresholds(snapshot)

        # Store alerts
        if self.store_alerts:
            self._alert_history.extend(alerts)
            if len(self._alert_history) > self.max_history_size:
                self._alert_history = self._alert_history[-self.max_history_size:]

        # Store to Supabase if available
        if self._supabase_client and alerts:
            self._store_alerts_to_supabase(alerts)

        return alerts

    def _check_thresholds(self, snapshot: ExposureSnapshot) -> List[ExposureAlert]:
        """Check all Greeks against thresholds and generate alerts."""
        alerts = []

        # Check delta
        if snapshot.delta_limit_pct >= self.critical_threshold * Decimal('100'):
            alerts.append(self._create_alert(
                level=AlertLevel.CRITICAL,
                greek_type='delta',
                current_value=snapshot.portfolio_delta,
                limit_value=self.delta_limit,
                utilization_pct=snapshot.delta_limit_pct,
                position_count=snapshot.num_positions,
            ))
        elif snapshot.delta_limit_pct >= self.warning_threshold * Decimal('100'):
            alerts.append(self._create_alert(
                level=AlertLevel.WARNING,
                greek_type='delta',
                current_value=snapshot.portfolio_delta,
                limit_value=self.delta_limit,
                utilization_pct=snapshot.delta_limit_pct,
                position_count=snapshot.num_positions,
            ))

        # Check vega
        if snapshot.vega_limit_pct >= self.critical_threshold * Decimal('100'):
            alerts.append(self._create_alert(
                level=AlertLevel.CRITICAL,
                greek_type='vega',
                current_value=snapshot.portfolio_vega,
                limit_value=self.vega_limit,
                utilization_pct=snapshot.vega_limit_pct,
                position_count=snapshot.num_positions,
            ))
        elif snapshot.vega_limit_pct >= self.warning_threshold * Decimal('100'):
            alerts.append(self._create_alert(
                level=AlertLevel.WARNING,
                greek_type='vega',
                current_value=snapshot.portfolio_vega,
                limit_value=self.vega_limit,
                utilization_pct=snapshot.vega_limit_pct,
                position_count=snapshot.num_positions,
            ))

        # Check theta
        if snapshot.theta_limit_pct >= self.critical_threshold * Decimal('100'):
            alerts.append(self._create_alert(
                level=AlertLevel.CRITICAL,
                greek_type='theta',
                current_value=snapshot.portfolio_theta,
                limit_value=self.theta_limit,
                utilization_pct=snapshot.theta_limit_pct,
                position_count=snapshot.num_positions,
            ))
        elif snapshot.theta_limit_pct >= self.warning_threshold * Decimal('100'):
            alerts.append(self._create_alert(
                level=AlertLevel.WARNING,
                greek_type='theta',
                current_value=snapshot.portfolio_theta,
                limit_value=self.theta_limit,
                utilization_pct=snapshot.theta_limit_pct,
                position_count=snapshot.num_positions,
            ))

        # Check gamma
        if snapshot.gamma_limit_pct >= self.critical_threshold * Decimal('100'):
            alerts.append(self._create_alert(
                level=AlertLevel.CRITICAL,
                greek_type='gamma',
                current_value=snapshot.portfolio_gamma,
                limit_value=self.gamma_limit,
                utilization_pct=snapshot.gamma_limit_pct,
                position_count=snapshot.num_positions,
            ))
        elif snapshot.gamma_limit_pct >= self.warning_threshold * Decimal('100'):
            alerts.append(self._create_alert(
                level=AlertLevel.WARNING,
                greek_type='gamma',
                current_value=snapshot.portfolio_gamma,
                limit_value=self.gamma_limit,
                utilization_pct=snapshot.gamma_limit_pct,
                position_count=snapshot.num_positions,
            ))

        # Check for breach (> 100%)
        for greek, pct in [
            ('delta', snapshot.delta_limit_pct),
            ('vega', snapshot.vega_limit_pct),
            ('theta', snapshot.theta_limit_pct),
            ('gamma', snapshot.gamma_limit_pct),
        ]:
            if pct > Decimal('100'):
                alerts.append(self._create_alert(
                    level=AlertLevel.BREACH,
                    greek_type=greek,
                    current_value=getattr(snapshot, f'portfolio_{greek}'),
                    limit_value=getattr(self, f'{greek}_limit'),
                    utilization_pct=pct,
                    position_count=snapshot.num_positions,
                ))

        return alerts

    def _create_alert(
        self,
        level: AlertLevel,
        greek_type: str,
        current_value: Decimal,
        limit_value: Decimal,
        utilization_pct: Decimal,
        position_count: int,
    ) -> ExposureAlert:
        """Create an exposure alert."""
        self._alert_counter += 1
        alert_id = f"ALERT-{datetime.now().strftime('%Y%m%d')}-{self._alert_counter:04d}"

        # Generate message and recommendation
        if level == AlertLevel.BREACH:
            message = f"BREACH: Portfolio {greek_type.upper()} ({current_value:.1f}) exceeds limit ({limit_value:.1f})"
            recommendation = f"IMMEDIATE ACTION REQUIRED: Close positions to reduce {greek_type} exposure"
        elif level == AlertLevel.CRITICAL:
            message = f"CRITICAL: Portfolio {greek_type.upper()} at {utilization_pct:.1f}% of limit"
            recommendation = f"Urgent: Avoid new {greek_type}-increasing positions, consider reducing exposure"
        elif level == AlertLevel.WARNING:
            message = f"WARNING: Portfolio {greek_type.upper()} at {utilization_pct:.1f}% of limit"
            recommendation = f"Caution: Monitor {greek_type} exposure closely before new positions"
        else:
            message = f"INFO: Portfolio {greek_type.upper()} update: {current_value:.1f}"
            recommendation = "No action required"

        return ExposureAlert(
            alert_id=alert_id,
            timestamp=datetime.now(),
            level=level,
            greek_type=greek_type,
            current_value=current_value,
            limit_value=limit_value,
            utilization_pct=utilization_pct,
            message=message,
            recommendation=recommendation,
            position_count=position_count,
        )

    def _store_alerts_to_supabase(self, alerts: List[ExposureAlert]) -> None:
        """Store alerts to Supabase alerts table."""
        if not self._supabase_client:
            return

        try:
            for alert in alerts:
                # Store in alerts table
                alert_data = {
                    'alert_type': 'EXPOSURE',
                    'severity': alert.level.value,
                    'message': alert.message,
                    'details': {
                        'greek_type': alert.greek_type,
                        'current_value': float(alert.current_value),
                        'limit_value': float(alert.limit_value),
                        'utilization_pct': float(alert.utilization_pct),
                        'recommendation': alert.recommendation,
                        'position_count': alert.position_count,
                    },
                    'created_at': alert.timestamp.isoformat(),
                }

                self._supabase_client.client.table('alerts').insert(alert_data).execute()

        except Exception as e:
            # Log error but don't fail monitoring
            print(f"Error storing alerts to Supabase: {e}")

    def get_current_exposure(self) -> Optional[ExposureSnapshot]:
        """Get the most recent exposure snapshot."""
        return self._current_snapshot

    def get_exposure_history(
        self,
        hours: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[ExposureSnapshot]:
        """
        Get historical exposure snapshots.

        Args:
            hours: Only return snapshots from last N hours
            limit: Maximum number of snapshots to return

        Returns:
            List of exposure snapshots (most recent last)
        """
        history = self._exposure_history

        if hours:
            cutoff = datetime.now().timestamp() - (hours * 3600)
            history = [s for s in history if s.timestamp.timestamp() >= cutoff]

        if limit:
            history = history[-limit:]

        return history

    def get_alerts(
        self,
        level: Optional[AlertLevel] = None,
        greek_type: Optional[str] = None,
        hours: Optional[int] = None,
    ) -> List[ExposureAlert]:
        """
        Get historical alerts with optional filtering.

        Args:
            level: Filter by alert level
            greek_type: Filter by Greek type
            hours: Only return alerts from last N hours

        Returns:
            List of alerts matching filters
        """
        alerts = self._alert_history

        if level:
            alerts = [a for a in alerts if a.level == level]

        if greek_type:
            alerts = [a for a in alerts if a.greek_type == greek_type]

        if hours:
            cutoff = datetime.now().timestamp() - (hours * 3600)
            alerts = [a for a in alerts if a.timestamp.timestamp() >= cutoff]

        return alerts

    def get_dashboard_metrics(self) -> Dict[str, Any]:
        """
        Get metrics suitable for dashboard display.

        Returns:
            Dictionary with current exposure and alert summary
        """
        current = self._current_snapshot

        if not current:
            return {
                'status': 'NO_DATA',
                'message': 'No exposure data available',
            }

        # Determine overall status
        max_utilization = max(
            current.delta_limit_pct,
            current.vega_limit_pct,
            current.theta_limit_pct,
            current.gamma_limit_pct,
        )

        if max_utilization >= Decimal('100'):
            status = 'BREACH'
        elif max_utilization >= self.critical_threshold * Decimal('100'):
            status = 'CRITICAL'
        elif max_utilization >= self.warning_threshold * Decimal('100'):
            status = 'WARNING'
        else:
            status = 'HEALTHY'

        # Recent alerts
        recent_alerts = self.get_alerts(hours=24)
        critical_alerts = [a for a in recent_alerts if a.level in (AlertLevel.CRITICAL, AlertLevel.BREACH)]

        return {
            'status': status,
            'timestamp': current.timestamp.isoformat(),
            'exposure': {
                'delta': {
                    'value': float(current.portfolio_delta),
                    'limit': float(self.delta_limit),
                    'utilization_pct': float(current.delta_limit_pct),
                },
                'vega': {
                    'value': float(current.portfolio_vega),
                    'limit': float(self.vega_limit),
                    'utilization_pct': float(current.vega_limit_pct),
                },
                'theta': {
                    'value': float(current.portfolio_theta),
                    'limit': float(self.theta_limit),
                    'utilization_pct': float(current.theta_limit_pct),
                },
                'gamma': {
                    'value': float(current.portfolio_gamma),
                    'limit': float(self.gamma_limit),
                    'utilization_pct': float(current.gamma_limit_pct),
                },
            },
            'portfolio': {
                'value': float(current.portfolio_value),
                'num_positions': current.num_positions,
                'underlying_price': float(current.underlying_price),
            },
            'alerts': {
                'total_24h': len(recent_alerts),
                'critical_24h': len(critical_alerts),
                'latest': recent_alerts[-1].to_dict() if recent_alerts else None,
            },
            'limits': {
                'warning_threshold': float(self.warning_threshold * Decimal('100')),
                'critical_threshold': float(self.critical_threshold * Decimal('100')),
            },
        }

    def reset_alerts(self) -> None:
        """Clear alert history (useful for testing)."""
        self._alert_history.clear()
        self._alert_counter = 0

    def get_monitor_config(self) -> Dict[str, Any]:
        """Get monitor configuration."""
        return {
            'limits': {
                'delta': float(self.delta_limit),
                'vega': float(self.vega_limit),
                'theta': float(self.theta_limit),
                'gamma': float(self.gamma_limit),
            },
            'thresholds': {
                'warning': float(self.warning_threshold),
                'critical': float(self.critical_threshold),
            },
            'settings': {
                'store_alerts': self.store_alerts,
                'max_history_size': self.max_history_size,
                'supabase_enabled': self._supabase_client is not None,
            },
        }
