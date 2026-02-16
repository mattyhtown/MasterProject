"""
Test fixture loader for real ORATS data.

Loads JSON fixtures captured by capture_fixtures.py. Falls back to
synthetic data if fixtures haven't been captured yet.

Usage in tests:
    from apex_sharpe.tests.fixtures import load_fixture, has_fixtures

    @pytest.mark.skipif(not has_fixtures(), reason="Run capture_fixtures.py first")
    def test_with_real_data():
        chain = load_fixture("spy_chain_30dte")
        ...
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


FIXTURES_DIR = Path(__file__).parent


def has_fixtures() -> bool:
    """Check if real data fixtures have been captured."""
    data_files = [
        p for p in FIXTURES_DIR.glob("*.json")
        if not p.stem.startswith("_")
    ]
    return len(data_files) > 0


def load_fixture(name: str) -> Optional[Dict[str, Any]]:
    """Load a JSON fixture by name (without .json extension)."""
    path = FIXTURES_DIR / f"{name}.json"
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def list_fixtures() -> List[str]:
    """List all available fixture names."""
    return sorted(
        p.stem for p in FIXTURES_DIR.glob("*.json")
        if not p.stem.startswith("_")
    )


def fixture_chain(ticker: str = "spy") -> Optional[List[Dict]]:
    """Load the first available chain fixture for a ticker.

    Returns the chain data list (not the wrapper dict).
    """
    tag = ticker.lower()
    for path in sorted(FIXTURES_DIR.glob(f"{tag}_chain_*.json")):
        data = json.loads(path.read_text())
        if data and data.get("data"):
            return data["data"]
    return None


def fixture_summary(ticker: str = "spx") -> Optional[Dict]:
    """Load live summary for a ticker. Returns the first data record."""
    data = load_fixture(f"{ticker.lower()}_summaries")
    if data and data.get("data"):
        return data["data"][0]
    return None


def fixture_ivrank(ticker: str = "spy") -> Optional[Dict]:
    """Load IV rank for a ticker. Returns the first data record."""
    data = load_fixture(f"{ticker.lower()}_ivrank")
    if data and data.get("data"):
        return data["data"][0]
    return None


def fixture_hist_chain(date_str: str = None, dte: str = "30dte") -> Optional[List[Dict]]:
    """Load a historical chain fixture.

    If date_str is None, returns the first available.
    """
    pattern = f"hist_spx_chain_{dte}_*.json"
    for path in sorted(FIXTURES_DIR.glob(pattern)):
        if date_str and date_str.replace("-", "") not in path.stem:
            continue
        data = json.loads(path.read_text())
        if data and data.get("data"):
            return data["data"]
    return None


def fixture_hist_summary(date_str: str = None) -> Optional[Dict]:
    """Load a historical SPX summary fixture.

    Returns the first data record from the first available date.
    """
    for path in sorted(FIXTURES_DIR.glob("hist_spx_summary_*.json")):
        if date_str and date_str.replace("-", "") not in path.stem:
            continue
        data = json.loads(path.read_text())
        if data and data.get("data"):
            return data["data"][0]
    return None
