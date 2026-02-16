"""
Risk Management System for APEX-SHARPE Trading System.

This module provides comprehensive risk management for options trading,
including Greeks-based limits, position sizing, margin calculations, and
real-time exposure monitoring.

Note: OptionsRiskManager requires CrewTrader on PYTHONPATH.
      Imports are wrapped in try/except for graceful degradation.
"""

__all__: list = []

try:
    from .options_risk_manager import (
        OptionsRiskManager,
        RiskAction,
        OptionsRiskAssessment,
        GreeksLimits,
    )
    __all__ += ['OptionsRiskManager', 'RiskAction', 'OptionsRiskAssessment', 'GreeksLimits']
except ImportError:
    pass

try:
    from .position_sizer import (
        OptionsPositionSizer,
        PositionSizeResult,
    )
    __all__ += ['OptionsPositionSizer', 'PositionSizeResult']
except ImportError:
    pass

try:
    from .margin_calculator import (
        MarginCalculator,
        MarginRequirement,
    )
    __all__ += ['MarginCalculator', 'MarginRequirement']
except ImportError:
    pass

try:
    from .exposure_monitor import (
        ExposureMonitor,
        ExposureSnapshot,
        ExposureAlert,
        AlertLevel,
    )
    __all__ += ['ExposureMonitor', 'ExposureSnapshot', 'ExposureAlert', 'AlertLevel']
except ImportError:
    pass
