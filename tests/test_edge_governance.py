import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from apex_sharpe.governance.edge_ledger import (
    GraduationEvidence,
    GraduationStage,
    HistorianEvent,
    append_historian_event,
    evaluate_graduation,
)


def _complete_evidence(**overrides) -> GraduationEvidence:
    data = dict(
        out_of_sample=True,
        walk_forward_recent=True,
        execution_costs_modeled=True,
        sufficient_observations=True,
        shadow_passed=False,
        small_capital_passed=False,
        data_integrity_passed=True,
        failure_criteria_defined=True,
    )
    data.update(overrides)
    return GraduationEvidence(**data)


def test_graduation_cannot_skip_stages():
    e = _complete_evidence(shadow_passed=True, small_capital_passed=True)
    assert evaluate_graduation(GraduationStage.RESEARCH, e) == GraduationStage.WALK_FORWARD
    assert evaluate_graduation(GraduationStage.WALK_FORWARD, e) == GraduationStage.SHADOW
    assert evaluate_graduation(GraduationStage.SHADOW, e) == GraduationStage.SMALL_CAPITAL
    assert evaluate_graduation(GraduationStage.SMALL_CAPITAL, e) == GraduationStage.SCALED
    assert evaluate_graduation(GraduationStage.SCALED, e) == GraduationStage.SCALED


def test_graduation_requires_base_evidence():
    incomplete = _complete_evidence(data_integrity_passed=False)
    assert evaluate_graduation(GraduationStage.RESEARCH, incomplete) == GraduationStage.RESEARCH


def test_walk_forward_enters_shadow_without_prior_shadow_pass():
    e = _complete_evidence(shadow_passed=False)
    assert evaluate_graduation(GraduationStage.WALK_FORWARD, e) == GraduationStage.SHADOW
    assert evaluate_graduation(GraduationStage.SHADOW, e) == GraduationStage.SHADOW


def test_small_capital_requires_stage_proof():
    e = _complete_evidence(shadow_passed=True, small_capital_passed=False)
    assert evaluate_graduation(GraduationStage.SMALL_CAPITAL, e) == GraduationStage.SMALL_CAPITAL


def test_retired_stays_retired():
    e = _complete_evidence(shadow_passed=True, small_capital_passed=True)
    assert evaluate_graduation(GraduationStage.RETIRED, e) == GraduationStage.RETIRED


def test_historian_is_append_only_and_hashed(tmp_path: Path):
    event = HistorianEvent(
        event_type="edge_observation",
        observed_at="2026-09-19T14:30:00-04:00",
        source="test",
        payload={"signal": 1.0},
        edge_id="EDGE-TEST-001",
    )
    path = append_historian_event(event, root=str(tmp_path))
    first = path.read_text()
    append_historian_event(event, root=str(tmp_path))
    second = path.read_text()
    assert len(second.splitlines()) == 2
    assert first in second

    envelope = json.loads(first)
    assert envelope["sha256"] == hashlib.sha256(event.canonical().encode("utf-8")).hexdigest()
    assert envelope["event"]["event_id"] == event.event_id


def test_historian_partitions_by_utc_date(tmp_path: Path):
    event = HistorianEvent(
        event_type="edge_observation",
        observed_at="2026-09-19T22:30:00-04:00",
        recorded_at="2026-09-19T22:30:00-04:00",
        source="test",
        payload={"signal": 1.0},
    )
    path = append_historian_event(event, root=str(tmp_path))
    assert path.name == "2026-09-20.jsonl"


def test_historian_corrections_are_new_events(tmp_path: Path):
    original = HistorianEvent(
        event_type="edge_observation",
        observed_at="2026-09-19T14:30:00Z",
        recorded_at="2026-09-19T18:30:00+00:00",
        source="test",
        payload={"signal": 1.0},
        edge_id="EDGE-TEST-001",
    )
    correction = HistorianEvent(
        event_type="edge_correction",
        observed_at="2026-09-19T15:00:00Z",
        recorded_at="2026-09-19T19:00:00+00:00",
        source="test",
        payload={"signal": 0.0},
        edge_id="EDGE-TEST-001",
        corrects_event_id=original.event_id,
    )
    path = append_historian_event(original, root=str(tmp_path))
    append_historian_event(correction, root=str(tmp_path))
    lines = path.read_text().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])["event"]
    second = json.loads(lines[1])["event"]
    assert first["event_id"] == original.event_id
    assert second["corrects_event_id"] == original.event_id
    assert first["payload"]["signal"] == 1.0


def test_historian_rejects_unknown_correction_target(tmp_path: Path):
    orphan = HistorianEvent(
        event_type="edge_correction",
        observed_at="2026-09-19T15:00:00Z",
        recorded_at="2026-09-19T19:00:00+00:00",
        source="test",
        payload={"signal": 0.0},
        corrects_event_id="missing-event",
    )
    with pytest.raises(ValueError, match="correction target"):
        append_historian_event(orphan, root=str(tmp_path))
    assert list(tmp_path.glob("*.jsonl")) == []


def test_historian_rejects_non_strict_payload():
    with pytest.raises(ValueError, match="non-finite"):
        HistorianEvent(
            event_type="edge_observation",
            observed_at="2026-09-19T14:30:00Z",
            source="test",
            payload={"signal": float("nan")},
        ).canonical()
    with pytest.raises(ValueError, match="strict JSON"):
        HistorianEvent(
            event_type="edge_observation",
            observed_at="2026-09-19T14:30:00Z",
            source="test",
            payload={"when": datetime(2026, 9, 19, tzinfo=timezone.utc)},
        ).canonical()
