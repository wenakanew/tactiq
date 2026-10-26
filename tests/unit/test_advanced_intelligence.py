import pytest

from tactiq.application.service import MatchService
from tactiq.domain.models import Event, MatchState, Position
from tactiq.intelligence.engine import detect_insights, reduce_match_state
from tactiq.intelligence.tactics import possession_chains
from tactiq.intelligence.value import ValueModel
from tactiq.simulator.scenarios import load_scenario


def event(sequence, time, kind, team="team_a", start=20, end=30):
    return Event(
        match_id="advanced", event_id=f"event_{sequence}", sequence=sequence, match_clock_ms=time,
        type=kind, team_id=team, position=Position(x=start, y=50), end_position=Position(x=end, y=50),
        qualifiers={"progressive": end > start},
    )


def test_chains_close_on_shot_turnover_and_team_change():
    events = [event(1, 0, "recovery"), event(2, 1000, "pass_completed"),
              event(3, 2000, "shot", start=85, end=100),
              event(4, 3000, "recovery", "team_b", 80, 70),
              event(5, 4000, "turnover", "team_b")]
    chains = possession_chains(events, {"team_a": 1, "team_b": -1}, ValueModel.reference())
    assert len(chains) == 2
    assert chains[0]["terminal_event"] == "shot"
    assert chains[1]["terminal_event"] == "turnover"
    assert chains[0]["xg_estimate"] > 0
    assert chains == possession_chains(events, {"team_a": 1, "team_b": -1}, ValueModel.reference())


def test_value_model_mirrors_direction_and_labels_reference():
    model = ValueModel.reference()
    assert not model.validated
    assert model.shot_probability(Position(x=90, y=50), 1) == pytest.approx(
        model.shot_probability(Position(x=10, y=50), -1))
    assert model.shot_probability(Position(x=90, y=50), 1) > model.shot_probability(Position(x=50, y=50), 1)
    assert model.threat(Position(x=90, y=50), 1) > model.threat(Position(x=20, y=50), 1)
    with pytest.raises(ValueError, match="benchmark report"):
        ValueModel.model_validate({**model.model_dump(), "validated": True})


def test_transition_has_evidence_and_no_formation_is_fabricated():
    state = MatchState(match_id="advanced", scenario_id="test", seed=1)
    history = []
    for item in [event(1, 0, "recovery", start=15, end=15),
                 event(2, 2000, "pass_completed", start=15, end=30),
                 event(3, 4000, "carry", start=30, end=50)]:
        history.append(item)
        reduce_match_state(state, item, history, ValueModel.reference())
    insights = detect_insights(state, history, [])
    transition = next(i for i in insights if i.type == "transition_attack")
    assert transition.evidence[0].value == pytest.approx(36.75)
    assert set(transition.evidence[0].supporting_event_ids) == {"event_1", "event_2", "event_3"}
    assert state.tactical_state["team_shapes"] == {}
    assert state.tactical_state["value_model"]["status"] == "reference_unvalidated"


def test_unknown_attack_direction_withholds_directional_estimates():
    state = MatchState(match_id="advanced", scenario_id="cv_upload", seed=1, attack_directions={})
    history = [event(1, 0, "recovery"), event(2, 1000, "pass_completed")]
    for item in history:
        reduce_match_state(state, item, history[:item.sequence], ValueModel.reference())
    chain = state.tactical_state["recent_chains"][0]
    assert chain["completed_passes"] == 1
    assert chain["progression_m"] is None
    assert chain["xt_gain_estimate"] is None
    assert chain["phase"] == "unknown"


def test_seed_changes_events_and_replays_exactly():
    _, first = load_scenario("sustained_pressure", "same", 123)
    _, repeat = load_scenario("sustained_pressure", "same", 123)
    _, other = load_scenario("sustained_pressure", "same", 124)
    assert first == repeat
    assert [e.position for e in first] != [e.position for e in other]
    assert [e.match_clock_ms for e in first] == sorted(e.match_clock_ms for e in first)


def test_match_processing_rejects_out_of_order_events_and_replays_real_state(tmp_path, monkeypatch):
    monkeypatch.setenv("TACTIQ_MEDIA_DIR", str(tmp_path))
    service = MatchService()
    state = service.create_match("sustained_pressure", 1)
    item = event(1, 1000, "recovery")
    item.match_id = state.match_id
    service.record_event(item)
    service.record_event(item)
    assert len(service.get_replay(state.match_id)["frames"]) == 1
    early = event(2, 500, "pass_completed")
    early.match_id = state.match_id
    with pytest.raises(ValueError, match="precede"):
        service.record_event(early)


def tracked_player(player_id, team, x, y):
    return {"track_id": player_id, "team_id": team, "position": {"x": x, "y": y},
            "confidence": 0.95, "inferred": False}


def tracking_event(sequence, timestamp, players, owner="team_a", calibrated=True):
    return Event(
        match_id="advanced", event_id=f"frame_{sequence}", sequence=sequence, match_clock_ms=timestamp,
        type="tracking_frame", team_id=owner,
        qualifiers={"cv_frame": {
            "tracking_source": "annotated-fixture", "calibrated": calibrated, "players": players,
            "ball_position": {"x": 60, "y": 20}, "possession_team_id": owner,
        }},
    )


def test_formation_requires_complete_coverage_and_temporal_agreement():
    positions = [(5, 50), *[(25, y) for y in (10, 35, 65, 90)],
                 *[(50, y) for y in (20, 50, 80)], *[(80, y) for y in (25, 50, 75)]]
    players = [tracked_player(f"a_{index}", "team_a", x, y) for index, (x, y) in enumerate(positions)]
    state = MatchState(match_id="advanced", scenario_id="test", seed=1)
    history = []
    for index, timestamp in enumerate((0, 500, 1000), 1):
        frame = tracking_event(index, timestamp, players)
        history.append(frame)
        reduce_match_state(state, frame, history)
        shape = state.tactical_state["team_shapes"]["team_a"]
        assert shape["formation_estimate"] == ("4-3-3" if index == 3 else None)
    assert shape["width_m"] == pytest.approx(54.4)


def test_overload_uses_observed_geometry_and_rejects_uncalibrated_frames():
    players = [
        tracked_player("a1", "team_a", 60, 20), tracked_player("a2", "team_a", 61, 22),
        tracked_player("a3", "team_a", 59, 18), tracked_player("a4", "team_a", 10, 80),
        tracked_player("a5", "team_a", 15, 80),
        tracked_player("b1", "team_b", 63, 21), *[tracked_player(f"b{i}", "team_b", 90, 80) for i in range(2, 6)],
    ]
    for calibrated in (True, False):
        state = MatchState(match_id="advanced", scenario_id="test", seed=1)
        history = [tracking_event(index + 1, timestamp, players, calibrated=calibrated)
                   for index, timestamp in enumerate((0, 500, 1000))]
        for frame in history:
            reduce_match_state(state, frame, history[:frame.sequence])
        detected = [item for item in detect_insights(state, history, []) if item.type == "local_overload"]
        assert bool(detected) is calibrated
        if calibrated:
            assert {e.metric: e.value for e in detected[0].evidence} == {"nearby_attackers": 3, "nearby_defenders": 1}


def test_pressing_convergence_requires_a_later_recovery():
    players = [
        tracked_player("a1", "team_a", 60, 20), *[tracked_player(f"a{i}", "team_a", 10, 80) for i in range(2, 6)],
        *[tracked_player(f"b{i}", "team_b", 60 + i, 20) for i in range(1, 4)],
        *[tracked_player(f"b{i}", "team_b", 90, 80) for i in (4, 5)],
    ]
    state = MatchState(match_id="advanced", scenario_id="test", seed=1)
    history = [tracking_event(index + 1, timestamp, players) for index, timestamp in enumerate((0, 250, 500))]
    for frame in history:
        reduce_match_state(state, frame, history[:frame.sequence])
    assert not any(item.type == "pressing_trap" for item in detect_insights(state, history, []))
    recovery = event(4, 1000, "recovery", "team_b", 60, 60)
    history.append(recovery)
    reduce_match_state(state, recovery, history)
    detected = detect_insights(state, history, [])
    assert any(item.type == "pressing_trap" for item in detected)
    assert not any(item.type == "local_overload" for item in detected)
