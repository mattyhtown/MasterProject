"""Tests for AdaptiveSelector.

NOTE: ALL DATA IN THIS FILE IS SYNTHETIC.
No real market data or ORATS API calls are used. The _summary() helper
generates fabricated IV rank, skew, and contango values that exercise
the selection logic but do not reflect real vol surface conditions.
"""

from apex_sharpe.selection.adaptive_selector import AdaptiveSelector
from apex_sharpe.types import TradeStructure


def _summary(iv_rank=50, skew=0.02, contango=0.05):
    return {
        "ivRank1m": iv_rank,
        "dlt25Iv30d": 0.20 + skew,
        "dlt75Iv30d": 0.20,
        "contango": contango,
    }


def test_high_iv_high_skew_favors_bps():
    a = AdaptiveSelector()
    result = a.select(_summary(iv_rank=60, skew=0.03), core_count=3)
    top = result[0][0]
    assert top == TradeStructure.BULL_PUT_SPREAD


def test_low_iv_strong_signal_favors_long_call():
    a = AdaptiveSelector()
    result = a.select(_summary(iv_rank=20, skew=0.01, contango=0.02),
                      core_count=4)
    top = result[0][0]
    assert top == TradeStructure.LONG_CALL


def test_moderate_iv_favors_cds():
    a = AdaptiveSelector()
    result = a.select(_summary(iv_rank=40, skew=0.015), core_count=3)
    # CDS should be competitive in moderate conditions
    structures = [r[0] for r in result]
    assert TradeStructure.CALL_DEBIT_SPREAD in structures[:3]


def test_very_strong_signal_enables_crs():
    a = AdaptiveSelector()
    result = a.select(_summary(iv_rank=45), core_count=5)
    structures = [r[0] for r in result]
    # CRS should rank higher with 5 signals
    crs_idx = structures.index(TradeStructure.CALL_RATIO_SPREAD)
    assert crs_idx <= 2


def test_select_top_returns_single():
    a = AdaptiveSelector()
    structure, reason = a.select_top(_summary(), core_count=3)
    assert isinstance(structure, TradeStructure)
    assert isinstance(reason, str)
    assert len(reason) > 0


def test_all_structures_ranked():
    a = AdaptiveSelector()
    result = a.select(_summary(), core_count=3)
    structures = [r[0] for r in result]
    assert len(structures) == 5
    assert set(structures) == set(TradeStructure)
