# APEX-SHARPE

Multi-agent options trading and research system. 29 agents, 4 pipelines, 27 CLI modes, 217 tests.

## Architecture

```
apex_sharpe/
  agents/           29 agents (BaseAgent ABC pattern)
    ops/            Performance, Latency, Security, Infra
    strategy/       CDS, BPS, LongCall, CRS, BWB
    backtest/       ExtendedBacktest, RegimeClassifier
    research/       DataCatalog, Research, Librarian, Pattern, Macro, StrategyDev
  pipelines/        IC, ZeroDTE, Directional, LEAPS
  data/             ORATSClient, StateManager, HistoricalLoader, yfinance
  selection/        SignalSizer, AdaptiveSelector
  database/         SupabaseSync
  tests/            217 tests
```

### Agent Registry

| Agent | Risk | Description |
|-------|------|-------------|
| Scanner | LOW | Scans for iron condor entry candidates |
| Risk | MEDIUM | 5-rule risk evaluation for trade approval |
| Executor | HIGH | Trade execution with slippage/commission |
| Monitor | MEDIUM | Position valuation, Greeks, exit alerts |
| ZeroDTE | LOW | 10-signal 0DTE vol surface monitor |
| Portfolio | HIGH | Top-level portfolio orchestrator |
| LEAPS | HIGH | LEAPS / PMCC position management |
| Tax | LOW | 1256 tracking, loss harvesting, wash sales |
| Margin | MEDIUM | SPAN/PM margin and buying power |
| Treasury | LOW | Idle cash management, T-bill laddering |
| Database | LOW | Supabase persistence |
| Reporter | LOW | Terminal reports and notifications |
| Manager | LOW | Agent registry and checklists |
| CallDebitSpread | MEDIUM | Call debit spread structure |
| BullPutSpread | MEDIUM | Bull put credit spread |
| LongCall | MEDIUM | Long call for directional convexity |
| CallRatioSpread | MEDIUM | 1x2 call ratio spread |
| BrokenWingButterfly | MEDIUM | BWB for price pin targeting |
| Performance | LOW | Strategy drift detection and Sharpe tracking |
| Latency | LOW | API latency benchmarking |
| Security | LOW | Config auditing and anomaly detection |
| Infra | LOW | Infrastructure health checks |
| ExtendedBacktest | LOW | Extended signal backtesting with historical data |
| RegimeClassifier | LOW | VIX/trend-based regime classification |
| DataCatalog | LOW | Catalogs all historical data with quality metrics |
| Research | LOW | Cross-asset correlation, drawdown, screening |
| Librarian | LOW | Scientific data formatting and report generation |
| PatternFinder | LOW | Seasonal, mean reversion, momentum patterns |
| Macro | LOW | Cross-asset macro analysis and risk regime |
| StrategyDev | LOW | Strategy development and validation |

### 0DTE Signal System

10 signals, 5 core composite. ANY 3 of 5 core signals firing = FEAR_BOUNCE_STRONG (85-88% hit rate).

**Core 5:** skewing, rip, skew_25d_rr, contango, credit_spread (HYG-TLT)

**Supplemental 5:** iv_rv_spread, fbfwd, rSlp30, fwd_kink, rDrv30

### Historical Data Archive

508 tickers across 8 asset classes with full OHLCV + technicals (SMA, RSI, MACD, BB, ATR, volatility). SPY from 1993, VIX from 1990, 20 hourly tickers, 22 economic indicator files.

## Setup

```bash
# Clone
git clone git@github.com:mattyhtown/MasterProject.git
cd MasterProject

# Environment
cp .env.example .env
# Edit .env with your ORATS_TOKEN (required), SUPABASE_URL/KEY (optional)

# Dependencies
pip install -r requirements.txt

# Verify
python -m pytest apex_sharpe/tests/ -v
```

## CLI

```bash
# IC Pipeline
python -m apex_sharpe scan              # Find iron condor candidates
python -m apex_sharpe monitor           # Check exits on open positions
python -m apex_sharpe full              # Both scan + monitor

# 0DTE Signal Monitor
python -m apex_sharpe 0dte              # Live 2-min polling
python -m apex_sharpe 0dte-demo         # Offline demo
python -m apex_sharpe 0dte-backtest     # Backtest 6 months of signals
python -m apex_sharpe 0dte-trades       # Backtest trade structures

# Portfolio Management
python -m apex_sharpe directional       # Live 0DTE with portfolio orchestration
python -m apex_sharpe backtest-all      # Compare all 5 structures + adaptive
python -m apex_sharpe leaps             # LEAPS / PMCC management
python -m apex_sharpe portfolio         # Full portfolio status
python -m apex_sharpe tax               # Tax summary + optimization
python -m apex_sharpe margin            # Margin utilization
python -m apex_sharpe treasury          # Idle cash / T-bill status
python -m apex_sharpe agents            # Agent registry + capabilities

# Ops & Infrastructure
python -m apex_sharpe perf              # Strategy performance validation
python -m apex_sharpe latency           # API latency benchmark
python -m apex_sharpe security          # Security audit
python -m apex_sharpe health            # Infrastructure health check

# Research & Backtesting
python -m apex_sharpe backtest-ext [months]             # Extended signal backtest
python -m apex_sharpe regime [months]                   # Regime classification
python -m apex_sharpe walk-forward [months]             # Walk-forward validation
python -m apex_sharpe catalog summary|inspect|quality   # Data catalog
python -m apex_sharpe research correlation|returns|drawdown|compare|screen
python -m apex_sharpe patterns seasonal|mr|momentum|events|vol [ticker]
python -m apex_sharpe macro dashboard|risk|yield|rotation|signals
python -m apex_sharpe strategy-scan [ticker]            # Strategy development
```

## Docker

```bash
docker build -t apex-sharpe .
docker compose up                       # Runs trading + monitor + health
```

## Stack

- **Python 3.13** — stdlib-first (urllib, json, dataclasses, pathlib)
- **ORATS API** — options data, vol surface, IV rank, historical chains
- **yfinance** — spot price cross-checks
- **Supabase** — optional trade persistence
- **pytest** — 217 tests
