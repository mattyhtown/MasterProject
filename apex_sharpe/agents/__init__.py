"""APEX-SHARPE Trading Agents."""

from .base import BaseAgent
from .scanner import ScannerAgent
from .risk import RiskAgent
from .executor import ExecutorAgent
from .monitor import MonitorAgent
from .reporter import ReporterAgent
from .database import DatabaseAgent
from .zero_dte import ZeroDTEAgent
from .trade_backtest import TradeStructureBacktest
from .portfolio import PortfolioAgent
from .leaps import LEAPSAgent
from .tax import TaxAgent
from .margin import MarginAgent
from .treasury import TreasuryAgent
from .manager import AgentManager
from .ib_executor import IBExecutorAgent
from .ib_sync import IBSyncAgent
from .strategy import (
    StrategyAgentBase,
    CallDebitSpreadAgent,
    BullPutSpreadAgent,
    LongCallAgent,
    CallRatioSpreadAgent,
    BrokenWingButterflyAgent,
)
from .ops import PerformanceAgent, LatencyAgent, SecurityAgent, InfraAgent
from .backtest import ExtendedBacktest, RegimeClassifier
from .research import (
    DataCatalogAgent, ResearchAgent, LibrarianAgent,
    PatternAgent, MacroAgent, StrategyDevAgent,
    NoveltyAgent, DataScoutAgent,
)

__all__ = [
    # Core agents
    "BaseAgent",
    "ScannerAgent",
    "RiskAgent",
    "ExecutorAgent",
    "MonitorAgent",
    "ReporterAgent",
    "DatabaseAgent",
    "ZeroDTEAgent",
    "TradeStructureBacktest",
    # Portfolio management
    "PortfolioAgent",
    "LEAPSAgent",
    "TaxAgent",
    "MarginAgent",
    "TreasuryAgent",
    "AgentManager",
    # Strategy agents
    "StrategyAgentBase",
    "CallDebitSpreadAgent",
    "BullPutSpreadAgent",
    "LongCallAgent",
    "CallRatioSpreadAgent",
    "BrokenWingButterflyAgent",
    # Ops agents
    "PerformanceAgent",
    "LatencyAgent",
    "SecurityAgent",
    "InfraAgent",
    # Backtest agents
    "ExtendedBacktest",
    "RegimeClassifier",
    # IB agents
    "IBExecutorAgent",
    "IBSyncAgent",
    # Research agents
    "DataCatalogAgent",
    "ResearchAgent",
    "LibrarianAgent",
    "PatternAgent",
    "MacroAgent",
    "StrategyDevAgent",
    "NoveltyAgent",
    "DataScoutAgent",
]
