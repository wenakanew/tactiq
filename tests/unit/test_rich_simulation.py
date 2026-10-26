import asyncio
from math import hypot
from threading import Event as ThreadEvent
from uuid import NAMESPACE_URL, uuid5

import pytest

from tactiq.application.service import MatchService
from tactiq.domain.models import MatchState, MatchStatus
from tactiq.intelligence.engine import compute_snapshot
from tactiq.evaluation.smoke import run_smoke
from tactiq.simulator.scenarios import load_scenario, scenario_timeline


@pytest.mark.parametrize("scenario", ["sustained_pressure", "rhythm_shift", "player_influence"])
def test_richer_recipes_are_reproducible_and_keep_actions_separate_from_samples(scenario):
    config, actions = load_scenario(scenario, "rich", 123)
    timeline = scenario_timeline(scenario, "rich", 123, 2)
    assert timeline == scenario_timeline(scenario, "rich", 123, 2)
    frames = [event for event in timeline if event.type == "tracking_frame"]
    assert len(actions) >= 30
    assert 400 <= len(frames) <= 1000
    assert all(event.synthetic for event in timeline)
    assert [event.sequence for event in timeline] == list(range(1, len(timeline) + 1))
    assert len({event.event_id for event in timeline}) == len(timeline)
    assert max(event.match_clock_ms for event in timeline) <= config.total_ms
    assert {event.event_id for event in timeline if event.type != "tracking_frame"} == {event.event_id for event in actions}
    for frame in frames:
        data = frame.qualifiers["cv_frame"]
        assert data["geometry_source"] == "simulation" and not data["calibrated"]
        assert len(data["players"]) == 22
        assert max(player["speed_kmh"] for player in data["players"]) <= 28
    metrics = compute_snapshot("rich", timeline, "team_a").metrics
    plain = compute_snapshot("rich", actions, "team_a").metrics
    for key in ("pass_attempts", "pass_completed", "progressive_actions", "shots", "event_intensity_per_min"):
        assert metrics[key] == plain[key]


def test_completed_passes_connect_to_the_receiving_player_without_teleporting_the_ball():
    timeline = scenario_timeline("player_influence", "contacts", 42, 2)
    frames = [event for event in timeline if event.type == "tracking_frame"]
    for action in [event for event in timeline if event.type == "pass_completed"]:
        closest = min(frames, key=lambda frame: abs(frame.match_clock_ms - action.match_clock_ms))
        player = next(player for player in closest.qualifiers["cv_frame"]["players"] if player["track_id"] == action.target_player_id)
        assert hypot((player["position"]["x"] - action.end_position.x) * 1.05,
                     (player["position"]["y"] - action.end_position.y) * .68) < 1
    for before, after in zip(frames, frames[1:]):
        a, b = before.qualifiers["cv_frame"]["ball_position"], after.qualifiers["cv_frame"]["ball_position"]
        speed = hypot((b["x"] - a["x"]) * 1.05, (b["y"] - a["y"]) * .68) / .25 * 3.6
        assert speed <= 102


def test_legacy_recipe_remains_byte_for_byte_reproducible():
    config, events = load_scenario("sustained_pressure", "legacy", 123, version=1)
    assert config.total_ms == 68000 and len(events) == 8
    assert events[0].event_id == str(uuid5(NAMESPACE_URL, "tactiq:sustained_pressure:legacy:123:1"))
    assert 61000 <= events[0].match_clock_ms < 61250
    old = MatchState.model_validate({"match_id": "legacy", "scenario_id": "sustained_pressure", "seed": 123})
    assert old.scenario_recipe_version == 1
    assert scenario_timeline("sustained_pressure", "legacy", 123, old.scenario_recipe_version) == events


def test_scenario_preparation_finishes_without_sleeping_for_every_tracking_sample(tmp_path, monkeypatch):
    monkeypatch.setenv("TACTIQ_MEDIA_DIR", str(tmp_path))
    monkeypatch.setenv("FOUNDRY_ENABLED", "false")
    service = MatchService()
    state = service.create_match("sustained_pressure", 123)

    async def prepare():
        await service.start_match(state.match_id)
        await service.tasks[state.match_id]

    asyncio.run(prepare())
    assert state.status == MatchStatus.COMPLETED
    assert state.action_count >= 30 and state.tracking_sample_count >= 400
    assert state.last_sequence == state.action_count + state.tracking_sample_count
    assert sum(state.score.values()) == 1
    assert state.tactical_state["geometry_status"] == "simulation"
    assert service.get_replay(state.match_id)["frames"][-1]["score"] == state.score


def test_resuming_a_v2_prefix_keeps_committed_ids_times_and_sample_order(tmp_path, monkeypatch):
    from tactiq.application import service as service_module

    monkeypatch.setenv("TACTIQ_MEDIA_DIR", str(tmp_path))
    service = MatchService()
    state = service.create_match("sustained_pressure", 123)
    timeline = scenario_timeline(state.scenario_id, state.match_id, state.seed, 2)[:100]
    for event in timeline[:43]:
        service.record_event(event, persist=False)
    state.status = MatchStatus.PAUSED
    called = []

    def recipe(scenario, match_id, seed, version):
        called.append(version)
        return timeline

    monkeypatch.setattr(service_module, "scenario_timeline", recipe)

    async def resume():
        await service.start_match(state.match_id)
        await service.tasks[state.match_id]

    asyncio.run(resume())
    assert called == [2]
    assert service.events[state.match_id] == timeline
    assert len({event.event_id for event in service.events[state.match_id]}) == len(timeline)
    assert state.last_sequence == 100 and state.status == MatchStatus.COMPLETED


def test_pause_joins_the_current_mutating_worker_before_returning(tmp_path, monkeypatch):
    from tactiq.application import service as service_module

    monkeypatch.setenv("TACTIQ_MEDIA_DIR", str(tmp_path))
    service = MatchService()
    state = service.create_match("sustained_pressure", 1)
    _, events = load_scenario(state.scenario_id, state.match_id, state.seed, version=1)
    monkeypatch.setattr(service_module, "scenario_timeline", lambda *args: events[:1])
    entered, release = ThreadEvent(), ThreadEvent()
    original = service.record_event

    def blocked(event, *, persist=True):
        entered.set()
        assert release.wait(5)
        return original(event, persist=persist)

    monkeypatch.setattr(service, "record_event", blocked)

    async def exercise():
        await service.start_match(state.match_id)
        assert await asyncio.to_thread(entered.wait, 3)
        pausing = asyncio.create_task(service.pause_match(state.match_id))
        try:
            await asyncio.sleep(.02)
            assert not pausing.done()
        finally:
            release.set()
        await pausing
        stopped = state.last_sequence
        await asyncio.sleep(.02)
        assert state.last_sequence == stopped

    asyncio.run(exercise())
    assert state.status == MatchStatus.PAUSED and state.last_sequence == 1
    assert state.match_id not in service.tasks


def test_all_three_richer_themes_remain_detectable_across_the_seeded_smoke_dataset():
    report = run_smoke()
    assert report["smoke_passed"]
    assert all(item["expected_detected"] and item["evidence_resolves"] for item in report["scenarios"])
    assert report["dataset"]["source"] == "seeded-rule-fixtures-v2"
