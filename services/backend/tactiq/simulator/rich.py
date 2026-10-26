from __future__ import annotations

from bisect import bisect_right
from math import ceil, hypot, sin
from random import Random
from uuid import NAMESPACE_URL, uuid5

from tactiq.domain.models import Event, Position

RECIPE_VERSION = 2
FRAME_MS = 250
FORMATION = {
    "01": (5, 50), "02": (22, 15), "03": (23, 38), "04": (23, 62), "05": (22, 85),
    "06": (38, 48), "08": (53, 30), "10": (63, 50), "07": (61, 75), "11": (70, 18), "09": (78, 48),
}
Step = tuple[str, str, float, float]
OPENING: list[Step] = [
    ("recovery", "a_06", 38, 48), ("pass_completed", "a_04", 32, 32),
    ("pass_completed", "a_06", 43, 46), ("pass_completed", "a_08", 51, 65),
    ("pass_completed", "a_10", 62, 48), ("pass_completed", "a_11", 72, 22),
    ("carry", "a_11", 80, 25), ("pass_completed", "a_09", 84, 48), ("shot", "a_09", 96, 50),
]
COUNTER: list[Step] = [
    ("recovery", "b_01", 96, 50), ("pass_completed", "b_04", 87, 35),
    ("pass_completed", "b_06", 74, 50), ("pass_completed", "b_08", 62, 68),
    ("pass_completed", "b_10", 51, 55), ("pass_incomplete", "b_09", 42, 35),
    ("recovery", "a_06", 42, 35),
]
PRESSURE: list[Step] = [
    ("pass_completed", "a_08", 52, 42), ("pass_completed", "a_10", 64, 48),
    ("pass_completed", "a_07", 77, 76), ("pass_completed", "a_08", 62, 60),
    ("pass_completed", "a_10", 78, 52), ("pass_completed", "a_09", 84, 44),
    ("shot", "a_09", 96, 50), ("recovery", "b_01", 96, 50),
    ("pass_completed", "b_04", 85, 38), ("pass_completed", "b_06", 73, 50),
    ("pass_incomplete", "b_08", 63, 62), ("recovery", "a_08", 63, 62),
    ("pass_completed", "a_10", 76, 54), ("carry", "a_10", 88, 48),
    ("pass_completed", "a_08", 69, 40), ("pass_completed", "a_11", 82, 22),
    ("pass_completed", "a_10", 84, 48), ("pass_completed", "a_09", 88, 50), ("shot", "a_09", 100, 50),
]
INFLUENCE: list[Step] = [
    ("pass_completed", "a_10", 54, 43), ("carry", "a_10", 66, 42),
    ("pass_completed", "a_08", 72, 60), ("pass_completed", "a_10", 62, 48),
    ("carry", "a_10", 74, 45), ("pass_completed", "a_11", 86, 24),
    ("pass_completed", "a_10", 68, 44), ("pass_completed", "a_08", 80, 63),
    ("pass_completed", "a_10", 69, 50), ("carry", "a_10", 82, 47),
    ("pass_completed", "a_09", 86, 50), ("shot", "a_09", 96, 50),
    ("recovery", "b_01", 96, 50), ("pass_completed", "b_04", 85, 38),
    ("pass_incomplete", "b_08", 73, 40), ("recovery", "a_10", 73, 40),
    ("carry", "a_10", 85, 46), ("pass_completed", "a_08", 76, 65),
    ("pass_completed", "a_10", 66, 50), ("carry", "a_10", 80, 48),
    ("pass_completed", "a_09", 90, 50), ("shot", "a_09", 100, 50),
]
RHYTHM: list[Step] = [
    ("recovery", "b_01", 96, 50), ("pass_completed", "b_04", 86, 35),
    ("pass_completed", "b_06", 73, 48), ("pass_completed", "b_08", 61, 66),
    ("pass_completed", "b_10", 49, 48), ("pass_completed", "b_11", 36, 74),
    ("pass_completed", "b_08", 40, 58), ("pass_completed", "b_10", 28, 46),
    ("carry", "b_10", 17, 44), ("pass_completed", "b_09", 13, 50), ("shot", "b_09", 4, 50),
    ("recovery", "a_01", 4, 50), ("pass_completed", "a_04", 16, 32),
    ("pass_completed", "a_06", 29, 46), ("pass_incomplete", "a_08", 42, 58),
    ("recovery", "b_08", 42, 58), ("pass_completed", "b_10", 31, 48),
    ("pass_completed", "b_11", 20, 76), ("carry", "b_11", 14, 68),
    ("pass_completed", "b_10", 17, 48), ("carry", "b_10", 9, 46),
    ("pass_completed", "b_09", 11, 50), ("shot", "b_09", 0, 50),
]


def initial_players() -> dict[str, Position]:
    return {f"{team}_{number}": Position(x=x if team == "a" else 100 - x, y=y)
            for team in ("a", "b") for number, (x, y) in FORMATION.items()}


def _distance(a: Position, b: Position) -> float:
    return hypot((a.x - b.x) * 1.05, (a.y - b.y) * .68)


def _identity(match_id: str, scenario: str, seed: int, kind: str, index: int) -> str:
    return str(uuid5(NAMESPACE_URL, f"tactiq:scenario-v2:{scenario}:{match_id}:{seed}:{kind}:{index}"))


def rich_actions(scenario: str, match_id: str, seed: int) -> list[Event]:
    tail = {"sustained_pressure": COUNTER + PRESSURE, "rhythm_shift": RHYTHM,
            "player_influence": COUNTER + INFLUENCE}[scenario]
    script = OPENING + tail
    random = Random(seed)
    contacts = {player: (0, point) for player, point in initial_players().items()}
    ball = Position(x=38, y=48)
    owner: str | None = None
    creator: str | None = None
    clock = 0
    events = []
    for index, (kind, receiver, x, y) in enumerate(script):
        actor = receiver if kind == "recovery" else owner
        if actor is None:
            raise ValueError("A simulated action requires a preceding controlled possession")
        team = "team_a" if actor.startswith("a_") else "team_b"
        if kind == "recovery" and index:
            end = ball.model_copy()
        else:
            end = Position(x=max(0, min(100, x + random.uniform(-.7, .7))),
                           y=max(1, min(99, y + random.uniform(-1.2, 1.2))))
        if index == len(script) - 1:
            end = Position(x=x, y=50)
        start = ball.model_copy() if index else end.model_copy()
        distance = _distance(start, end)
        speed_mps = 28 if kind == "shot" else 4.5 if kind == "carry" else 21
        travel = ceil(distance / speed_mps * 1000) if distance else 0
        pace = 6500 if scenario == "rhythm_shift" and index < len(OPENING) else 2800 if scenario == "rhythm_shift" else 3800
        clock += max(pace + random.randrange(0, 180), travel + 300) if index else 1500
        touched = [(actor, start, travel)]
        if kind == "pass_completed":
            touched.append((receiver, end, 0))
        elif kind in {"carry", "recovery"}:
            touched.append((actor, end, 0))
        for player, point, offset in touched:
            previous_time, previous_point = contacts[player]
            clock = max(clock, previous_time + ceil(_distance(previous_point, point) / 7.5 * 1000) + offset)
        for player, point, offset in touched:
            contacts[player] = clock - offset, point
        direction = 1 if team == "team_a" else -1
        progressive = kind in {"pass_completed", "carry"} and (end.x - start.x) * direction >= 10
        qualifiers = {
            "scenario_recipe_version": RECIPE_VERSION, "travel_ms": travel, "progressive": progressive,
            "final_third_entry": kind in {"pass_completed", "carry"} and
                (start.x < 66.67 <= end.x if direction == 1 else start.x > 33.33 >= end.x),
            "pressured": index > 10 and index % 3 == 0,
        }
        if kind == "shot":
            qualifiers.update({"outcome": "goal" if index == len(script) - 1 else "saved",
                               "shot_speed_kmh": round(distance / (travel / 1000) * 3.6, 1)})
            if creator:
                qualifiers["created_by"] = creator
        events.append(Event(
            event_id=_identity(match_id, scenario, seed, "action", index), match_id=match_id,
            sequence=index + 1, match_clock_ms=clock, type=kind, team_id=team, player_id=actor,
            target_player_id=receiver if kind in {"pass_completed", "pass_incomplete"} else None,
            position=start, end_position=end, qualifiers=qualifiers, synthetic=True,
        ))
        ball = end
        if kind == "pass_completed":
            creator, owner = actor, receiver
        elif kind in {"shot", "pass_incomplete"}:
            owner, creator = None, None
        else:
            owner = actor
    final = events[-1]
    events.append(Event(
        event_id=_identity(match_id, scenario, seed, "goal", 0), match_id=match_id,
        sequence=len(events) + 1, match_clock_ms=clock + 1, type="goal", team_id=final.team_id,
        player_id=final.player_id, position=ball, end_position=ball,
        qualifiers={"scenario_recipe_version": RECIPE_VERSION, "source_shot_id": final.event_id},
    ))
    return events


def rich_timeline(scenario: str, match_id: str, seed: int) -> list[Event]:
    actions = rich_actions(scenario, match_id, seed)
    initial = initial_players()
    contacts: dict[str, list[tuple[int, Position]]] = {player: [(0, point)] for player, point in initial.items()}
    for event in actions:
        if event.type == "goal":
            continue
        assert event.position and event.end_position and event.player_id
        start = event.match_clock_ms - event.qualifiers.get("travel_ms", 0)
        contacts[event.player_id].append((start, event.position))
        if event.type == "pass_completed" and event.target_player_id:
            contacts[event.target_player_id].append((event.match_clock_ms, event.end_position))
        elif event.type in {"carry", "recovery"}:
            contacts[event.player_id].append((event.match_clock_ms, event.end_position))
    for points in contacts.values():
        points.sort(key=lambda item: item[0])

    def player_at(player: str, timestamp: int, ball: Position) -> Position:
        points = contacts[player]
        if len(points) == 1:
            base = initial[player]
            return Position(x=max(1, min(99, base.x + (ball.x - 50) * .1)),
                            y=max(1, min(99, base.y + sin(timestamp / 18000) * 2)))
        index = bisect_right([time for time, _ in points], timestamp) - 1
        before_time, before = points[max(0, index)]
        if index + 1 >= len(points):
            return before
        after_time, after = points[index + 1]
        fraction = (timestamp - before_time) / max(1, after_time - before_time)
        return Position(x=before.x + (after.x - before.x) * fraction, y=before.y + (after.y - before.y) * fraction)

    frames = []
    previous: dict[str, Position] = {}
    completed = -1
    duration = actions[-1].match_clock_ms + FRAME_MS
    for timestamp in range(0, duration + FRAME_MS, FRAME_MS):
        while completed + 1 < len(actions) and actions[completed + 1].match_clock_ms <= timestamp:
            completed += 1
        current = actions[completed] if completed >= 0 else None
        following = actions[completed + 1] if completed + 1 < len(actions) else None
        ball = (current.end_position if current else actions[0].position).model_copy()
        speed = 0.0
        owner = current.target_player_id if current and current.type == "pass_completed" else (
            current.player_id if current and current.type in {"carry", "recovery"} else None)
        team = current.team_id if owner else None
        if following and following.qualifiers.get("travel_ms"):
            travel = following.qualifiers["travel_ms"]
            starts = following.match_clock_ms - travel
            if timestamp >= starts:
                assert following.position and following.end_position
                fraction = (timestamp - starts) / travel
                ball = Position(x=following.position.x + (following.end_position.x - following.position.x) * fraction,
                                y=following.position.y + (following.end_position.y - following.position.y) * fraction)
                speed = round(_distance(following.position, following.end_position) / (travel / 1000) * 3.6, 2)
                owner = following.player_id if following.type == "carry" else None
                team = following.team_id
        players = []
        for player in initial:
            point = player_at(player, timestamp, ball)
            velocity = _distance(previous[player], point) / (FRAME_MS / 1000) * 3.6 if player in previous else 0.0
            previous[player] = point
            players.append({"track_id": player, "team_id": "team_a" if player.startswith("a_") else "team_b",
                            "position": point.model_dump(), "image_position": None, "confidence": 1.0,
                            "team_confidence": 1.0, "inferred": False, "speed_kmh": round(velocity, 3)})
        position = ball.model_dump()
        frame = {
            "tracking_source": "scenario-ground-truth-v2", "detection_source": "synthetic",
            "geometry_source": "simulation", "coordinate_space": "pitch_percent", "calibrated": False,
            "calibration": {"status": "not_applicable", "revision": 0, "reason": "generated_pitch_coordinates"},
            "players": players, "tracked_players": len(players),
            "ball": {"track_id": "sim_ball", "position": position, "image_position": None,
                     "inferred": False, "confidence": 1.0, "speed_kmh": speed},
            "ball_position": position, "ball_speed_kmh": speed,
            "top_player_speed_kmh": max(player["speed_kmh"] for player in players),
            "possession_team_id": team, "possession_player_id": owner,
            "scene_id": 0, "updated_at_ms": timestamp,
        }
        frames.append(Event(
            event_id=_identity(match_id, scenario, seed, "frame", timestamp), match_id=match_id,
            sequence=1, match_clock_ms=timestamp, type="tracking_frame", team_id=team or "unknown",
            qualifiers={"cv_frame": frame, "scenario_recipe_version": RECIPE_VERSION},
        ))
    timeline = sorted([*actions, *frames], key=lambda event: (event.match_clock_ms, event.type == "tracking_frame"))
    for sequence, event in enumerate(timeline, 1):
        event.sequence = sequence
    return timeline
