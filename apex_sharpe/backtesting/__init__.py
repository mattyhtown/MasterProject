"""
Backtesting Engine for APEX-SHARPE Trading System.

Event-driven backtesting infrastructure for options trading strategies
with comprehensive performance analysis and validation.
"""

from .backtest_engine import (
    BacktestEngine,
    MarketDataEvent,
    ExpirationEvent,
    SignalEvent,
    EventType,
    BacktestConfig
)
from .historical_data_manager import (
    HistoricalDataManager,
    DataCache
)
from .performance_analyzer import (
    PerformanceAnalyzer,
    BacktestResults,
    TradeStatistics,
    GreeksAttribution
)
from .validator import (
    BacktestValidator,
    ValidationMethod,
    ValidationResults,
    WalkForwardConfig
)

__all__ = [
    # Engine
    'BacktestEngine',
    'MarketDataEvent',
    'ExpirationEvent',
    'SignalEvent',
    'EventType',
    'BacktestConfig',

    # Data Management
    'HistoricalDataManager',
    'DataCache',

    # Performance Analysis
    'PerformanceAnalyzer',
    'BacktestResults',
    'TradeStatistics',
    'GreeksAttribution',

    # Validation
    'BacktestValidator',
    'ValidationMethod',
    'ValidationResults',
    'WalkForwardConfig',
]
