"""Tests for ChainIngestAgent — intraday chain snapshots.

NOTE: ALL DATA IN THIS FILE IS SYNTHETIC.
Supabase calls are mocked. No real DB or IB connection is made.
"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from datetime import date, datetime

from apex_sharpe.config import ChainIngestCfg, SupabaseCfg
from apex_sharpe.agents.chain_ingest import ChainIngestAgent


# ---------------------------------------------------------------------------
# Mock chain data (ORATS format)
# ---------------------------------------------------------------------------

MOCK_ORATS_CHAIN = {
    "data": [
        {
            "strike": 590.0, "expirDate": "2026-03-13", "stockPrice": 600.0,
            "delta": 0.85, "gamma": 0.005, "theta": -0.15, "vega": 0.30,
            "callBidPrice": 14.20, "callAskPrice": 14.50, "callValue": 14.35,
            "callSmvVol": 0.18,
            "putBidPrice": 1.80, "putAskPrice": 2.10, "putValue": 1.95,
            "putSmvVol": 0.20,
        },
        {
            "strike": 600.0, "expirDate": "2026-03-13", "stockPrice": 600.0,
            "delta": 0.52, "gamma": 0.012, "theta": -0.25, "vega": 0.45,
            "callBidPrice": 7.50, "callAskPrice": 7.80, "callValue": 7.65,
            "callSmvVol": 0.17,
            "putBidPrice": 5.10, "putAskPrice": 5.40, "putValue": 5.25,
            "putSmvVol": 0.19,
        },
        {
            "strike": 610.0, "expirDate": "2026-03-13", "stockPrice": 600.0,
            "delta": 0.25, "gamma": 0.008, "theta": -0.18, "vega": 0.35,
            "callBidPrice": 3.00, "callAskPrice": 3.30, "callValue": 3.15,
            "callSmvVol": 0.19,
            "putBidPrice": 10.60, "putAskPrice": 10.90, "putValue": 10.75,
            "putSmvVol": 0.21,
        },
    ]
}

MOCK_ORATS_CHAIN_MULTI = {
    "data": [
        # Expiry 1
        {"strike": 590.0, "expirDate": "2026-03-13", "stockPrice": 600.0,
         "delta": 0.85, "callBidPrice": 14.20, "callAskPrice": 14.50,
         "putBidPrice": 1.80, "putAskPrice": 2.10},
        {"strike": 600.0, "expirDate": "2026-03-13", "stockPrice": 600.0,
         "delta": 0.52, "callBidPrice": 7.50, "callAskPrice": 7.80,
         "putBidPrice": 5.10, "putAskPrice": 5.40},
        # Expiry 2
        {"strike": 590.0, "expirDate": "2026-03-20", "stockPrice": 600.0,
         "delta": 0.82, "callBidPrice": 15.00, "callAskPrice": 15.40,
         "putBidPrice": 2.50, "putAskPrice": 2.80},
        {"strike": 600.0, "expirDate": "2026-03-20", "stockPrice": 600.0,
         "delta": 0.50, "callBidPrice": 8.80, "callAskPrice": 9.10,
         "putBidPrice": 6.30, "putAskPrice": 6.60},
    ]
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_mock_db(enabled=True):
    """Create a mock SupabaseSync."""
    db = MagicMock()
    db.enabled = enabled
    db.client = MagicMock()
    return db


def _make_agent(orats_chain=None, db_enabled=True):
    """Create ChainIngestAgent with mocked dependencies."""
    agent = ChainIngestAgent(
        config=ChainIngestCfg(
            tickers=("SPY",),
            poll_interval=60,
            strike_range=50.0,
            source="orats",
            max_expiries=3,
            dte_max=45,
        ),
        supabase_cfg=SupabaseCfg(),
    )
    # Replace DB with mock
    mock_db = _make_mock_db(db_enabled)
    agent._db = mock_db

    # Mock ORATS — chain returns data, summaries returns None
    # (so vol surface snapshot is skipped cleanly)
    mock_orats = MagicMock()
    mock_orats.chain.return_value = orats_chain
    mock_orats.summaries.return_value = None
    agent._orats = mock_orats

    # Prevent file I/O in tests
    agent._cache = {}
    agent._save_cache = MagicMock()

    return agent, mock_db


# ---------------------------------------------------------------------------
# Tests: Ingestion
# ---------------------------------------------------------------------------

class TestChainIngest:

    def test_ingest_stores_correct_row_count(self):
        """Single expiry, 3 strikes = 3 rows upserted."""
        agent, mock_db = _make_agent(MOCK_ORATS_CHAIN)

        result = agent.run({"action": "ingest", "tickers": ["SPY"]})

        assert result.success
        assert result.data["rows_inserted"] == 3
        # Verify upsert was called on chain_snapshots
        mock_db.client.table.assert_called_with("chain_snapshots")
        upsert_call = mock_db.client.table.return_value.upsert
        assert upsert_call.called
        rows = upsert_call.call_args[0][0]
        assert len(rows) == 3

    def test_ingest_multi_expiry(self):
        """Two expiries, 2 strikes each = 4 rows."""
        agent, mock_db = _make_agent(MOCK_ORATS_CHAIN_MULTI)

        result = agent.run({"action": "ingest", "tickers": ["SPY"]})

        assert result.success
        assert result.data["rows_inserted"] == 4

    def test_ingest_normalizes_fields(self):
        """Verify row fields match Supabase column names."""
        agent, mock_db = _make_agent(MOCK_ORATS_CHAIN)

        agent.run({"action": "ingest", "tickers": ["SPY"]})

        rows = mock_db.client.table.return_value.upsert.call_args[0][0]
        row = rows[0]
        assert "snapshot_time" in row
        assert "ticker" in row
        assert row["ticker"] == "SPY"
        assert "expir_date" in row
        assert "strike" in row
        assert "call_bid" in row
        assert "call_mid" in row
        assert "put_bid" in row
        assert "put_mid" in row
        assert "delta" in row
        assert "source" in row
        assert row["source"] == "orats"

    def test_ingest_no_data(self):
        """No chain data returns errors."""
        agent, mock_db = _make_agent(None)

        result = agent.run({"action": "ingest", "tickers": ["SPY"]})

        assert len(result.errors) > 0
        assert "No chain data" in result.errors[0]

    def test_ingest_db_disabled_uses_local_cache(self):
        """Disabled DB still counts rows (stored locally)."""
        agent, mock_db = _make_agent(MOCK_ORATS_CHAIN, db_enabled=False)

        result = agent.run({"action": "ingest", "tickers": ["SPY"]})

        # Should succeed with local-only storage
        assert result.success
        assert result.data["rows_inserted"] == 3


# ---------------------------------------------------------------------------
# Tests: Query — latest
# ---------------------------------------------------------------------------

class TestChainLatest:

    def _setup_latest_query(self, agent, mock_db, rows):
        """Wire up mock Supabase for a latest query."""
        ts_mock = MagicMock()
        ts_mock.data = [{"snapshot_time": "2026-02-16T15:00:00Z"}]

        rows_mock = MagicMock()
        rows_mock.data = rows

        call_count = [0]
        def table_side_effect(name):
            call_count[0] += 1
            if call_count[0] <= 1:
                chain = MagicMock()
                chain.select.return_value = chain
                chain.eq.return_value = chain
                chain.order.return_value = chain
                chain.limit.return_value = chain
                chain.execute.return_value = ts_mock
                return chain
            else:
                chain = MagicMock()
                chain.select.return_value = chain
                chain.eq.return_value = chain
                chain.order.return_value = chain
                chain.execute.return_value = rows_mock
                return chain

        mock_db.client.table.side_effect = table_side_effect

    def test_latest_returns_orats_format(self):
        """Latest query returns ORATS-compatible chain dict."""
        agent, mock_db = _make_agent(MOCK_ORATS_CHAIN)

        supabase_rows = [
            {
                "strike": "600", "expir_date": "2026-03-13",
                "stock_price": "600.0", "delta": "0.52",
                "gamma": "0.012", "theta": "-0.25", "vega": "0.45",
                "call_bid": "7.50", "call_ask": "7.80",
                "call_iv": "0.17",
                "put_bid": "5.10", "put_ask": "5.40",
                "put_iv": "0.19",
            },
        ]
        self._setup_latest_query(agent, mock_db, supabase_rows)

        result = agent.run({"action": "latest", "ticker": "SPY"})

        assert result.success
        chain = result.data["chain"]
        assert "data" in chain
        assert len(chain["data"]) == 1
        row = chain["data"][0]
        assert row["strike"] == 600.0
        assert row["expirDate"] == "2026-03-13"
        assert row["delta"] == 0.52
        assert row["callBidPrice"] == 7.50
        assert row["putAskPrice"] == 5.40

    def test_latest_no_data(self):
        """No data returns failure."""
        agent, mock_db = _make_agent(MOCK_ORATS_CHAIN)

        chain = MagicMock()
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.order.return_value = chain
        chain.limit.return_value = chain
        empty_resp = MagicMock()
        empty_resp.data = []
        chain.execute.return_value = empty_resp
        mock_db.client.table.return_value = chain

        result = agent.run({"action": "latest", "ticker": "XYZ"})

        assert not result.success

    def test_latest_db_disabled(self):
        """Latest query requires Supabase."""
        agent, mock_db = _make_agent(MOCK_ORATS_CHAIN, db_enabled=False)

        result = agent.run({"action": "latest", "ticker": "SPY"})

        assert not result.success
        assert "Supabase not connected" in result.errors[0]


# ---------------------------------------------------------------------------
# Tests: rows_to_chain conversion
# ---------------------------------------------------------------------------

class TestRowsToChain:

    def test_round_trip_fidelity(self):
        """Data survives normalize → insert → query → denormalize."""
        agent, _ = _make_agent(MOCK_ORATS_CHAIN)

        chains = [{"expiry": "2026-03-13", "data": MOCK_ORATS_CHAIN["data"]}]
        rows = agent._normalize_rows(chains, "SPY", "2026-02-16T15:00:00Z", "orats")

        chain = ChainIngestAgent._rows_to_chain(rows)

        assert len(chain["data"]) == 3
        original = MOCK_ORATS_CHAIN["data"][1]  # 600 strike
        restored = chain["data"][1]

        assert restored["strike"] == original["strike"]
        assert restored["delta"] == original["delta"]
        assert restored["callBidPrice"] == original["callBidPrice"]
        assert restored["putAskPrice"] == original["putAskPrice"]
        assert restored["callSmvVol"] == original["callSmvVol"]

    def test_empty_rows(self):
        """Empty rows → empty chain."""
        chain = ChainIngestAgent._rows_to_chain([])
        assert chain == {"data": []}

    def test_null_handling(self):
        """None fields convert to 0."""
        rows = [{"strike": "600", "expir_date": "2026-03-13",
                 "stock_price": None, "delta": None,
                 "gamma": None, "theta": None, "vega": None,
                 "call_bid": None, "call_ask": None,
                 "call_iv": None, "put_bid": None, "put_ask": None,
                 "put_iv": None}]
        chain = ChainIngestAgent._rows_to_chain(rows)
        row = chain["data"][0]
        assert row["strike"] == 600.0
        assert row["delta"] == 0
        assert row["callBidPrice"] == 0


# ---------------------------------------------------------------------------
# Tests: Expiry filtering
# ---------------------------------------------------------------------------

class TestExpiryFilter:

    def test_filters_by_dte_max(self):
        agent, _ = _make_agent(MOCK_ORATS_CHAIN)
        expiries = ["2026-03-01", "2026-03-13", "2026-03-20",
                     "2026-06-19", "2026-09-18"]

        with patch("apex_sharpe.agents.chain_ingest.date") as mock_date:
            mock_date.today.return_value = date(2026, 2, 16)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            result = agent._filter_expiries(expiries)

        assert len(result) == 3
        assert "2026-03-01" in result
        assert "2026-03-13" in result
        assert "2026-03-20" in result

    def test_limits_to_max_expiries(self):
        agent, _ = _make_agent(MOCK_ORATS_CHAIN)
        expiries = [f"2026-02-{d:02d}" for d in range(17, 29)]

        with patch("apex_sharpe.agents.chain_ingest.date") as mock_date:
            mock_date.today.return_value = date(2026, 2, 16)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            result = agent._filter_expiries(expiries)

        assert len(result) <= 3

    def test_handles_yyyymmdd_format(self):
        agent, _ = _make_agent(MOCK_ORATS_CHAIN)
        expiries = ["20260313", "20260320"]

        with patch("apex_sharpe.agents.chain_ingest.date") as mock_date:
            mock_date.today.return_value = date(2026, 2, 16)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            result = agent._filter_expiries(expiries)

        assert len(result) == 2
        assert all("-" in r for r in result)


# ---------------------------------------------------------------------------
# Tests: Schema check
# ---------------------------------------------------------------------------

class TestChainSchema:

    def test_schema_exists(self):
        agent, mock_db = _make_agent(MOCK_ORATS_CHAIN)
        mock_db.client.table.return_value.select.return_value.limit.return_value.execute.return_value = MagicMock()

        result = agent.run({"action": "ensure_schema"})
        assert result.success

    def test_schema_missing(self):
        agent, mock_db = _make_agent(MOCK_ORATS_CHAIN)
        mock_db.client.table.return_value.select.return_value.limit.return_value.execute.side_effect = Exception("table not found")

        result = agent.run({"action": "ensure_schema"})
        assert not result.success


# ---------------------------------------------------------------------------
# Tests: Status
# ---------------------------------------------------------------------------

class TestChainStatus:

    def test_status_empty_cache(self):
        agent, _ = _make_agent(MOCK_ORATS_CHAIN)
        result = agent.run({"action": "status"})
        assert result.success
        assert result.data["cache"] == {}

    def test_status_with_cache_data(self):
        agent, _ = _make_agent(MOCK_ORATS_CHAIN)
        agent._cache = {
            "chain_SPY": {"2026-02-16": [{"time": "t1"}]},
            "vol_surface_SPX": {"2026-02-16": [{"time": "t1"}, {"time": "t2"}]},
        }
        result = agent.run({"action": "status"})
        assert result.success
        assert result.data["cache"]["chain_SPY"]["days"] == 1
        assert result.data["cache"]["chain_SPY"]["entries"] == 1
        assert result.data["cache"]["vol_surface_SPX"]["entries"] == 2
