"""Tests for PortfolioAgent.

NOTE: ALL DATA IN THIS FILE IS SYNTHETIC.
No real market data, ORATS API calls, or portfolio state is used. Signal
composites, position lists, and capital amounts are fabricated to test
the portfolio orchestration logic. Real portfolio behavior depends on
live market conditions, actual Greeks, and real position interactions.
"""

from apex_sharpe.agents.portfolio import PortfolioAgent
from apex_sharpe.config import PortfolioCfg, SignalSizingCfg
from apex_sharpe.types import PortfolioTier


def test_empty_portfolio_status():
    p = PortfolioAgent()
    snap = p._snapshot()
    assert snap["capital"] == 250000.0
    assert snap["total_deployed"] == 0
    assert snap["idle_cash"] == 250000.0
    assert snap["utilization_pct"] == 0.0


def test_tier_available():
    p = PortfolioAgent()
    # With nothing deployed, directional tier has full budget
    avail = p._tier_available(PortfolioTier.DIRECTIONAL)
    assert avail == 250000.0 * 0.08  # 8%


def test_signal_triggers_trade():
    p = PortfolioAgent()
    result = p.run({
        "signals": {
            "composite": "FEAR_BOUNCE_STRONG",
            "core_count": 4,
            "firing": ["skewing", "rip", "contango", "credit_spread"],
        },
        "summary": {"ivRank1m": 50, "dlt25Iv30d": 0.22, "dlt75Iv30d": 0.20,
                     "contango": 0.05},
        "chain": [],
        "positions": [],
        "spot": 6000.0,
        "signal_system": "vol_surface",
    })
    assert result.success
    assert result.data["risk_budget"] > 0
    assert result.data["structure"] is not None
    assert result.data["core_count"] == 4


def test_no_composite_fails():
    p = PortfolioAgent()
    result = p.run({
        "signals": {"composite": None, "core_count": 0},
        "summary": {},
        "chain": [],
        "positions": [],
        "spot": 6000.0,
    })
    assert not result.success
    assert "No composite signal" in result.errors[0]


def test_correlation_discount():
    p = PortfolioAgent()
    # One existing position from same signal system
    positions = [{
        "status": "OPEN",
        "signal_system": "vol_surface",
        "tier": "directional",
        "max_risk": 5000,
    }]
    budget = p._apply_correlation_discount(
        10000.0, "vol_surface", positions)
    assert budget == 7000.0  # 30% discount


def test_greeks_limits():
    p = PortfolioAgent()
    # Set Greeks near limits
    p._greeks["delta"] = 60.0  # Over limit of 50
    checks = p._pre_trade_checks({"composite": "FEAR_BOUNCE_STRONG"})
    assert any("delta" in c.lower() for c in checks)


def test_daily_cap():
    p = PortfolioAgent()
    p._daily_deployed = 100000.0  # At daily cap (40% of $250K)
    result = p.run({
        "signals": {"composite": "FEAR_BOUNCE_STRONG", "core_count": 3},
        "summary": {},
        "chain": [],
        "positions": [],
        "spot": 6000.0,
    })
    assert not result.success
    assert "Daily deployment cap" in result.errors[0]
