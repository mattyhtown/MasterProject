"""Tests using REAL ORATS market data.

This file uses captured fixtures from the ORATS API — real strikes,
real deltas, real IV rank, real bid/ask prices. No synthetic data.

To refresh fixtures: python -m apex_sharpe.tests.fixtures.capture_fixtures
To run only these tests: pytest apex_sharpe/tests/test_real_data.py -v
"""

import pytest

from apex_sharpe.tests.fixtures import (
    has_fixtures,
    fixture_chain,
    fixture_summary,
    fixture_ivrank,
    fixture_hist_chain,
    fixture_hist_summary,
)

pytestmark = pytest.mark.skipif(
    not has_fixtures(),
    reason="Real data fixtures not captured. Run: python -m apex_sharpe.tests.fixtures.capture_fixtures",
)


# ---------------------------------------------------------------------------
# Strategy agents with real chains
# ---------------------------------------------------------------------------

class TestStrategyAgentsRealData:
    """Test all 5 strategy agents against real SPY option chain data."""

    @pytest.fixture
    def spy_chain(self):
        chain = fixture_chain("spy")
        assert chain is not None, "No SPY chain fixture found"
        # Get spot from first strike
        spot = chain[0].get("stockPrice", 600.0)
        return chain, spot

    @pytest.fixture
    def spx_chain(self):
        chain = fixture_chain("spx")
        assert chain is not None, "No SPX chain fixture found"
        spot = chain[0].get("stockPrice", 6000.0)
        return chain, spot

    def test_call_debit_spread_real_spy(self, spy_chain):
        from apex_sharpe.agents.strategy import CallDebitSpreadAgent
        chain, spot = spy_chain
        agent = CallDebitSpreadAgent()
        strikes = agent.find_strikes(chain, spot)
        assert strikes is not None, (
            f"CDS failed to find strikes in real SPY chain "
            f"({len(chain)} strikes, spot={spot})"
        )
        assert strikes["long_strike"] < strikes["short_strike"]
        assert strikes["width"] > 0
        # Simulate entry
        fill = agent.simulate_entry(strikes, 10000.0)
        assert fill is not None
        assert fill["qty"] >= 1
        assert fill["max_risk"] > 0
        assert fill["entry_cost"] > 0

    def test_bull_put_spread_real_spy(self, spy_chain):
        from apex_sharpe.agents.strategy import BullPutSpreadAgent
        chain, spot = spy_chain
        agent = BullPutSpreadAgent()
        strikes = agent.find_strikes(chain, spot)
        if strikes:  # May legitimately fail if put deltas don't align
            assert strikes["short_strike"] > strikes["long_strike"]
            fill = agent.simulate_entry(strikes, 10000.0)
            assert fill is not None
            assert fill["entry_credit"] > 0

    def test_long_call_real_spy(self, spy_chain):
        from apex_sharpe.agents.strategy import LongCallAgent
        chain, spot = spy_chain
        agent = LongCallAgent()
        strikes = agent.find_strikes(chain, spot)
        assert strikes is not None
        assert strikes["strike"] > 0
        fill = agent.simulate_entry(strikes, 10000.0)
        assert fill is not None
        assert fill["max_profit"] is None  # unlimited

    def test_call_ratio_spread_real_spy(self, spy_chain):
        from apex_sharpe.agents.strategy import CallRatioSpreadAgent
        chain, spot = spy_chain
        agent = CallRatioSpreadAgent()
        strikes = agent.find_strikes(chain, spot)
        if strikes:
            assert strikes["long_strike"] < strikes["short_strike"]
            assert strikes["ratio"] == "1x2"

    def test_broken_wing_butterfly_real_spy(self, spy_chain):
        from apex_sharpe.agents.strategy import BrokenWingButterflyAgent
        chain, spot = spy_chain
        agent = BrokenWingButterflyAgent()
        strikes = agent.find_strikes(chain, spot)
        if strikes:
            assert strikes["lower_strike"] < strikes["middle_strike"] < strikes["upper_strike"]

    def test_call_debit_spread_real_spx(self, spx_chain):
        from apex_sharpe.agents.strategy import CallDebitSpreadAgent
        chain, spot = spx_chain
        agent = CallDebitSpreadAgent()
        strikes = agent.find_strikes(chain, spot)
        assert strikes is not None, (
            f"CDS failed to find strikes in real SPX chain "
            f"({len(chain)} strikes, spot={spot})"
        )

    def test_all_structures_find_something_spy(self, spy_chain):
        """At least 3 of 5 structures should find valid strikes in real data."""
        from apex_sharpe.agents.strategy import (
            CallDebitSpreadAgent, BullPutSpreadAgent, LongCallAgent,
            CallRatioSpreadAgent, BrokenWingButterflyAgent,
        )
        chain, spot = spy_chain
        found = 0
        for cls in [CallDebitSpreadAgent, BullPutSpreadAgent, LongCallAgent,
                    CallRatioSpreadAgent, BrokenWingButterflyAgent]:
            agent = cls()
            if agent.find_strikes(chain, spot) is not None:
                found += 1
        assert found >= 3, f"Only {found}/5 structures found strikes in real SPY data"


# ---------------------------------------------------------------------------
# Adaptive selector with real vol surface
# ---------------------------------------------------------------------------

class TestAdaptiveSelectorRealData:
    """Test adaptive selection using real ORATS summary data."""

    def test_select_with_real_spx_summary(self):
        from apex_sharpe.selection.adaptive_selector import AdaptiveSelector
        from apex_sharpe.types import TradeStructure
        summary = fixture_summary("spx")
        assert summary is not None, "No SPX summary fixture"
        a = AdaptiveSelector()
        result = a.select(summary, core_count=3)
        assert len(result) == 5
        structures = [r[0] for r in result]
        assert set(structures) == set(TradeStructure)
        # Top pick should have a reason
        _, reason = result[0]
        assert len(reason) > 0

    def test_select_with_real_iv_rank(self):
        from apex_sharpe.selection.adaptive_selector import AdaptiveSelector
        summary = fixture_summary("spx")
        ivrank = fixture_ivrank("spx")
        assert summary is not None
        assert ivrank is not None
        a = AdaptiveSelector()
        iv_rank_val = ivrank.get("ivRank1m", 50)
        result = a.select(summary, core_count=4, iv_rank=iv_rank_val)
        assert len(result) == 5


# ---------------------------------------------------------------------------
# Signal sizer with real capital context
# ---------------------------------------------------------------------------

class TestSignalSizerRealData:
    """Signal sizer tests are math-only (no market data needed).
    But we validate that sizing makes sense relative to real prices."""

    def test_risk_budget_vs_real_spread_width(self):
        """Verify risk budget can actually buy spreads at real prices."""
        from apex_sharpe.selection.signal_sizer import SignalSizer
        from apex_sharpe.agents.strategy import CallDebitSpreadAgent
        chain = fixture_chain("spy")
        if not chain:
            pytest.skip("No SPY chain fixture")
        spot = chain[0].get("stockPrice", 600.0)
        agent = CallDebitSpreadAgent()
        strikes = agent.find_strikes(chain, spot)
        if not strikes:
            pytest.skip("CDS couldn't find strikes in real data")
        sizer = SignalSizer()
        for core_count in [3, 4, 5]:
            sizing = sizer.compute(core_count)
            fill = agent.simulate_entry(strikes, sizing["risk_budget"])
            assert fill is not None, (
                f"Risk budget ${sizing['risk_budget']} can't buy any "
                f"CDS at real prices (core_count={core_count})"
            )
            assert fill["qty"] >= 1


# ---------------------------------------------------------------------------
# Portfolio agent with real signal data
# ---------------------------------------------------------------------------

class TestPortfolioRealData:
    """Test portfolio agent with real vol surface data."""

    def test_portfolio_with_real_summary(self):
        from apex_sharpe.agents.portfolio import PortfolioAgent
        summary = fixture_summary("spx")
        chain = fixture_chain("spy")
        if not summary or not chain:
            pytest.skip("Missing SPX summary or SPY chain fixture")
        spot = chain[0].get("stockPrice", 600.0)
        p = PortfolioAgent()
        result = p.run({
            "signals": {
                "composite": "FEAR_BOUNCE_STRONG",
                "core_count": 3,
                "firing": ["skewing", "rip", "contango"],
            },
            "summary": summary,
            "chain": chain,
            "positions": [],
            "spot": spot,
            "signal_system": "vol_surface",
        })
        assert result.success
        assert result.data["risk_budget"] > 0
        assert result.data["structure"] is not None


# ---------------------------------------------------------------------------
# Historical data validation
# ---------------------------------------------------------------------------

class TestHistoricalDataRealData:
    """Validate that historical fixtures contain expected fields."""

    def test_hist_summary_has_signal_fields(self):
        summary = fixture_hist_summary()
        if not summary:
            pytest.skip("No historical SPX summary fixture")
        # These fields are required for the 10-signal system
        signal_fields = ["skewing", "contango", "rip"]
        for field in signal_fields:
            assert field in summary, f"Historical summary missing '{field}' field"

    def test_hist_chain_has_required_fields(self):
        chain = fixture_hist_chain(dte="30dte")
        if not chain:
            pytest.skip("No historical SPX 30dte chain fixture")
        required = ["strike", "delta", "callBidPrice", "callAskPrice",
                    "putBidPrice", "putAskPrice", "expirDate"]
        sample = chain[0]
        for field in required:
            assert field in sample, f"Historical chain missing '{field}' field"

    def test_hist_0dte_chain_exists(self):
        chain = fixture_hist_chain(dte="0dte")
        if not chain:
            pytest.skip("No historical SPX 0dte chain fixture")
        assert len(chain) > 20, f"0DTE chain only has {len(chain)} strikes"

    def test_hist_summary_field_ranges(self):
        """Sanity check that real data falls in reasonable ranges."""
        summary = fixture_hist_summary()
        if not summary:
            pytest.skip("No historical summary fixture")
        # IV should be 0-200%, stock price > 0
        if "ivMean30" in summary:
            assert 0 < summary["ivMean30"] < 2.0, f"ivMean30={summary['ivMean30']} out of range"
        if "stockPrice" in summary:
            assert summary["stockPrice"] > 100, f"stockPrice={summary['stockPrice']} too low"
        if "skewing" in summary:
            assert -1 < summary["skewing"] < 1, f"skewing={summary['skewing']} out of range"
