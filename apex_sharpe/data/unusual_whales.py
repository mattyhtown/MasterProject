"""
Unusual Whales REST client (stdlib urllib).

Research / paper-data only — no live trading authority. Mirrors ORATSClient
style: config via DI, urllib.request, JSON responses.

Auth: Authorization: Bearer $UNUSUAL_WHALES_API_KEY
Never hardcode the key; load it from UnusualWhalesCfg / the environment.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Mapping, Optional

from ..config import UnusualWhalesCfg

MISSING_KEY_MSG = (
    "UNUSUAL_WHALES_API_KEY is not set. Add it to .env (see .env.example) "
    "or export it in the environment. Unusual Whales is optional next to ORATS "
    "and is research/paper-data only."
)


class UnusualWhalesError(RuntimeError):
    """Raised when the Unusual Whales client cannot complete a request."""


class UnusualWhalesClient:
    """Unusual Whales API client using stdlib urllib. Research/paper only."""

    def __init__(self, config: UnusualWhalesCfg):
        self.api_key = (config.api_key or "").strip()
        self.base_url = config.base_url.rstrip("/")
        self.timeout = config.timeout

    def _require_key(self) -> None:
        if not self.api_key:
            raise UnusualWhalesError(MISSING_KEY_MSG)

    def _encode_params(self, params: Optional[Mapping[str, Any]]) -> str:
        """Drop empty values, stringify the rest, keep commas unencoded."""
        if not params:
            return ""
        cleaned = {}
        for key, value in params.items():
            if value is None or value == "":
                continue
            if isinstance(value, bool):
                cleaned[key] = "true" if value else "false"
            elif isinstance(value, (list, tuple)):
                cleaned[key] = ",".join(str(item) for item in value)
            else:
                cleaned[key] = str(value)
        return urllib.parse.urlencode(cleaned, safe=",")

    def get(
        self,
        path: str,
        params: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Low-level GET. Returns parsed JSON. Raises on missing key or HTTP error."""
        self._require_key()
        if not path.startswith("/"):
            path = f"/{path}"
        qs = self._encode_params(params)
        url = f"{self.base_url}{path}"
        if qs:
            url = f"{url}?{qs}"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace") if exc.fp else ""
            snippet = body[:200].strip()
            detail = f" — {snippet}" if snippet else ""
            raise UnusualWhalesError(
                f"Unusual Whales GET {path} failed: HTTP {exc.code}{detail}"
            ) from exc
        except (urllib.error.URLError, json.JSONDecodeError, OSError) as exc:
            raise UnusualWhalesError(
                f"Unusual Whales GET {path} failed: {exc}"
            ) from exc

    def flow_alerts(self, **params: Any) -> Dict[str, Any]:
        """GET /api/option-trades/flow-alerts with query-param passthrough."""
        return self.get("/api/option-trades/flow-alerts", params)

    def option_trades(self, **params: Any) -> Dict[str, Any]:
        """GET /api/option-trades with query-param passthrough."""
        return self.get("/api/option-trades", params)

    def option_contracts(self, ticker: str, expiry: str, page_size: int = 500,
                         max_pages: int = 10) -> list:
        """All option contracts for one expiry, following pagination.

        The endpoint returns at most 500 rows per page, ordered by volume, and
        volume changes between page requests during the session. A contract can
        therefore appear on two pages; each symbol is kept once, from the
        latest page that returned it.

        Pages are numbered from 0 (an omitted ``page`` is page 0). Raises
        UnusualWhalesError if a page or row is malformed, or if the chain is
        longer than ``max_pages`` pages, rather than returning a partial chain.
        When the last allowed page is full, one extra page is requested to
        confirm the chain ended there.
        """
        if not 0 < page_size <= 500:
            raise ValueError("page_size must be 1-500 (the endpoint's cap)")
        if max_pages < 1:
            raise ValueError("max_pages must be at least 1")
        by_symbol: dict = {}
        for page in range(max_pages + 1):
            payload = self.get(
                f"/api/stock/{ticker}/option-contracts",
                {"expiry": expiry, "limit": page_size, "page": page},
            )
            batch = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(batch, list):
                raise UnusualWhalesError(
                    f"Unusual Whales option-contracts page {page} for {ticker} {expiry}"
                    " has no 'data' list")
            if page == max_pages:
                if batch:
                    break  # the probe page has rows: the chain is too long
                return list(by_symbol.values())
            for i, row in enumerate(batch):
                symbol = row.get("option_symbol") if isinstance(row, dict) else None
                if not isinstance(symbol, str) or not symbol.strip():
                    raise UnusualWhalesError(
                        f"Unusual Whales option-contracts page {page} row {i} for"
                        f" {ticker} {expiry} has no option_symbol string")
                by_symbol[symbol] = row
            if len(batch) < page_size:
                return list(by_symbol.values())
        raise UnusualWhalesError(
            f"{ticker} {expiry} chain is longer than {max_pages} pages of {page_size};"
            " refusing to rank a partial chain")
