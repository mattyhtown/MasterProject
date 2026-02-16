"""
Shared test fixtures for APEX-SHARPE agent tests.

Provides mock ORATS responses, sample positions, and canned data
so tests run with zero API calls.

NOTE: ALL DATA IN THIS FILE IS SYNTHETIC.
Strike prices, deltas, bid/ask prices, IV rank, and stock prices are
fabricated values chosen to exercise agent logic. They are loosely
modeled on real SPY option chain structure but are NOT derived from
actual ORATS API responses or historical market data.
"""

import pytest
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Sample position (matches positions.json format)
# ---------------------------------------------------------------------------

SAMPLE_POSITION = {
    "id": "IC-SPY-20260209",
    "symbol": "SPY",
    "type": "IRON_CONDOR",
    "entry_date": "2026-02-09",
    "expiration": "2026-03-13",
    "entry_credit": 3.98,
    "entry_stock_price": 694.42,
    "legs": [
        {"type": "PUT",  "strike": 605, "action": "BUY",  "entry_price": 0.93, "delta": -0.049},
        {"type": "PUT",  "strike": 659, "action": "SELL", "entry_price": 3.62, "delta": -0.16},
        {"type": "CALL", "strike": 721, "action": "SELL", "entry_price": 1.69, "delta": 0.161},
        {"type": "CALL", "strike": 735, "action": "BUY",  "entry_price": 0.40, "delta": 0.055},
    ],
    "max_profit": 398.0,
    "max_loss": 5002.0,
    "breakeven_lower": 655.02,
    "breakeven_upper": 724.98,
    "exit_rules": {"profit_target_pct": 0.5, "dte_exit": 21, "delta_max": 0.3},
    "status": "OPEN",
}


# ---------------------------------------------------------------------------
# Mock ORATS API responses
# ---------------------------------------------------------------------------

MOCK_IV_RANK = {
    "data": [
        {"ticker": "SPY", "ivRank1m": 42.5, "ivPct1m": 55.0, "ivRank1y": 38.0, "ivPct1y": 50.0}
    ]
}

MOCK_SUMMARIES = {
    "data": [
        {
            "ticker": "SPY",
            "stockPrice": 690.0,
            "annActDiv": 6.5,
            "ivMean30": 0.145,
        }
    ]
}

MOCK_EXPIRATIONS = {
    "data": [
        "2026-03-06",
        "2026-03-13",
        "2026-03-20",
        "2026-03-27",
        "2026-04-17",
        "2026-05-15",
    ]
}


def _make_strike(strike, call_delta, put_bid, put_ask, call_bid, call_ask, expir="2026-03-13"):
    """Helper to build a single mock strike row."""
    return {
        "ticker": "SPY",
        "expirDate": expir,
        "strike": strike,
        "delta": call_delta,
        "putBidPrice": put_bid,
        "putAskPrice": put_ask,
        "callBidPrice": call_bid,
        "callAskPrice": call_ask,
        "putValue": (put_bid + put_ask) / 2,
        "callValue": (call_bid + call_ask) / 2,
        "stockPrice": 690.0,
    }


MOCK_CHAIN = {
    "data": [
        # Long put ~5 delta
        _make_strike(610, 0.95, 0.80, 1.00, 82.0, 84.0),
        # Short put ~16 delta
        _make_strike(660, 0.84, 3.20, 3.60, 38.0, 40.0),
        # Short call ~16 delta
        _make_strike(720, 0.16, 31.0, 33.0, 1.50, 1.90),
        # Long call ~5 delta
        _make_strike(740, 0.05, 51.0, 53.0, 0.30, 0.50),
    ]
}


# ---------------------------------------------------------------------------
# Mock ORATS summaries for 0DTE signals
# ---------------------------------------------------------------------------

MOCK_0DTE_SUMMARIES = {
    "data": [
        {
            "ticker": "SPX",
            "stockPrice": 5800.0,
            "ivMean30": 0.18,
            "iv30": 0.17,
            "iv60": 0.19,
            "iv90": 0.20,
            "orHv20": 0.15,
            "orIvFcst20d": 0.16,
            "impliedEarningsMove": 0.0,
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
    ]
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_position():
    """A sample open SPY iron condor position."""
    return dict(SAMPLE_POSITION)


@pytest.fixture
def mock_orats():
    """Mock ORATSClient that returns canned data without API calls."""
    client = MagicMock()
    client.iv_rank.return_value = MOCK_IV_RANK
    client.summaries.return_value = MOCK_SUMMARIES
    client.expirations.return_value = MOCK_EXPIRATIONS
    client.chain.return_value = MOCK_CHAIN
    client.hist_summaries.return_value = {"data": [MOCK_0DTE_SUMMARIES["data"][0]]}
    client.hist_dailies.return_value = {"data": [{"ticker": "SPY", "close": 690.0}]}
    return client


@pytest.fixture
def sample_candidate():
    """A sample IC candidate (output of ScannerAgent)."""
    return {
        "symbol": "SPY",
        "expiration": "2026-03-20",
        "dte": 33,
        "stock_price": 690.0,
        "iv_rank": 42.5,
        "legs": [
            {"type": "PUT",  "action": "BUY",  "strike": 610, "price": 1.00, "delta": -0.05},
            {"type": "PUT",  "action": "SELL", "strike": 660, "price": 3.20, "delta": -0.16},
            {"type": "CALL", "action": "SELL", "strike": 720, "price": 1.50, "delta": 0.16},
            {"type": "CALL", "action": "BUY",  "strike": 740, "price": 0.50, "delta": 0.05},
        ],
        "total_credit": 3.20,
        "max_profit": 320.0,
        "max_loss": 4680.0,
        "breakeven_lower": 656.80,
        "breakeven_upper": 723.20,
        "put_width": 50,
        "call_width": 20,
    }
