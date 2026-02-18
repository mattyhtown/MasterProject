"""Tests for ZeroDTEAgent signal computation.

NOTE: ALL DATA IN THIS FILE IS SYNTHETIC.
All tests use canned dicts — zero API calls. Signal thresholds and
summary values are fabricated to exercise signal logic, not derived
from real ORATS historical data.
"""

from apex_sharpe.agents.zero_dte import ZeroDTEAgent
from apex_sharpe.config import ZeroDTECfg


def _make_summary(**overrides):
    """Build a mock ORATS summary row with sane defaults."""
    base = {
        "ticker": "SPX",
        "stockPrice": 5800.0,
        "ivMean30": 0.18,
        "iv30": 0.17,
        "iv60": 0.19,
        "iv90": 0.20,
        "orHv20": 0.15,
        "orIvFcst20d": 0.16,
        "skewing": 0.04,
        "contango": 0.15,
        "rip": 0.005,
        "fbfwd": 0.01,
        "fwd30_20": 0.18,
        "slope": 4.5,
        "rSlp30": 0.08,
        "rDrv30": 0.02,
        "rvol20": 0.14,
    }
    base.update(overrides)
    return base


class TestComputeSignals:
    def test_returns_dict_of_signal_dicts(self):
        agent = ZeroDTEAgent()
        row = _make_summary()
        signals = agent.compute_signals("SPX", row)

        assert isinstance(signals, dict)
        # Each signal should have level, tier, value keys
        for name, sig in signals.items():
            assert "level" in sig, f"Signal {name} missing 'level'"
            assert "tier" in sig, f"Signal {name} missing 'tier'"

    def test_skewing_fires_on_spike(self):
        agent = ZeroDTEAgent()
        row = _make_summary(skewing=0.25)  # Well above default threshold
        signals = agent.compute_signals("SPX", row)
        assert signals["skewing"]["level"] == "ACTION"

    def test_contango_fires_on_negative(self):
        agent = ZeroDTEAgent()
        row = _make_summary(contango=-0.05)  # Negative = backwardation
        signals = agent.compute_signals("SPX", row)
        assert signals["contango"]["level"] == "ACTION"

    def test_contango_fires_on_drop_from_baseline(self):
        agent = ZeroDTEAgent()
        # Set a baseline first, then send collapsed value
        agent.baseline["SPX"] = {"contango": 0.20}
        row = _make_summary(contango=0.02)  # 90% drop from baseline
        signals = agent.compute_signals("SPX", row)
        assert signals["contango"]["level"] == "ACTION"

    def test_calm_data_no_action(self):
        agent = ZeroDTEAgent()
        row = _make_summary()  # All defaults are calm
        signals = agent.compute_signals("SPX", row)
        action_count = sum(1 for s in signals.values() if s.get("level") == "ACTION")
        # Calm data should have few or no ACTION signals
        assert action_count <= 2


class TestDetermineDirection:
    def _make_agent_with_signals(self, action_keys):
        """Create agent and mock signals with given keys set to ACTION."""
        agent = ZeroDTEAgent()
        # Build signals dict with all 20 signal keys
        all_keys = [
            "skewing", "contango", "rip", "skew_25d_rr", "credit_spread",
            "wing_skew_30d", "wing_skew_10d",
            "borrow_term", "borrow_spread",
            "iv_momentum", "skewing_change", "contango_change",
            "model_confidence", "mw_adj_30", "iv10_iv30_ratio",
            "iv_rv_spread", "fbfwd30_20", "rSlp30", "fwd_kink", "rDrv30",
        ]
        signals = {}
        for k in all_keys:
            signals[k] = {
                "level": "ACTION" if k in action_keys else "OK",
                "tier": 1 if k not in ("model_confidence", "mw_adj_30",
                                        "iv10_iv30_ratio", "iv_rv_spread",
                                        "fbfwd30_20", "rSlp30", "fwd_kink",
                                        "rDrv30") else 2,
                "value": 0.1 if k in action_keys else 0.0,
            }
        return agent, signals

    def test_strong_fear_on_3_of_5_core(self):
        agent, signals = self._make_agent_with_signals(
            ["skewing", "contango", "rip"]
        )
        direction, t1 = agent.determine_direction(signals)
        assert direction == "FEAR_BOUNCE_STRONG"

    def test_weak_fear_on_2_core(self):
        agent, signals = self._make_agent_with_signals(
            ["skewing", "contango"]
        )
        direction, t1 = agent.determine_direction(signals)
        # 2 core signals = FEAR_BOUNCE_LONG (not strong)
        assert direction == "FEAR_BOUNCE_LONG"

    def test_neutral_on_one_signal(self):
        agent, signals = self._make_agent_with_signals(["skewing"])
        direction, t1 = agent.determine_direction(signals)
        assert direction is None

    def test_neutral_on_no_signals(self):
        agent, signals = self._make_agent_with_signals([])
        direction, t1 = agent.determine_direction(signals)
        assert direction is None

    def test_intraday_bearish_weak(self):
        """3 core signals alone = WEAK (need 2+ groups for strong)."""
        agent, signals = self._make_agent_with_signals(
            ["skewing", "contango", "rip"]
        )
        direction, t1 = agent.determine_direction(signals, intraday=True)
        assert direction == "DIRECTIONAL_BEARISH_WEAK"

    def test_intraday_bearish_strong(self):
        """Core + wing + momentum = 3 groups = STRONG bearish."""
        agent, signals = self._make_agent_with_signals(
            ["skewing", "contango", "rip", "wing_skew_30d", "iv_momentum", "skewing_change"]
        )
        direction, t1 = agent.determine_direction(signals, intraday=True)
        assert direction == "DIRECTIONAL_BEARISH"

    def test_multi_signal_strong(self):
        """3+ signal groups firing = MULTI_SIGNAL_STRONG."""
        agent, signals = self._make_agent_with_signals(
            ["skewing", "contango", "rip",           # core
             "wing_skew_30d",                        # wing
             "borrow_term",                          # funding
             "iv_momentum", "skewing_change"]        # momentum
        )
        direction, t1 = agent.determine_direction(signals)
        assert direction == "MULTI_SIGNAL_STRONG"

    def test_funding_stress_composite(self):
        """Both borrow signals + 1 other group = FUNDING_STRESS."""
        agent, signals = self._make_agent_with_signals(
            ["skewing", "contango",  # core (2 = group fires)
             "borrow_term", "borrow_spread"]  # funding
        )
        direction, t1 = agent.determine_direction(signals)
        assert direction == "FUNDING_STRESS"

    def test_wing_panic_composite(self):
        """Both wing signals + 1 other group = WING_PANIC."""
        agent, signals = self._make_agent_with_signals(
            ["skewing", "contango",  # core (2 = group fires)
             "wing_skew_30d", "wing_skew_10d"]  # wing
        )
        direction, t1 = agent.determine_direction(signals)
        assert direction == "WING_PANIC"

    def test_vol_acceleration_composite(self):
        """2+ momentum signals + 1 other group = VOL_ACCELERATION."""
        agent, signals = self._make_agent_with_signals(
            ["skewing", "contango",  # core (2 = group fires)
             "iv_momentum", "skewing_change"]  # momentum
        )
        direction, t1 = agent.determine_direction(signals)
        assert direction == "VOL_ACCELERATION"


class TestZeroDTEAgent:
    def test_init(self):
        agent = ZeroDTEAgent()
        assert agent.name == "ZeroDTE"

    def test_run_interface(self):
        agent = ZeroDTEAgent()
        row = _make_summary()
        result = agent.run({
            "ticker": "SPX",
            "summary": row,
        })

        assert result.success
        assert "signals" in result.data
        assert "composite" in result.data
