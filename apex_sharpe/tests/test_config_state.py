"""Offline regression tests for configuration and persisted state."""

from pathlib import Path
from unittest.mock import patch

import pytest

from apex_sharpe import config
from apex_sharpe.config import StateCfg
from apex_sharpe.data.state import StateManager


def test_root_env_and_legacy_env_precedence(tmp_path, monkeypatch):
    package_dir = tmp_path / "apex_sharpe"
    package_dir.mkdir()
    monkeypatch.setattr(config, "__file__", str(package_dir / "config.py"))
    for key in ("ORATS_TOKEN", "SUPABASE_URL", "UNUSUAL_WHALES_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ORATS_TOKEN", "environment-value")
    (tmp_path / ".env").write_text(
        'ORATS_TOKEN=root-value\nSUPABASE_URL="https://example.test"\n'
    )
    (package_dir / ".env").write_text(
        "SUPABASE_URL=legacy-value\nUNUSUAL_WHALES_API_KEY='legacy-value'\n"
    )
    loaded = config.load_config()
    assert loaded.orats.token == "environment-value"
    assert loaded.supabase.url == "https://example.test"
    assert loaded.unusual_whales.api_key == "legacy-value"


def test_default_positions_path_uses_home(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert StateCfg().positions_path == str(tmp_path / "positions.json")


def test_state_creates_parents_and_round_trips(tmp_path):
    state = StateManager(StateCfg(
        positions_path=str(tmp_path / "positions" / "nested" / "positions.json"),
        signals_path=str(tmp_path / "signals" / "signals.json"),
        cache_path=str(tmp_path / "cache" / "cache.json"),
    ))
    state.save_positions([{"id": "position"}])
    state.append_signal({"signal": "test"})
    state.save_cache({"key": "value"})
    assert state.load_positions() == [{"id": "position"}]
    assert state.load_signals() == [{"signal": "test"}]
    assert state.load_cache() == {"key": "value"}


def test_state_expands_home_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    state = StateManager(StateCfg(
        positions_path="~/positions.json",
        signals_path="~/signals.json",
        cache_path="~/cache.json",
    ))
    assert state.positions_path == tmp_path / "positions.json"
    assert state.signals_path == tmp_path / "signals.json"
    assert state.cache_path == tmp_path / "cache.json"


def test_failed_atomic_write_preserves_existing_positions(tmp_path):
    state = StateManager(StateCfg(positions_path=str(tmp_path / "positions.json")))
    state.save_positions([{"id": "original"}])
    with patch("apex_sharpe.data.state.os.replace", side_effect=OSError("write failed")):
        with pytest.raises(OSError, match="write failed"):
            state.save_positions([{"id": "replacement"}])
    assert state.load_positions() == [{"id": "original"}]
    assert list(tmp_path.iterdir()) == [state.positions_path]
