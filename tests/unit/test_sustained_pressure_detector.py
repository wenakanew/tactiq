from tactiq.domain.models import MatchState
from tactiq.intelligence.engine import compute_snapshot, detect_sustained_pressure
from tactiq.simulator.scenarios import sustained_pressure_events


def test_sustained_pressure_detector_emits_with_evidence() -> None:
    match_id = "match_test"
    state = MatchState(match_id=match_id, scenario_id="sustained_pressure", seed=1)
    events = sustained_pressure_events(match_id)

    snapshot = compute_snapshot(match_id, events, "team_a")

    assert snapshot.metrics["progressive_actions"] >= 5
    assert snapshot.metrics["final_third_entries"] >= 3
    assert snapshot.metrics["shots"] >= 1

    insight = detect_sustained_pressure(state, events, existing_insights=[])

    assert insight is not None
    assert insight.type == "sustained_pressure"
    assert insight.title == "Pressure Building"
    assert len(insight.evidence) == 3


def test_sustained_pressure_detector_deduplicates() -> None:
    match_id = "match_test"
    state = MatchState(match_id=match_id, scenario_id="sustained_pressure", seed=1)
    events = sustained_pressure_events(match_id)

    first = detect_sustained_pressure(state, events, existing_insights=[])
    assert first is not None

    second = detect_sustained_pressure(state, events, existing_insights=[first])
    assert second is None
