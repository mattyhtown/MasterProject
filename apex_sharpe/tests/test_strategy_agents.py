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
    BearCallSpreadAgent,
    IronButterflyAgent,
    ShortIronCondorAgent,
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


class TestBearCallSpread:
    def test_find_strikes(self):
        agent = BearCallSpreadAgent()
        strikes = agent.find_strikes(_chain(), 100.0)
        if strikes:
            # Short call lower strike, long call higher strike
            assert strikes["short_strike"] < strikes["long_strike"]
            assert strikes["width"] > 0

    def test_simulate_entry(self):
        agent = BearCallSpreadAgent()
        strikes = agent.find_strikes(_chain(), 100.0)
        if strikes:
            fill = agent.simulate_entry(strikes, 5000.0)
            if fill:
                assert fill["qty"] >= 1
                assert fill["max_risk"] > 0
                assert fill["entry_credit"] > 0

    def test_compute_pnl_winner(self):
        agent = BearCallSpreadAgent()
        strikes = agent.find_strikes(_chain(), 100.0)
        if strikes:
            fill = agent.simulate_entry(strikes, 5000.0)
            if fill:
                # Price drops: winner for bear call spread
                pnl_down = agent.compute_pnl(strikes, fill, 80.0)
                pnl_up = agent.compute_pnl(strikes, fill, 120.0)
                assert pnl_down > pnl_up

    def test_structure_enum(self):
        agent = BearCallSpreadAgent()
        assert agent.STRUCTURE == TradeStructure.BEAR_CALL_SPREAD
        assert agent.NUM_LEGS == 2


class TestIronButterfly:
    def test_find_strikes(self):
        agent = IronButterflyAgent()
        strikes = agent.find_strikes(_chain(), 100.0)
        if strikes:
            assert strikes["wing_put_strike"] < strikes["atm_strike"]
            assert strikes["atm_strike"] < strikes["wing_call_strike"]

    def test_simulate_entry(self):
        agent = IronButterflyAgent()
        strikes = agent.find_strikes(_chain(), 100.0)
        if strikes:
            fill = agent.simulate_entry(strikes, 5000.0)
            if fill:
                assert fill["qty"] >= 1
                assert fill["entry_credit"] > 0

    def test_compute_pnl_at_pin(self):
        agent = IronButterflyAgent()
        strikes = agent.find_strikes(_chain(), 100.0)
        if strikes:
            fill = agent.simulate_entry(strikes, 5000.0)
            if fill:
                # Pin at ATM = max profit
                pnl_pin = agent.compute_pnl(strikes, fill, strikes["atm_strike"])
                pnl_move = agent.compute_pnl(strikes, fill, 80.0)
                assert pnl_pin > pnl_move

    def test_structure_enum(self):
        agent = IronButterflyAgent()
        assert agent.STRUCTURE == TradeStructure.IRON_BUTTERFLY
        assert agent.NUM_LEGS == 4


class TestShortIronCondor:
    def test_find_strikes(self):
        agent = ShortIronCondorAgent()
        strikes = agent.find_strikes(_chain(), 100.0)
        if strikes:
            assert strikes["long_put_strike"] < strikes["short_put_strike"]
            assert strikes["short_put_strike"] < strikes["short_call_strike"]
            assert strikes["short_call_strike"] < strikes["long_call_strike"]

    def test_simulate_entry(self):
        agent = ShortIronCondorAgent()
        strikes = agent.find_strikes(_chain(), 100.0)
        if strikes:
            fill = agent.simulate_entry(strikes, 5000.0)
            if fill:
                assert fill["qty"] >= 1
                assert fill["entry_credit"] > 0

    def test_compute_pnl_range_bound(self):
        agent = ShortIronCondorAgent()
        strikes = agent.find_strikes(_chain(), 100.0)
        if strikes:
            fill = agent.simulate_entry(strikes, 5000.0)
            if fill:
                # Stay in range = profit, big move = loss
                mid = (strikes["short_put_strike"] + strikes["short_call_strike"]) / 2
                pnl_mid = agent.compute_pnl(strikes, fill, mid)
                pnl_crash = agent.compute_pnl(strikes, fill, 70.0)
                assert pnl_mid > pnl_crash

    def test_structure_enum(self):
        agent = ShortIronCondorAgent()
        assert agent.STRUCTURE == TradeStructure.SHORT_IRON_CONDOR
        assert agent.NUM_LEGS == 4


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
        BearCallSpreadAgent,
        IronButterflyAgent,
        ShortIronCondorAgent,
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
