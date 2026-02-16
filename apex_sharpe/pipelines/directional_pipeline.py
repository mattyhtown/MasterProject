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
    PutDebitSpreadAgent, LongPutAgent,
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
    TradeStructure.PUT_DEBIT_SPREAD: PutDebitSpreadAgent,
    TradeStructure.LONG_PUT: LongPutAgent,
}

# Bearish structures for flip logic
BEARISH_STRUCTURES = {
    TradeStructure.PUT_DEBIT_SPREAD,
    TradeStructure.LONG_PUT,
}

BULLISH_STRUCTURES = {
    TradeStructure.CALL_DEBIT_SPREAD,
    TradeStructure.BULL_PUT_SPREAD,
    TradeStructure.LONG_CALL,
    TradeStructure.CALL_RATIO_SPREAD,
    TradeStructure.BROKEN_WING_BUTTERFLY,
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

    def _select_bearish_structure(self, summary: Dict,
                                  core_count: int) -> Optional[TradeStructure]:
        """Select the best bearish structure for a flip.

        Uses AdaptiveSelector but filters to bearish structures only.
        """
        ranked = self.portfolio.selector.select(summary, core_count)
        for structure, reason in ranked:
            if structure in BEARISH_STRUCTURES:
                return structure
        return TradeStructure.PUT_DEBIT_SPREAD  # default fallback

    def _check_flip_signals(self, signals: Dict, composite: str) -> bool:
        """Check if conditions favor a bearish flip.

        Flip triggers when:
          - Composite signal is still firing (fear persists)
          - Skewing is spiking (put demand surging)
          - Contango is collapsing (near-term fear)
        """
        if not composite:
            return False
        skewing = signals.get("skewing", {})
        contango = signals.get("contango", {})
        # Both must be at ACTION level for a flip
        sk_action = skewing.get("level") == "ACTION"
        ct_action = contango.get("level") == "ACTION"
        return sk_action and ct_action

    def run_live(self, db=None) -> None:
        """Live polling with portfolio-aware trade generation + flip logic.

        When a bullish position is open and intraday signals show continued
        selling (skewing spike + contango collapse), the position is marked
        for close and a bearish structure is opened as a flip.
        """
        cfg = self.config.zero_dte
        from ..agents.reporter import send_notification

        print(f"{C.BOLD}{C.CYAN}Directional Pipeline — Live Mode{C.RESET}")
        print(f"  Capital: ${self.config.portfolio.account_capital:,.0f}")
        print(f"  Signal sizing: {self.config.signal_sizing.base_risk_pct:.0%} base")
        print(f"  Tickers: {', '.join(cfg.tickers)}")
        print(f"  Interval: {cfg.poll_interval}s")
        print(f"  Structures: {', '.join(s.value for s in TradeStructure)}")
        print(f"  Flip logic: {C.GREEN}ENABLED{C.RESET} "
              f"(skewing + contango → bearish flip)\n")

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
        # Track intraday directional trades for flip logic
        # {ticker: {"structure": TradeStructure, "direction": "bull"|"bear",
        #           "trade": Dict, "flipped": bool}}
        active_trades: Dict[str, Dict] = {}
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

                    core_count = len([
                        k for k in cfg.core_signals
                        if signals.get(k, {}).get("level") == "ACTION"
                    ])

                    # -- Flip logic: check open bullish positions --
                    active = active_trades.get(ticker)
                    if (active and active["direction"] == "bull"
                            and not active.get("flipped")):
                        # Check if signals now favor bearish flip
                        if self._check_flip_signals(signals, composite):
                            bear_structure = self._select_bearish_structure(
                                summary, core_count)
                            print(f"\n  {C.BOLD}{C.RED}FLIP SIGNAL{C.RESET} "
                                  f"— {ticker} bullish "
                                  f"{active['structure'].value} → "
                                  f"bearish {bear_structure.value}")
                            print(f"    Skewing + contango at ACTION level "
                                  f"→ sell-through expected")

                            # Build bearish trade
                            bear_agent = self._get_strategy_agent(bear_structure)
                            if bear_agent:
                                chain_resp = self.orats.chain(ticker, "")
                                if chain_resp and chain_resp.get("data"):
                                    sizing = self.portfolio.sizer.compute(
                                        core_count)
                                    bear_result = bear_agent.run({
                                        "chain": chain_resp["data"],
                                        "spot": spot,
                                        "risk_budget": sizing["risk_budget"],
                                        "mode": "live",
                                    })
                                    if bear_result.success:
                                        td = bear_result.data
                                        print(
                                            f"  {C.BOLD}{C.RED}FLIP TRADE: "
                                            f"{td['structure']} | "
                                            f"qty={td['fill']['qty']} | "
                                            f"risk=${td['fill']['max_risk']:,.0f}"
                                            f"{C.RESET}")
                                        active_trades[ticker] = {
                                            "structure": bear_structure,
                                            "direction": "bear",
                                            "trade": td,
                                            "flipped": True,
                                        }

                                        send_notification(
                                            title=f"0DTE FLIP: {ticker}",
                                            subtitle=f"→ {bear_structure.value}",
                                            message=f"Bearish flip | "
                                                    f"{core_count} signals",
                                        )

                    # -- Open new position if composite and no active trade --
                    elif composite and ticker not in active_trades:
                        portfolio_result = self.portfolio.run({
                            "signals": {
                                "composite": composite,
                                "core_count": core_count,
                                "firing": t1,
                            },
                            "chain": [],
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
                                        direction = ("bear"
                                                     if structure in BEARISH_STRUCTURES
                                                     else "bull")
                                        active_trades[ticker] = {
                                            "structure": structure,
                                            "direction": direction,
                                            "trade": td,
                                            "flipped": False,
                                        }
                                        print(
                                            f"  {C.BOLD}TRADE: "
                                            f"{td['structure']} | "
                                            f"qty={td['fill']['qty']} | "
                                            f"risk=${td['fill']['max_risk']:,.0f}"
                                            f"{C.RESET}")

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
                        "active_direction": active_trades.get(ticker, {}).get(
                            "direction"),
                    })

                # Portfolio status every 10 polls
                if n % 10 == 0:
                    self.portfolio.print_status()
                    if active_trades:
                        print(f"\n  {C.BOLD}Active directional:{C.RESET}")
                        for tk, at in active_trades.items():
                            flip_tag = " [FLIPPED]" if at.get("flipped") else ""
                            print(f"    {tk}: {at['direction']} "
                                  f"{at['structure'].value}{flip_tag}")

                print(f"\n  {C.DIM}Poll #{n}. Next in {cfg.poll_interval}s "
                      f"(Ctrl+C to stop){C.RESET}")
                time.sleep(cfg.poll_interval)

        except KeyboardInterrupt:
            print(f"\n{C.BOLD}Directional pipeline stopped.{C.RESET}")
            self.portfolio.print_status()
            if active_trades:
                print(f"\n  Active directional at shutdown:")
                for tk, at in active_trades.items():
                    flip_tag = " [FLIPPED]" if at.get("flipped") else ""
                    print(f"    {tk}: {at['direction']} "
                          f"{at['structure'].value}{flip_tag}")

    def run_portfolio_status(self) -> None:
        """Display current portfolio status."""
        positions = self.state.load_positions()
        self.portfolio._rebuild_deployed(positions)
        self.portfolio.print_status()
