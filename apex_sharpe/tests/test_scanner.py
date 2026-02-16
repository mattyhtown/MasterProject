"""Tests for ScannerAgent.

NOTE: ALL DATA IN THIS FILE IS SYNTHETIC.
Uses mock ORATS responses from conftest.py (fabricated strikes, deltas,
bid/ask prices). No real API calls are made.
"""

from apex_sharpe.agents.scanner import ScannerAgent
from apex_sharpe.config import ScannerCfg


class TestScannerAgent:
    def test_init_defaults(self):
        agent = ScannerAgent()
        assert agent.name == "Scanner"
        assert isinstance(agent.config, ScannerCfg)

    def test_find_put_by_delta(self):
        strikes = [
            {"strike": 660, "delta": 0.84},  # put_delta = -0.16
            {"strike": 610, "delta": 0.95},  # put_delta = -0.05
            {"strike": 700, "delta": 0.50},  # put_delta = -0.50
        ]
        matches = ScannerAgent._find_put_by_delta(strikes, 0.16, 0.05)
        assert len(matches) == 1
        assert matches[0]["strike"] == 660

    def test_find_call_by_delta(self):
        strikes = [
            {"strike": 720, "delta": 0.16},
            {"strike": 740, "delta": 0.05},
            {"strike": 700, "delta": 0.50},
        ]
        matches = ScannerAgent._find_call_by_delta(strikes, 0.16, 0.05)
        assert len(matches) == 1
        assert matches[0]["strike"] == 720

    def test_scan_finds_candidate(self, mock_orats):
        cfg = ScannerCfg(
            watchlist=["SPY"],
            dte_min=25,
            dte_max=45,
            iv_rank_min=20,
            short_delta=0.16,
            long_delta=0.05,
            delta_tolerance=0.12,
        )
        agent = ScannerAgent(cfg)
        result = agent.run({"orats": mock_orats})

        assert result.success
        candidates = result.data["candidates"]
        assert len(candidates) == 1
        assert candidates[0]["symbol"] == "SPY"
        assert len(candidates[0]["legs"]) == 4
        assert candidates[0]["total_credit"] > 0

    def test_scan_skips_low_iv(self, mock_orats):
        cfg = ScannerCfg(watchlist=["SPY"], iv_rank_min=80)
        agent = ScannerAgent(cfg)
        result = agent.run({"orats": mock_orats})

        assert result.success
        assert len(result.data["candidates"]) == 0
        assert any("IV rank" in m for m in result.messages)

    def test_scan_empty_watchlist(self, mock_orats):
        cfg = ScannerCfg(watchlist=[])
        agent = ScannerAgent(cfg)
        result = agent.run({"orats": mock_orats})

        assert result.success
        assert len(result.data["candidates"]) == 0
