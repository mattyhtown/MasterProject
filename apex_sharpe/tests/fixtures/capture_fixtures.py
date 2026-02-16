#!/usr/bin/env python3
"""
Capture real ORATS API data and save as JSON test fixtures.

Run this script to refresh test fixtures with real market data:
    python -m apex_sharpe.tests.fixtures.capture_fixtures

This fetches live and historical data from ORATS and writes JSON files
into the fixtures/ directory. Tests then load these files instead of
using hand-crafted synthetic data.

NOTE: Requires a valid ORATS_TOKEN in .env or environment.
NOTE: Run during market hours for live data, or any time for historical.
"""

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from apex_sharpe.config import OratsCfg
from apex_sharpe.data.orats_client import ORATSClient

FIXTURES_DIR = Path(__file__).parent
TICKERS = ["SPY", "SPX"]


def _save(name: str, data: dict) -> None:
    path = FIXTURES_DIR / f"{name}.json"
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"  Saved {path.name} ({len(json.dumps(data))} bytes)")


def capture_live(client: ORATSClient) -> None:
    """Capture live market data for SPY and SPX."""
    print("\n=== Capturing live data ===")

    for ticker in TICKERS:
        tag = ticker.lower()

        # IV rank
        iv = client.iv_rank(ticker)
        if iv and iv.get("data"):
            _save(f"{tag}_ivrank", iv)

        # Summaries
        summ = client.summaries(ticker)
        if summ and summ.get("data"):
            _save(f"{tag}_summaries", summ)

        # Expirations
        exp = client.expirations(ticker)
        if exp and exp.get("data"):
            _save(f"{tag}_expirations", exp)
            # Get chain for nearest expiry with >7 DTE
            today = date.today()
            for exp_date_str in exp["data"]:
                exp_date = date.fromisoformat(exp_date_str)
                dte = (exp_date - today).days
                if 7 < dte < 45:
                    chain = client.chain(ticker, exp_date_str)
                    if chain and chain.get("data"):
                        # Filter to just this expiry (ORATS returns all)
                        filtered = [s for s in chain["data"]
                                    if s.get("expirDate") == exp_date_str]
                        if filtered:
                            _save(f"{tag}_chain_{dte}dte", {
                                "data": filtered,
                                "_meta": {
                                    "ticker": ticker,
                                    "expiry": exp_date_str,
                                    "dte": dte,
                                    "capture_date": str(today),
                                    "strike_count": len(filtered),
                                }
                            })
                    break  # Only capture one expiry per ticker


def capture_historical(client: ORATSClient) -> None:
    """Capture historical data for known signal days."""
    print("\n=== Capturing historical data ===")

    # Use recent trading days (go back from today to find valid dates)
    today = date.today()
    # Try last 5 business days
    candidates = []
    d = today - timedelta(days=1)
    while len(candidates) < 5:
        if d.weekday() < 5:  # Mon-Fri
            candidates.append(d)
        d -= timedelta(days=1)

    for trade_date in candidates[:3]:
        date_str = trade_date.isoformat()
        tag = date_str.replace("-", "")

        # Historical summaries for SPX (signal data)
        summ = client.hist_summaries("SPX", date_str)
        if summ and summ.get("data"):
            _save(f"hist_spx_summary_{tag}", summ)
            print(f"    Got SPX summary for {date_str}")
        else:
            print(f"    No SPX summary for {date_str}")

        # Historical chain for SPX on that date
        strikes = client.hist_strikes("SPX", date_str)
        if strikes and strikes.get("data"):
            # Filter to 0DTE (same-day expiry)
            zero_dte = [s for s in strikes["data"]
                        if s.get("expirDate") == date_str]
            if zero_dte:
                _save(f"hist_spx_chain_0dte_{tag}", {
                    "data": zero_dte,
                    "_meta": {
                        "ticker": "SPX",
                        "trade_date": date_str,
                        "expiry": date_str,
                        "strike_count": len(zero_dte),
                    }
                })
            # Also get ~30 DTE chain
            target_dte = trade_date + timedelta(days=30)
            near_30 = [s for s in strikes["data"]
                       if s.get("expirDate") and
                       abs((date.fromisoformat(s["expirDate"]) - target_dte).days) < 7]
            if near_30:
                expiry_used = near_30[0]["expirDate"]
                filtered = [s for s in near_30 if s["expirDate"] == expiry_used]
                _save(f"hist_spx_chain_30dte_{tag}", {
                    "data": filtered,
                    "_meta": {
                        "ticker": "SPX",
                        "trade_date": date_str,
                        "expiry": expiry_used,
                        "strike_count": len(filtered),
                    }
                })

        # Historical dailies for SPY (close prices)
        start = (trade_date - timedelta(days=5)).isoformat()
        dailies = client.hist_dailies("SPY", f"{start},{date_str}")
        if dailies and dailies.get("data"):
            _save(f"hist_spy_dailies_{tag}", dailies)


def capture_credit_spread_data(client: ORATSClient) -> None:
    """Capture HYG and TLT data for credit spread signal."""
    print("\n=== Capturing credit spread data ===")
    for ticker in ["HYG", "TLT"]:
        summ = client.summaries(ticker)
        if summ and summ.get("data"):
            _save(f"{ticker.lower()}_summaries", summ)


def main():
    token = os.environ.get("ORATS_TOKEN")
    if not token:
        env_path = Path(__file__).resolve().parents[3] / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("ORATS_TOKEN="):
                    token = line.split("=", 1)[1].strip()

    if not token:
        print("ERROR: No ORATS_TOKEN found in environment or .env")
        sys.exit(1)

    cfg = OratsCfg(token=token)
    client = ORATSClient(cfg)

    print(f"Capturing ORATS fixtures to {FIXTURES_DIR}")
    print(f"Token: {token[:8]}...{token[-4:]}")

    capture_live(client)
    capture_historical(client)
    capture_credit_spread_data(client)

    # Write manifest
    fixtures = sorted(FIXTURES_DIR.glob("*.json"))
    manifest = {
        "capture_date": str(date.today()),
        "files": [f.name for f in fixtures],
        "count": len(fixtures),
    }
    _save("_manifest", manifest)

    print(f"\nDone! Captured {len(fixtures)} fixture files.")
    print("Run tests with: pytest apex_sharpe/tests/ -v")


if __name__ == "__main__":
    main()
