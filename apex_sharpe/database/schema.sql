-- APEX-SHARPE Trading System - Supabase Database Schema
-- Options trading database with multi-leg positions, Greeks tracking, and performance analytics

-- ============================================================================
-- 1. STRATEGIES TABLE
-- ============================================================================
CREATE TABLE IF NOT EXISTS strategies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL UNIQUE,
    strategy_type VARCHAR(50) NOT NULL, -- 'IV_RANK', 'IRON_CONDOR', 'DELTA_NEUTRAL', etc.
    description TEXT,
    parameters JSONB NOT NULL, -- Strategy-specific parameters
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- 2. POSITIONS TABLE (Multi-leg options positions)
-- ============================================================================
CREATE TABLE IF NOT EXISTS positions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    strategy_id UUID REFERENCES strategies(id) ON DELETE SET NULL,

    -- Position identification
    symbol VARCHAR(10) NOT NULL,
    position_type VARCHAR(50) NOT NULL, -- 'IRON_CONDOR', 'VERTICAL_SPREAD', 'STRANGLE', etc.

    -- Entry details
    entry_date DATE NOT NULL,
    entry_time TIMESTAMPTZ NOT NULL,
    entry_iv_rank DECIMAL(5,2), -- IV rank at entry (0-100)
    entry_dte INTEGER, -- Days to expiration at entry

    -- Premium and P&L
    entry_premium DECIMAL(12,2) NOT NULL, -- Net premium (credit positive, debit negative)
    current_premium DECIMAL(12,2),
    realized_pnl DECIMAL(12,2),
    unrealized_pnl DECIMAL(12,2),

    -- Exit details
    exit_date DATE,
    exit_time TIMESTAMPTZ,
    exit_reason VARCHAR(50), -- 'PROFIT_TARGET', 'STOP_LOSS', 'DTE_THRESHOLD', 'MANUAL'
    exit_dte INTEGER,

    -- Risk metrics
    max_loss DECIMAL(12,2), -- Maximum possible loss
    max_profit DECIMAL(12,2), -- Maximum possible profit
    margin_required DECIMAL(12,2),

    -- Greeks at entry (aggregated across legs)
    entry_delta DECIMAL(8,4),
    entry_gamma DECIMAL(8,4),
    entry_theta DECIMAL(8,4),
    entry_vega DECIMAL(8,4),

    -- Greeks at exit
    exit_delta DECIMAL(8,4),
    exit_gamma DECIMAL(8,4),
    exit_theta DECIMAL(8,4),
    exit_vega DECIMAL(8,4),

    -- Status
    status VARCHAR(20) NOT NULL DEFAULT 'OPEN', -- 'OPEN', 'CLOSED', 'ASSIGNED'
    notes TEXT,

    -- Metadata
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    -- Indexes
    CONSTRAINT positions_status_check CHECK (status IN ('OPEN', 'CLOSED', 'ASSIGNED'))
);

CREATE INDEX idx_positions_symbol ON positions(symbol);
CREATE INDEX idx_positions_status ON positions(status);
CREATE INDEX idx_positions_entry_date ON positions(entry_date);
CREATE INDEX idx_positions_strategy_id ON positions(strategy_id);

-- ============================================================================
-- 3. POSITION_LEGS TABLE (Individual options in a spread)
-- ============================================================================
CREATE TABLE IF NOT EXISTS position_legs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    position_id UUID NOT NULL REFERENCES positions(id) ON DELETE CASCADE,

    -- Option details
    leg_index INTEGER NOT NULL, -- 0, 1, 2, 3 for iron condor
    option_symbol VARCHAR(50), -- OCC symbol if available
    option_type VARCHAR(4) NOT NULL, -- 'CALL', 'PUT'
    strike DECIMAL(10,2) NOT NULL,
    expiration_date DATE NOT NULL,

    -- Position details
    quantity INTEGER NOT NULL, -- Positive for long, negative for short
    action VARCHAR(10) NOT NULL, -- 'BTO', 'STO', 'BTC', 'STC'

    -- Pricing
    entry_price DECIMAL(8,2) NOT NULL, -- Price per contract
    exit_price DECIMAL(8,2),

    -- Greeks at entry
    entry_delta DECIMAL(8,4),
    entry_gamma DECIMAL(8,4),
    entry_theta DECIMAL(8,4),
    entry_vega DECIMAL(8,4),
    entry_iv DECIMAL(6,4), -- Implied volatility (e.g., 0.2450 = 24.50%)

    -- Execution details
    entry_fill_time TIMESTAMPTZ NOT NULL,
    exit_fill_time TIMESTAMPTZ,
    commission DECIMAL(8,2),

    -- Metadata
    created_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT position_legs_action_check CHECK (action IN ('BTO', 'STO', 'BTC', 'STC')),
    CONSTRAINT position_legs_option_type_check CHECK (option_type IN ('CALL', 'PUT'))
);

CREATE INDEX idx_position_legs_position_id ON position_legs(position_id);
CREATE INDEX idx_position_legs_expiration ON position_legs(expiration_date);

-- ============================================================================
-- 4. GREEKS_HISTORY TABLE (Track Greeks evolution over time)
-- ============================================================================
CREATE TABLE IF NOT EXISTS greeks_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    position_id UUID NOT NULL REFERENCES positions(id) ON DELETE CASCADE,

    -- Timestamp
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    trade_date DATE NOT NULL,
    dte INTEGER NOT NULL, -- Days to expiration remaining

    -- Underlying price
    underlying_price DECIMAL(10,2) NOT NULL,

    -- Portfolio Greeks (aggregated)
    portfolio_delta DECIMAL(8,4),
    portfolio_gamma DECIMAL(8,4),
    portfolio_theta DECIMAL(8,4),
    portfolio_vega DECIMAL(8,4),

    -- Position value
    position_value DECIMAL(12,2),
    unrealized_pnl DECIMAL(12,2),

    -- Metadata
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_greeks_history_position_id ON greeks_history(position_id);
CREATE INDEX idx_greeks_history_trade_date ON greeks_history(trade_date);

-- ============================================================================
-- 5. TRADES TABLE (Individual trade executions)
-- ============================================================================
CREATE TABLE IF NOT EXISTS trades (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    position_id UUID REFERENCES positions(id) ON DELETE SET NULL,

    -- Trade identification
    trade_type VARCHAR(20) NOT NULL, -- 'OPEN', 'CLOSE', 'ADJUST'
    order_id VARCHAR(100), -- Broker order ID if available

    -- Execution details
    executed_at TIMESTAMPTZ NOT NULL,
    symbol VARCHAR(10) NOT NULL,
    quantity INTEGER NOT NULL,
    fill_price DECIMAL(10,2) NOT NULL,

    -- Costs
    commission DECIMAL(8,2) DEFAULT 0,
    fees DECIMAL(8,2) DEFAULT 0,
    slippage DECIMAL(8,2) DEFAULT 0,

    -- Order details
    order_type VARCHAR(20), -- 'MARKET', 'LIMIT', 'STOP'
    side VARCHAR(10) NOT NULL, -- 'BUY', 'SELL'

    -- Metadata
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT trades_side_check CHECK (side IN ('BUY', 'SELL'))
);

CREATE INDEX idx_trades_position_id ON trades(position_id);
CREATE INDEX idx_trades_executed_at ON trades(executed_at);
CREATE INDEX idx_trades_symbol ON trades(symbol);

-- ============================================================================
-- 6. PERFORMANCE_METRICS TABLE (Daily/periodic performance snapshots)
-- ============================================================================
CREATE TABLE IF NOT EXISTS performance_metrics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    strategy_id UUID REFERENCES strategies(id) ON DELETE SET NULL,

    -- Time period
    date DATE NOT NULL,
    period_type VARCHAR(20) NOT NULL, -- 'DAILY', 'WEEKLY', 'MONTHLY'

    -- Capital metrics
    starting_capital DECIMAL(15,2) NOT NULL,
    ending_capital DECIMAL(15,2) NOT NULL,
    total_pnl DECIMAL(12,2) NOT NULL,

    -- Trade statistics
    num_trades INTEGER DEFAULT 0,
    winning_trades INTEGER DEFAULT 0,
    losing_trades INTEGER DEFAULT 0,
    win_rate DECIMAL(5,2),

    -- Performance ratios
    sharpe_ratio DECIMAL(8,4),
    sortino_ratio DECIMAL(8,4),
    profit_factor DECIMAL(8,4),
    max_drawdown DECIMAL(8,4),

    -- Options-specific metrics
    avg_iv_rank_at_entry DECIMAL(5,2),
    theta_collected DECIMAL(12,2),
    avg_dte_at_entry DECIMAL(5,1),
    avg_dte_at_exit DECIMAL(5,1),

    -- Greeks exposure
    avg_portfolio_delta DECIMAL(8,4),
    max_portfolio_delta DECIMAL(8,4),
    avg_portfolio_vega DECIMAL(8,4),
    max_portfolio_vega DECIMAL(8,4),

    -- Metadata
    created_at TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(strategy_id, date, period_type)
);

CREATE INDEX idx_performance_metrics_strategy_date ON performance_metrics(strategy_id, date);
CREATE INDEX idx_performance_metrics_date ON performance_metrics(date);

-- ============================================================================
-- 7. BACKTEST_RUNS TABLE (Store backtest results)
-- ============================================================================
CREATE TABLE IF NOT EXISTS backtest_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    strategy_id UUID REFERENCES strategies(id) ON DELETE SET NULL,

    -- Backtest configuration
    run_name VARCHAR(200) NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    initial_capital DECIMAL(15,2) NOT NULL,

    -- Parameters used
    strategy_parameters JSONB NOT NULL,

    -- Results
    final_capital DECIMAL(15,2),
    total_return_pct DECIMAL(8,4),
    sharpe_ratio DECIMAL(8,4),
    sortino_ratio DECIMAL(8,4),
    max_drawdown DECIMAL(8,4),

    -- Trade statistics
    total_trades INTEGER,
    winning_trades INTEGER,
    losing_trades INTEGER,
    win_rate DECIMAL(5,2),
    profit_factor DECIMAL(8,4),

    -- Options-specific
    avg_days_in_trade DECIMAL(6,2),
    theta_pnl DECIMAL(12,2),
    vega_pnl DECIMAL(12,2),

    -- Validation results
    validation_type VARCHAR(50), -- 'TRAIN_TEST', 'WALK_FORWARD', 'ROBUSTNESS'
    is_validated BOOLEAN DEFAULT false,
    validation_notes TEXT,

    -- Metadata
    run_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_backtest_runs_strategy_id ON backtest_runs(strategy_id);
CREATE INDEX idx_backtest_runs_run_at ON backtest_runs(run_at);

-- ============================================================================
-- 8. IV_RANK_HISTORY TABLE (Track IV rank over time for analysis)
-- ============================================================================
CREATE TABLE IF NOT EXISTS iv_rank_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Symbol and date
    symbol VARCHAR(10) NOT NULL,
    trade_date DATE NOT NULL,

    -- IV metrics
    current_iv DECIMAL(6,4) NOT NULL,
    iv_rank DECIMAL(5,2) NOT NULL, -- 0-100
    iv_percentile DECIMAL(5,2),

    -- Historical volatility
    hv_10d DECIMAL(6,4),
    hv_30d DECIMAL(6,4),
    hv_60d DECIMAL(6,4),

    -- Underlying price
    underlying_price DECIMAL(10,2),

    -- Metadata
    created_at TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(symbol, trade_date)
);

CREATE INDEX idx_iv_rank_history_symbol_date ON iv_rank_history(symbol, trade_date);
CREATE INDEX idx_iv_rank_history_iv_rank ON iv_rank_history(iv_rank);

-- ============================================================================
-- 9. MARKET_CONDITIONS TABLE (Track broader market conditions)
-- ============================================================================
CREATE TABLE IF NOT EXISTS market_conditions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Date
    trade_date DATE NOT NULL UNIQUE,

    -- Indices
    spx_close DECIMAL(10,2),
    spx_change_pct DECIMAL(6,4),
    vix_close DECIMAL(6,2),
    vix_change_pct DECIMAL(6,4),

    -- Market regime classification
    volatility_regime VARCHAR(20), -- 'LOW', 'MEDIUM', 'HIGH', 'EXTREME'
    trend VARCHAR(20), -- 'BULL', 'BEAR', 'SIDEWAYS'

    -- Metadata
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_market_conditions_date ON market_conditions(trade_date);

-- ============================================================================
-- 10. ALERTS TABLE (Track alerts and notifications)
-- ============================================================================
CREATE TABLE IF NOT EXISTS alerts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    position_id UUID REFERENCES positions(id) ON DELETE SET NULL,

    -- Alert details
    alert_type VARCHAR(50) NOT NULL, -- 'PROFIT_TARGET', 'STOP_LOSS', 'DELTA_BREACH', etc.
    severity VARCHAR(20) NOT NULL, -- 'INFO', 'WARNING', 'CRITICAL'
    message TEXT NOT NULL,

    -- Status
    is_acknowledged BOOLEAN DEFAULT false,
    acknowledged_at TIMESTAMPTZ,

    -- Metadata
    triggered_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT alerts_severity_check CHECK (severity IN ('INFO', 'WARNING', 'CRITICAL'))
);

CREATE INDEX idx_alerts_position_id ON alerts(position_id);
CREATE INDEX idx_alerts_triggered_at ON alerts(triggered_at);
CREATE INDEX idx_alerts_is_acknowledged ON alerts(is_acknowledged);

-- ============================================================================
-- 11. ZERO_DTE_SIGNALS TABLE (0DTE signal snapshots)
-- ============================================================================
CREATE TABLE IF NOT EXISTS zero_dte_signals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Identification
    ticker VARCHAR(10) NOT NULL,
    trade_date DATE NOT NULL,

    -- Price
    spot_price DECIMAL(10,2),

    -- Composite signal
    composite VARCHAR(50),  -- FEAR_BOUNCE_STRONG, MULTI_SIGNAL_STRONG, etc.

    -- Group counts
    core_count INTEGER DEFAULT 0,
    wing_count INTEGER DEFAULT 0,
    fund_count INTEGER DEFAULT 0,
    mom_count INTEGER DEFAULT 0,
    groups_firing INTEGER DEFAULT 0,

    -- Regime
    regime VARCHAR(30),  -- FEAR, NERVOUS, FLAT, COMPLACENT, GREED

    -- Full signal data (20 signals as JSONB)
    signals JSONB,

    -- Metadata
    created_at TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(ticker, trade_date)
);

CREATE INDEX idx_zero_dte_signals_date ON zero_dte_signals(trade_date);
CREATE INDEX idx_zero_dte_signals_composite ON zero_dte_signals(composite);
CREATE INDEX idx_zero_dte_signals_groups ON zero_dte_signals(groups_firing);

-- ============================================================================
-- 12. ZERO_DTE_TRADES TABLE (0DTE trade backtest results)
-- ============================================================================
CREATE TABLE IF NOT EXISTS zero_dte_trades (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Identification
    signal_date DATE NOT NULL,
    ticker VARCHAR(10) NOT NULL,
    structure VARCHAR(50) NOT NULL,

    -- Pricing
    entry_price DECIMAL(10,4),
    exit_price DECIMAL(10,4),
    pnl DECIMAL(10,4),

    -- Spot prices
    spot_at_entry DECIMAL(10,2),
    spot_at_exit DECIMAL(10,2),
    move_pct DECIMAL(8,4),

    -- Signal context
    composite VARCHAR(50),

    -- Metadata
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_zero_dte_trades_date ON zero_dte_trades(signal_date);
CREATE INDEX idx_zero_dte_trades_structure ON zero_dte_trades(structure);
CREATE INDEX idx_zero_dte_trades_composite ON zero_dte_trades(composite);

-- ============================================================================
-- 13. CHAIN_SNAPSHOTS TABLE (Option chain data from IB/ORATS)
-- ============================================================================
CREATE TABLE IF NOT EXISTS chain_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Identification
    ticker VARCHAR(10) NOT NULL,
    snapshot_time TIMESTAMPTZ NOT NULL,
    expir_date DATE NOT NULL,
    strike DECIMAL(10,2) NOT NULL,

    -- Underlying
    stock_price DECIMAL(10,2),

    -- Call side
    call_bid DECIMAL(8,4),
    call_ask DECIMAL(8,4),
    call_mid DECIMAL(8,4),
    call_iv DECIMAL(6,4),
    call_volume INTEGER,
    call_oi INTEGER,

    -- Put side
    put_bid DECIMAL(8,4),
    put_ask DECIMAL(8,4),
    put_mid DECIMAL(8,4),
    put_iv DECIMAL(6,4),
    put_volume INTEGER,
    put_oi INTEGER,

    -- Greeks (call-side by convention, put delta = delta - 1)
    delta DECIMAL(8,4),
    gamma DECIMAL(8,6),
    theta DECIMAL(8,4),
    vega DECIMAL(8,4),

    -- Source
    source VARCHAR(10) NOT NULL DEFAULT 'ib',  -- 'ib' or 'orats'

    -- Metadata
    created_at TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(ticker, snapshot_time, expir_date, strike, source)
);

CREATE INDEX idx_chain_snapshots_ticker_time ON chain_snapshots(ticker, snapshot_time);
CREATE INDEX idx_chain_snapshots_expir ON chain_snapshots(expir_date);
CREATE INDEX idx_chain_snapshots_strike ON chain_snapshots(strike);
CREATE INDEX idx_chain_snapshots_source ON chain_snapshots(source);

-- ============================================================================
-- 14. INTRADAY_BARS TABLE (Price bars from IB)
-- ============================================================================
CREATE TABLE IF NOT EXISTS intraday_bars (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Identification
    ticker VARCHAR(10) NOT NULL,
    bar_time TIMESTAMPTZ NOT NULL,
    bar_size VARCHAR(10) NOT NULL,  -- '1 min', '5 mins', '1 hour', '1 day'

    -- OHLCV
    open DECIMAL(10,2) NOT NULL,
    high DECIMAL(10,2) NOT NULL,
    low DECIMAL(10,2) NOT NULL,
    close DECIMAL(10,2) NOT NULL,
    volume BIGINT DEFAULT 0,
    bar_count INTEGER DEFAULT 0,

    -- Source
    source VARCHAR(10) NOT NULL DEFAULT 'ib',

    -- Metadata
    created_at TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(ticker, bar_time, bar_size, source)
);

CREATE INDEX idx_intraday_bars_ticker_time ON intraday_bars(ticker, bar_time);
CREATE INDEX idx_intraday_bars_bar_size ON intraday_bars(bar_size);
CREATE INDEX idx_intraday_bars_source ON intraday_bars(source);

-- ============================================================================
-- 15. VOL_SURFACE_SNAPSHOTS TABLE (ORATS intraday vol surface data)
-- ============================================================================
CREATE TABLE IF NOT EXISTS vol_surface_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Identification
    ticker VARCHAR(10) NOT NULL,
    snapshot_time TIMESTAMPTZ NOT NULL,
    trade_date DATE NOT NULL,

    -- Spot
    stock_price DECIMAL(10,2),

    -- Vol surface core fields
    iv10d DECIMAL(6,4),
    iv20d DECIMAL(6,4),
    iv30d DECIMAL(6,4),
    iv60d DECIMAL(6,4),
    iv90d DECIMAL(6,4),

    -- Skew and term structure
    skewing DECIMAL(8,4),
    contango DECIMAL(8,4),
    skew_25d_rr DECIMAL(8,4),

    -- Realized vol
    hv10d DECIMAL(6,4),
    hv20d DECIMAL(6,4),
    hv30d DECIMAL(6,4),
    hv60d DECIMAL(6,4),

    -- Forward vol and slopes
    fbfwd DECIMAL(8,4),
    rSlp30 DECIMAL(8,4),
    rDrv30 DECIMAL(8,4),

    -- Wing skew
    dlt25Iv30d DECIMAL(6,4),
    dlt75Iv30d DECIMAL(6,4),
    dlt95Iv30d DECIMAL(6,4),
    dlt5Iv30d DECIMAL(6,4),

    -- Borrow/funding
    borrow30 DECIMAL(8,4),
    borrow2y DECIMAL(8,4),
    riskFree30 DECIMAL(8,4),

    -- IV rank
    iv_rank_1m DECIMAL(5,2),
    iv_pct_1m DECIMAL(5,2),

    -- Full snapshot as JSONB (all fields)
    raw_data JSONB,

    -- Source
    source VARCHAR(10) NOT NULL DEFAULT 'orats',

    -- Metadata
    created_at TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(ticker, snapshot_time, source)
);

CREATE INDEX idx_vol_surface_ticker_time ON vol_surface_snapshots(ticker, snapshot_time);
CREATE INDEX idx_vol_surface_date ON vol_surface_snapshots(trade_date);
CREATE INDEX idx_vol_surface_source ON vol_surface_snapshots(source);

-- ============================================================================
-- FUNCTIONS AND TRIGGERS
-- ============================================================================

-- Function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Triggers for updated_at
CREATE TRIGGER update_strategies_updated_at
    BEFORE UPDATE ON strategies
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_positions_updated_at
    BEFORE UPDATE ON positions
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Function to calculate position P&L
CREATE OR REPLACE FUNCTION calculate_position_pnl(position_uuid UUID)
RETURNS DECIMAL(12,2) AS $$
DECLARE
    total_pnl DECIMAL(12,2);
BEGIN
    SELECT
        COALESCE(SUM(
            CASE
                WHEN quantity > 0 THEN (COALESCE(exit_price, 0) - entry_price) * quantity * 100
                ELSE (entry_price - COALESCE(exit_price, 0)) * ABS(quantity) * 100
            END - COALESCE(commission, 0)
        ), 0)
    INTO total_pnl
    FROM position_legs
    WHERE position_id = position_uuid;

    RETURN total_pnl;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- VIEWS FOR COMMON QUERIES
-- ============================================================================

-- View: Open positions summary
CREATE OR REPLACE VIEW open_positions_summary AS
SELECT
    p.id,
    p.symbol,
    p.position_type,
    p.entry_date,
    p.entry_dte,
    p.entry_premium,
    p.entry_iv_rank,
    p.unrealized_pnl,
    p.entry_delta,
    p.entry_theta,
    COUNT(pl.id) as num_legs,
    s.name as strategy_name
FROM positions p
LEFT JOIN position_legs pl ON p.id = pl.position_id
LEFT JOIN strategies s ON p.strategy_id = s.id
WHERE p.status = 'OPEN'
GROUP BY p.id, s.name
ORDER BY p.entry_date DESC;

-- View: Daily performance summary
CREATE OR REPLACE VIEW daily_performance AS
SELECT
    date,
    SUM(total_pnl) as daily_pnl,
    SUM(num_trades) as trades,
    AVG(win_rate) as avg_win_rate,
    AVG(sharpe_ratio) as avg_sharpe,
    SUM(theta_collected) as theta_collected
FROM performance_metrics
WHERE period_type = 'DAILY'
GROUP BY date
ORDER BY date DESC;

-- View: Strategy performance comparison
CREATE OR REPLACE VIEW strategy_performance_comparison AS
SELECT
    s.name as strategy_name,
    s.strategy_type,
    COUNT(DISTINCT p.id) as total_positions,
    COUNT(DISTINCT CASE WHEN p.status = 'OPEN' THEN p.id END) as open_positions,
    AVG(p.realized_pnl) as avg_pnl,
    SUM(p.realized_pnl) as total_pnl,
    AVG(CASE WHEN p.realized_pnl > 0 THEN 1.0 ELSE 0.0 END) as win_rate,
    AVG(p.entry_iv_rank) as avg_entry_iv_rank
FROM strategies s
LEFT JOIN positions p ON s.id = p.strategy_id
GROUP BY s.id, s.name, s.strategy_type
ORDER BY total_pnl DESC;

-- ============================================================================
-- SAMPLE DATA INSERTS (Optional - for testing)
-- ============================================================================

-- Insert a sample strategy
INSERT INTO strategies (name, strategy_type, description, parameters)
VALUES (
    'Premium Selling - High IV',
    'IV_RANK',
    'Sell premium when IV rank > 50%',
    '{"high_iv_threshold": 50, "target_dte": 35, "profit_target_pct": 0.50}'
);

-- Grant permissions (adjust as needed for your setup)
-- GRANT ALL ON ALL TABLES IN SCHEMA public TO authenticated;
-- GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO authenticated;
