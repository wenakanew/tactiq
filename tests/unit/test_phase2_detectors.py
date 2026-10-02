from tactiq.domain.models import MatchState
from tactiq.intelligence.engine import detect_player_influence, detect_rhythm_shift
from tactiq.simulator.scenarios import player_influence_events, rhythm_shift_events


def test_rhythm_shift_detector_emits() -> None:
    match_id = "match_rhythm"
    state = MatchState(match_id=match_id, scenario_id="rhythm_shift", seed=1)
    events = rhythm_shift_events(match_id)

    insight = detect_rhythm_shift(state, events, existing_insights=[])

    assert insight is not None
    assert insight.type == "rhythm_shift"
    assert insight.team_id == "team_b"
    assert insight.title == "Momentum Shift"


def test_player_influence_detector_emits() -> None:
    match_id = "match_player"
    state = MatchState(match_id=match_id, scenario_id="player_influence", seed=1)
    events = player_influence_events(match_id)

    insight = detect_player_influence(state, events, existing_insights=[])

    assert insight is not None
    assert insight.type == "player_influence"
    assert insight.team_id == "team_a"
    assert insight.title == "Player Influence Rising"
