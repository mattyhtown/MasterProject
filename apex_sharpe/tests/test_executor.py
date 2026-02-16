"""Tests for ExecutorAgent.

NOTE: ALL DATA IN THIS FILE IS SYNTHETIC.
Uses mock candidates and positions from conftest.py. Fill prices and
slippage values are fabricated. No real brokerage execution occurs.
"""

from apex_sharpe.agents.executor import ExecutorAgent, _HAS_FILL_SIM
from apex_sharpe.config import ExecutorCfg, MonitorCfg


class TestExecutorAgent:
    def test_init_defaults(self):
        agent = ExecutorAgent()
        assert agent.name == "Executor"
        assert isinstance(agent.config, ExecutorCfg)

    def test_fill_sim_detected(self):
        """FillSimulator should be available (pure stdlib)."""
        assert _HAS_FILL_SIM is True

    def test_open_allowed_candidate(self, sample_candidate):
        cfg = ExecutorCfg(slippage_pct=0.02, commission_per_ic=6.0)
        mon = MonitorCfg(profit_target_pct=0.5, dte_exit=21, delta_exit=0.3)
        agent = ExecutorAgent(cfg, mon)

        decision = {"candidate": sample_candidate, "decision": "ALLOW", "reasons": ["OK"]}
        result = agent.run({"decisions": [decision]})

        assert result.success
        positions = result.data["new_positions"]
        assert len(positions) == 1

        pos = positions[0]
        assert pos["symbol"] == "SPY"
        assert pos["status"] == "OPEN"
        assert pos["entry_credit"] > 0
        assert pos["entry_credit"] < sample_candidate["total_credit"]  # slippage reduces credit
        assert pos["max_profit"] > 0
        assert pos["max_loss"] > 0
        assert len(pos["legs"]) == 4
        assert pos["exit_rules"]["profit_target_pct"] == 0.5

    def test_skip_blocked_candidate(self, sample_candidate):
        agent = ExecutorAgent()
        decision = {"candidate": sample_candidate, "decision": "BLOCK", "reasons": ["Risk"]}
        result = agent.run({"decisions": [decision]})

        assert result.success
        assert len(result.data["new_positions"]) == 0

    def test_close_position(self, sample_position):
        agent = ExecutorAgent()
        closed = agent.run_close(sample_position, "PROFIT_TARGET", 200.0)

        assert closed["status"] == "CLOSED"
        assert closed["exit_reason"] == "PROFIT_TARGET"
        assert "realized_pnl" in closed
        assert closed["exit_date"] is not None

    def test_commission_deducted_on_close(self, sample_position):
        cfg = ExecutorCfg(commission_per_ic=10.0)
        agent = ExecutorAgent(cfg)
        closed = agent.run_close(sample_position, "DTE", 100.0)

        assert closed["realized_pnl"] == 90.0  # 100 - 10 commission

    def test_simulate_leg_fill_with_fill_sim(self, sample_candidate):
        """Test that _simulate_leg_fill works via FillSimulator."""
        agent = ExecutorAgent()
        leg = sample_candidate["legs"][0]
        fill = agent._simulate_leg_fill(leg, is_buy=True)
        assert isinstance(fill, float)
        assert fill > 0
