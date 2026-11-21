"""Unit tests for UnusualWhalesClient.

HTTP is mocked — no live Unusual Whales API calls.
"""

import io
import json
from unittest.mock import MagicMock, patch

import pytest

from apex_sharpe.config import UnusualWhalesCfg
from apex_sharpe.data.unusual_whales import (
    MISSING_KEY_MSG,
    UnusualWhalesClient,
    UnusualWhalesError,
)


MOCK_FLOW_ALERTS = {
    "data": [
        {
            "id": "alert-1",
            "ticker": "SPY",
            "option_chain": "SPY260321C00690000",
            "total_premium": 125000.0,
            "total_size": 500,
            "has_sweep": True,
        }
    ]
}


def _cfg(api_key: str = "test-uw-key") -> UnusualWhalesCfg:
    return UnusualWhalesCfg(api_key=api_key)


def _mock_urlopen(payload):
    """Return a urlopen stand-in that yields JSON bytes."""
    body = json.dumps(payload).encode()
    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


class TestMissingKey:
    def test_flow_alerts_raises_when_key_missing(self):
        client = UnusualWhalesClient(_cfg(api_key=""))
        with pytest.raises(UnusualWhalesError, match="UNUSUAL_WHALES_API_KEY"):
            client.flow_alerts()

    def test_option_trades_raises_when_key_blank(self):
        client = UnusualWhalesClient(_cfg(api_key="   "))
        with pytest.raises(UnusualWhalesError) as exc_info:
            client.option_trades(ticker_symbol="SPY")
        assert "UNUSUAL_WHALES_API_KEY" in str(exc_info.value)
        assert str(exc_info.value) == MISSING_KEY_MSG

    def test_missing_key_does_not_open_socket(self):
        client = UnusualWhalesClient(_cfg(api_key=""))
        with patch("apex_sharpe.data.unusual_whales.urllib.request.urlopen") as mock_open:
            with pytest.raises(UnusualWhalesError):
                client.flow_alerts(ticker_symbol="SPY")
            mock_open.assert_not_called()


class TestFlowAlertsMocked:
    def test_successful_flow_alerts_parse(self):
        client = UnusualWhalesClient(_cfg())
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["headers"] = {k.lower(): v for k, v in req.header_items()}
            captured["timeout"] = timeout
            return _mock_urlopen(MOCK_FLOW_ALERTS)

        with patch(
            "apex_sharpe.data.unusual_whales.urllib.request.urlopen",
            side_effect=fake_urlopen,
        ):
            result = client.flow_alerts(ticker_symbol="SPY", limit=10)

        assert result["data"][0]["ticker"] == "SPY"
        assert result["data"][0]["total_premium"] == 125000.0
        assert result["data"][0]["option_chain"] == "SPY260321C00690000"
        assert captured["url"].startswith(
            "https://api.unusualwhales.com/api/option-trades/flow-alerts?"
        )
        assert "ticker_symbol=SPY" in captured["url"]
        assert "limit=10" in captured["url"]
        assert captured["headers"]["authorization"] == "Bearer test-uw-key"
        assert captured["headers"]["accept"] == "application/json"
        assert captured["timeout"] == 30


class TestOptionTradesMocked:
    def test_option_trades_passthrough(self):
        client = UnusualWhalesClient(_cfg())
        captured = {}
        payload = {"data": [{"ticker": "AAPL", "premium": 50000}]}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            return _mock_urlopen(payload)

        with patch(
            "apex_sharpe.data.unusual_whales.urllib.request.urlopen",
            side_effect=fake_urlopen,
        ):
            result = client.option_trades(ticker_symbol="AAPL,INTC", min_premium=25000)

        assert result["data"][0]["ticker"] == "AAPL"
        assert captured["url"].startswith(
            "https://api.unusualwhales.com/api/option-trades?"
        )
        assert "ticker_symbol=AAPL,INTC" in captured["url"]
        assert "min_premium=25000" in captured["url"]


class TestHttpErrors:
    def test_http_error_surfaces_status(self):
        import urllib.error

        client = UnusualWhalesClient(_cfg())
        err = urllib.error.HTTPError(
            url="https://api.unusualwhales.com/api/option-trades/flow-alerts",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=io.BytesIO(b'{"error":"invalid token"}'),
        )
        with patch(
            "apex_sharpe.data.unusual_whales.urllib.request.urlopen",
            side_effect=err,
        ):
            with pytest.raises(UnusualWhalesError, match="HTTP 401"):
                client.flow_alerts()


class TestOptionContractsPagination:
    def test_duplicate_symbols_across_pages_are_kept_once(self):
        client = UnusualWhalesClient(_cfg())
        page0 = {"data": [{"option_symbol": f"SPXW261116C0{7000 + i:04d}000",
                           "volume": 100 - i} for i in range(3)]}
        # Volume re-ordered between requests: the last row of page 0 shows up
        # again on page 1 with an updated volume.
        page1 = {"data": [{"option_symbol": "SPXW261116C07002000", "volume": 999},
                          {"option_symbol": "SPXW261116C07003000", "volume": 1}]}
        by_page = {0: page0, 1: page1}
        requested = []

        def fake_get(path, params):
            assert path == "/api/stock/SPX/option-contracts"
            assert params["expiry"] == "2026-11-16" and params["limit"] == 3
            requested.append(params["page"])
            return by_page[params["page"]]

        with patch.object(client, "get", side_effect=fake_get):
            rows = client.option_contracts("SPX", "2026-11-16", page_size=3)
        assert requested == [0, 1]  # pages are 0-based and advance
        symbols = [r["option_symbol"] for r in rows]
        assert len(symbols) == len(set(symbols)) == 4
        latest = {r["option_symbol"]: r["volume"] for r in rows}
        assert latest["SPXW261116C07002000"] == 999

    def test_malformed_page_raises_instead_of_returning_partial_chain(self):
        client = UnusualWhalesClient(_cfg())
        with patch.object(client, "get", return_value={"error": "nope"}):
            with pytest.raises(UnusualWhalesError, match="no 'data' list"):
                client.option_contracts("SPX", "2026-11-16")

    def test_chain_longer_than_max_pages_raises(self):
        client = UnusualWhalesClient(_cfg())
        requested = []

        def fake_get(path, params):  # distinct symbols on every page, probe included
            requested.append(params["page"])
            p = params["page"]
            return {"data": [{"option_symbol": f"S{p}-{i}"} for i in range(2)]}

        with patch.object(client, "get", side_effect=fake_get):
            with pytest.raises(UnusualWhalesError, match="partial chain"):
                client.option_contracts("SPX", "2026-11-16", page_size=2, max_pages=3)
        assert requested == [0, 1, 2, 3]

    def test_page_size_above_endpoint_cap_is_rejected(self):
        client = UnusualWhalesClient(_cfg())
        with patch.object(client, "get") as mock_get:
            with pytest.raises(ValueError, match="1-500"):
                client.option_contracts("SPX", "2026-11-16", page_size=501)
            mock_get.assert_not_called()

    def test_chain_exactly_filling_page_budget_is_complete(self):
        client = UnusualWhalesClient(_cfg())
        pages = {0: {"data": [{"option_symbol": "A"}, {"option_symbol": "B"}]},
                 1: {"data": [{"option_symbol": "C"}, {"option_symbol": "D"}]},
                 2: {"data": []}}
        requested = []

        def fake_get(path, params):
            requested.append(params["page"])
            return pages[params["page"]]

        with patch.object(client, "get", side_effect=fake_get):
            rows = client.option_contracts("SPX", "2026-11-16", page_size=2, max_pages=2)
        assert [r["option_symbol"] for r in rows] == ["A", "B", "C", "D"]
        assert requested == [0, 1, 2]  # the empty probe page was fetched

    @pytest.mark.parametrize("bad_row", [["not", "a", "dict"], {"option_symbol": ["x"]},
                                         {"option_symbol": ""}, {"option_symbol": "   "},
                                         {"volume": 1}])
    def test_malformed_row_raises_client_error(self, bad_row):
        client = UnusualWhalesClient(_cfg())
        with patch.object(client, "get", return_value={"data": [bad_row]}):
            with pytest.raises(UnusualWhalesError, match="row 0"):
                client.option_contracts("SPX", "2026-11-16")

    def test_zero_page_budget_is_rejected_without_a_request(self):
        client = UnusualWhalesClient(_cfg())
        with patch.object(client, "get") as mock_get:
            with pytest.raises(ValueError, match="max_pages"):
                client.option_contracts("SPX", "2026-11-16", max_pages=0)
            mock_get.assert_not_called()
