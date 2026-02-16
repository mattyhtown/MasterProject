"""
APEX-SHARPE Trading System.

Advanced Performance EXecution with Sharpe Ratio Adaptive Portfolio Engine.
"""

__version__ = "0.1.0"
__author__ = "APEX-SHARPE Development Team"

# Import main components for easy access
try:
    from . import strategies
    from . import data
    from . import greeks
    from . import backtesting
    from . import database
except ImportError:
    # Allow running without full installation
    pass

__all__ = [
    'strategies',
    'data',
    'greeks',
    'backtesting',
    'database',
]
