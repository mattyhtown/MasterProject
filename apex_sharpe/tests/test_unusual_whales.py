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
