"""Tests for SignalSizer.

NOTE: ALL DATA IN THIS FILE IS SYNTHETIC.
No real market data or ORATS API calls are used. All capital amounts,
multipliers, and risk percentages are hardcoded test values that exercise
the sizing logic but do not reflect real market conditions.
"""

from apex_sharpe.selection.signal_sizer import SignalSizer
from apex_sharpe.config import SignalSizingCfg
from apex_sharpe.types import SignalStrength


def test_default_sizing():
    s = SignalSizer()
    r = s.compute(3)
    assert r["risk_budget"] == 5000.0
    assert r["multiplier"] == 1.0
    assert r["strength"] == SignalStrength.STRONG


def test_4_signal_sizing():
    s = SignalSizer()
    r = s.compute(4)
    assert r["risk_budget"] == 7500.0
    assert r["multiplier"] == 1.5
    assert r["strength"] == SignalStrength.VERY_STRONG


def test_5_signal_sizing():
    s = SignalSizer()
    r = s.compute(5)
    assert r["risk_budget"] == 10000.0
    assert r["multiplier"] == 2.0
    assert r["strength"] == SignalStrength.EXTREME


def test_max_risk_cap():
    cfg = SignalSizingCfg(
        account_capital=100000.0,
        base_risk_pct=0.05,
        max_risk_pct=0.05,
        multipliers=((3, 1.0), (4, 1.5), (5, 2.0)),
    )
    s = SignalSizer(cfg)
    r = s.compute(5)
    # 100K * 5% * 2.0 = 10K, but max is 100K * 5% = 5K
    assert r["risk_budget"] == 5000.0


def test_capital_override():
    s = SignalSizer()
    r = s.compute(3, capital_override=100000.0)
    assert r["risk_budget"] == 2000.0  # 100K * 2% * 1.0
    assert r["capital"] == 100000.0


def test_unknown_core_count():
    s = SignalSizer()
    r = s.compute(1)
    assert r["multiplier"] == 1.0  # default
    assert r["strength"] == SignalStrength.NONE


def test_max_daily_budget():
    s = SignalSizer()
    assert s.max_daily_budget() == 25000.0  # 250K * 10%
