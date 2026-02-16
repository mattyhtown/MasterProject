"""
APEX-SHARPE CLI — unified entry point for all pipeline modes.

Usage:
    python -m apex_sharpe scan            — Find and open new iron condor trades
    python -m apex_sharpe monitor         — Check exits on open positions
    python -m apex_sharpe full            — Both scan + monitor
    python -m apex_sharpe 0dte            — Live 0DTE directional signal monitor
    python -m apex_sharpe 0dte-demo       — Offline demo using recent data
    python -m apex_sharpe 0dte-backtest   — Backtest signals against 6 months
    python -m apex_sharpe 0dte-cron       — Auto-start/stop 0DTE for cron (market hours)
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
    python -m apex_sharpe backtest-ext    — Extended signal backtest (historical)
    python -m apex_sharpe regime          — Regime classification analysis
    python -m apex_sharpe walk-forward    — Walk-forward validation
    python -m apex_sharpe catalog         — Historical data catalog
    python -m apex_sharpe research        — Cross-asset research analysis
    python -m apex_sharpe patterns        — Pattern finding (seasonal, MR, momentum)
    python -m apex_sharpe macro           — Macro dashboard and risk regime
    python -m apex_sharpe strategy-scan   — Scan for new strategy candidates
    python -m apex_sharpe novelty         — Novelty / anomaly / lead-lag discovery
    python -m apex_sharpe scout           — External dataset scouting
    python -m apex_sharpe hierarchy       — Agent hierarchy and division structure
    python -m apex_sharpe ib-status       — IB account status, positions, P&L
    python -m apex_sharpe ib-sync         — Reconcile positions.json with IB
    python -m apex_sharpe ib-history      — Download historical bars from IB
    python -m apex_sharpe ib-chain        — Fetch live option chain from IB
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
  0dte-cron       Auto-start/stop 0DTE monitor (market hours only, for cron)
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

Research & Backtesting:
  backtest-ext    Extended signal backtest on historical data
  regime          VIX/trend regime classification
  walk-forward    Rolling train/test walk-forward validation
  catalog         Historical data catalog and quality check
  research        Cross-asset correlation, drawdown, screening
  patterns        Seasonal, mean reversion, momentum patterns
  macro           Macro dashboard and risk regime detection
  strategy-scan   Scan for new strategy candidates
  novelty         Novelty / anomaly / lead-lag discovery
  scout           External dataset scouting and recommendations
  hierarchy       Agent hierarchy and division structure

Interactive Brokers:
  ib-status       IB account status, positions, P&L
  ib-sync         Reconcile positions.json with IB account
  ib-history      Download historical bars from IB
  ib-chain        Fetch live option chain from IB
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
        ib_client = None
        if config.ib.enabled:
            from .data.ib_client import IBClient
            try:
                ib_client = IBClient(config.ib)
                ib_client.connect()
            except Exception as exc:
                print(f"[IB] Connection failed: {exc} — falling back to simulated execution")
                ib_client = None
        pipeline = ICPipeline(config, orats, state, ib_client=ib_client)
        try:
            if mode == "scan":
                pipeline.run_scan()
            elif mode == "monitor":
                pipeline.run_monitor()
            else:
                pipeline.run_full()
        finally:
            if ib_client:
                ib_client.disconnect()

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

    elif mode == "0dte-cron":
        from .pipelines.zero_dte_pipeline import ZeroDTEPipeline
        ZeroDTEPipeline(config, orats, state).run_live(auto_exit=True)

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

    # -- Research & Backtesting -----------------------------------------------

    elif mode in ("backtest-ext", "backtest-extended"):
        from .data.historical_loader import HistoricalLoader
        from .agents.backtest import ExtendedBacktest
        loader = HistoricalLoader(config.historical_data.data_dir)
        agent = ExtendedBacktest()
        months = int(sys.argv[2]) if len(sys.argv) > 2 else 24
        result = agent.run({
            "action": "signal_history",
            "loader": loader,
            "months": months,
        })
        agent.print_report(result)

    elif mode == "regime":
        from .data.historical_loader import HistoricalLoader
        from .agents.backtest import ExtendedBacktest, RegimeClassifier
        loader = HistoricalLoader(config.historical_data.data_dir)
        agent = ExtendedBacktest()
        months = int(sys.argv[2]) if len(sys.argv) > 2 else 24
        result = agent.run({
            "action": "regime",
            "loader": loader,
            "months": months,
        })
        RegimeClassifier().print_report(result)

    elif mode == "walk-forward":
        from .data.historical_loader import HistoricalLoader
        from .agents.backtest import ExtendedBacktest
        loader = HistoricalLoader(config.historical_data.data_dir)
        agent = ExtendedBacktest()
        months = int(sys.argv[2]) if len(sys.argv) > 2 else 24
        result = agent.run({
            "action": "walk_forward",
            "loader": loader,
            "total_months": months,
        })
        agent.print_walk_forward(result)

    elif mode == "catalog":
        from .data.historical_loader import HistoricalLoader
        from .agents.research import DataCatalogAgent
        loader = HistoricalLoader(config.historical_data.data_dir)
        agent = DataCatalogAgent()
        sub = sys.argv[2] if len(sys.argv) > 2 else "summary"
        if sub == "summary":
            result = agent.run({"action": "summary", "loader": loader})
            agent.print_catalog(result)
        elif sub == "inspect":
            ticker = sys.argv[3] if len(sys.argv) > 3 else "SPY"
            result = agent.run({"action": "inspect", "loader": loader, "ticker": ticker})
            agent.print_inspect(result)
        elif sub == "quality":
            ticker = sys.argv[3] if len(sys.argv) > 3 else "SPY"
            result = agent.run({"action": "quality", "loader": loader, "ticker": ticker})
            agent.print_quality(result)
        elif sub == "search":
            query = sys.argv[3] if len(sys.argv) > 3 else ""
            result = agent.run({"action": "search", "loader": loader, "query": query})
            for r in result.data.get("results", []):
                print(f"  {r['ticker']:<10} {r['asset_class']:<15}"
                      f" {r['rows']:>6} rows  {r['start']} to {r['end']}")
        else:
            print("catalog sub-commands: summary, inspect [ticker], quality [ticker], search [query]")

    elif mode == "research":
        from .data.historical_loader import HistoricalLoader
        from .agents.research import ResearchAgent
        loader = HistoricalLoader(config.historical_data.data_dir)
        agent = ResearchAgent()
        sub = sys.argv[2] if len(sys.argv) > 2 else "correlation"
        if sub == "correlation":
            tickers = sys.argv[3].split(",") if len(sys.argv) > 3 else ["SPY", "QQQ", "IWM", "TLT", "GLD"]
            result = agent.run({"action": "correlation", "loader": loader, "tickers": tickers})
            agent.print_correlation(result)
        elif sub == "returns":
            ticker = sys.argv[3] if len(sys.argv) > 3 else "SPY"
            result = agent.run({"action": "returns", "loader": loader, "ticker": ticker})
            agent.print_returns(result)
        elif sub == "drawdown":
            ticker = sys.argv[3] if len(sys.argv) > 3 else "SPY"
            result = agent.run({"action": "drawdown", "loader": loader, "ticker": ticker})
            d = result.data
            print(f"\n  Drawdown Analysis: {d.get('ticker', '?')}")
            print(f"  Max drawdown: {d.get('max_drawdown_pct', 0):.2f}%")
            print(f"  Current drawdown: {d.get('current_drawdown_pct', 0):.2f}%")
            peak = d.get("max_dd_peak", {})
            trough = d.get("max_dd_trough", {})
            print(f"  Worst: {peak.get('date', '?')} (${peak.get('price', 0):,.2f})"
                  f" to {trough.get('date', '?')} (${trough.get('price', 0):,.2f})")
        elif sub == "compare":
            tickers = sys.argv[3].split(",") if len(sys.argv) > 3 else ["SPY", "QQQ", "IWM"]
            result = agent.run({"action": "compare", "loader": loader, "tickers": tickers})
            agent.print_compare(result)
        elif sub == "screen":
            result = agent.run({
                "action": "screen", "loader": loader,
                "min_sharpe": float(sys.argv[3]) if len(sys.argv) > 3 else 0.5,
            })
            for r in result.data.get("results", [])[:20]:
                print(f"  {r['ticker']:<10} {r['asset_class']:<15}"
                      f" ret {r['return_pct']:>+8.2f}%  vol {r['vol_pct']:>6.2f}%"
                      f"  sharpe {r['sharpe']:>6.3f}")
        else:
            print("research sub-commands: correlation [t1,t2,...], returns [ticker],"
                  " drawdown [ticker], compare [t1,t2,...], screen [min_sharpe]")

    elif mode == "patterns":
        from .data.historical_loader import HistoricalLoader
        from .agents.research import PatternAgent
        loader = HistoricalLoader(config.historical_data.data_dir)
        agent = PatternAgent()
        sub = sys.argv[2] if len(sys.argv) > 2 else "seasonal"
        ticker = sys.argv[3] if len(sys.argv) > 3 else "SPY"
        if sub == "seasonal":
            result = agent.run({"action": "seasonal", "loader": loader, "ticker": ticker})
            agent.print_seasonal(result)
        elif sub in ("mr", "mean-reversion"):
            result = agent.run({"action": "mean_reversion", "loader": loader, "ticker": ticker})
            agent.print_setups(result)
        elif sub == "momentum":
            result = agent.run({"action": "momentum", "loader": loader, "ticker": ticker})
            agent.print_setups(result)
        elif sub in ("events", "post-event"):
            result = agent.run({"action": "post_event", "loader": loader, "ticker": ticker})
            agent.print_setups(result)
        elif sub in ("vol", "vol-clustering"):
            result = agent.run({"action": "vol_clustering", "loader": loader, "ticker": ticker})
            d = result.data
            print(f"\n  Vol Clustering: {d.get('ticker', '?')}")
            print(f"  ACF lag-1: {d.get('abs_return_acf_lag1', 0):.4f}")
            print(f"  Clustering ratio: {d.get('clustering_ratio', 0):.3f}")
            print(f"  Interpretation: {d.get('interpretation', '?')}")
        else:
            print("patterns sub-commands: seasonal [ticker], mr [ticker],"
                  " momentum [ticker], events [ticker], vol [ticker]")

    elif mode == "macro":
        from .data.historical_loader import HistoricalLoader
        from .agents.research import MacroAgent
        loader = HistoricalLoader(config.historical_data.data_dir)
        agent = MacroAgent()
        sub = sys.argv[2] if len(sys.argv) > 2 else "dashboard"
        if sub == "dashboard":
            result = agent.run({"action": "dashboard", "loader": loader})
            agent.print_dashboard(result)
        elif sub in ("risk", "risk-regime"):
            result = agent.run({"action": "risk_regime", "loader": loader})
            agent.print_risk_regime(result)
        elif sub in ("yield", "yield-curve"):
            result = agent.run({"action": "yield_curve", "loader": loader})
            d = result.data
            print(f"\n  Yield Curve Analysis")
            print(f"  Available tenors: {', '.join(d.get('available_tenors', []))}")
            print(f"  Inverted days: {d.get('inverted_days', 0)}"
                  f" ({d.get('inversion_pct', 0):.1f}%)")
            current = d.get("current", {})
            if current:
                print(f"  Current 10y-3m spread: {current.get('spread_10y_3m', 0):.4f}")
        elif sub == "rotation":
            days = int(sys.argv[3]) if len(sys.argv) > 3 else 60
            result = agent.run({"action": "rotation", "loader": loader, "lookback_days": days})
            d = result.data
            print(f"\n  Sector Rotation ({d.get('lookback_days', 60)}d lookback)")
            print(f"  {d.get('leadership', '')}")
            for p in d.get("performances", []):
                clr = "\033[92m" if p["return_pct"] > 0 else "\033[91m"
                print(f"  {p['label']:<18} {p['ticker']:<6}"
                      f" {clr}{p['return_pct']:>+7.2f}%\033[0m"
                      f"  sharpe {p['sharpe']:.3f}")
        elif sub in ("signals", "cross-asset"):
            result = agent.run({"action": "cross_asset", "loader": loader})
            for s in result.data.get("signals", []):
                sig_clr = ("\033[91m" if "DIVERGENCE" in s["signal"]
                           else "\033[93m" if s["signal"] != "NORMAL"
                           else "\033[92m")
                print(f"  {s['pair']:<22} {s['tickers']:<12}"
                      f" z={s['ratio_z_score']:>+5.2f}"
                      f"  corr={s['recent_correlation']:>+6.4f}"
                      f"  {sig_clr}{s['signal']}\033[0m")
        else:
            print("macro sub-commands: dashboard, risk, yield, rotation [days], signals")

    elif mode in ("strategy-scan", "strategy_scan"):
        from .data.historical_loader import HistoricalLoader
        from .agents.research import StrategyDevAgent
        loader = HistoricalLoader(config.historical_data.data_dir)
        agent = StrategyDevAgent()
        ticker = sys.argv[2] if len(sys.argv) > 2 else "SPY"
        result = agent.run({
            "action": "scan_strategies",
            "loader": loader,
            "ticker": ticker,
        })
        agent.print_scan(result)

    elif mode == "novelty":
        from .data.historical_loader import HistoricalLoader
        from .agents.research import NoveltyAgent
        loader = HistoricalLoader(config.historical_data.data_dir)
        agent = NoveltyAgent()
        sub = sys.argv[2] if len(sys.argv) > 2 else "scan"
        if sub == "scan":
            target = sys.argv[3] if len(sys.argv) > 3 else "SPY"
            result = agent.run({
                "action": "scan", "loader": loader, "target": target,
            })
            agent.print_scan(result)
        elif sub == "anomalies":
            ticker = sys.argv[3] if len(sys.argv) > 3 else "SPY"
            result = agent.run({
                "action": "anomalies", "loader": loader, "ticker": ticker,
            })
            d = result.data
            print(f"\n  Anomalies: {d.get('ticker', '?')}")
            for a in d.get("anomalies", [])[:15]:
                print(f"    {a.get('date', '?')} {a.get('type', '?'):<20}"
                      f" z={a.get('z_score', 0):>+6.2f}")
        elif sub in ("lead-lag", "lead_lag"):
            target = sys.argv[3] if len(sys.argv) > 3 else "SPY"
            result = agent.run({
                "action": "lead_lag", "loader": loader, "target": target,
            })
            agent.print_lead_lag(result)
        elif sub in ("regime-breaks", "regime_breaks"):
            ticker = sys.argv[3] if len(sys.argv) > 3 else "SPY"
            result = agent.run({
                "action": "regime_breaks", "loader": loader, "ticker": ticker,
            })
            d = result.data
            print(f"\n  Regime Breaks: {d.get('ticker', '?')}")
            for b in d.get("breaks", []):
                print(f"    {b.get('date', '?')} t-stat={b.get('t_stat', 0):>+6.2f}"
                      f"  before={b.get('mean_before', 0):>+.4f}"
                      f"  after={b.get('mean_after', 0):>+.4f}")
        elif sub in ("hidden-factors", "hidden_factors"):
            ticker = sys.argv[3] if len(sys.argv) > 3 else "SPY"
            result = agent.run({
                "action": "hidden_factors", "loader": loader, "ticker": ticker,
            })
            agent.print_factors(result)
        elif sub == "underexplored":
            result = agent.run({
                "action": "underexplored", "loader": loader,
            })
            for t in result.data.get("underexplored", [])[:20]:
                print(f"  {t['ticker']:<12} {t['asset_class']:<15}"
                      f" sharpe={t['sharpe']:>+6.3f}  vol={t['vol_pct']:>6.1f}%")
        else:
            print("novelty sub-commands: scan [target], anomalies [ticker],"
                  " lead-lag [target], regime-breaks [ticker],"
                  " hidden-factors [ticker], underexplored")

    elif mode == "scout":
        from .agents.research import DataScoutAgent
        agent = DataScoutAgent()
        sub = sys.argv[2] if len(sys.argv) > 2 else "catalog"
        if sub == "catalog":
            result = agent.run({"action": "catalog"})
            agent.print_catalog(result)
        elif sub == "recommend":
            result = agent.run({"action": "recommend"})
            agent.print_recommend(result)
        elif sub == "evaluate":
            source = sys.argv[3] if len(sys.argv) > 3 else "fred_gdp"
            result = agent.run({"action": "evaluate", "source_id": source})
            d = result.data
            print(f"\n  Source: {d.get('name', source)}")
            print(f"  Category: {d.get('category', '?')}")
            print(f"  URL: {d.get('url', '?')}")
            print(f"  Update frequency: {d.get('update_freq', '?')}")
            print(f"  History depth: {d.get('history_depth', '?')}")
            print(f"  Alpha potential: {d.get('alpha_potential', '?')}")
            print(f"  Integration effort: {d.get('integration_effort', '?')}")
        elif sub == "gaps":
            from .data.historical_loader import HistoricalLoader
            loader = HistoricalLoader(config.historical_data.data_dir)
            result = agent.run({"action": "gaps", "loader": loader})
            agent.print_gaps(result)
        else:
            print("scout sub-commands: catalog, recommend, evaluate [source_id], gaps")

    elif mode == "hierarchy":
        from .agents.manager import AgentManager
        mgr = AgentManager()
        mgr.print_hierarchy()

    # -- Interactive Brokers ---------------------------------------------------

    elif mode in ("ib-status", "ib_status"):
        from .data.ib_client import IBClient
        from .agents.ib_sync import IBSyncAgent
        with IBClient(config.ib) as ib:
            agent = IBSyncAgent(ib)
            result = agent.run({"action": "account"})
            agent.print_account(result)

    elif mode in ("ib-sync", "ib_sync"):
        from .data.ib_client import IBClient
        from .agents.ib_sync import IBSyncAgent
        with IBClient(config.ib) as ib:
            agent = IBSyncAgent(ib)
            positions = state.load_positions()
            sub = sys.argv[2] if len(sys.argv) > 2 else "sync"
            if sub == "sync":
                result = agent.run({"action": "sync", "positions": positions})
                agent.print_sync(result)
            elif sub == "reconcile":
                result = agent.run({"action": "reconcile", "positions": positions})
                if result.success:
                    state.save_positions(result.data["positions"])
                    print(f"  Updated {len(result.data['changes'])} positions")
                    for c in result.data["changes"]:
                        print(f"    {c}")
            elif sub == "import":
                result = agent.run({"action": "import"})
                if result.data["count"] > 0:
                    positions.extend(result.data["imported"])
                    state.save_positions(positions)
                    print(f"  Imported {result.data['count']} positions from IB")
                else:
                    print("  No IB positions to import")
            else:
                print("ib-sync sub-commands: sync, reconcile, import")

    elif mode in ("ib-history", "ib_history"):
        from .data.ib_client import IBClient
        ticker = sys.argv[2] if len(sys.argv) > 2 else "SPY"
        duration = sys.argv[3] if len(sys.argv) > 3 else "30 D"
        bar_size = sys.argv[4] if len(sys.argv) > 4 else "1 day"
        with IBClient(config.ib) as ib:
            bars = ib.historical_bars(ticker, duration, bar_size)
            print(f"\n  {ticker} — {len(bars)} bars ({duration}, {bar_size})")
            print(f"  {'Date':<12} {'Open':>8} {'High':>8} {'Low':>8}"
                  f" {'Close':>8} {'Volume':>10}")
            print(f"  {'-' * 60}")
            for b in bars[-20:]:
                print(f"  {b['date']:<12} {b['open']:>8.2f} {b['high']:>8.2f}"
                      f" {b['low']:>8.2f} {b['close']:>8.2f}"
                      f" {b['volume']:>10,}")
            if len(bars) > 20:
                print(f"  ... showing last 20 of {len(bars)} bars")

    elif mode in ("ib-chain", "ib_chain"):
        from .data.ib_client import IBClient
        ticker = sys.argv[2] if len(sys.argv) > 2 else "SPY"
        expiry = sys.argv[3] if len(sys.argv) > 3 else ""
        with IBClient(config.ib) as ib:
            if not expiry:
                params = ib.option_params(ticker)
                exps = params.get("expirations", [])[:5]
                print(f"\n  {ticker} — next 5 expirations: {', '.join(exps)}")
                if exps:
                    expiry = exps[0]
                else:
                    print("  No expirations found")
                    sys.exit(1)
            chain = ib.option_chain(ticker, expiry)
            if chain and chain.get("data"):
                data = chain["data"]
                spot = data[0].get("stockPrice", 0)
                print(f"\n  {ticker} {expiry} — {len(data)} strikes"
                      f"  spot=${spot:.2f}")
                print(f"  {'Strike':>8} {'Delta':>7} {'C.Bid':>7} {'C.Ask':>7}"
                      f" {'P.Bid':>7} {'P.Ask':>7} {'IV':>7}")
                print(f"  {'-' * 52}")
                for row in data:
                    print(f"  {row['strike']:>8.1f}"
                          f" {row.get('delta', 0):>+7.3f}"
                          f" {row.get('callBidPrice', 0):>7.2f}"
                          f" {row.get('callAskPrice', 0):>7.2f}"
                          f" {row.get('putBidPrice', 0):>7.2f}"
                          f" {row.get('putAskPrice', 0):>7.2f}"
                          f" {row.get('callSmvVol', 0):>7.1%}")
            else:
                print(f"  No chain data for {ticker} {expiry}")

    else:
        print(f"Unknown mode: {mode}\n{USAGE}")
        sys.exit(1)


if __name__ == "__main__":
    main()
