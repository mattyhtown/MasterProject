"""Integration tests for IC pipeline with mocked ORATS data.

NOTE: ALL DATA IN THIS FILE IS SYNTHETIC.
Uses mocked ORATS responses and temporary position files. No real
API calls, no real brokerage execution.
"""

import json
import tempfile
from unittest.mock import MagicMock, patch

from apex_sharpe.config import (
    AppConfig, OratsCfg, ScannerCfg, RiskCfg,
    ExecutorCfg, MonitorCfg, StateCfg,
)
from apex_sharpe.data.orats_client import ORATSClient
from apex_sharpe.data.state import StateManager
from apex_sharpe.pipelines.ic_pipeline import ICPipeline
from apex_sharpe.tests.conftest import (
    MOCK_IV_RANK, MOCK_SUMMARIES, MOCK_EXPIRATIONS, MOCK_CHAIN,
    SAMPLE_POSITION,
)


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
