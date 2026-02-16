"""
0DTE Pipeline — live monitoring, demo, backtest, and trade structure analysis.

Orchestrates ZeroDTEAgent, TradeStructureBacktest, and DatabaseAgent.
"""

from ..config import AppConfig
from ..data.orats_client import ORATSClient
from ..data.state import StateManager
from ..agents.zero_dte import ZeroDTEAgent
from ..agents.trade_backtest import TradeStructureBacktest
from ..agents.database import DatabaseAgent


class ZeroDTEPipeline:
    """Orchestrate 0DTE signal system across 4 modes."""

    def __init__(self, config: AppConfig, orats: ORATSClient, state: StateManager):
        self.config = config
        self.orats = orats
        self.state = state
        self.agent = ZeroDTEAgent(config.zero_dte)
        self.db = DatabaseAgent(config.supabase)

    def _ensure_tables(self) -> None:
        """Check that 0DTE tables exist in Supabase."""
        if self.db.enabled:
            self.db.run({"action": "ensure_schema", "pipeline": "zero_dte"})

    def run_live(self, auto_exit: bool = False) -> None:
        self._ensure_tables()
        self.agent.run_live(self.orats, self.state, db=self.db,
                            auto_exit=auto_exit)

    def run_demo(self) -> None:
        # Demo is display-only — log signals but don't persist trades
        self._ensure_tables()
        self.agent.run_demo(self.orats)

    def run_backtest(self, months: int = 6) -> None:
        self._ensure_tables()
        self.agent.run_backtest(self.orats, self.state, months=months, db=self.db)

    def run_trade_backtest(self, months: int = 6) -> None:
        self._ensure_tables()
        bt = TradeStructureBacktest(self.config.trade_backtest, self.config.zero_dte)
        bt.run_backtest(self.orats, self.state, months=months, db=self.db)
