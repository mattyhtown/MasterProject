"""
APEX-SHARPE Configuration — frozen dataclass configs with env overrides.

All pipeline settings in one place. Each agent gets its own sub-config.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List


# ---------------------------------------------------------------------------
# .env loader (stdlib only — no python-dotenv dependency)
# ---------------------------------------------------------------------------

def _load_env() -> None:
    """Load .env from apex_sharpe root into os.environ (setdefault)."""
    env_file = Path(__file__).resolve().parent / ".env"
    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    os.environ.setdefault(key.strip(), val.strip())


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


# ---------------------------------------------------------------------------
# Sub-configs (frozen)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OratsCfg:
    token: str = ""
    base_url: str = "https://api.orats.io/datav2"
    timeout: int = 30


@dataclass(frozen=True)
class SupabaseCfg:
    url: str = ""
    key: str = ""
    service_key: str = ""


@dataclass(frozen=True)
class IBCfg:
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 4002          # 4001=Gateway live, 4002=Gateway paper
    client_id: int = 1
    account_id: str = ""
    timeout: float = 30.0
    max_positions: int = 10
    order_timeout: int = 60
    paper: bool = True


@dataclass(frozen=True)
class ScannerCfg:
    watchlist: tuple = ("SPY",)
    iv_rank_min: float = 30.0
    dte_min: int = 30
    dte_max: int = 45
    short_delta: float = 0.16
    long_delta: float = 0.05
    delta_tolerance: float = 0.03


@dataclass(frozen=True)
class RiskCfg:
    max_positions: int = 3
    per_trade_risk_pct: float = 0.05
    total_risk_pct: float = 0.15
    credit_width_min: float = 0.15
    account_capital: float = 100000.0


@dataclass(frozen=True)
class ExecutorCfg:
    slippage_pct: float = 0.03
    commission_per_ic: float = 2.60


@dataclass(frozen=True)
class MonitorCfg:
    profit_target_pct: float = 0.50
    dte_warning: int = 25
    dte_exit: int = 21
    delta_warning: float = 0.25
    delta_exit: float = 0.30
    breakeven_buffer: float = 5.0
    loss_warning_pct: float = 0.50
    loss_exit_pct: float = 1.00


@dataclass(frozen=True)
class ZeroDTECfg:
    tickers: tuple = ("SPX", "SPY")
    poll_interval: int = 120
    # Tier 1 thresholds
    iv_rv_thresh: float = -0.08
    skew_change_thresh: float = 0.010
    contango_drop_thresh: float = 0.50
    skewing_thresh: float = 0.05
    rip_thresh: float = 70.0
    credit_thresh: float = -0.005
    # Composite
    core_signals: tuple = ("skewing", "rip", "skew_25d_rr", "contango", "credit_spread")
    composite_min: int = 3
    # Tier 2 thresholds
    fbfwd_high: float = 1.05
    fbfwd_low: float = 0.95
    slope_change_thresh: float = 0.3
    # Tier 3 thresholds
    fwd_kink_thresh: float = 0.01


@dataclass(frozen=True)
class TradeBacktestCfg:
    tickers: tuple = ("SPX", "SPY")
    max_risk: float = 1000.0
    slippage: float = 0.03
    commission_per_leg: float = 0.65
    delta_tol: float = 0.08
    # Delta targets
    call_ds_long: float = 0.40
    call_ds_short: float = 0.25
    bull_ps_short: float = 0.30
    bull_ps_long: float = 0.15
    long_call_delta: float = 0.50
    # Bearish structures
    put_ds_long: float = 0.40       # Buy ~40d put (closer to ATM)
    put_ds_short: float = 0.25      # Sell ~25d put (more OTM)
    long_put_delta: float = 0.50    # Buy ~50d put (ATM)


@dataclass(frozen=True)
class SignalSizingCfg:
    """Signal-weighted position sizing."""
    account_capital: float = 250000.0
    base_risk_pct: float = 0.02             # 2% per trade = $5K base
    multipliers: tuple = (                   # (core_count, multiplier)
        (3, 1.0),                            # 3 signals: $5K
        (4, 1.5),                            # 4 signals: $7.5K
        (5, 2.0),                            # 5 signals: $10K
    )
    max_risk_pct: float = 0.05              # Hard cap: 5% per trade
    max_daily_risk_pct: float = 0.10        # Max 10% daily deployment


@dataclass(frozen=True)
class AdaptiveSelectorCfg:
    """Vol surface → structure selection thresholds."""
    high_iv_rank: float = 50.0              # Above = sell premium
    low_iv_rank: float = 30.0               # Below = buy cheap convexity
    high_skew: float = 0.02                 # Skew threshold for BPS
    strong_signal_min: int = 4              # Signals for CRS/BWB


@dataclass(frozen=True)
class CallRatioSpreadCfg:
    """Call ratio spread (1x2) parameters."""
    long_delta: float = 0.50                # Buy 1x ~50d call
    short_delta: float = 0.25               # Sell 2x ~25d call
    slippage: float = 0.03
    commission_per_leg: float = 0.65
    delta_tol: float = 0.08


@dataclass(frozen=True)
class BrokenWingButterflyCfg:
    """Broken wing butterfly parameters."""
    lower_delta: float = 0.55               # Buy 1 lower call
    middle_delta: float = 0.35              # Sell 2 middle calls
    upper_delta: float = 0.15               # Buy 1 higher call (wider wing)
    slippage: float = 0.03
    commission_per_leg: float = 0.65
    delta_tol: float = 0.08


@dataclass(frozen=True)
class PortfolioCfg:
    """Portfolio-level allocation and risk limits."""
    account_capital: float = 250000.0
    # Tier allocations (fraction of capital)
    treasury_pct: float = 0.50              # 40-60% idle cash in T-bills
    leaps_pct: float = 0.25                 # 20-30% LEAPS/PMCC
    ic_pct: float = 0.12                    # 10-15% iron condors
    directional_pct: float = 0.08           # 5-10% 0DTE directional
    margin_buffer_pct: float = 0.10         # 10% margin reserve
    # Portfolio Greeks limits
    max_portfolio_delta: float = 50.0       # Max net delta
    max_portfolio_gamma: float = 20.0       # Max gamma
    max_portfolio_vega: float = 5000.0      # Max vega exposure
    # Sharpe target
    target_sharpe: float = 2.0


@dataclass(frozen=True)
class LEAPSCfg:
    """LEAPS / Poor Man's Covered Call parameters."""
    target_delta: float = 0.70              # Deep ITM LEAPS call delta
    min_dte: int = 270                      # Minimum 9 months to expiry
    max_dte: int = 540                      # Maximum 18 months
    roll_dte: int = 180                     # Roll LEAPS when DTE < 180
    short_delta: float = 0.30               # OTM short call delta
    short_dte_min: int = 30                 # Minimum 30 DTE for short leg
    short_dte_max: int = 45                 # Maximum 45 DTE for short leg
    short_roll_profit_pct: float = 0.50     # Roll short at 50% profit
    short_roll_dte: int = 21                # Roll short at 21 DTE
    short_roll_delta: float = 0.50          # Roll if delta > 0.50
    max_capital_pct: float = 0.25           # Max 25% of portfolio


@dataclass(frozen=True)
class TaxCfg:
    """Tax optimization parameters."""
    section_1256_lt_pct: float = 0.60       # 60% long-term for 1256
    section_1256_st_pct: float = 0.40       # 40% short-term for 1256
    lt_rate: float = 0.20                   # Long-term cap gains rate
    st_rate: float = 0.37                   # Short-term / ordinary income
    wash_sale_days: int = 30                # Wash sale window
    harvest_threshold: float = -500.0       # Min loss to harvest


@dataclass(frozen=True)
class MarginCfg:
    """Margin calculation parameters."""
    portfolio_margin: bool = True           # PM eligible ($100K+)
    pm_spread_margin_pct: float = 0.15      # PM: 15% of spread notional
    reg_t_spread_margin_pct: float = 1.00   # Reg-T: full width of spread
    buying_power_warning: float = 0.80      # Warn at 80% utilization
    buying_power_max: float = 0.90          # Hard stop at 90%


@dataclass(frozen=True)
class TreasuryCfg:
    """Treasury / idle cash management."""
    min_cash_reserve_pct: float = 0.10      # Always keep 10% liquid
    tbill_yield: float = 0.05              # ~5% annualized
    ladder_intervals: tuple = (4, 8, 13, 26)  # weeks for T-bill ladder


@dataclass(frozen=True)
class PerformanceCfg:
    """Performance monitoring thresholds."""
    baseline_sharpe: float = 1.0
    max_drawdown_pct: float = 0.20
    drift_window: int = 20
    execution_tolerance: float = 1.5

@dataclass(frozen=True)
class LatencyCfg:
    """Latency monitoring thresholds."""
    api_warn_ms: float = 5000.0
    api_crit_ms: float = 10000.0
    staleness_warn_sec: float = 300.0
    benchmark_iterations: int = 3

@dataclass(frozen=True)
class SecurityCfg:
    """Security audit thresholds."""
    max_trades_per_day: int = 10
    max_single_position_pct: float = 0.10
    audit_log_gap_hours: int = 24

@dataclass(frozen=True)
class InfraCfg:
    """Infrastructure health check settings."""
    health_check_timeout_sec: int = 10
    min_disk_gb: float = 1.0


@dataclass(frozen=True)
class HistoricalDataCfg:
    """Historical data archive settings."""
    data_dir: str = ""  # Path to extracted market_data/ directory


@dataclass(frozen=True)
class ResearchCfg:
    """Research agent settings."""
    default_lookback_months: int = 24
    screen_min_days: int = 60
    correlation_min_overlap: int = 120


@dataclass(frozen=True)
class StateCfg:
    positions_path: str = "/Users/mh/positions.json"
    signals_path: str = ""    # resolved at load time
    cache_path: str = ""      # resolved at load time


# ---------------------------------------------------------------------------
# Top-level config
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AppConfig:
    """Top-level config composing all sub-configs."""
    orats: OratsCfg = field(default_factory=OratsCfg)
    supabase: SupabaseCfg = field(default_factory=SupabaseCfg)
    scanner: ScannerCfg = field(default_factory=ScannerCfg)
    risk: RiskCfg = field(default_factory=RiskCfg)
    executor: ExecutorCfg = field(default_factory=ExecutorCfg)
    monitor: MonitorCfg = field(default_factory=MonitorCfg)
    zero_dte: ZeroDTECfg = field(default_factory=ZeroDTECfg)
    trade_backtest: TradeBacktestCfg = field(default_factory=TradeBacktestCfg)
    state: StateCfg = field(default_factory=StateCfg)
    # Phase 1+
    signal_sizing: SignalSizingCfg = field(default_factory=SignalSizingCfg)
    adaptive_selector: AdaptiveSelectorCfg = field(default_factory=AdaptiveSelectorCfg)
    call_ratio_spread: CallRatioSpreadCfg = field(default_factory=CallRatioSpreadCfg)
    broken_wing_butterfly: BrokenWingButterflyCfg = field(default_factory=BrokenWingButterflyCfg)
    # Phase 3+
    portfolio: PortfolioCfg = field(default_factory=PortfolioCfg)
    leaps: LEAPSCfg = field(default_factory=LEAPSCfg)
    tax: TaxCfg = field(default_factory=TaxCfg)
    margin: MarginCfg = field(default_factory=MarginCfg)
    treasury: TreasuryCfg = field(default_factory=TreasuryCfg)
    # Ops agents
    performance: PerformanceCfg = field(default_factory=PerformanceCfg)
    latency: LatencyCfg = field(default_factory=LatencyCfg)
    security: SecurityCfg = field(default_factory=SecurityCfg)
    infra: InfraCfg = field(default_factory=InfraCfg)
    # Research
    historical_data: HistoricalDataCfg = field(default_factory=HistoricalDataCfg)
    research: ResearchCfg = field(default_factory=ResearchCfg)
    # IB
    ib: IBCfg = field(default_factory=IBCfg)


def load_config() -> AppConfig:
    """Load config: read .env, then construct frozen dataclasses."""
    _load_env()

    home = str(Path.home())

    # Auto-detect historical data directory
    data_dir = _env("HISTORICAL_DATA_DIR")
    if not data_dir:
        # Check common locations
        pkg_dir = Path(__file__).resolve().parent.parent
        candidates = [
            pkg_dir / "market_data",
            Path(home) / "market_data",
        ]
        for p in candidates:
            if p.exists() and (p / "data").exists():
                data_dir = str(p)
                break

    return AppConfig(
        orats=OratsCfg(
            token=_env("ORATS_TOKEN"),
        ),
        supabase=SupabaseCfg(
            url=_env("SUPABASE_URL"),
            key=_env("SUPABASE_KEY"),
            service_key=_env("SUPABASE_SERVICE_KEY"),
        ),
        state=StateCfg(
            signals_path=str(Path(home) / "0dte_signals.json"),
            cache_path=str(Path(home) / ".0dte_backtest_cache.json"),
        ),
        historical_data=HistoricalDataCfg(
            data_dir=data_dir,
        ),
        ib=IBCfg(
            enabled=_env("IB_ENABLED", "false").lower() == "true",
            host=_env("IB_HOST", "127.0.0.1"),
            port=int(_env("IB_PORT", "4002")),
            client_id=int(_env("IB_CLIENT_ID", "1")),
            account_id=_env("IB_ACCOUNT"),
            paper=_env("IB_PAPER", "true").lower() == "true",
        ),
    )
