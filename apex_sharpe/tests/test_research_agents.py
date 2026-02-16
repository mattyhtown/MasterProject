"""Tests for research and backtest agents using real historical data."""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from apex_sharpe.agents.backtest import ExtendedBacktest, RegimeClassifier
from apex_sharpe.agents.backtest.regime_classifier import Regime
from apex_sharpe.agents.research import (
    DataCatalogAgent,
    ResearchAgent,
    LibrarianAgent,
    PatternAgent,
    MacroAgent,
    StrategyDevAgent,
)
from apex_sharpe.data.historical_loader import HistoricalLoader


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _find_data_dir():
    """Find the market_data directory."""
    candidates = [
        Path(__file__).resolve().parent.parent.parent / "market_data",
        Path.home() / "market_data",
    ]
    for p in candidates:
        if p.exists() and (p / "data").exists():
            return str(p)
    return None


DATA_DIR = _find_data_dir()
SKIP_NO_DATA = pytest.mark.skipif(
    DATA_DIR is None,
    reason="No market_data directory found"
)


@pytest.fixture
def loader():
    if DATA_DIR is None:
        pytest.skip("No market_data directory found")
    return HistoricalLoader(DATA_DIR)


# ---------------------------------------------------------------------------
# HistoricalLoader
# ---------------------------------------------------------------------------

class TestHistoricalLoader:

    @SKIP_NO_DATA
    def test_load_daily_spy(self, loader):
        data = loader.load_daily("SPY")
        assert len(data) > 5000
        assert data[0]["date"] < "2000-01-01"
        assert data[-1]["date"] > "2025-01-01"

    @SKIP_NO_DATA
    def test_load_daily_with_date_filter(self, loader):
        data = loader.load_daily("SPY", start="2020-01-01", end="2020-12-31")
        assert len(data) > 200
        assert all("2020" in r["date"] for r in data)

    @SKIP_NO_DATA
    def test_load_vix(self, loader):
        vix = loader.load_vix()
        assert len(vix) > 5000
        # VIX values should be reasonable
        for dt, val in list(vix.items())[-10:]:
            assert 5 < val < 90

    @SKIP_NO_DATA
    def test_load_credit_spread(self, loader):
        credit = loader.load_credit_spread("2020-01-01", "2020-12-31")
        assert len(credit) > 200
        for row in credit:
            assert "spread" in row
            assert "spread_change" in row

    @SKIP_NO_DATA
    def test_load_hourly(self, loader):
        hourly = loader.load_hourly("SPY")
        assert len(hourly) > 0
        assert "datetime" in hourly[0]

    @SKIP_NO_DATA
    def test_ticker_mapping_spx(self, loader):
        data = loader.load_daily("SPX")
        assert len(data) > 5000

    @SKIP_NO_DATA
    def test_ticker_mapping_vix(self, loader):
        data = loader.load_daily("VIX")
        assert len(data) > 5000

    @SKIP_NO_DATA
    def test_available_tickers(self, loader):
        available = loader.available_tickers()
        assert "etfs" in available
        assert "stocks/us" in available
        assert len(available["etfs"]) > 40

    @SKIP_NO_DATA
    def test_ticker_info(self, loader):
        info = loader.ticker_info("SPY")
        assert info["found"] is True
        assert info["rows"] > 5000
        assert info["start"] < "2000-01-01"

    @SKIP_NO_DATA
    def test_date_range(self, loader):
        start, end = loader.date_range("SPY")
        assert start < "2000-01-01"
        assert end > "2025-01-01"

    @SKIP_NO_DATA
    def test_unknown_ticker(self, loader):
        data = loader.load_daily("ZZZZZ_DOES_NOT_EXIST")
        assert data == []

    @SKIP_NO_DATA
    def test_crypto_ticker(self, loader):
        data = loader.load_daily("BTC_USD")
        assert len(data) > 100

    @SKIP_NO_DATA
    def test_bond_yields(self, loader):
        data = loader.load_daily("YIELD_TNX")
        assert len(data) > 1000


# ---------------------------------------------------------------------------
# RegimeClassifier
# ---------------------------------------------------------------------------

class TestRegimeClassifier:

    def test_classify_day_low_vol_bull(self):
        rc = RegimeClassifier()
        assert rc.classify_day(450, 420, 15) == Regime.LOW_VOL_BULL

    def test_classify_day_high_vol_bear(self):
        rc = RegimeClassifier()
        assert rc.classify_day(380, 420, 22) == Regime.HIGH_VOL_BEAR

    def test_classify_day_extreme_vol(self):
        rc = RegimeClassifier()
        assert rc.classify_day(450, 420, 35) == Regime.EXTREME_VOL_BULL
        assert rc.classify_day(380, 420, 35) == Regime.EXTREME_VOL_BEAR

    @SKIP_NO_DATA
    def test_classify_with_real_data(self, loader):
        rc = RegimeClassifier()
        spy = loader.load_daily("SPY", "2020-01-01", "2020-12-31")
        vix = loader.load_vix("2020-01-01", "2020-12-31")
        result = rc.run({
            "action": "classify",
            "daily_data": spy,
            "vix_data": vix,
        })
        assert result.success
        assert result.data["total_days"] > 200
        assert len(result.data["counts"]) >= 2  # Should have multiple regimes

    @SKIP_NO_DATA
    def test_regime_analysis(self, loader):
        rc = RegimeClassifier()
        spy = loader.load_daily("SPY", "2020-01-01", "2021-12-31")
        vix = loader.load_vix("2020-01-01", "2021-12-31")
        credit = loader.load_credit_spread("2020-01-01", "2021-12-31")
        result = rc.run({
            "action": "analyze",
            "daily_data": spy,
            "vix_data": vix,
            "credit_data": credit,
        })
        assert result.success
        stats = result.data["regime_stats"]
        assert len(stats) >= 2
        for regime_name, s in stats.items():
            assert "days" in s
            assert "fwd_1d_mean" in s

    @SKIP_NO_DATA
    def test_transition_matrix(self, loader):
        rc = RegimeClassifier()
        spy = loader.load_daily("SPY", "2020-01-01", "2021-12-31")
        vix = loader.load_vix("2020-01-01", "2021-12-31")
        result = rc.run({
            "action": "transitions",
            "daily_data": spy,
            "vix_data": vix,
        })
        assert result.success
        matrix = result.data["transition_matrix"]
        assert len(matrix) >= 2
        # Probabilities should sum to ~1
        for from_regime, targets in matrix.items():
            total = sum(targets.values())
            assert 0.99 < total < 1.01


# ---------------------------------------------------------------------------
# ExtendedBacktest
# ---------------------------------------------------------------------------

class TestExtendedBacktest:

    @SKIP_NO_DATA
    def test_signal_history(self, loader):
        agent = ExtendedBacktest()
        result = agent.run({
            "action": "signal_history",
            "loader": loader,
            "months": 24,
        })
        assert result.success
        assert result.data["total_days"] > 200
        assert "per_signal" in result.data
        assert len(result.data["per_signal"]) > 0

    @SKIP_NO_DATA
    def test_walk_forward(self, loader):
        agent = ExtendedBacktest()
        result = agent.run({
            "action": "walk_forward",
            "loader": loader,
            "total_months": 36,
            "train_months": 12,
            "test_months": 3,
        })
        assert result.success
        assert result.data["window_count"] >= 1
        assert "avg_test_sharpe" in result.data

    @SKIP_NO_DATA
    def test_regime_action(self, loader):
        agent = ExtendedBacktest()
        result = agent.run({
            "action": "regime",
            "loader": loader,
            "months": 12,
        })
        assert result.success
        assert "regime_stats" in result.data

    def test_no_loader_fails(self):
        agent = ExtendedBacktest()
        result = agent.run({"action": "signal_history"})
        assert not result.success


# ---------------------------------------------------------------------------
# DataCatalogAgent
# ---------------------------------------------------------------------------

class TestDataCatalogAgent:

    @SKIP_NO_DATA
    def test_summary(self, loader):
        agent = DataCatalogAgent()
        result = agent.run({"action": "summary", "loader": loader})
        assert result.success
        assert result.data["total_tickers"] > 400
        assert "etfs" in result.data["asset_classes"]

    @SKIP_NO_DATA
    def test_inspect(self, loader):
        agent = DataCatalogAgent()
        result = agent.run({"action": "inspect", "loader": loader, "ticker": "SPY"})
        assert result.success
        assert result.data["rows"] > 5000
        assert result.data["total_return_pct"] > 100

    @SKIP_NO_DATA
    def test_quality(self, loader):
        agent = DataCatalogAgent()
        result = agent.run({"action": "quality", "loader": loader, "ticker": "SPY"})
        assert result.success
        assert result.data["quality_score"] > 50
        assert "technical_coverage" in result.data

    @SKIP_NO_DATA
    def test_coverage(self, loader):
        agent = DataCatalogAgent()
        result = agent.run({
            "action": "coverage", "loader": loader,
            "tickers": ["SPY", "QQQ", "IWM"],
        })
        assert result.success
        assert result.data["overlap"]["common_days"] > 1000

    @SKIP_NO_DATA
    def test_search(self, loader):
        agent = DataCatalogAgent()
        result = agent.run({
            "action": "search", "loader": loader,
            "query": "GLD",
        })
        assert result.success
        assert result.data["count"] >= 1

    def test_no_loader_fails(self):
        agent = DataCatalogAgent()
        result = agent.run({"action": "summary"})
        assert not result.success


# ---------------------------------------------------------------------------
# ResearchAgent
# ---------------------------------------------------------------------------

class TestResearchAgent:

    @SKIP_NO_DATA
    def test_correlation(self, loader):
        agent = ResearchAgent()
        result = agent.run({
            "action": "correlation", "loader": loader,
            "tickers": ["SPY", "QQQ", "TLT"],
        })
        assert result.success
        matrix = result.data["matrix"]
        # SPY-QQQ should be highly correlated
        assert matrix["SPY"]["QQQ"] > 0.7
        # SPY-TLT should be negatively correlated
        assert matrix["SPY"]["TLT"] < 0

    @SKIP_NO_DATA
    def test_returns(self, loader):
        agent = ResearchAgent()
        result = agent.run({
            "action": "returns", "loader": loader,
            "ticker": "SPY",
        })
        assert result.success
        assert result.data["cagr_pct"] > 0
        assert result.data["daily"]["sharpe"] > 0

    @SKIP_NO_DATA
    def test_drawdown(self, loader):
        agent = ResearchAgent()
        result = agent.run({
            "action": "drawdown", "loader": loader,
            "ticker": "SPY",
        })
        assert result.success
        assert result.data["max_drawdown_pct"] < -20  # SPY had COVID, GFC

    @SKIP_NO_DATA
    def test_compare(self, loader):
        agent = ResearchAgent()
        result = agent.run({
            "action": "compare", "loader": loader,
            "tickers": ["SPY", "QQQ", "IWM"],
        })
        assert result.success
        assert len(result.data["performances"]) == 3

    @SKIP_NO_DATA
    def test_screen(self, loader):
        agent = ResearchAgent()
        result = agent.run({
            "action": "screen", "loader": loader,
            "min_sharpe": 0.3,
            "asset_class": "etfs",
        })
        assert result.success
        assert result.data["total_matches"] >= 1


# ---------------------------------------------------------------------------
# LibrarianAgent
# ---------------------------------------------------------------------------

class TestLibrarianAgent:

    def test_summary_stats(self):
        agent = LibrarianAgent()
        result = agent.run({
            "action": "summary_stats",
            "values": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0] * 5,
            "label": "Test Distribution",
        })
        assert result.success
        stats = result.data["stats"]
        assert stats["n"] == 50
        assert "formatted" in result.data

    def test_compare_table(self):
        agent = LibrarianAgent()
        result = agent.run({
            "action": "compare_table",
            "rows": [
                {"ticker": "SPY", "return": 10.5, "sharpe": 1.2},
                {"ticker": "QQQ", "return": 15.3, "sharpe": 0.9},
            ],
            "columns": ["ticker", "return", "sharpe"],
            "title": "Performance Comparison",
        })
        assert result.success
        assert "table" in result.data

    def test_research_note(self):
        agent = LibrarianAgent()
        result = agent.run({
            "action": "research_note",
            "title": "Mean Reversion in SPY",
            "tickers": ["SPY"],
            "hypothesis": "RSI < 30 predicts 1-day bounces",
            "findings": ["63% win rate on next-day", "Higher in bull regimes"],
            "conclusion": "Signal is statistically significant",
        })
        assert result.success
        assert "HYPOTHESIS" in result.data["note"]
        assert "FINDINGS" in result.data["note"]

    def test_format_report(self):
        agent = LibrarianAgent()
        result = agent.run({
            "action": "format",
            "data": {"metric_a": 1.234, "metric_b": "hello"},
            "title": "Test Report",
        })
        assert result.success
        assert "TEST REPORT" in result.data["report"]


# ---------------------------------------------------------------------------
# PatternAgent
# ---------------------------------------------------------------------------

class TestPatternAgent:

    @SKIP_NO_DATA
    def test_seasonal(self, loader):
        agent = PatternAgent()
        result = agent.run({
            "action": "seasonal", "loader": loader, "ticker": "SPY",
        })
        assert result.success
        assert len(result.data["monthly"]) == 12
        assert len(result.data["day_of_week"]) == 5

    @SKIP_NO_DATA
    def test_mean_reversion(self, loader):
        agent = PatternAgent()
        result = agent.run({
            "action": "mean_reversion", "loader": loader, "ticker": "SPY",
        })
        assert result.success
        setups = result.data["setups"]
        assert len(setups) >= 3  # At least a few setups should fire

    @SKIP_NO_DATA
    def test_momentum(self, loader):
        agent = PatternAgent()
        result = agent.run({
            "action": "momentum", "loader": loader, "ticker": "SPY",
        })
        assert result.success
        assert "setups" in result.data

    @SKIP_NO_DATA
    def test_post_event(self, loader):
        agent = PatternAgent()
        result = agent.run({
            "action": "post_event", "loader": loader, "ticker": "SPY",
        })
        assert result.success
        events = result.data["events"]
        assert "drop_2pct" in events

    @SKIP_NO_DATA
    def test_vol_clustering(self, loader):
        agent = PatternAgent()
        result = agent.run({
            "action": "vol_clustering", "loader": loader, "ticker": "SPY",
        })
        assert result.success
        # Vol clustering is well-documented in equities
        assert result.data["abs_return_acf_lag1"] > 0.1


# ---------------------------------------------------------------------------
# MacroAgent
# ---------------------------------------------------------------------------

class TestMacroAgent:

    @SKIP_NO_DATA
    def test_dashboard(self, loader):
        agent = MacroAgent()
        result = agent.run({"action": "dashboard", "loader": loader})
        assert result.success
        dashboard = result.data["dashboard"]
        assert "equities" in dashboard

    @SKIP_NO_DATA
    def test_risk_regime(self, loader):
        agent = MacroAgent()
        result = agent.run({"action": "risk_regime", "loader": loader})
        assert result.success
        assert result.data["current_regime"] in ["RISK_ON", "RISK_OFF", "NEUTRAL"]

    @SKIP_NO_DATA
    def test_yield_curve(self, loader):
        agent = MacroAgent()
        result = agent.run({"action": "yield_curve", "loader": loader})
        assert result.success
        assert len(result.data["available_tenors"]) >= 2

    @SKIP_NO_DATA
    def test_rotation(self, loader):
        agent = MacroAgent()
        result = agent.run({
            "action": "rotation", "loader": loader, "lookback_days": 60,
        })
        assert result.success
        assert len(result.data["performances"]) >= 3

    @SKIP_NO_DATA
    def test_cross_asset(self, loader):
        agent = MacroAgent()
        result = agent.run({"action": "cross_asset", "loader": loader})
        assert result.success
        assert len(result.data["signals"]) >= 2


# ---------------------------------------------------------------------------
# StrategyDevAgent
# ---------------------------------------------------------------------------

class TestStrategyDevAgent:

    @SKIP_NO_DATA
    def test_scan_strategies(self, loader):
        agent = StrategyDevAgent()
        result = agent.run({
            "action": "scan_strategies", "loader": loader, "ticker": "SPY",
        })
        assert result.success
        assert len(result.data["ranked"]) > 0
        assert result.data["baseline"]["sharpe"] > 0

    @SKIP_NO_DATA
    def test_test_strategy(self, loader):
        agent = StrategyDevAgent()
        result = agent.run({
            "action": "test_strategy",
            "loader": loader,
            "ticker": "SPY",
            "strategy": {
                "name": "RSI Oversold Bounce",
                "rules": {"rsi_below": 30},
                "hold_days": 5,
            },
        })
        assert result.success
        assert result.data["trades"] > 50

    @SKIP_NO_DATA
    def test_compare_strategies(self, loader):
        agent = StrategyDevAgent()
        result = agent.run({
            "action": "compare",
            "loader": loader,
            "ticker": "SPY",
            "strategies": [
                {"name": "RSI<30", "rules": {"rsi_below": 30}, "hold_days": 5},
                {"name": "RSI<20", "rules": {"rsi_below": 20}, "hold_days": 5},
            ],
        })
        assert result.success
        assert len(result.data["strategies"]) == 2

    @SKIP_NO_DATA
    def test_multi_asset(self, loader):
        agent = StrategyDevAgent()
        result = agent.run({
            "action": "multi_asset",
            "loader": loader,
            "tickers": ["SPY", "QQQ", "IWM"],
        })
        assert result.success
        assert result.data["trades"] > 10


# ---------------------------------------------------------------------------
# Agent interface compliance
# ---------------------------------------------------------------------------

class TestAgentInterface:

    @pytest.mark.parametrize("AgentClass", [
        ExtendedBacktest, RegimeClassifier,
        DataCatalogAgent, ResearchAgent, LibrarianAgent,
        PatternAgent, MacroAgent, StrategyDevAgent,
    ])
    def test_has_run_method(self, AgentClass):
        agent = AgentClass()
        assert hasattr(agent, "run")
        assert callable(agent.run)

    @pytest.mark.parametrize("AgentClass", [
        ExtendedBacktest, RegimeClassifier,
        DataCatalogAgent, ResearchAgent, LibrarianAgent,
        PatternAgent, MacroAgent, StrategyDevAgent,
    ])
    def test_inherits_base_agent(self, AgentClass):
        from apex_sharpe.agents.base import BaseAgent
        assert issubclass(AgentClass, BaseAgent)

    @pytest.mark.parametrize("AgentClass", [
        DataCatalogAgent, ResearchAgent, PatternAgent, MacroAgent,
    ])
    def test_no_loader_returns_error(self, AgentClass):
        agent = AgentClass()
        result = agent.run({"action": "summary"})
        assert not result.success
        assert len(result.errors) > 0
