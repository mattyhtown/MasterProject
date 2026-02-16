"""
AgentManager — Meta-agent that manages all other agents.

Responsibilities:
  - Registry: Track all available agents and their capabilities
  - Tool access control: Which agents can access which data sources
  - Checklists: Pre-trade, post-trade, and strategy validation checklists
  - Walk-forward validation: Schedule backtests and track strategy decay
  - Agent health: Monitor agent performance and detect degradation
  - Novelty detection: Flag unusual market conditions for review

Every agent has a capability profile:
  - data_sources: which APIs/data it needs (ORATS, yfinance, Supabase, etc.)
  - signal_systems: which signal systems it produces/consumes
  - trade_actions: what trading actions it can take
  - risk_level: low/medium/high (determines approval requirements)
"""

from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Type

from .base import BaseAgent
from ..types import AgentResult, C, SignalSystemType


class RiskLevel(Enum):
    LOW = "low"         # Read-only, monitoring
    MEDIUM = "medium"   # Generates recommendations
    HIGH = "high"       # Can execute trades


class AgentCapability:
    """Capability profile for an agent."""

    def __init__(self, agent_name: str, agent_class: str,
                 data_sources: List[str],
                 signal_systems: List[str],
                 trade_actions: List[str],
                 risk_level: RiskLevel,
                 requires_approval: bool = False,
                 description: str = ""):
        self.agent_name = agent_name
        self.agent_class = agent_class
        self.data_sources = data_sources
        self.signal_systems = signal_systems
        self.trade_actions = trade_actions
        self.risk_level = risk_level
        self.requires_approval = requires_approval
        self.description = description


# Pre-defined agent capability registry
AGENT_REGISTRY: List[AgentCapability] = [
    AgentCapability(
        "Scanner", "ScannerAgent",
        data_sources=["orats_live", "orats_ivrank"],
        signal_systems=[],
        trade_actions=["scan_candidates"],
        risk_level=RiskLevel.LOW,
        description="Scans for iron condor entry candidates",
    ),
    AgentCapability(
        "Risk", "RiskAgent",
        data_sources=["positions"],
        signal_systems=[],
        trade_actions=["approve_reject"],
        risk_level=RiskLevel.MEDIUM,
        description="5-rule risk evaluation for trade approval",
    ),
    AgentCapability(
        "Executor", "ExecutorAgent",
        data_sources=["positions"],
        signal_systems=[],
        trade_actions=["open_position", "close_position"],
        risk_level=RiskLevel.HIGH,
        requires_approval=True,
        description="Simulates trade execution with slippage/commission",
    ),
    AgentCapability(
        "Monitor", "MonitorAgent",
        data_sources=["orats_live", "positions", "yfinance"],
        signal_systems=[],
        trade_actions=["generate_alerts", "close_position"],
        risk_level=RiskLevel.MEDIUM,
        description="Position valuation, Greeks, exit alerts",
    ),
    AgentCapability(
        "ZeroDTE", "ZeroDTEAgent",
        data_sources=["orats_live", "orats_hist", "yfinance"],
        signal_systems=["vol_surface", "credit_market"],
        trade_actions=["generate_signals"],
        risk_level=RiskLevel.LOW,
        description="10-signal 0DTE vol surface monitor",
    ),
    AgentCapability(
        "Portfolio", "PortfolioAgent",
        data_sources=["positions", "orats_live"],
        signal_systems=["vol_surface", "credit_market", "momentum",
                        "mean_reversion", "event_driven", "seasonality",
                        "pairs", "meme", "lstm", "political"],
        trade_actions=["allocate_capital", "size_position", "select_structure"],
        risk_level=RiskLevel.HIGH,
        requires_approval=True,
        description="Top-level portfolio orchestrator across all strategies",
    ),
    AgentCapability(
        "LEAPS", "LEAPSAgent",
        data_sources=["orats_live", "orats_expirations"],
        signal_systems=[],
        trade_actions=["scan_leaps", "roll_short", "roll_leaps"],
        risk_level=RiskLevel.HIGH,
        requires_approval=True,
        description="LEAPS / PMCC position management",
    ),
    AgentCapability(
        "Tax", "TaxAgent",
        data_sources=["positions", "closed_positions"],
        signal_systems=[],
        trade_actions=["harvest_losses", "optimize_instrument"],
        risk_level=RiskLevel.LOW,
        description="Tax optimization, 1256 tracking, wash sale monitoring",
    ),
    AgentCapability(
        "Margin", "MarginAgent",
        data_sources=["positions"],
        signal_systems=[],
        trade_actions=["check_margin", "block_trade"],
        risk_level=RiskLevel.MEDIUM,
        description="SPAN/PM margin calculation and buying power tracking",
    ),
    AgentCapability(
        "Treasury", "TreasuryAgent",
        data_sources=["positions"],
        signal_systems=[],
        trade_actions=["allocate_tbills"],
        risk_level=RiskLevel.LOW,
        description="Idle cash management, T-bill laddering",
    ),
    AgentCapability(
        "Database", "DatabaseAgent",
        data_sources=["supabase"],
        signal_systems=[],
        trade_actions=["log_trade", "query_history"],
        risk_level=RiskLevel.LOW,
        description="Supabase persistence for all pipeline data",
    ),
    AgentCapability(
        "Reporter", "ReporterAgent",
        data_sources=[],
        signal_systems=[],
        trade_actions=["notify"],
        risk_level=RiskLevel.LOW,
        description="Terminal reports and macOS notifications",
    ),
    # Strategy agents
    AgentCapability(
        "CallDebitSpread", "CallDebitSpreadAgent",
        data_sources=["orats_live"],
        signal_systems=["vol_surface"],
        trade_actions=["build_trade"],
        risk_level=RiskLevel.MEDIUM,
        description="Call debit spread structure (buy low Δ, sell high Δ call)",
    ),
    AgentCapability(
        "BullPutSpread", "BullPutSpreadAgent",
        data_sources=["orats_live"],
        signal_systems=["vol_surface"],
        trade_actions=["build_trade"],
        risk_level=RiskLevel.MEDIUM,
        description="Bull put credit spread (sell high Δ, buy low Δ put)",
    ),
    AgentCapability(
        "LongCall", "LongCallAgent",
        data_sources=["orats_live"],
        signal_systems=["vol_surface"],
        trade_actions=["build_trade"],
        risk_level=RiskLevel.MEDIUM,
        description="Long call for directional convexity",
    ),
    AgentCapability(
        "CallRatioSpread", "CallRatioSpreadAgent",
        data_sources=["orats_live"],
        signal_systems=["vol_surface"],
        trade_actions=["build_trade"],
        risk_level=RiskLevel.MEDIUM,
        description="Call ratio spread 1x2 for moderate up-moves",
    ),
    AgentCapability(
        "BrokenWingButterfly", "BrokenWingButterflyAgent",
        data_sources=["orats_live"],
        signal_systems=["vol_surface"],
        trade_actions=["build_trade"],
        risk_level=RiskLevel.MEDIUM,
        description="Broken wing butterfly for price pin targeting",
    ),
    # Ops agents
    AgentCapability(
        "Performance", "PerformanceAgent",
        data_sources=["positions", "closed_positions"],
        signal_systems=[],
        trade_actions=["validate_strategy", "drift_check"],
        risk_level=RiskLevel.LOW,
        description="Strategy performance monitoring and drift detection",
    ),
    AgentCapability(
        "Latency", "LatencyAgent",
        data_sources=["orats_live"],
        signal_systems=[],
        trade_actions=["benchmark", "data_freshness"],
        risk_level=RiskLevel.LOW,
        description="API latency benchmarking and staleness detection",
    ),
    AgentCapability(
        "Security", "SecurityAgent",
        data_sources=["positions", "config"],
        signal_systems=[],
        trade_actions=["audit_config", "audit_positions"],
        risk_level=RiskLevel.LOW,
        description="Configuration auditing and anomaly detection",
    ),
    AgentCapability(
        "Infra", "InfraAgent",
        data_sources=["orats_live", "supabase", "yfinance"],
        signal_systems=[],
        trade_actions=["health_check", "validate_env"],
        risk_level=RiskLevel.LOW,
        description="Infrastructure health checks and deployment readiness",
    ),
    # Backtest agents
    AgentCapability(
        "ExtendedBacktest", "ExtendedBacktest",
        data_sources=["historical_csv"],
        signal_systems=["credit_market", "vol_surface"],
        trade_actions=["backtest_signals", "walk_forward"],
        risk_level=RiskLevel.LOW,
        description="Extended signal backtesting with historical price data",
    ),
    AgentCapability(
        "RegimeClassifier", "RegimeClassifier",
        data_sources=["historical_csv"],
        signal_systems=[],
        trade_actions=["classify_regime", "transition_matrix"],
        risk_level=RiskLevel.LOW,
        description="VIX/trend-based regime classification and analysis",
    ),
    # Research agents
    AgentCapability(
        "DataCatalog", "DataCatalogAgent",
        data_sources=["historical_csv"],
        signal_systems=[],
        trade_actions=["catalog", "inspect", "quality_check"],
        risk_level=RiskLevel.LOW,
        description="Catalogs all available historical data with quality metrics",
    ),
    AgentCapability(
        "Research", "ResearchAgent",
        data_sources=["historical_csv"],
        signal_systems=[],
        trade_actions=["correlation", "drawdown", "screen"],
        risk_level=RiskLevel.LOW,
        description="Cross-asset correlation, return, and drawdown analysis",
    ),
    AgentCapability(
        "Librarian", "LibrarianAgent",
        data_sources=[],
        signal_systems=[],
        trade_actions=["format_report", "research_note"],
        risk_level=RiskLevel.LOW,
        description="Scientific data formatting and report generation",
    ),
    AgentCapability(
        "PatternFinder", "PatternAgent",
        data_sources=["historical_csv"],
        signal_systems=[],
        trade_actions=["seasonal", "mean_reversion", "momentum", "post_event"],
        risk_level=RiskLevel.LOW,
        description="Finds seasonal, mean reversion, and momentum patterns",
    ),
    AgentCapability(
        "Macro", "MacroAgent",
        data_sources=["historical_csv"],
        signal_systems=[],
        trade_actions=["macro_dashboard", "risk_regime", "yield_curve", "rotation"],
        risk_level=RiskLevel.LOW,
        description="Cross-asset macro analysis and risk regime detection",
    ),
    AgentCapability(
        "StrategyDev", "StrategyDevAgent",
        data_sources=["historical_csv"],
        signal_systems=[],
        trade_actions=["scan_strategies", "test_strategy", "compare"],
        risk_level=RiskLevel.LOW,
        description="Develops and validates new trading strategies from data",
    ),
    AgentCapability(
        "Novelty", "NoveltyAgent",
        data_sources=["historical_csv"],
        signal_systems=[],
        trade_actions=["anomaly_scan", "lead_lag", "regime_breaks", "hidden_factors"],
        risk_level=RiskLevel.LOW,
        description="Discovers anomalies, lead/lag relationships, and hidden factors",
    ),
    AgentCapability(
        "DataScout", "DataScoutAgent",
        data_sources=["external_catalogs"],
        signal_systems=[],
        trade_actions=["catalog_external", "recommend_sources", "coverage_gaps"],
        risk_level=RiskLevel.LOW,
        description="Scouts external datasets (climate, conflict, sentiment, macro)",
    ),
]


# -- Strategy checklists ------------------------------------------------

PRE_TRADE_CHECKLIST = [
    ("signal_confirmed", "Composite signal confirmed (3+ core)"),
    ("risk_budget_set", "Signal-weighted risk budget computed"),
    ("structure_selected", "Adaptive structure selected for vol regime"),
    ("margin_checked", "Margin capacity verified"),
    ("correlation_checked", "Correlation discount applied if needed"),
    ("daily_cap_checked", "Daily deployment cap not exceeded"),
    ("greeks_within_limits", "Portfolio Greeks within limits"),
    ("wash_sale_clear", "No wash sale violations"),
    ("tax_optimized", "Tax-optimal instrument selected (SPX > SPY)"),
    ("capital_tier_available", "Directional tier has capacity"),
    ("chain_quality_ok", "Option chain has sufficient liquidity"),
    ("bid_ask_acceptable", "Bid-ask spread within tolerance"),
]

POST_TRADE_CHECKLIST = [
    ("position_logged", "Position logged to state + database"),
    ("greeks_captured", "Greeks snapshot taken"),
    ("stop_loss_set", "Stop loss / exit rules defined"),
    ("notification_sent", "Trade notification sent"),
    ("margin_updated", "Margin utilization updated"),
]

STRATEGY_VALIDATION_CHECKLIST = [
    ("backtest_recent", "Walk-forward backtest within 30 days"),
    ("win_rate_acceptable", "Win rate >= 50% on recent signals"),
    ("sharpe_acceptable", "Strategy Sharpe >= 1.0"),
    ("max_drawdown_ok", "Max drawdown < 20% of allocated capital"),
    ("signal_count_ok", "Sufficient signal days for statistical validity"),
    ("execution_realistic", "Slippage + commission modeled"),
    ("no_data_snooping", "Out-of-sample validation performed"),
]


# -- Agent Hierarchy -------------------------------------------------------
# Defines how all agents are organized into divisions, groups, and
# reporting chains.  The hierarchy is the single source of truth for
# the org chart printed by `python -m apex_sharpe hierarchy`.

AGENT_HIERARCHY = {
    "name": "AgentManager",
    "role": "Meta-orchestrator",
    "description": "Top-level coordinator — registry, checklists, hierarchy",
    "divisions": [
        {
            "name": "Trading",
            "lead": "Portfolio",
            "description": "Signal generation, trade execution, position management",
            "groups": [
                {
                    "name": "Signal Generation",
                    "agents": ["Scanner", "ZeroDTE"],
                    "description": "Scan markets and generate trade signals",
                },
                {
                    "name": "Strategy Selection",
                    "agents": [
                        "CallDebitSpread", "BullPutSpread", "LongCall",
                        "CallRatioSpread", "BrokenWingButterfly",
                    ],
                    "description": "Build optimal trade structures per signal",
                },
                {
                    "name": "Execution",
                    "agents": ["Risk", "Executor"],
                    "description": "Risk-gate and execute approved trades",
                },
                {
                    "name": "Position Management",
                    "agents": ["Monitor", "LEAPS"],
                    "description": "Track open positions and manage exits",
                },
            ],
        },
        {
            "name": "Portfolio",
            "lead": "Portfolio",
            "description": "Capital allocation, margin, tax, treasury",
            "groups": [
                {
                    "name": "Capital Management",
                    "agents": ["Portfolio", "Margin", "Treasury"],
                    "description": "Allocate capital across tiers and manage reserves",
                },
                {
                    "name": "Tax & Compliance",
                    "agents": ["Tax"],
                    "description": "Tax optimization, wash sale monitoring, 1256 tracking",
                },
            ],
        },
        {
            "name": "Research",
            "lead": "DataCatalog",
            "description": "Data analysis, backtesting, strategy development",
            "groups": [
                {
                    "name": "Data Management",
                    "agents": ["DataCatalog", "DataScout", "Librarian"],
                    "description": "Catalog, format, and discover new data sources",
                },
                {
                    "name": "Analysis",
                    "agents": ["Research", "PatternFinder", "Macro"],
                    "description": "Cross-asset correlation, pattern, and macro analysis",
                },
                {
                    "name": "Backtesting",
                    "agents": ["ExtendedBacktest", "RegimeClassifier"],
                    "description": "Historical signal validation and regime classification",
                },
                {
                    "name": "Discovery",
                    "agents": ["Novelty", "StrategyDev"],
                    "description": "Find new anomalies, signals, and strategy candidates",
                },
            ],
        },
        {
            "name": "Ops",
            "lead": "Infra",
            "description": "Infrastructure health, performance, and security",
            "groups": [
                {
                    "name": "Monitoring",
                    "agents": ["Performance", "Latency"],
                    "description": "Strategy drift detection and API latency tracking",
                },
                {
                    "name": "Security & Infra",
                    "agents": ["Security", "Infra"],
                    "description": "Config auditing, health checks, deployment readiness",
                },
            ],
        },
        {
            "name": "Data Services",
            "lead": "Database",
            "description": "Persistence, notifications, shared services",
            "groups": [
                {
                    "name": "Shared Services",
                    "agents": ["Database", "Reporter"],
                    "description": "Supabase persistence and terminal/macOS notifications",
                },
            ],
        },
    ],
}


class AgentManager(BaseAgent):
    """Meta-agent that manages all other agents.

    Tracks capabilities, enforces checklists, schedules walk-forward
    backtests, and monitors agent health.
    """

    def __init__(self):
        super().__init__("Manager", None)
        self.registry = {cap.agent_name: cap for cap in AGENT_REGISTRY}
        self._validation_status: Dict[str, Dict] = {}
        self._backtest_schedule: Dict[str, str] = {}
        self.hierarchy = AGENT_HIERARCHY

    def run(self, context: Dict[str, Any]) -> AgentResult:
        """Manager operations.

        Context keys:
            action: str — 'status', 'checklist', 'validate', 'capabilities',
                          'hierarchy'
            agent_name: str (for agent-specific queries)
            trade: Dict (for checklist validation)
        """
        action = context.get("action", "status")

        if action == "status":
            return self._full_status()
        elif action == "checklist":
            return self._run_checklist(context)
        elif action == "validate":
            return self._validate_strategy(context)
        elif action == "capabilities":
            agent = context.get("agent_name")
            return self._agent_capabilities(agent)
        elif action == "hierarchy":
            return self._get_hierarchy()
        else:
            return self._result(success=False,
                                errors=[f"Unknown action: {action}"])

    def _full_status(self) -> AgentResult:
        """Full status of all registered agents."""
        agents = []
        for name, cap in sorted(self.registry.items()):
            agents.append({
                "name": name,
                "class": cap.agent_class,
                "risk_level": cap.risk_level.value,
                "requires_approval": cap.requires_approval,
                "data_sources": cap.data_sources,
                "signal_systems": cap.signal_systems,
                "trade_actions": cap.trade_actions,
                "description": cap.description,
            })

        # Summary stats
        by_risk = {}
        for a in agents:
            by_risk.setdefault(a["risk_level"], []).append(a["name"])

        return self._result(
            success=True,
            data={
                "agents": agents,
                "count": len(agents),
                "by_risk_level": by_risk,
                "requiring_approval": [
                    a["name"] for a in agents if a["requires_approval"]],
            },
        )

    def _run_checklist(self, context: Dict) -> AgentResult:
        """Run pre/post trade checklist."""
        checklist_type = context.get("checklist_type", "pre_trade")
        trade = context.get("trade", {})
        results = {}

        checklist = (PRE_TRADE_CHECKLIST if checklist_type == "pre_trade"
                     else POST_TRADE_CHECKLIST)

        passed = 0
        failed = 0
        for check_id, description in checklist:
            # Check if the trade context has this flag set
            status = trade.get(check_id, False)
            results[check_id] = {
                "description": description,
                "passed": bool(status),
            }
            if status:
                passed += 1
            else:
                failed += 1

        return self._result(
            success=failed == 0,
            data={
                "checklist_type": checklist_type,
                "results": results,
                "passed": passed,
                "failed": failed,
                "total": passed + failed,
            },
        )

    def _validate_strategy(self, context: Dict) -> AgentResult:
        """Validate a strategy against the validation checklist."""
        strategy = context.get("strategy", "")
        validation = context.get("validation", {})
        results = {}

        for check_id, description in STRATEGY_VALIDATION_CHECKLIST:
            status = validation.get(check_id, False)
            results[check_id] = {
                "description": description,
                "passed": bool(status),
            }

        passed = sum(1 for r in results.values() if r["passed"])
        failed = len(results) - passed

        return self._result(
            success=failed == 0,
            data={
                "strategy": strategy,
                "results": results,
                "passed": passed,
                "failed": failed,
                "ready": failed == 0,
            },
        )

    def _agent_capabilities(self, agent_name: Optional[str]) -> AgentResult:
        """Get capabilities for a specific agent or all agents."""
        if agent_name:
            cap = self.registry.get(agent_name)
            if not cap:
                return self._result(
                    success=False,
                    errors=[f"Agent '{agent_name}' not registered"])
            return self._result(
                success=True,
                data={
                    "agent": agent_name,
                    "class": cap.agent_class,
                    "data_sources": cap.data_sources,
                    "signal_systems": cap.signal_systems,
                    "trade_actions": cap.trade_actions,
                    "risk_level": cap.risk_level.value,
                    "requires_approval": cap.requires_approval,
                    "description": cap.description,
                },
            )

        # All agents' capabilities
        all_sources = set()
        all_signals = set()
        all_actions = set()
        for cap in self.registry.values():
            all_sources.update(cap.data_sources)
            all_signals.update(cap.signal_systems)
            all_actions.update(cap.trade_actions)

        return self._result(
            success=True,
            data={
                "all_data_sources": sorted(all_sources),
                "all_signal_systems": sorted(all_signals),
                "all_trade_actions": sorted(all_actions),
                "agent_count": len(self.registry),
            },
        )

    def can_access(self, agent_name: str, data_source: str) -> bool:
        """Check if an agent has access to a data source."""
        cap = self.registry.get(agent_name)
        if not cap:
            return False
        return data_source in cap.data_sources

    def requires_approval(self, agent_name: str) -> bool:
        """Check if an agent's actions require human approval."""
        cap = self.registry.get(agent_name)
        if not cap:
            return True  # Unknown agents require approval
        return cap.requires_approval

    def print_status(self) -> None:
        """Pretty-print agent registry."""
        print(f"\n{C.BOLD}{C.CYAN}{'=' * 74}{C.RESET}")
        print(f"  {C.BOLD}AGENT MANAGER — REGISTRY{C.RESET}")
        print(f"{C.BOLD}{C.CYAN}{'=' * 74}{C.RESET}")

        print(f"\n  {'AGENT':<24} {'RISK':>6} {'APPROVAL':>9} "
              f"{'DATA SOURCES':<25}")
        print(f"  {'-' * 74}")

        for name in sorted(self.registry):
            cap = self.registry[name]
            risk_clr = (C.RED if cap.risk_level == RiskLevel.HIGH
                        else C.YELLOW if cap.risk_level == RiskLevel.MEDIUM
                        else C.GREEN)
            appr = f"{C.RED}YES{C.RESET}" if cap.requires_approval else f"{C.GREEN}no{C.RESET}"
            sources = ", ".join(cap.data_sources[:3])
            if len(cap.data_sources) > 3:
                sources += f" +{len(cap.data_sources)-3}"
            print(f"  {name:<24} {risk_clr}{cap.risk_level.value:>6}{C.RESET} "
                  f"{appr:>18}  {sources:<25}")

        # Checklists
        print(f"\n  {C.BOLD}Pre-Trade Checklist:{C.RESET} "
              f"{len(PRE_TRADE_CHECKLIST)} items")
        print(f"  {C.BOLD}Post-Trade Checklist:{C.RESET} "
              f"{len(POST_TRADE_CHECKLIST)} items")
        print(f"  {C.BOLD}Strategy Validation:{C.RESET} "
              f"{len(STRATEGY_VALIDATION_CHECKLIST)} items")
        print()

    # -- Hierarchy ----------------------------------------------------------

    def _get_hierarchy(self) -> AgentResult:
        """Return the full hierarchy as structured data."""
        total_agents = sum(
            len(a)
            for div in self.hierarchy["divisions"]
            for g in div["groups"]
            for a in [g["agents"]]
        )
        # Deduplicate (Portfolio appears in Trading + Portfolio divisions)
        all_agents = set()
        for div in self.hierarchy["divisions"]:
            for g in div["groups"]:
                all_agents.update(g["agents"])
        return self._result(
            success=True,
            data={
                "hierarchy": self.hierarchy,
                "divisions": len(self.hierarchy["divisions"]),
                "unique_agents": len(all_agents),
                "total_slots": total_agents,
            },
        )

    def print_hierarchy(self) -> None:
        """Pretty-print the full agent hierarchy."""
        h = self.hierarchy
        W = 78

        print(f"\n{C.BOLD}{C.CYAN}{'=' * W}{C.RESET}")
        print(f"  {C.BOLD}APEX-SHARPE AGENT HIERARCHY{C.RESET}")
        print(f"{C.BOLD}{C.CYAN}{'=' * W}{C.RESET}")

        # Top level
        print(f"\n  {C.BOLD}{C.MAGENTA}{h['name']}{C.RESET}"
              f" {C.DIM}({h['role']}){C.RESET}")
        print(f"  {C.DIM}{h['description']}{C.RESET}")

        for div in h["divisions"]:
            # Division header
            lead_cap = self.registry.get(div["lead"])
            lead_risk = ""
            if lead_cap:
                risk_clr = (C.RED if lead_cap.risk_level == RiskLevel.HIGH
                            else C.YELLOW if lead_cap.risk_level == RiskLevel.MEDIUM
                            else C.GREEN)
                lead_risk = f" {risk_clr}[{lead_cap.risk_level.value}]{C.RESET}"

            print(f"\n  {C.BOLD}{C.CYAN}{'─' * W}{C.RESET}")
            print(f"  {C.BOLD}{div['name'].upper()} DIVISION{C.RESET}"
                  f"  {C.DIM}lead: {div['lead']}{C.RESET}{lead_risk}")
            print(f"  {C.DIM}{div['description']}{C.RESET}")

            for group in div["groups"]:
                print(f"\n    {C.BOLD}{C.YELLOW}{group['name']}{C.RESET}"
                      f"  {C.DIM}— {group['description']}{C.RESET}")

                for agent_name in group["agents"]:
                    cap = self.registry.get(agent_name)
                    if not cap:
                        print(f"      {C.RED}? {agent_name} (not registered){C.RESET}")
                        continue

                    risk_clr = (C.RED if cap.risk_level == RiskLevel.HIGH
                                else C.YELLOW if cap.risk_level == RiskLevel.MEDIUM
                                else C.GREEN)
                    appr = f" {C.RED}*{C.RESET}" if cap.requires_approval else ""
                    actions = ", ".join(cap.trade_actions[:3])
                    if len(cap.trade_actions) > 3:
                        actions += f" +{len(cap.trade_actions) - 3}"

                    print(f"      {risk_clr}●{C.RESET} {agent_name:<22}"
                          f" {C.DIM}{actions}{C.RESET}{appr}")

        # Summary
        all_agents = set()
        for div in h["divisions"]:
            for g in div["groups"]:
                all_agents.update(g["agents"])
        unregistered = all_agents - set(self.registry.keys())

        print(f"\n  {C.BOLD}{C.CYAN}{'─' * W}{C.RESET}")
        print(f"  {C.BOLD}Summary{C.RESET}")
        print(f"    Divisions:  {len(h['divisions'])}")
        print(f"    Agents:     {len(all_agents)} unique"
              f" ({len(self.registry)} registered)")

        by_risk = {"low": 0, "medium": 0, "high": 0}
        approval_needed = []
        for name in all_agents:
            cap = self.registry.get(name)
            if cap:
                by_risk[cap.risk_level.value] += 1
                if cap.requires_approval:
                    approval_needed.append(name)

        print(f"    Risk:       {C.GREEN}{by_risk['low']} low{C.RESET}, "
              f"{C.YELLOW}{by_risk['medium']} medium{C.RESET}, "
              f"{C.RED}{by_risk['high']} high{C.RESET}")

        if approval_needed:
            print(f"    Approval:   {C.RED}{', '.join(sorted(approval_needed))}{C.RESET}")

        if unregistered:
            print(f"    {C.RED}Unregistered: {', '.join(sorted(unregistered))}{C.RESET}")

        # Data flow summary
        print(f"\n  {C.BOLD}Data Flow{C.RESET}")
        print(f"    {C.DIM}Signals{C.RESET}    Scanner, ZeroDTE"
              f" {C.DIM}→{C.RESET} Risk {C.DIM}→{C.RESET} Executor"
              f" {C.DIM}→{C.RESET} Monitor")
        print(f"    {C.DIM}Research{C.RESET}   DataCatalog"
              f" {C.DIM}→{C.RESET} Research, Pattern, Macro"
              f" {C.DIM}→{C.RESET} StrategyDev"
              f" {C.DIM}→{C.RESET} Backtest")
        print(f"    {C.DIM}Portfolio{C.RESET}  Portfolio"
              f" {C.DIM}→{C.RESET} Margin, Treasury, Tax")
        print(f"    {C.DIM}Services{C.RESET}   Database, Reporter"
              f" {C.DIM}← all divisions{C.RESET}")
        print()
