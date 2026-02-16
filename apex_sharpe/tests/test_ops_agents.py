"""
Tests for operational agents: Performance, Latency, Security, Infra.

NOTE: ALL DATA IN THIS FILE IS SYNTHETIC — fabricated for testing.
No real trading, market data, or account information is used.
"""

import math
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from apex_sharpe.agents.ops import (
    PerformanceAgent,
    LatencyAgent,
    SecurityAgent,
    InfraAgent,
)


# ---------------------------------------------------------------------------
# PerformanceAgent
# ---------------------------------------------------------------------------

class TestPerformanceAgent:
    """NOTE: ALL DATA IS SYNTHETIC."""

    def setup_method(self):
        self.agent = PerformanceAgent()

    def test_validate_strategy_insufficient_data(self):
        result = self.agent.run({
            "action": "validate_strategy",
            "trades": [{"pnl": 100}],
            "strategy": "call_debit_spread",
        })
        assert result.success
        assert result.data["status"] == "INSUFFICIENT_DATA"

    def test_validate_strategy_ok(self):
        trades = [
            {"pnl": 500, "structure": "cds"},
            {"pnl": -200, "structure": "cds"},
            {"pnl": 300, "structure": "cds"},
            {"pnl": 400, "structure": "cds"},
            {"pnl": -100, "structure": "cds"},
            {"pnl": 600, "structure": "cds"},
        ]
        result = self.agent.run({
            "action": "validate_strategy",
            "trades": trades,
            "strategy": "cds",
        })
        assert result.success
        assert result.data["trade_count"] == 6
        assert result.data["win_rate"] > 0.5
        assert result.data["total_pnl"] == 1500.0
        assert result.data["sharpe"] > 0

    def test_validate_strategy_warnings(self):
        # All losses → low win rate + low sharpe
        trades = [{"pnl": -100, "structure": "x"} for _ in range(6)]
        result = self.agent.run({
            "action": "validate_strategy",
            "trades": trades,
        })
        assert result.data["status"] == "WARNING"
        assert result.data["win_rate"] == 0.0

    def test_drift_check_stable(self):
        # 40 trades, consistent performance
        trades = [{"pnl": 100 + (i % 5) * 10} for i in range(40)]
        result = self.agent.run({
            "action": "drift_check",
            "trades": trades,
        })
        assert result.success
        assert result.data["drifting"] is False
        assert result.data["direction"] == "stable"

    def test_drift_check_degrading(self):
        # 20 good trades with variance, then 20 terrible ones
        trades = [{"pnl": 400 + i * 10} for i in range(20)]
        trades += [{"pnl": -500} for _ in range(20)]
        result = self.agent.run({
            "action": "drift_check",
            "trades": trades,
        })
        assert result.success
        assert result.data["drifting"] is True
        assert result.data["direction"] == "degrading"

    def test_execution_quality_ok(self):
        trades = [
            {"actual_slippage": 0.02, "modeled_slippage": 0.03},
            {"actual_slippage": 0.03, "modeled_slippage": 0.03},
            {"actual_slippage": 0.01, "modeled_slippage": 0.03},
        ]
        result = self.agent.run({
            "action": "execution_quality",
            "trades": trades,
        })
        assert result.success
        assert result.data["status"] == "OK"
        assert result.data["trades_scored"] == 3
        assert result.data["avg_slippage_ratio"] <= 1.0

    def test_execution_quality_warning(self):
        trades = [
            {"actual_slippage": 0.10, "modeled_slippage": 0.03},
            {"actual_slippage": 0.08, "modeled_slippage": 0.03},
        ]
        result = self.agent.run({
            "action": "execution_quality",
            "trades": trades,
        })
        assert result.data["status"] == "WARNING"
        assert result.data["avg_slippage_ratio"] > 1.5

    def test_full_report(self):
        trades = [
            {"pnl": 200, "structure": "cds"},
            {"pnl": -50, "structure": "bps"},
            {"pnl": 300, "structure": "cds"},
        ]
        positions = [{"status": "OPEN"}, {"status": "CLOSED"}]
        result = self.agent.run({
            "trades": trades,
            "positions": positions,
        })
        assert result.success
        assert result.data["total_trades"] == 3
        assert result.data["open_positions"] == 1
        assert result.data["total_pnl"] == 450.0
        assert "cds" in result.data["by_structure"]
        assert "bps" in result.data["by_structure"]

    def test_rolling_sharpe(self):
        # Known: [100, 100, 100] → std=0 → sharpe=0
        assert PerformanceAgent._rolling_sharpe([100, 100, 100]) == 0.0
        # Known: positive returns → positive sharpe
        assert PerformanceAgent._rolling_sharpe([100, 200, 150, 300, 250]) > 0


# ---------------------------------------------------------------------------
# LatencyAgent
# ---------------------------------------------------------------------------

class TestLatencyAgent:
    """NOTE: ALL DATA IS SYNTHETIC."""

    def setup_method(self):
        self.agent = LatencyAgent()

    def test_benchmark_no_client(self):
        result = self.agent.run({"action": "benchmark", "orats": None})
        assert not result.success

    def test_benchmark_with_mock_client(self):
        mock_orats = MagicMock()
        mock_orats.summaries.return_value = {"data": [{"ticker": "SPY"}]}
        mock_orats.iv_rank.return_value = {"data": [{"ticker": "SPY"}]}
        mock_orats.expirations.return_value = {"data": ["2026-03-20"]}

        result = self.agent.run({
            "action": "benchmark",
            "orats": mock_orats,
            "iterations": 2,
        })
        assert result.success
        assert "summaries" in result.data["endpoints"]
        assert "iv_rank" in result.data["endpoints"]
        assert result.data["endpoints"]["summaries"]["p50_ms"] >= 0

    def test_pipeline_timing(self):
        marks = {
            "data_fetch": 1000.0,
            "signal_compute": 1000.5,
            "portfolio_decision": 1000.8,
        }
        result = self.agent.run({
            "action": "pipeline_timing",
            "timing_marks": marks,
        })
        assert result.success
        assert result.data["total_ms"] == pytest.approx(800.0, abs=1.0)
        assert result.data["stage_count"] == 2

    def test_pipeline_timing_no_data(self):
        result = self.agent.run({"action": "pipeline_timing"})
        assert result.success
        assert result.data["status"] == "NO_TIMING_DATA"

    def test_data_freshness_fresh(self):
        from datetime import date
        result = self.agent.run({
            "action": "data_freshness",
            "summary": {
                "stockPrice": 600.0,
                "tradeDate": date.today().isoformat(),
            },
        })
        assert result.success
        assert result.data["status"] == "FRESH"

    def test_data_freshness_stale(self):
        result = self.agent.run({
            "action": "data_freshness",
            "summary": {
                "stockPrice": 600.0,
                "tradeDate": "2025-01-01",
            },
        })
        assert result.success
        assert result.data["status"] == "STALE"

    def test_data_freshness_no_price(self):
        result = self.agent.run({
            "action": "data_freshness",
            "summary": {"stockPrice": 0},
        })
        assert result.data["status"] == "STALE"

    def test_time_call_utility(self):
        result, elapsed = LatencyAgent.time_call(time.sleep, 0.01)
        assert elapsed >= 10  # at least 10ms
        assert result is None  # sleep returns None

    def test_report_empty(self):
        result = self.agent.run({"action": "report"})
        assert result.success
        assert result.data["endpoints"] == {}


# ---------------------------------------------------------------------------
# SecurityAgent
# ---------------------------------------------------------------------------

class TestSecurityAgent:
    """NOTE: ALL DATA IS SYNTHETIC."""

    def setup_method(self):
        self.agent = SecurityAgent()

    def test_audit_positions_clean(self):
        positions = [
            {"status": "OPEN", "max_risk": 5000, "entry_date": "2026-02-01"},
            {"status": "OPEN", "max_risk": 3000, "entry_date": "2026-02-10"},
        ]
        result = self.agent.run({
            "action": "audit_positions",
            "positions": positions,
            "account_capital": 250000.0,
        })
        assert result.success
        assert result.data["status"] == "PASS"
        assert result.data["open_count"] == 2
        assert result.data["total_risk"] == 8000.0

    def test_audit_positions_oversized(self):
        positions = [
            {"status": "OPEN", "max_risk": 50000, "entry_date": "2026-02-01"},
        ]
        result = self.agent.run({
            "action": "audit_positions",
            "positions": positions,
            "account_capital": 250000.0,
        })
        assert result.data["status"] == "FAIL"
        assert any(f["check"] == "position_size" for f in result.data["findings"])

    def test_audit_positions_total_exposure(self):
        positions = [
            {"status": "OPEN", "max_risk": 40000, "entry_date": "2026-02-01"},
            {"status": "OPEN", "max_risk": 40000, "entry_date": "2026-02-02"},
            {"status": "OPEN", "max_risk": 40000, "entry_date": "2026-02-03"},
            {"status": "OPEN", "max_risk": 40000, "entry_date": "2026-02-04"},
        ]
        result = self.agent.run({
            "action": "audit_positions",
            "positions": positions,
            "account_capital": 250000.0,
        })
        assert result.data["status"] == "FAIL"
        assert any(f["check"] == "total_exposure" for f in result.data["findings"])

    def test_audit_permissions_ok(self):
        registry = {
            "Executor": {
                "risk_level": "HIGH",
                "requires_approval": True,
                "trade_actions": ["open_position"],
            },
        }
        result = self.agent.run({
            "action": "audit_permissions",
            "agent_registry": registry,
        })
        assert result.data["status"] == "PASS"

    def test_audit_permissions_mismatch(self):
        registry = {
            "RogueAgent": {
                "risk_level": "HIGH",
                "requires_approval": False,
                "trade_actions": ["execute_trade"],
            },
        }
        result = self.agent.run({
            "action": "audit_permissions",
            "agent_registry": registry,
        })
        assert result.data["status"] == "FAIL"
        assert any(f["check"] == "permission_mismatch" for f in result.data["findings"])

    def test_audit_permissions_underclassified(self):
        registry = {
            "BadAgent": {
                "risk_level": "LOW",
                "requires_approval": False,
                "trade_actions": ["open_position"],
            },
        }
        result = self.agent.run({
            "action": "audit_permissions",
            "agent_registry": registry,
        })
        assert any(f["check"] == "risk_underclassified" for f in result.data["findings"])


# ---------------------------------------------------------------------------
# InfraAgent
# ---------------------------------------------------------------------------

class TestInfraAgent:
    """NOTE: ALL DATA IS SYNTHETIC."""

    def setup_method(self):
        self.agent = InfraAgent()

    def test_health_check_no_orats(self):
        result = self.agent.run({"action": "health_check"})
        assert result.success
        assert result.data["checks"]["orats_api"]["status"] == "SKIP"
        assert result.data["checks"]["python"]["status"] == "OK"
        assert result.data["ok_count"] >= 5  # python + disk + stdlib imports

    def test_health_check_with_mock_orats(self):
        mock_orats = MagicMock()
        mock_orats.summaries.return_value = {"data": [{"ticker": "SPY"}]}
        result = self.agent.run({
            "action": "health_check",
            "orats": mock_orats,
        })
        assert result.success
        assert result.data["checks"]["orats_api"]["status"] == "OK"

    def test_validate_env(self):
        result = self.agent.run({"action": "validate_env"})
        assert result.success
        assert "present" in result.data
        assert "missing_required" in result.data
        assert "config_loads" in result.data

    def test_docker_status(self):
        result = self.agent.run({"action": "docker_status"})
        assert result.success
        assert "in_docker" in result.data
        assert "platform" in result.data
        assert "python" in result.data
        assert result.data["in_docker"] is False  # we're not in Docker

    @patch("apex_sharpe.agents.ops.infra_agent.shutil.disk_usage")
    def test_full_report(self, mock_disk):
        mock_disk.return_value = type("Usage", (), {"free": 50 * 1024**3})()
        with patch.dict(os.environ, {"ORATS_TOKEN": "test-token"}):
            result = self.agent.run({"action": "full"})
        assert result.success
        assert "health" in result.data
        assert "env" in result.data
        assert "docker" in result.data
        assert result.data["status"] in ("OK", "WARNING", "FAIL")


# ---------------------------------------------------------------------------
# Print reports (smoke tests — just verify no crash)
# ---------------------------------------------------------------------------

class TestPrintReports:
    """Verify print_report methods don't crash. SYNTHETIC data."""

    def test_performance_print(self, capsys):
        agent = PerformanceAgent()
        result = agent._result(
            success=True,
            data={
                "total_trades": 5,
                "open_positions": 1,
                "total_pnl": 450.0,
                "overall_sharpe": 1.5,
                "by_structure": {
                    "cds": {"count": 3, "win_rate": 0.667, "total_pnl": 500.0, "sharpe": 1.2},
                },
            },
        )
        agent.print_report(result)
        captured = capsys.readouterr()
        assert "PERFORMANCE REPORT" in captured.out

    def test_latency_print(self, capsys):
        agent = LatencyAgent()
        result = agent._result(
            success=True,
            data={"endpoints": {}, "status": "OK"},
        )
        agent.print_report(result)
        captured = capsys.readouterr()
        assert "LATENCY REPORT" in captured.out

    def test_security_print(self, capsys):
        agent = SecurityAgent()
        result = agent._result(
            success=True,
            data={
                "status": "PASS",
                "high": 0,
                "medium": 0,
                "findings": [],
                "passed": ["env_permissions: .env OK"],
            },
        )
        agent.print_report(result)
        captured = capsys.readouterr()
        assert "SECURITY AUDIT" in captured.out

    def test_infra_print(self, capsys):
        agent = InfraAgent()
        result = agent._result(
            success=True,
            data={
                "status": "OK",
                "checks": {"python": {"status": "OK", "detail": "3.13"}},
                "docker": {"in_docker": False, "platform": "Darwin", "arch": "arm64", "python": "3.13"},
            },
        )
        agent.print_report(result)
        captured = capsys.readouterr()
        assert "INFRASTRUCTURE HEALTH" in captured.out
