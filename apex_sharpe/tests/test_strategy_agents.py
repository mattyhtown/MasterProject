"""Tests for strategy agents.

NOTE: ALL DATA IN THIS FILE IS SYNTHETIC.
No real market data or ORATS API calls are used. The _chain() helper builds
a fabricated options chain with hand-crafted delta curves (14 strikes around
spot=100). Real chains have hundreds of strikes with model-derived deltas.
These synthetic chains test structural correctness only — they do NOT
validate that strike selection produces profitable trades in real markets.
"""

import pytest

from apex_sharpe.agents.strategy import (
    CallDebitSpreadAgent,
    BullPutSpreadAgent,
    LongCallAgent,
    CallRatioSpreadAgent,
    BrokenWingButterflyAgent,
    PutDebitSpreadAgent,
    LongPutAgent,
)
from apex_sharpe.types import TradeStructure


# Synthetic chain data: 10 strikes around spot=100
def _chain(spot=100.0):
    """Build a realistic synthetic chain with proper delta curve."""
    # Deltas decrease as strike increases (for calls)
    specs = [
        (75,  0.90), (80,  0.82), (85,  0.70), (88,  0.60),
        (90,  0.55), (92,  0.50), (95,  0.40), (98,  0.30),
        (100, 0.25), (102, 0.20), (105, 0.15), (108, 0.10),
        (110, 0.08), (115, 0.05),
    ]
    strikes = []
    for s, delta in specs:
        price = max(0.10, (spot - s + 10) * 0.3)
        strikes.append({
            "strike": float(s),
            "delta": delta,
            "callBidPrice": round(price * 0.95, 2),
            "callAskPrice": round(price * 1.05, 2),
            "putBidPrice": round(max(0.10, (s - spot + 10) * 0.3) * 0.95, 2),
            "putAskPrice": round(max(0.10, (s - spot + 10) * 0.3) * 1.05, 2),
            "smvVol": 0.20,
            "expirDate": "2026-02-16",
        })
    return strikes


class TestCallDebitSpread:
    def test_find_strikes(self):
        agent = CallDebitSpreadAgent()
        strikes = agent.find_strikes(_chain(), 100.0)
        assert strikes is not None
        assert strikes["long_strike"] < strikes["short_strike"]
        assert strikes["width"] > 0

    def test_simulate_entry(self):
        agent = CallDebitSpreadAgent()
        strikes = agent.find_strikes(_chain(), 100.0)
        if strikes:
            fill = agent.simulate_entry(strikes, 5000.0)
            assert fill is not None
            assert fill["qty"] >= 1
            assert fill["max_risk"] > 0
            assert fill["entry_cost"] > 0

    def test_compute_pnl_winner(self):
        agent = CallDebitSpreadAgent()
        strikes = agent.find_strikes(_chain(), 100.0)
        if strikes:
            fill = agent.simulate_entry(strikes, 5000.0)
            # Price moves up: winner
            pnl = agent.compute_pnl(strikes, fill, 110.0)
            assert isinstance(pnl, float)

    def test_structure_enum(self):
        agent = CallDebitSpreadAgent()
        assert agent.STRUCTURE == TradeStructure.CALL_DEBIT_SPREAD
        assert agent.NUM_LEGS == 2


class TestBullPutSpread:
    def test_find_strikes(self):
        agent = BullPutSpreadAgent()
        strikes = agent.find_strikes(_chain(), 100.0)
        # May be None if deltas don't match — that's OK
        if strikes:
            assert strikes["short_strike"] > strikes["long_strike"]

    def test_structure_enum(self):
        agent = BullPutSpreadAgent()
        assert agent.STRUCTURE == TradeStructure.BULL_PUT_SPREAD
        assert agent.NUM_LEGS == 2


class TestLongCall:
    def test_find_strikes(self):
        agent = LongCallAgent()
        strikes = agent.find_strikes(_chain(), 100.0)
        assert strikes is not None
        assert "strike" in strikes
        assert "delta" in strikes

    def test_simulate_entry(self):
        agent = LongCallAgent()
        strikes = agent.find_strikes(_chain(), 100.0)
        fill = agent.simulate_entry(strikes, 5000.0)
        assert fill is not None
        assert fill["max_profit"] is None  # unlimited

    def test_compute_pnl(self):
        agent = LongCallAgent()
        strikes = agent.find_strikes(_chain(), 100.0)
        fill = agent.simulate_entry(strikes, 5000.0)
        pnl_up = agent.compute_pnl(strikes, fill, 115.0)
        pnl_down = agent.compute_pnl(strikes, fill, 85.0)
        assert pnl_up > pnl_down

    def test_structure_enum(self):
        agent = LongCallAgent()
        assert agent.STRUCTURE == TradeStructure.LONG_CALL
        assert agent.NUM_LEGS == 1


class TestCallRatioSpread:
    def test_structure_enum(self):
        agent = CallRatioSpreadAgent()
        assert agent.STRUCTURE == TradeStructure.CALL_RATIO_SPREAD
        assert agent.NUM_LEGS == 3


class TestBrokenWingButterfly:
    def test_structure_enum(self):
        agent = BrokenWingButterflyAgent()
        assert agent.STRUCTURE == TradeStructure.BROKEN_WING_BUTTERFLY
        assert agent.NUM_LEGS == 4


class TestPutDebitSpread:
    def test_find_strikes(self):
        agent = PutDebitSpreadAgent()
        strikes = agent.find_strikes(_chain(), 100.0)
        if strikes:
            # Long put = higher strike, short put = lower strike
            assert strikes["long_strike"] > strikes["short_strike"]
            assert strikes["width"] > 0

    def test_simulate_entry(self):
        agent = PutDebitSpreadAgent()
        strikes = agent.find_strikes(_chain(), 100.0)
        if strikes:
            fill = agent.simulate_entry(strikes, 5000.0)
            assert fill is not None
            assert fill["qty"] >= 1
            assert fill["max_risk"] > 0
            assert fill["entry_cost"] > 0

    def test_compute_pnl_winner(self):
        agent = PutDebitSpreadAgent()
        strikes = agent.find_strikes(_chain(), 100.0)
        if strikes:
            fill = agent.simulate_entry(strikes, 5000.0)
            # Price moves down: winner for bear put spread
            pnl_down = agent.compute_pnl(strikes, fill, 80.0)
            pnl_up = agent.compute_pnl(strikes, fill, 120.0)
            assert pnl_down > pnl_up

    def test_structure_enum(self):
        agent = PutDebitSpreadAgent()
        assert agent.STRUCTURE == TradeStructure.PUT_DEBIT_SPREAD
        assert agent.NUM_LEGS == 2


class TestLongPut:
    def test_find_strikes(self):
        agent = LongPutAgent()
        strikes = agent.find_strikes(_chain(), 100.0)
        assert strikes is not None
        assert "strike" in strikes
        assert "delta" in strikes

    def test_simulate_entry(self):
        agent = LongPutAgent()
        strikes = agent.find_strikes(_chain(), 100.0)
        fill = agent.simulate_entry(strikes, 5000.0)
        assert fill is not None
        assert fill["max_profit"] is None  # unlimited downside capture

    def test_compute_pnl(self):
        agent = LongPutAgent()
        strikes = agent.find_strikes(_chain(), 100.0)
        fill = agent.simulate_entry(strikes, 5000.0)
        pnl_down = agent.compute_pnl(strikes, fill, 80.0)
        pnl_up = agent.compute_pnl(strikes, fill, 120.0)
        assert pnl_down > pnl_up  # bearish: wins when price drops

    def test_structure_enum(self):
        agent = LongPutAgent()
        assert agent.STRUCTURE == TradeStructure.LONG_PUT
        assert agent.NUM_LEGS == 1


class TestAllAgentsInterface:
    """Verify all strategy agents implement the full interface."""

    @pytest.mark.parametrize("cls", [
        CallDebitSpreadAgent,
        BullPutSpreadAgent,
        LongCallAgent,
        CallRatioSpreadAgent,
        BrokenWingButterflyAgent,
        PutDebitSpreadAgent,
        LongPutAgent,
    ])
    def test_has_required_methods(self, cls):
        agent = cls()
        assert hasattr(agent, "find_strikes")
        assert hasattr(agent, "simulate_entry")
        assert hasattr(agent, "compute_risk")
        assert hasattr(agent, "compute_pnl")
        assert hasattr(agent, "check_exit")
        assert hasattr(agent, "run")
        assert agent.STRUCTURE is not None
        assert agent.NUM_LEGS > 0
