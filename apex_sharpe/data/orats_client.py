"""
Unified ORATS REST client (stdlib urllib).

Extracted from trading_pipeline.py. Replaces module-level orats_get/fetch_*
functions with a proper class that takes config via DI.
"""

import json
import urllib.request
import urllib.error
from typing import Dict, Optional

from ..config import OratsCfg


class ORATSClient:
    """ORATS API client using stdlib urllib."""

    def __init__(self, config: OratsCfg):
        self.token = config.token
        self.base_url = config.base_url
        self.timeout = config.timeout

    def get(self, endpoint: str, params: Optional[Dict[str, str]] = None) -> Optional[Dict]:
        """Low-level GET. Returns parsed JSON or None on error."""
        params = params or {}
        params["token"] = self.token
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{self.base_url}/{endpoint}?{qs}"
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode())
        except (urllib.error.URLError, json.JSONDecodeError, OSError) as exc:
            print(f"  [WARN] ORATS fetch failed ({endpoint}): {exc}")
            return None

    # -- live endpoints -------------------------------------------------------

    def summaries(self, ticker: str) -> Optional[Dict]:
        return self.get("live/summaries", {"ticker": ticker})

    def expirations(self, ticker: str) -> Optional[Dict]:
        return self.get("live/expirations", {"ticker": ticker})

    def chain(self, ticker: str, expiry: str) -> Optional[Dict]:
        return self.get("live/strikes", {"ticker": ticker, "expiry": expiry})

    def iv_rank(self, ticker: str) -> Optional[Dict]:
        return self.get("ivrank", {"ticker": ticker})

    # -- historical endpoints -------------------------------------------------

    def hist_summaries(self, ticker: str, trade_date: str) -> Optional[Dict]:
        return self.get("hist/summaries", {"ticker": ticker, "tradeDate": trade_date})

    def hist_dailies(self, ticker: str, date_range: str) -> Optional[Dict]:
        return self.get("hist/dailies", {"ticker": ticker, "tradeDate": date_range})

    def hist_strikes(self, ticker: str, trade_date: str) -> Optional[Dict]:
        return self.get("hist/strikes", {"ticker": ticker, "tradeDate": trade_date})
