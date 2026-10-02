from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from tactiq.domain.models import Event, Position


@dataclass(frozen=True)
class ScenarioConfig:
    scenario_id: str
    total_ms: int


SUSTAINED_PRESSURE = ScenarioConfig(scenario_id="sustained_pressure", total_ms=68_000)
RHYTHM_SHIFT = ScenarioConfig(scenario_id="rhythm_shift", total_ms=74_000)
PLAYER_INFLUENCE = ScenarioConfig(scenario_id="player_influence", total_ms=71_000)


def sustained_pressure_events(match_id: str) -> list[Event]:
    # Deliberately scripted to satisfy detector thresholds in a 90s window.
    raw = [
        (61_000, "recovery", "team_a", "a_06", 42.0, 43.0, 43.0, 44.0, {}),
        (62_000, "pass_completed", "team_a", "a_08", 45.0, 45.0, 58.0, 41.0, {"progressive": True}),
        (63_000, "pass_completed", "team_a", "a_10", 58.0, 41.0, 68.0, 39.0, {"progressive": True}),
        (64_000, "pass_completed", "team_a", "a_11", 63.0, 39.0, 72.0, 35.0, {"progressive": True}),
        (65_000, "pass_completed", "team_a", "a_08", 62.0, 35.0, 75.0, 32.0, {"progressive": True}),
        (66_000, "carry", "team_a", "a_10", 64.0, 32.0, 81.0, 30.0, {"progressive": True}),
        (67_000, "pass_completed", "team_a", "a_07", 81.0, 30.0, 78.0, 34.0, {"progressive": False}),
        (68_000, "shot", "team_a", "a_09", 78.0, 34.0, 90.0, 30.0, {}),
    ]

    events: list[Event] = []
    for idx, (clock_ms, event_type, team_id, player_id, sx, sy, ex, ey, qualifiers) in enumerate(raw, start=1):
        entry_qualifiers = dict(qualifiers)
        if sx < 66.67 <= ex and event_type in {"pass_completed", "carry"}:
            entry_qualifiers["final_third_entry"] = True
        events.append(
            Event(
                match_id=match_id,
                sequence=idx,
                match_clock_ms=clock_ms,
                type=event_type,
                team_id=team_id,
                player_id=player_id,
                position=Position(x=sx, y=sy),
                end_position=Position(x=ex, y=ey),
                qualifiers=entry_qualifiers,
            )
        )
    return events


def rhythm_shift_events(match_id: str) -> list[Event]:
    # Early low tempo, then Team B surges in event intensity and progression.
    raw = [
        (20_000, "pass_completed", "team_a", "a_04", 41.0, 42.0, 48.0, 43.0, {"progressive": False}),
        (30_000, "pass_completed", "team_a", "a_06", 48.0, 43.0, 54.0, 39.0, {"progressive": False}),
        (40_000, "turnover", "team_a", "a_08", 54.0, 39.0, 52.0, 37.0, {}),
        (45_000, "recovery", "team_b", "b_06", 52.0, 37.0, 53.0, 36.0, {}),
        (52_000, "pass_completed", "team_b", "b_08", 53.0, 36.0, 63.0, 34.0, {"progressive": True}),
        (56_000, "carry", "team_b", "b_11", 63.0, 34.0, 71.0, 30.0, {"progressive": True}),
        (60_000, "pass_completed", "team_b", "b_10", 71.0, 30.0, 76.0, 31.0, {"progressive": True}),
        (64_000, "pass_completed", "team_b", "b_07", 76.0, 31.0, 81.0, 29.0, {"progressive": True}),
        (68_000, "shot", "team_b", "b_09", 81.0, 29.0, 90.0, 31.0, {}),
        (72_000, "pass_completed", "team_b", "b_08", 66.0, 31.0, 78.0, 33.0, {"progressive": True}),
    ]

    events: list[Event] = []
    for idx, (clock_ms, event_type, team_id, player_id, sx, sy, ex, ey, qualifiers) in enumerate(raw, start=1):
        entry_qualifiers = dict(qualifiers)
        if sx < 66.67 <= ex and event_type in {"pass_completed", "carry"}:
            entry_qualifiers["final_third_entry"] = True
        events.append(
            Event(
                match_id=match_id,
                sequence=idx,
                match_clock_ms=clock_ms,
                type=event_type,
                team_id=team_id,
                player_id=player_id,
                position=Position(x=sx, y=sy),
                end_position=Position(x=ex, y=ey),
                qualifiers=entry_qualifiers,
            )
        )
    return events


def player_influence_events(match_id: str) -> list[Event]:
    # Player a_10 drives most progressive actions and contributes to the shot sequence.
    raw = [
        (58_000, "recovery", "team_a", "a_06", 43.0, 45.0, 44.0, 45.0, {}),
        (60_000, "pass_completed", "team_a", "a_10", 44.0, 45.0, 58.0, 42.0, {"progressive": True}),
        (62_000, "carry", "team_a", "a_10", 58.0, 42.0, 68.0, 39.0, {"progressive": True}),
        (64_000, "pass_completed", "team_a", "a_10", 68.0, 39.0, 74.0, 34.0, {"progressive": True}),
        (66_000, "pass_completed", "team_a", "a_08", 74.0, 34.0, 77.0, 31.0, {"progressive": True}),
        (68_000, "pass_completed", "team_a", "a_10", 77.0, 31.0, 82.0, 29.0, {"progressive": True}),
        (70_000, "shot", "team_a", "a_09", 82.0, 29.0, 91.0, 30.0, {"created_by": "a_10"}),
    ]

    events: list[Event] = []
    for idx, (clock_ms, event_type, team_id, player_id, sx, sy, ex, ey, qualifiers) in enumerate(raw, start=1):
        entry_qualifiers = dict(qualifiers)
        if sx < 66.67 <= ex and event_type in {"pass_completed", "carry"}:
            entry_qualifiers["final_third_entry"] = True
        events.append(
            Event(
                match_id=match_id,
                sequence=idx,
                match_clock_ms=clock_ms,
                type=event_type,
                team_id=team_id,
                player_id=player_id,
                position=Position(x=sx, y=sy),
                end_position=Position(x=ex, y=ey),
                qualifiers=entry_qualifiers,
            )
        )
    return events


SCENARIOS: dict[str, tuple[ScenarioConfig, Callable[[str], list[Event]]]] = {
    SUSTAINED_PRESSURE.scenario_id: (SUSTAINED_PRESSURE, sustained_pressure_events),
    RHYTHM_SHIFT.scenario_id: (RHYTHM_SHIFT, rhythm_shift_events),
    PLAYER_INFLUENCE.scenario_id: (PLAYER_INFLUENCE, player_influence_events),
}


def list_scenarios() -> list[ScenarioConfig]:
    return [item[0] for item in SCENARIOS.values()]


def load_scenario(scenario_id: str, match_id: str) -> tuple[ScenarioConfig, list[Event]]:
    if scenario_id not in SCENARIOS:
        raise ValueError(f"Unsupported scenario: {scenario_id}")
    config, factory = SCENARIOS[scenario_id]
    return config, factory(match_id)
