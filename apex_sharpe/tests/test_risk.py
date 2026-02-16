"""Tests for RiskAgent.

NOTE: ALL DATA IN THIS FILE IS SYNTHETIC.
Uses mock candidates and positions from conftest.py. All capital amounts,
risk percentages, and position data are fabricated for unit testing.
"""

from apex_sharpe.agents.risk import RiskAgent
from apex_sharpe.config import RiskCfg


class TestRiskAgent:
    def test_init_defaults(self):
        agent = RiskAgent()
        assert agent.name == "Risk"
        assert isinstance(agent.config, RiskCfg)

    def test_allow_good_candidate(self, sample_candidate):
        cfg = RiskCfg(
            max_positions=5,
            account_capital=100_000,
            per_trade_risk_pct=0.10,
            total_risk_pct=0.30,
            credit_width_min=0.05,
        )
        agent = RiskAgent(cfg)
        result = agent.run({
            "candidates": [sample_candidate],
            "positions": [],
        })

        assert result.success
        decisions = result.data["decisions"]
        assert len(decisions) == 1
        assert decisions[0]["decision"] == "ALLOW"

    def test_block_position_limit(self, sample_candidate, sample_position):
        cfg = RiskCfg(max_positions=1)
        agent = RiskAgent(cfg)
        result = agent.run({
            "candidates": [sample_candidate],
            "positions": [sample_position],  # 1 open = at limit
        })

        decisions = result.data["decisions"]
        assert decisions[0]["decision"] == "BLOCK"
        assert any("Position limit" in r for r in decisions[0]["reasons"])

    def test_block_per_trade_risk(self, sample_candidate):
        cfg = RiskCfg(
            account_capital=10_000,
            per_trade_risk_pct=0.05,  # $500 limit, candidate max_loss = $4680
        )
        agent = RiskAgent(cfg)
        result = agent.run({
            "candidates": [sample_candidate],
            "positions": [],
        })

        decisions = result.data["decisions"]
        assert decisions[0]["decision"] == "BLOCK"
        assert any("Per-trade risk" in r for r in decisions[0]["reasons"])

    def test_block_total_risk(self, sample_candidate, sample_position):
        cfg = RiskCfg(
            account_capital=10_000,
            per_trade_risk_pct=0.50,
            total_risk_pct=0.10,  # $1000 total, existing max_loss = $5002
            max_positions=10,
        )
        agent = RiskAgent(cfg)
        result = agent.run({
            "candidates": [sample_candidate],
            "positions": [sample_position],
        })

        decisions = result.data["decisions"]
        assert decisions[0]["decision"] == "BLOCK"
        assert any("Total risk" in r for r in decisions[0]["reasons"])

    def test_block_credit_quality(self, sample_candidate):
        cfg = RiskCfg(credit_width_min=0.50)  # 50% — much higher than any real IC
        agent = RiskAgent(cfg)
        result = agent.run({
            "candidates": [sample_candidate],
            "positions": [],
        })

        decisions = result.data["decisions"]
        assert decisions[0]["decision"] == "BLOCK"
        assert any("Credit/width" in r for r in decisions[0]["reasons"])

    def test_block_duplicate_symbol(self, sample_candidate, sample_position):
        # Make candidate have close expiration to existing position
        sample_candidate["expiration"] = "2026-03-13"
        cfg = RiskCfg(max_positions=10)
        agent = RiskAgent(cfg)
        result = agent.run({
            "candidates": [sample_candidate],
            "positions": [sample_position],
        })

        decisions = result.data["decisions"]
        assert decisions[0]["decision"] == "BLOCK"
        assert any("Duplicate" in r for r in decisions[0]["reasons"])

    def test_multiple_candidates(self, sample_candidate):
        cand2 = dict(sample_candidate)
        cand2["symbol"] = "QQQ"
        cfg = RiskCfg()
        agent = RiskAgent(cfg)
        result = agent.run({
            "candidates": [sample_candidate, cand2],
            "positions": [],
        })

        assert len(result.data["decisions"]) == 2
