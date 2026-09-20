from pathlib import Path

from apex_sharpe.governance.edge_ledger import (
    GraduationEvidence,
    GraduationStage,
    HistorianEvent,
    append_historian_event,
    evaluate_graduation,
)


def test_graduation_cannot_skip_stages():
    e = GraduationEvidence(
        out_of_sample=True,
        walk_forward_recent=True,
        execution_costs_modeled=True,
        sufficient_observations=True,
        shadow_passed=True,
        small_capital_passed=True,
        data_integrity_passed=True,
        failure_criteria_defined=True,
    )
    assert evaluate_graduation(GraduationStage.RESEARCH, e) == GraduationStage.WALK_FORWARD


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
    assert "sha256" in second
