"""
Execution Layer for APEX-SHARPE Trading System.

This module provides order execution and position management infrastructure
for options trading with multi-leg spreads.

Note: SpreadBuilder and OptionsPaperBroker may require strategies module.
      FillSimulator is standalone (pure stdlib + Decimal).
"""

__all__: list = []

# FillSimulator is standalone — always available
from .fill_simulator import FillSimulator
__all__ += ['FillSimulator']

try:
    from .spread_builder import SpreadBuilder
    __all__ += ['SpreadBuilder']
except ImportError:
    pass

try:
    from .options_broker import OptionsPaperBroker
    __all__ += ['OptionsPaperBroker']
except ImportError:
    pass

try:
    from .position_tracker import PositionTracker
    __all__ += ['PositionTracker']
except ImportError:
    pass
