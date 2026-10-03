from tactiq.application.service import MatchService
from tactiq.domain.models import MatchState
from tactiq.intelligence.engine import compute_snapshot, detect_insights, reduce_match_state
from tactiq.simulator.scenarios import player_influence_events, sustained_pressure_events


def test_snapshot_includes_pass_and_speed_metrics() -> None:
    match_id = "match_metrics"
    events = sustained_pressure_events(match_id)

    snapshot = compute_snapshot(match_id=match_id, events=events, team_id="team_a")

    assert snapshot.metrics["pass_attempts"] >= 1
    assert snapshot.metrics["pass_completed"] >= 1
    assert snapshot.metrics["pass_accuracy_pct"] > 0
    assert snapshot.metrics["pass_avg_distance_m"] > 0
    assert snapshot.metrics["pass_difficulty_avg"] > 0
    assert snapshot.metrics["ball_speed_kmh_max"] > 0
    assert snapshot.metrics["shot_speed_kmh_max"] > 0


def test_speed_burst_detector_emits_on_player_influence_scenario() -> None:
    match_id = "match_speed"
    state = MatchState(match_id=match_id, scenario_id="player_influence", seed=1)
    events = player_influence_events(match_id)

    insights = detect_insights(match_state=state, events=events, existing_insights=[])
    insight_types = {item.type for item in insights}

    assert "player_speed_burst" in insight_types


def test_overlay_payload_contains_machine_readable_fields() -> None:
    service = MatchService()
    match_id = "match_overlay"
    service.matches[match_id] = MatchState(match_id=match_id, scenario_id="sustained_pressure", seed=1)

    events = sustained_pressure_events(match_id)
    service.events[match_id] = events

    for event in events:
        reduce_match_state(service.matches[match_id], event, events)

    insights = detect_insights(service.matches[match_id], events, [])
    service.insights[match_id].extend(insights)
    service.set_viewer_profile(match_id, favorite_team_id="team_a", favorite_player_id="a_10", focus_metric="pass_accuracy_pct")

    overlay = service.get_overlay_payload(match_id)

    assert overlay["overlay_version"] == "1.0.0"
    assert overlay["render_hints"]["machine_readable"] is True
    assert "metrics_by_team" in overlay
    assert "current_insight" in overlay
    assert overlay["viewer_profile"]["favorite_player_id"] == "a_10"
