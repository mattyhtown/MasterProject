"""
APEX-SHARPE CLI — unified entry point for all pipeline modes.

Usage:
    python -m apex_sharpe scan            — Find and open new iron condor trades
    python -m apex_sharpe monitor         — Check exits on open positions
    python -m apex_sharpe full            — Both scan + monitor
    python -m apex_sharpe 0dte            — Live 0DTE directional signal monitor
    python -m apex_sharpe 0dte-demo       — Offline demo using recent data
    python -m apex_sharpe 0dte-backtest   — Backtest signals against 6 months
    python -m apex_sharpe 0dte-trades     — Backtest trade structures on signals
    python -m apex_sharpe directional     — Live 0DTE with portfolio orchestration
    python -m apex_sharpe backtest-all    — Compare all 5 structures + adaptive
    python -m apex_sharpe leaps           — LEAPS / PMCC management
    python -m apex_sharpe portfolio       — Full portfolio status
    python -m apex_sharpe tax             — Tax summary + optimization
    python -m apex_sharpe margin          — Margin status
    python -m apex_sharpe treasury        — Treasury / idle cash status
    python -m apex_sharpe agents          — Agent registry + capabilities
    python -m apex_sharpe perf            — Strategy performance validation
    python -m apex_sharpe latency         — API latency benchmark
    python -m apex_sharpe security        — Security audit
    python -m apex_sharpe health          — Infrastructure health check
"""

import sys

from .config import load_config
from .data.orats_client import ORATSClient
from .data.state import StateManager


USAGE = """Usage: python -m apex_sharpe <mode>

IC Pipeline:
  scan            Find and open new iron condor trades
  monitor         Check exits on open positions
  full            Both scan + monitor

0DTE Signal Monitor:
  0dte            Live 0DTE directional signal monitor
  0dte-demo       Offline demo using recent historical data
  0dte-backtest   Backtest signals against 6 months of data
  0dte-trades     Backtest trade structures on signal days

Portfolio Management:
  directional     Live 0DTE with portfolio agent orchestration
  backtest-all    Compare all 5+ structures with signal-weighted sizing
  leaps           LEAPS / Poor Man's Covered Call management
  portfolio       Full portfolio status across all tiers
  tax             Tax summary, loss harvesting, wash sale check
  margin          Margin utilization and buying power
  treasury        Idle cash / T-bill ladder status
  agents          Agent registry, capabilities, and checklists

Ops & Infrastructure:
  perf            Strategy performance validation and drift detection
  latency         API latency benchmarking
  security        Security audit (config, positions, permissions)
  health          Infrastructure health check
"""


def main() -> None:
    if len(sys.argv) < 2:
        print(USAGE)
        sys.exit(1)

    mode = sys.argv[1].lower()
    config = load_config()
    orats = ORATSClient(config.orats)
    state = StateManager(config.state)

    # -- IC Pipeline -------------------------------------------------------

    if mode in ("scan", "monitor", "full"):
        from .pipelines.ic_pipeline import ICPipeline
        pipeline = ICPipeline(config, orats, state)
        if mode == "scan":
            pipeline.run_scan()
        elif mode == "monitor":
            pipeline.run_monitor()
        else:
            pipeline.run_full()

    # -- 0DTE Signal Monitor -----------------------------------------------

    elif mode in ("0dte", "0dte-monitor"):
        from .pipelines.zero_dte_pipeline import ZeroDTEPipeline
        ZeroDTEPipeline(config, orats, state).run_live()

    elif mode == "0dte-demo":
        from .pipelines.zero_dte_pipeline import ZeroDTEPipeline
        ZeroDTEPipeline(config, orats, state).run_demo()

    elif mode == "0dte-backtest":
        from .pipelines.zero_dte_pipeline import ZeroDTEPipeline
        ZeroDTEPipeline(config, orats, state).run_backtest()

    elif mode == "0dte-trades":
        from .pipelines.zero_dte_pipeline import ZeroDTEPipeline
        ZeroDTEPipeline(config, orats, state).run_trade_backtest()

    # -- Portfolio Management ----------------------------------------------

    elif mode == "directional":
        from .pipelines.directional_pipeline import DirectionalPipeline
        from .agents.database import DatabaseAgent
        db = DatabaseAgent(config.supabase)
        DirectionalPipeline(config, orats, state).run_live(db=db)

    elif mode in ("backtest-all", "backtest_all"):
        from .agents.trade_backtest import TradeStructureBacktest
        bt = TradeStructureBacktest(
            config.trade_backtest,
            config.zero_dte,
            config.signal_sizing,
            config.adaptive_selector,
            config.call_ratio_spread,
            config.broken_wing_butterfly,
        )
        months = int(sys.argv[2]) if len(sys.argv) > 2 else 6
        bt.run_backtest(orats, state, months=months)

    elif mode == "leaps":
        from .pipelines.leaps_pipeline import LEAPSPipeline
        pipeline = LEAPSPipeline(config, orats, state)
        sub = sys.argv[2] if len(sys.argv) > 2 else "scan"
        ticker = sys.argv[3] if len(sys.argv) > 3 else "SPY"
        if sub == "scan":
            pipeline.run_scan(ticker)
        elif sub == "manage":
            pipeline.run_manage()
        elif sub == "status":
            pipeline.run_status()
        else:
            print(f"leaps sub-commands: scan [ticker], manage, status")

    elif mode == "portfolio":
        from .pipelines.directional_pipeline import DirectionalPipeline
        DirectionalPipeline(config, orats, state).run_portfolio_status()

    elif mode == "tax":
        from .agents.tax import TaxAgent
        agent = TaxAgent(config.tax)
        positions = state.load_positions()
        closed = [p for p in positions if p.get("status") == "CLOSED"]
        sub = sys.argv[2] if len(sys.argv) > 2 else "summary"

        if sub == "summary":
            result = agent.run({
                "action": "summary",
                "positions": positions,
                "closed_ytd": closed,
            })
            if result.success:
                agent.print_summary(result.data)
        elif sub == "harvest":
            result = agent.run({"action": "harvest", "positions": positions})
            data = result.data
            print(f"\n  Loss harvest candidates: {data['count']}")
            for c in data.get("candidates", []):
                print(f"    {c['ticker']} {c['structure']}: "
                      f"${c['unrealized']:+,.0f}")
            print(f"  Total harvestable: ${data['total_harvestable']:+,.0f}")
            print(f"  Tax benefit: ~${data['tax_benefit_est']:,.0f}")
        elif sub == "wash":
            result = agent.run({
                "action": "wash_check",
                "positions": positions,
                "closed_ytd": closed,
            })
            data = result.data
            if data["violations"]:
                for v in data["violations"]:
                    print(f"  WARNING: {v['warning']}")
            else:
                print("  No wash sale violations detected")
        else:
            print("tax sub-commands: summary, harvest, wash")

    elif mode == "margin":
        from .agents.margin import MarginAgent
        agent = MarginAgent(config.margin)
        positions = state.load_positions()
        result = agent.run({
            "action": "status",
            "positions": positions,
            "account_capital": config.portfolio.account_capital,
        })
        if result.success:
            agent.print_status(result.data)

    elif mode == "treasury":
        from .agents.treasury import TreasuryAgent
        agent = TreasuryAgent(config.treasury)
        positions = state.load_positions()
        deployed = sum(
            p.get("max_risk", 0) for p in positions
            if p.get("status") == "OPEN"
        )
        result = agent.run({
            "account_capital": config.portfolio.account_capital,
            "deployed": deployed,
            "positions": positions,
        })
        if result.success:
            agent.print_status(result.data)

    elif mode == "agents":
        from .agents.manager import AgentManager
        mgr = AgentManager()
        sub = sys.argv[2] if len(sys.argv) > 2 else "status"
        if sub == "status":
            mgr.print_status()
        elif sub == "capabilities":
            agent_name = sys.argv[3] if len(sys.argv) > 3 else None
            result = mgr.run({
                "action": "capabilities",
                "agent_name": agent_name,
            })
            data = result.data
            if agent_name:
                print(f"\n  Agent: {data['agent']}")
                print(f"  Class: {data['class']}")
                print(f"  Risk:  {data['risk_level']}")
                print(f"  Data:  {', '.join(data['data_sources'])}")
                print(f"  Signals: {', '.join(data['signal_systems'])}")
                print(f"  Actions: {', '.join(data['trade_actions'])}")
                print(f"  Description: {data['description']}")
            else:
                print(f"\n  Data sources: {', '.join(data['all_data_sources'])}")
                print(f"  Signal systems: {', '.join(data['all_signal_systems'])}")
                print(f"  Trade actions: {', '.join(data['all_trade_actions'])}")
        elif sub == "checklist":
            from .agents.manager import PRE_TRADE_CHECKLIST, POST_TRADE_CHECKLIST
            print("\n  Pre-Trade Checklist:")
            for i, (_, desc) in enumerate(PRE_TRADE_CHECKLIST, 1):
                print(f"    {i:>2}. [ ] {desc}")
            print("\n  Post-Trade Checklist:")
            for i, (_, desc) in enumerate(POST_TRADE_CHECKLIST, 1):
                print(f"    {i:>2}. [ ] {desc}")
        else:
            print("agents sub-commands: status, capabilities [name], checklist")

    # -- Ops & Infrastructure -----------------------------------------------

    elif mode == "perf":
        from .agents.ops import PerformanceAgent
        agent = PerformanceAgent(config.performance)
        positions = state.load_positions()
        closed = [p for p in positions if p.get("status") == "CLOSED"]
        result = agent.run({
            "trades": closed,
            "positions": positions,
        })
        agent.print_report(result)

    elif mode == "latency":
        from .agents.ops import LatencyAgent
        agent = LatencyAgent(config.latency)
        result = agent.run({
            "action": "benchmark",
            "orats": orats,
            "iterations": config.latency.benchmark_iterations,
        })
        agent.print_report(result)

    elif mode == "security":
        from .agents.ops import SecurityAgent
        agent = SecurityAgent(config.security)
        positions = state.load_positions()
        result = agent.run({
            "positions": positions,
            "account_capital": config.portfolio.account_capital,
        })
        agent.print_report(result)

    elif mode == "health":
        from .agents.ops import InfraAgent
        agent = InfraAgent(config.infra)
        result = agent.run({"orats": orats})
        agent.print_report(result)

    else:
        print(f"Unknown mode: {mode}\n{USAGE}")
        sys.exit(1)


if __name__ == "__main__":
    main()
