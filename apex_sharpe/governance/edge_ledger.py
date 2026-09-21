"""Edge ledger and append-only historian.

Design rule: the canonical historical record is never rewritten by agents.
Corrections are new events referencing the event they correct.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4


class Book(str, Enum):
    CORE = "core"
    EDGE = "edge"
    LAB = "laboratory"


class EdgeClass(str, Enum):
    INFORMATION = "information"
    STRUCTURAL = "structural"
    BEHAVIORAL = "behavioral"
    LIQUIDITY = "liquidity"
    VOLATILITY = "volatility"
    EXECUTION = "execution"
    RELATIVE_VALUE = "relative_value"
    MODELING = "modeling"
    DATA_QUALITY = "data_quality"
    SPEED = "speed"


class GraduationStage(str, Enum):
    RESEARCH = "research"
    WALK_FORWARD = "walk_forward"
    SHADOW = "shadow"
    SMALL_CAPITAL = "small_capital"
    SCALED = "scaled"
    RETIRED = "retired"


@dataclass(frozen=True)
class EdgeRecord:
    edge_id: str
    name: str
    edge_class: EdgeClass
    book: Book = Book.LAB
    stage: GraduationStage = GraduationStage.RESEARCH
    hypothesis: str = ""
    counterparty: str = ""
    persistence_reason: str = ""
    decay_condition: str = ""
    invalidation_rule: str = ""
    owner: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass(frozen=True)
class HistorianEvent:
    event_type: str
    observed_at: str
    source: str
    payload: Dict[str, Any]
    edge_id: Optional[str] = None
    model_version: Optional[str] = None
    derived: bool = False
    corrects_event_id: Optional[str] = None
    event_id: str = field(default_factory=lambda: str(uuid4()))
    recorded_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def canonical(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), default=str)


def _utc_partition_date(recorded_at: str) -> str:
    """UTC calendar date for the JSONL partition, independent of source offset."""
    dt = datetime.fromisoformat(recorded_at)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).date().isoformat()


def append_historian_event(event: HistorianEvent, root: str = "data/historian") -> Path:
    """Append an immutable event to a UTC-date JSONL partition.

    This function intentionally has no update/delete path.
    """
    day = _utc_partition_date(event.recorded_at)
    path = Path(root) / f"{day}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = event.canonical()
    envelope = {
        "event": json.loads(body),
        "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(envelope, sort_keys=True, separators=(",", ":")) + "\n")
    return path


@dataclass(frozen=True)
class GraduationEvidence:
    out_of_sample: bool
    walk_forward_recent: bool
    execution_costs_modeled: bool
    sufficient_observations: bool
    shadow_passed: bool = False
    small_capital_passed: bool = False
    data_integrity_passed: bool = False
    failure_criteria_defined: bool = False


def evaluate_graduation(current: GraduationStage, e: GraduationEvidence) -> GraduationStage:
    """Conservative one-step graduation. No metric can skip a stage.

    Stage-specific proof is required to *leave* that stage, not to enter it.
    ``shadow_passed`` graduates out of SHADOW; ``small_capital_passed``
    graduates out of SMALL_CAPITAL. Retired edges stay retired.
    """
    if current == GraduationStage.RETIRED:
        return GraduationStage.RETIRED

    base = (
        e.out_of_sample
        and e.walk_forward_recent
        and e.execution_costs_modeled
        and e.sufficient_observations
        and e.data_integrity_passed
        and e.failure_criteria_defined
    )
    if not base:
        return current
    if current == GraduationStage.RESEARCH:
        return GraduationStage.WALK_FORWARD
    if current == GraduationStage.WALK_FORWARD:
        return GraduationStage.SHADOW
    if current == GraduationStage.SHADOW and e.shadow_passed:
        return GraduationStage.SMALL_CAPITAL
    if current == GraduationStage.SMALL_CAPITAL and e.small_capital_passed:
        return GraduationStage.SCALED
    return current
