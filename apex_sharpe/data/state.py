"""
State management — single source of truth for positions, signals, and cache.

Wraps the JSON files that persist across pipeline runs.
"""

import json
from pathlib import Path
from typing import Any, Dict, List

from ..config import StateCfg


class StateManager:
    """Manages persistence of positions, signals, and backtest cache.

    Single-process system — no file locking needed.
    """

    def __init__(self, config: StateCfg):
        self.positions_path = Path(config.positions_path)
        self.signals_path = Path(config.signals_path) if config.signals_path else Path.home() / "0dte_signals.json"
        self.cache_path = Path(config.cache_path) if config.cache_path else Path.home() / ".0dte_backtest_cache.json"

    # -- positions ------------------------------------------------------------

    def load_positions(self) -> List[Dict]:
        if self.positions_path.exists():
            with open(self.positions_path) as f:
                return json.load(f)
        return []

    def save_positions(self, positions: List[Dict]) -> None:
        with open(self.positions_path, "w") as f:
            json.dump(positions, f, indent=2, default=str)

    # -- signals log ----------------------------------------------------------

    def load_signals(self) -> List[Dict]:
        if self.signals_path.exists():
            try:
                return json.loads(self.signals_path.read_text())
            except (json.JSONDecodeError, OSError):
                return []
        return []

    def save_signals(self, entries: List[Dict]) -> None:
        self.signals_path.write_text(json.dumps(entries, indent=2, default=str))

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
        self.cache_path.write_text(json.dumps(cache, default=str))
