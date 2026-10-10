from tactiq.domain.models import Event, Insight, MatchState
from tactiq.intelligence.engine import detect_turning_point


def _event(match_id: str, sequence: int, clock_ms: int, team_id: str, event_type: str, progressive: bool = False) -> Event:
    qualifiers = {"progressive": True} if progressive else {}
    return Event(
        match_id=match_id,
        sequence=sequence,
        match_clock_ms=clock_ms,
        type=event_type,
        team_id=team_id,
        player_id=f"{team_id}_p{sequence}",
        qualifiers=qualifiers,
    )


def _existing_turning_insight(match_id: str, end_ms: int) -> Insight:
    return Insight(
        match_id=match_id,
        type="turning_point",
        priority="high",
        confidence=0.8,
        team_id="team_a",
        window_start_ms=max(0, end_ms - 20_000),
        window_end_ms=end_ms,
        title="Turning Point",
        summary="Existing turning point",
        evidence=[],
    )


def test_turning_point_detector_emits_with_threat_spike() -> None:
    match_id = "match_turning_emit"
    state = MatchState(match_id=match_id, scenario_id="sustained_pressure", seed=1)

    events = [
        _event(match_id, 1, 60_500, "team_a", "pass_completed", progressive=False),
        _event(match_id, 2, 64_000, "team_b", "carry", progressive=False),
        _event(match_id, 3, 67_500, "team_a", "pass_completed", progressive=False),
        _event(match_id, 4, 71_000, "team_b", "pass_completed", progressive=True),
        _event(match_id, 5, 74_500, "team_a", "recovery", progressive=False),
        _event(match_id, 6, 80_500, "team_b", "pass_completed", progressive=True),
        _event(match_id, 7, 84_000, "team_b", "carry", progressive=True),
        _event(match_id, 8, 87_500, "team_b", "pass_completed", progressive=True),
        _event(match_id, 9, 92_000, "team_a", "recovery", progressive=False),
        _event(match_id, 10, 96_000, "team_b", "pass_completed", progressive=True),
    ]

    insight = detect_turning_point(state, events, existing_insights=[])

    assert insight is not None
    assert insight.type == "turning_point"
    assert insight.team_id == "team_b"

    evidence_by_metric = {item.metric: item.supporting_event_ids for item in insight.evidence}
    assert set(evidence_by_metric["current_window_events"]) == {e.event_id for e in events[5:]}
    assert set(evidence_by_metric["previous_window_events"]) == {e.event_id for e in events[:5]}
    assert set(evidence_by_metric["current_progressive_actions"]) == {e.event_id for e in events[5:]}
    assert set(evidence_by_metric["previous_progressive_actions"]) == {e.event_id for e in events[:5]}


def test_turning_point_detector_skips_when_duplicate_recent_exists() -> None:
    match_id = "match_turning_dedup"
    state = MatchState(match_id=match_id, scenario_id="sustained_pressure", seed=1)

    events = [
        _event(match_id, 1, 60_500, "team_a", "pass_completed", progressive=False),
        _event(match_id, 2, 64_000, "team_b", "carry", progressive=False),
        _event(match_id, 3, 67_500, "team_a", "pass_completed", progressive=False),
        _event(match_id, 4, 71_000, "team_b", "pass_completed", progressive=True),
        _event(match_id, 5, 74_500, "team_a", "recovery", progressive=False),
        _event(match_id, 6, 80_500, "team_b", "pass_completed", progressive=True),
        _event(match_id, 7, 84_000, "team_b", "carry", progressive=True),
        _event(match_id, 8, 87_500, "team_b", "pass_completed", progressive=True),
        _event(match_id, 9, 92_000, "team_a", "recovery", progressive=False),
        _event(match_id, 10, 96_000, "team_b", "pass_completed", progressive=True),
    ]

    existing = [_existing_turning_insight(match_id, end_ms=85_000)]
    insight = detect_turning_point(state, events, existing_insights=existing)

    assert insight is None


def test_turning_point_detector_skips_without_required_spike() -> None:
    match_id = "match_turning_skip"
    state = MatchState(match_id=match_id, scenario_id="sustained_pressure", seed=1)

    events = [
        _event(match_id, 1, 60_500, "team_a", "pass_completed", progressive=False),
        _event(match_id, 2, 64_000, "team_b", "carry", progressive=False),
        _event(match_id, 3, 67_500, "team_a", "pass_completed", progressive=False),
        _event(match_id, 4, 71_000, "team_b", "pass_completed", progressive=False),
        _event(match_id, 5, 74_500, "team_a", "recovery", progressive=False),
        _event(match_id, 6, 80_500, "team_a", "pass_completed", progressive=False),
        _event(match_id, 7, 84_000, "team_a", "carry", progressive=False),
        _event(match_id, 8, 87_500, "team_b", "pass_completed", progressive=False),
        _event(match_id, 9, 92_000, "team_a", "recovery", progressive=False),
        _event(match_id, 10, 96_000, "team_b", "pass_completed", progressive=False),
    ]

    insight = detect_turning_point(state, events, existing_insights=[])

    assert insight is None
