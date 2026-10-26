import pytest

from tactiq.domain.models import Event, MatchState, Position
from tactiq.intelligence.engine import compute_snapshot, detect_insights, reduce_match_state


def tracking_frame(sequence, clock, x, *, revision=1):
    return Event(
        match_id="metrics", event_id=f"frame_{sequence}", sequence=sequence, match_clock_ms=clock,
        type="tracking_frame", team_id="team_a", synthetic=False,
        qualifiers={"cv_frame": {
            "calibrated": True, "calibration": {"revision": revision}, "tracking_source": "model-fixture",
            "possession_team_id": "team_a", "ball_speed_kmh": None,
            "players": [{"track_id": "player_1", "team_id": "team_a", "position": {"x": x, "y": 50},
                         "confidence": 0.95, "inferred": False, "speed_kmh": 37.8}],
        }},
    )


def test_calibrated_cv_distance_is_not_reported_as_zero():
    events = [tracking_frame(1, 0, 50), tracking_frame(2, 100, 51)]
    metrics = compute_snapshot("metrics", events, "team_a").metrics
    assert metrics["top_player_distance_m"] == pytest.approx(1.1)


def test_distance_does_not_bridge_a_camera_resurvey():
    events = [tracking_frame(1, 0, 50), tracking_frame(2, 100, 51, revision=2)]
    assert compute_snapshot("metrics", events, "team_a").metrics["top_player_distance_m"] == 0


def test_tracking_speed_insights_resolve_to_the_frames_that_measured_them():
    history = [tracking_frame(1, 0, 50), tracking_frame(2, 100, 51)]
    history[0].qualifiers["cv_frame"]["players"][0]["speed_kmh"] = None
    state = MatchState(match_id="metrics", scenario_id="cv_upload", seed=1)
    for index, event in enumerate(history):
        reduce_match_state(state, event, history[:index + 1])
    detected = next(item for item in detect_insights(state, history, []) if item.type == "player_speed_burst")
    ids = {key for item in detected.evidence for key in item.supporting_event_ids}
    assert ids == {"frame_1", "frame_2"}


def test_shot_speed_uses_its_own_measurement_not_an_earlier_ball_speed():
    events = [
        Event(match_id="metrics", sequence=1, match_clock_ms=1000, type="pass_completed", team_id="team_a",
              position=Position(x=30, y=50), end_position=Position(x=50, y=50),
              qualifiers={"ball_speed_kmh": 120}),
        Event(match_id="metrics", sequence=2, match_clock_ms=2000, type="shot", team_id="team_a"),
    ]
    metrics = compute_snapshot("metrics", events, "team_a").metrics
    assert metrics["ball_speed_kmh_max"] == 120
    assert metrics["shot_speed_kmh_max"] == 0


def test_an_explicit_shot_speed_is_also_a_ball_speed_measurement():
    shot = Event(match_id="metrics", sequence=1, match_clock_ms=0, type="shot", team_id="team_a",
                 qualifiers={"shot_speed_kmh": 108})
    metrics = compute_snapshot("metrics", [shot], "team_a").metrics
    assert metrics["shot_speed_kmh_max"] == metrics["ball_speed_kmh_max"] == 108
