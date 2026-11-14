"""Integration tests for IC pipeline with mocked ORATS data.

NOTE: ALL DATA IN THIS FILE IS SYNTHETIC.
Uses mocked ORATS responses and temporary position files. No real
API calls, no real brokerage execution.
"""

import json
import tempfile
import pytest
from unittest.mock import MagicMock, patch

from apex_sharpe.config import (
    AppConfig, OratsCfg, ScannerCfg, RiskCfg,
    ExecutorCfg, MonitorCfg, StateCfg, IBCfg,
)
from apex_sharpe.data.orats_client import ORATSClient
from apex_sharpe.data.state import StateManager
from apex_sharpe.pipelines.ic_pipeline import ICPipeline
from apex_sharpe.tests.conftest import (
    MOCK_IV_RANK, MOCK_SUMMARIES, MOCK_EXPIRATIONS, MOCK_CHAIN,
    SAMPLE_POSITION,
)

pytestmark = pytest.mark.usefixtures("fixed_market_date")


def _make_config(tmpdir):
    """Build AppConfig pointing state files to a temp directory."""
    return AppConfig(
        orats=OratsCfg(token="test-token"),
        scanner=ScannerCfg(
            watchlist=["SPY"],
            dte_min=25,
            dte_max=45,
            iv_rank_min=20,
            short_delta=0.16,
            long_delta=0.05,
            delta_tolerance=0.12,
        ),
        risk=RiskCfg(
            max_positions=5,
            account_capital=100_000,
            per_trade_risk_pct=0.10,
            total_risk_pct=0.30,
            credit_width_min=0.05,
        ),
        executor=ExecutorCfg(slippage_pct=0.02, commission_per_ic=6.0),
        monitor=MonitorCfg(profit_target_pct=0.5, dte_exit=21),
        state=StateCfg(
            positions_path=f"{tmpdir}/positions.json",
            signals_path=f"{tmpdir}/signals.json",
            cache_path=f"{tmpdir}/cache.json",
        ),
    )


class TestICPipelineScan:
    def test_scan_end_to_end(self, mock_orats, tmp_path):
        config = _make_config(str(tmp_path))
        state = StateManager(config.state)
        pipeline = ICPipeline(config, mock_orats, state)

        pipeline.run_scan()

        # Should have created a position
        positions = state.load_positions()
        assert len(positions) == 1
        pos = positions[0]
        assert pos["symbol"] == "SPY"
        assert pos["status"] == "OPEN"
        assert pos["entry_credit"] > 0
        assert len(pos["legs"]) == 4

    def test_scan_respects_position_limit(self, mock_orats, tmp_path):
        config = _make_config(str(tmp_path))
        config = AppConfig(
            **{**config.__dict__, "risk": RiskCfg(max_positions=0)}
        )
        state = StateManager(config.state)
        pipeline = ICPipeline(config, mock_orats, state)

        pipeline.run_scan()

        positions = state.load_positions()
        assert len(positions) == 0


class TestICPipelineMonitor:
    def test_ib_enabled_without_client_cannot_simulate(self, mock_orats, tmp_path):
        config = _make_config(str(tmp_path))
        config = AppConfig(**{**config.__dict__, "ib": IBCfg(enabled=True)})
        with pytest.raises(ConnectionError, match="no IB client"):
            ICPipeline(config, mock_orats, StateManager(config.state))

    def test_ib_position_cannot_close_in_simulation(self, mock_orats, tmp_path):
        config = _make_config(str(tmp_path))
        state = StateManager(config.state)
        state.save_positions([dict(SAMPLE_POSITION, execution_method="IB")])
        mock_orats.summaries.return_value = {"data": [{"stockPrice": 600.0}]}
        ICPipeline(config, mock_orats, state).run_monitor()
        position = state.load_positions()[0]
        assert position["status"] == "OPEN"
        assert "realized_pnl" not in position
        assert "IB monitoring" in position["close_error"]

    def test_successful_close_saved_before_next_position_fails(self, mock_orats, tmp_path):
        config = _make_config(str(tmp_path))
        state = StateManager(config.state)
        state.save_positions([SAMPLE_POSITION, dict(SAMPLE_POSITION, id="second")])
        pipeline = ICPipeline(config, mock_orats, state)
        action_result = MagicMock()
        action_result.data = {"valuation": {"pnl": 200}, "alerts": [
            {"level": "ACTION", "message": "PROFIT_TARGET"},
        ]}
        pipeline.monitor_agent.run = MagicMock(side_effect=[
            action_result, RuntimeError("market data failed"),
        ])
        with pytest.raises(RuntimeError, match="market data failed"):
            pipeline.run_monitor()
        positions = state.load_positions()
        assert positions[0]["status"] == "CLOSED"
        assert positions[1]["status"] == "OPEN"

    def test_expired_position_requires_reconciliation(self, mock_orats, tmp_path):
        config = _make_config(str(tmp_path))
        state = StateManager(config.state)
        state.save_positions([dict(SAMPLE_POSITION, expiration="2026-02-13")])
        ICPipeline(config, mock_orats, state).run_monitor()
        position = state.load_positions()[0]
        assert position["status"] == "OPEN"
        assert "settlement reconciliation" in position["close_error"]
        assert "realized_pnl" not in position

    def test_wrong_expiry_chain_is_not_used(self, mock_orats, tmp_path):
        config = _make_config(str(tmp_path))
        state = StateManager(config.state)
        state.save_positions([SAMPLE_POSITION])
        mock_orats.chain.return_value = {"data": [
            dict(row, expirDate="2026-03-20") for row in MOCK_CHAIN["data"]
        ]}
        pipeline = ICPipeline(config, mock_orats, state)
        with patch.object(pipeline.monitor_agent, "run", wraps=pipeline.monitor_agent.run) as monitor:
            pipeline.run_monitor()
        assert monitor.call_args.args[0]["chain"]["data"] == []

    def test_monitor_with_open_position(self, mock_orats, tmp_path):
        config = _make_config(str(tmp_path))
        state = StateManager(config.state)

        # Pre-load a position
        state.save_positions([SAMPLE_POSITION])

        pipeline = ICPipeline(config, mock_orats, state)
        pipeline.run_monitor()

        # Position should still be there (not closed — within normal range)
        positions = state.load_positions()
        assert len(positions) == 1
        assert positions[0]["status"] == "OPEN"

    def test_monitor_no_positions(self, mock_orats, tmp_path):
        config = _make_config(str(tmp_path))
        state = StateManager(config.state)

        pipeline = ICPipeline(config, mock_orats, state)
        # Should not raise with empty positions
        pipeline.run_monitor()


class TestICPipelineFull:
    def test_full_mode(self, mock_orats, tmp_path):
        config = _make_config(str(tmp_path))
        state = StateManager(config.state)

        pipeline = ICPipeline(config, mock_orats, state)
        pipeline.run_full()

        positions = state.load_positions()
        # Full mode = scan + monitor, so should have opened something
        assert len(positions) >= 1
