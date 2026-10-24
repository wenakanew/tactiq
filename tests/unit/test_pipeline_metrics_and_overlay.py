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
    assert overlay["team_display_names"]["team_a"] is not None
    assert overlay["cv_live"]["tracking_source"] == "event_stream_v1"
    assert overlay["team_display_names"]["team_b"] is not None
    assert "ball_position" in overlay["cv_live"]
    assert "control_index_by_team" in overlay["cv_live"]
    assert overlay["cv_live"]["controlling_team_id"] in {"team_a", "team_b", "neutral"}
    assert overlay["cv_live"]["pitch_zone"] in {"midfield", "final_third_team_a", "final_third_team_b"}
    assert overlay["cv_live"]["ball_travel_direction"] in {"left_to_right", "right_to_left", "static"}
    assert "smoothed_ball_position" in overlay["cv_live"]
    assert "raw_ball_position" in overlay["cv_live"]
    assert 0.0 <= overlay["cv_live"]["possession_transition_confidence"] <= 1.0
    assert 0 <= overlay["cv_live"]["ball_position"]["x"] <= 100
    assert 0 <= overlay["cv_live"]["ball_position"]["y"] <= 100
    assert overlay["viewer_profile"]["favorite_player_id"] == "a_10"


def test_reduce_match_state_populates_cv_live_snapshot() -> None:
    match_id = "match_cv_live"
    state = MatchState(match_id=match_id, scenario_id="sustained_pressure", seed=2)
    events = sustained_pressure_events(match_id)

    for event in events:
        reduce_match_state(state, event, events)

    assert state.cv_live["possession_team_id"] in {"team_a", "team_b"}
    assert state.cv_live["tracked_players"] >= 0
    assert state.cv_live["top_player_speed_kmh"] >= 0
    assert state.cv_live["ball_travel_direction"] in {"left_to_right", "right_to_left", "static"}
    assert 0.0 <= state.cv_live["possession_transition_confidence"] <= 1.0
