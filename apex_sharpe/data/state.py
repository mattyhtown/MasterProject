"""
State management — single source of truth for positions, signals, and cache.

Wraps the JSON files that persist across pipeline runs.
"""

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from ..config import StateCfg


class StateManager:
    """Manages persistence of positions, signals, and backtest cache.

    Single-process system — no file locking needed.
    """

    def __init__(self, config: StateCfg):
        self.positions_path = Path(config.positions_path).expanduser()
        self.signals_path = Path(config.signals_path).expanduser() if config.signals_path else Path.home() / "0dte_signals.json"
        self.cache_path = Path(config.cache_path).expanduser() if config.cache_path else Path.home() / ".0dte_backtest_cache.json"

    @staticmethod
    def _save_json(path: Path, data: Any, indent=None) -> None:
        contents = json.dumps(data, indent=indent, default=str)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", dir=path.parent, delete=False, encoding="utf-8"
            ) as f:
                temporary_path = Path(f.name)
                f.write(contents)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary_path, path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    # -- positions ------------------------------------------------------------

    def load_positions(self) -> List[Dict]:
        if self.positions_path.exists():
            with open(self.positions_path) as f:
                return json.load(f)
        return []

    def save_positions(self, positions: List[Dict]) -> None:
        self._save_json(self.positions_path, positions, indent=2)

    # -- signals log ----------------------------------------------------------

    def load_signals(self) -> List[Dict]:
        if self.signals_path.exists():
            try:
                return json.loads(self.signals_path.read_text())
            except (json.JSONDecodeError, OSError):
                return []
        return []

    def save_signals(self, entries: List[Dict]) -> None:
        self._save_json(self.signals_path, entries, indent=2)

    def append_signal(self, entry: Dict) -> None:
        entries = self.load_signals()
        entries.append(entry)
        self.save_signals(entries)

    # -- backtest cache -------------------------------------------------------

    def load_cache(self) -> Dict[str, Any]:
        if self.cache_path.exists():
            try:
                return json.loads(self.cache_path.read_text())
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def save_cache(self, cache: Dict[str, Any]) -> None:
        self._save_json(self.cache_path, cache)
