"""
DirectionalPipeline — live 0DTE trading with PortfolioAgent orchestration.

Flow:
    ZeroDTEAgent.run(context) -> signals + composite
      |
    PortfolioAgent.run(signals, chain, summary)
      ├── SignalSizer.compute(core_count) -> risk_budget
      ├── AdaptiveSelector.select(summary) -> ranked structures
      └── StrategyAgent.run(chain, spot, risk_budget) -> trade
          |
    DatabaseAgent.log_trade(trade)
    ReporterAgent.report(trade)

This pipeline replaces the flat 0DTE polling with portfolio-aware trading:
  - Signal-weighted sizing
  - Adaptive structure selection
  - Position correlation discount
  - Daily deployment caps
  - Greeks-based portfolio limits
"""

import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from ..agents.zero_dte import ZeroDTEAgent
from ..agents.portfolio import PortfolioAgent
from ..agents.reporter import ReporterAgent
from ..agents.strategy import (
    CallDebitSpreadAgent, BullPutSpreadAgent, LongCallAgent,
    CallRatioSpreadAgent, BrokenWingButterflyAgent,
)
from ..config import AppConfig
from ..data.orats_client import ORATSClient
from ..data.state import StateManager
from ..data.yfinance_client import yf_price, yf_credit
from ..types import C, TradeStructure


# Map structure enum to agent class
STRATEGY_AGENTS = {
    TradeStructure.CALL_DEBIT_SPREAD: CallDebitSpreadAgent,
    TradeStructure.BULL_PUT_SPREAD: BullPutSpreadAgent,
    TradeStructure.LONG_CALL: LongCallAgent,
    TradeStructure.CALL_RATIO_SPREAD: CallRatioSpreadAgent,
    TradeStructure.BROKEN_WING_BUTTERFLY: BrokenWingButterflyAgent,
}


class DirectionalPipeline:
    """Live 0DTE directional trading with portfolio orchestration."""

    def __init__(self, config: AppConfig, orats: ORATSClient,
                 state: StateManager):
        self.config = config
        self.orats = orats
        self.state = state
        self.monitor = ZeroDTEAgent(config.zero_dte)
        self.portfolio = PortfolioAgent(config.portfolio, config.signal_sizing)
        self.reporter = ReporterAgent()
        self._strategy_cache: Dict[TradeStructure, Any] = {}

    def _get_strategy_agent(self, structure: TradeStructure):
        """Get or create a strategy agent for the given structure."""
        if structure not in self._strategy_cache:
            cls = STRATEGY_AGENTS.get(structure)
            if cls is None:
                return None
            if structure == TradeStructure.CALL_RATIO_SPREAD:
                agent = cls(self.config.call_ratio_spread)
            elif structure == TradeStructure.BROKEN_WING_BUTTERFLY:
                agent = cls(self.config.broken_wing_butterfly)
            else:
                agent = cls(self.config.trade_backtest)
            self._strategy_cache[structure] = agent
        return self._strategy_cache[structure]

    def run_live(self, db=None) -> None:
        """Live polling with portfolio-aware trade generation."""
        cfg = self.config.zero_dte
        from ..agents.reporter import send_notification

        print(f"{C.BOLD}{C.CYAN}Directional Pipeline — Live Mode{C.RESET}")
        print(f"  Capital: ${self.config.portfolio.account_capital:,.0f}")
        print(f"  Signal sizing: {self.config.signal_sizing.base_risk_pct:.0%} base")
        print(f"  Tickers: {', '.join(cfg.tickers)}")
        print(f"  Interval: {cfg.poll_interval}s")
        print(f"  Structures: {', '.join(s.value for s in TradeStructure)}\n")

        # Load prev-day summaries for baseline
        for days_back in range(1, 5):
            d = (date.today() - timedelta(days=days_back)).strftime("%Y-%m-%d")
            for ticker in cfg.tickers:
                if ticker in self.monitor.prev_day:
                    continue
                resp = self.orats.hist_summaries(ticker, d)
                if resp and resp.get("data"):
                    self.monitor.prev_day[ticker] = resp["data"][0]
                    print(f"  Loaded prev-day: {ticker} ({d})")

        positions = self.state.load_positions()
        n = 0

        try:
            while True:
                n += 1
                hyg, tlt, hyg_p, tlt_p = yf_credit()
                credit_sig = self.monitor.compute_credit_signal(
                    hyg, tlt, hyg_p, tlt_p)

                for ticker in cfg.tickers:
                    resp = self.orats.summaries(ticker)
                    if not resp or not resp.get("data"):
                        print(f"  {C.RED}No data for {ticker}{C.RESET}")
                        continue

                    summary = resp["data"][0]
                    spot = self.monitor._safe(summary, "stockPrice")
                    spot_yf = yf_price(ticker)

                    if ticker not in self.monitor.baseline:
                        self.monitor.baseline[ticker] = dict(summary)

                    signals = self.monitor.compute_signals(ticker, summary)
                    signals.update(credit_sig)
                    composite, t1 = self.monitor.determine_direction(
                        signals, intraday=True)

                    # Display dashboard
                    self.monitor.print_dashboard(
                        ticker, spot, spot_yf, signals, composite, t1)

                    # If composite signal, engage portfolio agent
                    if composite:
                        core_count = len([
                            k for k in cfg.core_signals
                            if signals.get(k, {}).get("level") == "ACTION"
                        ])

                        portfolio_result = self.portfolio.run({
                            "signals": {
                                "composite": composite,
                                "core_count": core_count,
                                "firing": t1,
                            },
                            "chain": [],  # Will fetch if needed
                            "summary": summary,
                            "positions": positions,
                            "spot": spot,
                            "signal_system": "vol_surface",
                        })

                        if portfolio_result.success:
                            data = portfolio_result.data
                            print(f"\n  {C.BOLD}{C.GREEN}"
                                  f"PORTFOLIO: {data['structure']} "
                                  f"| ${data['risk_budget']:,.0f} risk "
                                  f"| {data['reason']}"
                                  f"{C.RESET}")

                            # Fetch chain for trade construction
                            structure = TradeStructure(data["structure"])
                            agent = self._get_strategy_agent(structure)

                            if agent:
                                chain_resp = self.orats.chain(ticker, "")
                                if chain_resp and chain_resp.get("data"):
                                    chain_data = chain_resp["data"]
                                    trade_result = agent.run({
                                        "chain": chain_data,
                                        "spot": spot,
                                        "risk_budget": data["risk_budget"],
                                        "mode": "live",
                                    })
                                    if trade_result.success:
                                        td = trade_result.data
                                        print(
                                            f"  {C.BOLD}TRADE: "
                                            f"{td['structure']} | "
                                            f"qty={td['fill']['qty']} | "
                                            f"risk=${td['fill']['max_risk']:,.0f}"
                                            f"{C.RESET}")

                            # Notification
                            send_notification(
                                title=f"0DTE: {composite}",
                                subtitle=f"{ticker} — {data['structure']}",
                                message=f"${data['risk_budget']:,.0f} risk | "
                                        f"{core_count} signals",
                            )
                        else:
                            for err in portfolio_result.errors:
                                print(f"  {C.YELLOW}PORTFOLIO: {err}{C.RESET}")

                    # Log signal
                    self.state.append_signal({
                        "timestamp": datetime.now().isoformat(),
                        "ticker": ticker,
                        "spot_orats": spot,
                        "spot_yfinance": spot_yf,
                        "composite": composite,
                        "pipeline": "directional",
                    })

                # Portfolio status every 10 polls
                if n % 10 == 0:
                    self.portfolio.print_status()

                print(f"\n  {C.DIM}Poll #{n}. Next in {cfg.poll_interval}s "
                      f"(Ctrl+C to stop){C.RESET}")
                time.sleep(cfg.poll_interval)

        except KeyboardInterrupt:
            print(f"\n{C.BOLD}Directional pipeline stopped.{C.RESET}")
            self.portfolio.print_status()

    def run_portfolio_status(self) -> None:
        """Display current portfolio status."""
        positions = self.state.load_positions()
        self.portfolio._rebuild_deployed(positions)
        self.portfolio.print_status()
