"""Tests for MonitorAgent.

NOTE: ALL DATA IN THIS FILE IS SYNTHETIC.
Uses mock positions and ORATS responses. No real API calls or live
position monitoring occurs.
"""

from apex_sharpe.agents.monitor import (
    MonitorAgent,
    calculate_dte,
    estimate_from_chain,
    estimate_from_model,
    generate_alerts,
)
from apex_sharpe.config import MonitorCfg
from apex_sharpe.tests.conftest import MOCK_CHAIN


class TestCalculateDte:
    def test_future_date(self):
        dte = calculate_dte("2030-12-31")
        assert dte > 0

    def test_past_date(self):
        dte = calculate_dte("2020-01-01")
        assert dte < 0


class TestEstimateFromChain:
    def test_returns_pnl(self, sample_position):
        chain = {"data": MOCK_CHAIN["data"]}
        # Override expirDate to match chain data for strike lookup
        result = estimate_from_chain(sample_position, chain)

        assert "pnl" in result
        assert "pnl_pct" in result
        assert "leg_details" in result
        assert result["data_source"] == "LIVE_CHAIN"
        assert len(result["leg_details"]) == 4

    def test_leg_details_have_current_mid(self, sample_position):
        chain = {"data": MOCK_CHAIN["data"]}
        result = estimate_from_chain(sample_position, chain)

        for ld in result["leg_details"]:
            assert "current_mid" in ld
            assert "current_delta" in ld
            assert "bid" in ld
            assert "ask" in ld


class TestEstimateFromModel:
    def test_returns_estimate(self, sample_position):
        result = estimate_from_model(sample_position, 690.0)

        assert "pnl" in result
        assert result["data_source"] == "ESTIMATED"
        # P&L should be bounded by max profit/loss
        assert result["pnl"] >= -sample_position["max_loss"]
        assert result["pnl"] <= sample_position["max_profit"]

    def test_price_below_breakeven_is_negative(self, sample_position):
        # Price well below lower breakeven
        result = estimate_from_model(sample_position, 600.0)
        assert result["pnl"] < 0

    def test_price_in_range_is_positive(self, sample_position):
        # Price right at entry stock price — should have theta profit
        result = estimate_from_model(sample_position, 694.0)
        assert result["pnl"] >= 0


class TestGenerateAlerts:
    def test_no_alerts_in_range(self, sample_position):
        valuation = {"pnl": 50.0, "leg_details": []}
        alerts = generate_alerts(sample_position, 690.0, valuation)
        # Should have no ACTION alerts (position is healthy and >21 DTE)
        action_alerts = [a for a in alerts if a["level"] == "ACTION"]
        assert len(action_alerts) == 0

    def test_profit_target_alert(self, sample_position):
        # P&L at 60% of max profit → should trigger
        cfg = MonitorCfg(profit_target_pct=0.5)
        valuation = {"pnl": 250.0, "leg_details": []}
        alerts = generate_alerts(sample_position, 690.0, valuation, cfg)

        profit_alerts = [a for a in alerts if "PROFIT TARGET" in a["message"]]
        assert len(profit_alerts) == 1

    def test_max_loss_alert(self, sample_position):
        cfg = MonitorCfg(loss_exit_pct=0.75)
        valuation = {"pnl": -4000.0, "leg_details": []}
        alerts = generate_alerts(sample_position, 600.0, valuation, cfg)

        loss_alerts = [a for a in alerts if "MAX LOSS" in a["message"]]
        assert len(loss_alerts) == 1

    def test_dte_exit_alert(self, sample_position):
        # Force expiration to be very close
        sample_position["expiration"] = "2026-02-16"
        cfg = MonitorCfg(dte_exit=21)
        valuation = {"pnl": 0, "leg_details": []}
        alerts = generate_alerts(sample_position, 690.0, valuation, cfg)

        dte_alerts = [a for a in alerts if "DTE EXIT" in a["message"]]
        assert len(dte_alerts) == 1

    def test_breakeven_proximity_alert(self, sample_position):
        cfg = MonitorCfg(breakeven_buffer=10)
        valuation = {"pnl": -100, "leg_details": []}
        # Price near lower breakeven
        alerts = generate_alerts(sample_position, 656.0, valuation, cfg)

        be_alerts = [a for a in alerts if "BREAKEVEN" in a["message"]]
        assert len(be_alerts) >= 1


class TestMonitorAgent:
    def test_run_with_chain(self, sample_position):
        agent = MonitorAgent()
        result = agent.run({
            "position": sample_position,
            "current_price": 690.0,
            "chain": MOCK_CHAIN,
        })

        assert result.success
        assert result.data["valuation"]["data_source"] == "LIVE_CHAIN"
        assert "alerts" in result.data

    def test_run_without_chain(self, sample_position):
        agent = MonitorAgent()
        result = agent.run({
            "position": sample_position,
            "current_price": 690.0,
            "chain": None,
        })

        assert result.success
        assert result.data["valuation"]["data_source"] == "ESTIMATED"
