"""
APEX-SHARPE Greeks Module

Provides options Greeks calculation and portfolio risk analytics
using FinancePy's Black-Scholes implementation.

Note: Requires financepy package. Imports wrapped for graceful degradation.
"""

__all__: list = []
__version__ = '1.0.0'

try:
    from .greeks_calculator import (
        GreeksCalculator,
        PortfolioGreeksCalculator,
        OptionContract,
        GreeksData,
        PositionGreeks,
        PortfolioGreeksSnapshot,
        OptionType,
        OptionAction,
        calculate_option_greeks,
    )
    __all__ += [
        'GreeksCalculator', 'PortfolioGreeksCalculator',
        'OptionContract', 'GreeksData', 'PositionGreeks', 'PortfolioGreeksSnapshot',
        'OptionType', 'OptionAction', 'calculate_option_greeks',
    ]
except ImportError:
    pass
