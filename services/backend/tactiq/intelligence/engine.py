from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from math import isfinite, sqrt
from typing import Any

from tactiq.domain.models import Event, EvidenceItem, Insight, MatchState, MetricSnapshot
from tactiq.intelligence.tactics import detect_tactical_insights, tactical_snapshot
from tactiq.intelligence.value import ValueModel
from tactiq.intelligence.geometry import has_pitch_geometry


WINDOW_MS = 90_000
PITCH_LENGTH_M = 105.0
PITCH_WIDTH_M = 68.0


def insight_priority_weight(priority: str) -> float:
    mapping = {
        "high": 1.0,
        "medium": 0.7,
        "low": 0.45,
    }
    return mapping.get(priority.lower(), 0.5)


def _distance_m(start: Event, end: Event) -> float:
    if not start.position or not end.end_position:
        return 0.0
    dx = ((end.end_position.x - start.position.x) / 100.0) * PITCH_LENGTH_M
    dy = ((end.end_position.y - start.position.y) / 100.0) * PITCH_WIDTH_M
    return sqrt((dx * dx) + (dy * dy))


def _event_distance_m(event: Event) -> float:
    if not event.position or not event.end_position:
        return 0.0
    dx = ((event.end_position.x - event.position.x) / 100.0) * PITCH_LENGTH_M
    dy = ((event.end_position.y - event.position.y) / 100.0) * PITCH_WIDTH_M
    return sqrt((dx * dx) + (dy * dy))


def _estimate_ball_speed_kmh(event: Event) -> float:
    names = ("shot_speed_kmh", "ball_speed_kmh") if event.type == "shot" else ("ball_speed_kmh", "shot_speed_kmh")
    for name in names:
        value = event.qualifiers.get(name)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value < 0:
            raise ValueError(f"{name} must be a finite non-negative measurement")
        return float(value)

    distance_m = _event_distance_m(event)
    travel_ms = float(event.qualifiers.get("travel_ms", 0) or 0)
    if not isfinite(travel_ms) or travel_ms < 0:
        raise ValueError("travel_ms must be a finite non-negative duration")
    if distance_m <= 0 or travel_ms <= 0:
        return 0.0
    return (distance_m / max(0.2, travel_ms / 1000.0)) * 3.6


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _possession_transition_confidence(event: Event, all_events: list[Event]) -> float:
    possession_types = {"recovery", "pass_completed", "carry", "shot", "turnover"}
    lookback = [item for item in all_events[-10:] if item.type in possession_types]
    if not lookback:
        return 0.5

    current_team = event.team_id
    switches = 0
    for idx in range(1, len(lookback)):
        if lookback[idx].team_id != lookback[idx - 1].team_id:
            switches += 1

    streak = 0
    for item in reversed(lookback):
        if item.team_id == current_team:
            streak += 1
        else:
            break

    type_bonus = 0.2 if event.type in {"recovery", "turnover"} else 0.08
    streak_score = min(1.0, streak / 3.0) * 0.34
    volatility_penalty = min(0.42, switches * 0.09)
    pressure_penalty = 0.07 if bool(event.qualifiers.get("pressured")) else 0.0

    confidence = 0.42 + type_bonus + streak_score - volatility_penalty - pressure_penalty
    return _clamp(confidence, 0.05, 0.99)


def _control_fields(metrics_by_team: dict[str, dict[str, float | int]]) -> dict[str, Any]:
    scores = {team: round(float(metrics.get("progressive_actions", 0)) * .55
                         + float(metrics.get("final_third_entries", 0)) * 1.35
                         + float(metrics.get("shots", 0)) * 1.8, 2)
              for team, metrics in metrics_by_team.items()}
    if not scores:
        return {"control_index_by_team": {}}
    delta = round(scores.get("team_a", 0) - scores.get("team_b", 0), 2)
    return {"control_index_by_team": scores, "control_delta": delta,
            "controlling_team_id": "team_a" if delta > 0 else "team_b" if delta < 0 else "neutral"}


def _derive_cv_live(match_state: MatchState, event: Event, all_events: list[Event]) -> dict[str, Any]:
    if event.type == "motion_sample":
        return {
            "tracking_source": "motion-preview", "detection_source": "heuristic",
            "geometry_source": "image", "coordinate_space": "image_percent", "calibrated": False,
            "players": [], "ball": None, "ball_position": None, "tracked_players": 0,
            "updated_at_ms": event.match_clock_ms,
        }
    point = event.end_position or event.position
    x = float(point.x) if point else 50.0
    y = float(point.y) if point else 50.0

    prev_smoothed = match_state.cv_live.get("smoothed_ball_position") if isinstance(match_state.cv_live, dict) else None
    prev_x = float(prev_smoothed.get("x")) if isinstance(prev_smoothed, dict) and isinstance(prev_smoothed.get("x"), (int, float)) else x
    prev_y = float(prev_smoothed.get("y")) if isinstance(prev_smoothed, dict) and isinstance(prev_smoothed.get("y"), (int, float)) else y
    alpha = 0.36
    smoothed_x = _clamp((alpha * x) + ((1.0 - alpha) * prev_x), 0.0, 100.0)
    smoothed_y = _clamp((alpha * y) + ((1.0 - alpha) * prev_y), 0.0, 100.0)

    delta_x = smoothed_x - prev_x
    travel_direction = "static"
    if delta_x > 0.75:
        travel_direction = "left_to_right"
    elif delta_x < -0.75:
        travel_direction = "right_to_left"

    speed_leader = max((float(metrics.get("top_player_speed_kmh", 0)) for metrics in match_state.metrics_by_team.values()), default=0)

    pitch_zone = "midfield"
    if x >= 66.67:
        pitch_zone = "final_third_team_b"
    elif x <= 33.33:
        pitch_zone = "final_third_team_a"

    return {
        "raw_ball_position": {"x": round(x, 2), "y": round(y, 2)},
        "smoothed_ball_position": {"x": round(smoothed_x, 2), "y": round(smoothed_y, 2)},
        "ball_position": {"x": round(smoothed_x, 2), "y": round(smoothed_y, 2)},
        "ball_speed_kmh": round(_estimate_ball_speed_kmh(event), 1),
        "ball_travel_direction": travel_direction,
        "top_player_speed_kmh": round(speed_leader, 1),
        "tracked_players": 0,
        "players": [],
        "calibrated": False,
        "coordinate_space": "event_percent",
        "detection_source": "synthetic" if event.synthetic else "inferred_event",
        "team_identification_confidence": None,
        "possession_team_id": match_state.possession_team_id,
        "possession_transition_confidence": round(_possession_transition_confidence(event, all_events), 2),
        **_control_fields(match_state.metrics_by_team),
        "pitch_zone": pitch_zone,
        "tracking_source": "event_stream_v1",
        "updated_at_ms": event.match_clock_ms,
    }


def _difficulty_rating(event: Event, distance_m: float) -> float:
    explicit = event.qualifiers.get("pass_difficulty")
    if isinstance(explicit, (int, float)):
        return max(0.0, min(1.0, float(explicit)))

    score = 0.3
    score += min(0.4, distance_m / 40.0)
    if bool(event.qualifiers.get("progressive")):
        score += 0.15
    if bool(event.qualifiers.get("pressured")):
        score += 0.15
    return max(0.0, min(1.0, score))


def _window_events(events: list[Event], end_ms: int) -> list[Event]:
    start = max(0, end_ms - WINDOW_MS)
    return [e for e in events if e.type not in {"tracking_frame", "motion_sample"} and start <= e.match_clock_ms <= end_ms]


def compute_snapshot(match_id: str, events: list[Event], team_id: str, as_of_ms: int | None = None) -> MetricSnapshot:
    if not events:
        return MetricSnapshot(
            match_id=match_id,
            as_of_sequence=0,
            as_of_match_clock_ms=0,
            team_id=team_id,
            metrics={
                "progressive_actions": 0,
                "final_third_entries": 0,
                "shots": 0,
                "event_intensity_per_min": 0.0,
                "pass_attempts": 0,
                "pass_completed": 0,
                "pass_accuracy_pct": 0.0,
                "pass_avg_distance_m": 0.0,
                "pass_difficulty_avg": 0.0,
                "ball_speed_kmh_max": 0.0,
                "shot_speed_kmh_max": 0.0,
                "top_player_distance_m": 0.0,
                "top_player_speed_kmh": 0.0,
                "player_high_speed_actions": 0,
            },
        )

    end_ms = events[-1].match_clock_ms if as_of_ms is None else as_of_ms
    windowed = _window_events(events, end_ms)

    progressive = 0
    entries = 0
    shots = 0
    pass_attempts = 0
    pass_completed = 0
    pass_distance_total = 0.0
    pass_difficulty_total = 0.0
    pass_difficulty_count = 0
    ball_speed_max = 0.0
    shot_speed_max = 0.0
    player_distance_m: dict[str, float] = defaultdict(float)
    player_speed_kmh_max: dict[str, float] = defaultdict(float)
    high_speed_actions = 0
    previous_by_player: dict[str, Event] = {}
    measured_trajectories = any(e.type == "tracking_frame" and has_pitch_geometry(e.qualifiers.get("cv_frame", {}))
                                and end_ms - WINDOW_MS <= e.match_clock_ms <= end_ms for e in events)

    for event in windowed:
        if event.team_id != team_id:
            continue

        player_id = event.player_id
        if player_id and event.synthetic and not measured_trajectories:
            previous = previous_by_player.get(player_id)
            if previous and previous.position and event.position:
                dx = ((event.position.x - previous.position.x) / 100.0) * PITCH_LENGTH_M
                dy = ((event.position.y - previous.position.y) / 100.0) * PITCH_WIDTH_M
                segment_m = sqrt((dx * dx) + (dy * dy))
                delta_s = max(0.2, (event.match_clock_ms - previous.match_clock_ms) / 1000.0)
                speed_kmh = (segment_m / delta_s) * 3.6
                if speed_kmh <= 50:
                    player_distance_m[player_id] += segment_m
                    player_speed_kmh_max[player_id] = max(player_speed_kmh_max[player_id], speed_kmh)
                    if speed_kmh >= 24.0:
                        high_speed_actions += 1
            previous_by_player[player_id] = event

        event_distance = _event_distance_m(event)
        ball_speed = _estimate_ball_speed_kmh(event)
        ball_speed_max = max(ball_speed_max, ball_speed)

        if event.type in {"pass_completed", "carry"} and bool(event.qualifiers.get("progressive")):
            progressive += 1
        if bool(event.qualifiers.get("final_third_entry")):
            entries += 1
        if event.type == "shot":
            shots += 1
            shot_speed_max = max(shot_speed_max, ball_speed)

        if event.type in {"pass_completed", "pass_incomplete"}:
            pass_attempts += 1
            pass_difficulty_total += _difficulty_rating(event, event_distance)
            pass_difficulty_count += 1
            if event.type == "pass_completed":
                pass_completed += 1
                pass_distance_total += event_distance

    previous_track_positions: dict[str, tuple[int, dict, int | None]] = {}
    for frame in events:
        if frame.type != "tracking_frame" or not end_ms - WINDOW_MS <= frame.match_clock_ms <= end_ms:
            continue
        tracking = frame.qualifiers.get("cv_frame", {})
        if not has_pitch_geometry(tracking):
            previous_track_positions.clear()
            continue
        revision = tracking.get("calibration", {}).get("revision")
        speed = tracking.get("ball_speed_kmh")
        if isinstance(speed, (float, int)) and tracking.get("possession_team_id") == team_id:
            ball_speed_max = max(ball_speed_max, speed)
        for player in tracking.get("players", []):
            speed = player.get("speed_kmh")
            player_id = player["track_id"]
            if player.get("team_id") != team_id or player.get("inferred") or not player.get("position"):
                previous_track_positions.pop(player_id, None)
                continue
            position = player["position"]
            previous = previous_track_positions.get(player_id)
            if previous is not None:
                previous_time, previous_position, previous_revision = previous
                delta_s = (frame.match_clock_ms - previous_time) / 1000
                if previous_revision == revision and 0 < delta_s <= 0.75:
                    dx = (position["x"] - previous_position["x"]) * 1.05
                    dy = (position["y"] - previous_position["y"]) * 0.68
                    segment = sqrt(dx * dx + dy * dy)
                    if segment / delta_s * 3.6 <= 50:
                        player_distance_m[player_id] += segment
            previous_track_positions[player_id] = frame.match_clock_ms, position, revision
            if isinstance(speed, (int, float)) and isfinite(speed) and speed >= 0:
                player_speed_kmh_max[player_id] = max(player_speed_kmh_max[player_id], speed)
                if speed >= 24:
                    high_speed_actions += 1

    intensity = sum(1 for event in windowed if event.team_id == team_id) / (WINDOW_MS / 60_000)
    pass_accuracy = (pass_completed / pass_attempts * 100.0) if pass_attempts else 0.0
    pass_avg_distance = (pass_distance_total / pass_completed) if pass_completed else 0.0
    pass_difficulty_avg = (pass_difficulty_total / pass_difficulty_count) if pass_difficulty_count else 0.0
    top_player_distance = max(player_distance_m.values()) if player_distance_m else 0.0
    top_player_speed = max(player_speed_kmh_max.values()) if player_speed_kmh_max else 0.0

    return MetricSnapshot(
        match_id=match_id,
        as_of_sequence=events[-1].sequence,
        as_of_match_clock_ms=end_ms,
        team_id=team_id,
        metrics={
            "progressive_actions": progressive,
            "final_third_entries": entries,
            "shots": shots,
            "event_intensity_per_min": round(intensity, 2),
            "pass_attempts": pass_attempts,
            "pass_completed": pass_completed,
            "pass_accuracy_pct": round(pass_accuracy, 1),
            "pass_avg_distance_m": round(pass_avg_distance, 2),
            "pass_difficulty_avg": round(pass_difficulty_avg, 2),
            "ball_speed_kmh_max": round(ball_speed_max, 1),
            "shot_speed_kmh_max": round(shot_speed_max, 1),
            "top_player_distance_m": round(top_player_distance, 1),
            "top_player_speed_kmh": round(top_player_speed, 1),
            "player_high_speed_actions": high_speed_actions,
        },
    )


def detect_sustained_pressure(match_state: MatchState, events: list[Event], existing_insights: list[Insight]) -> Insight | None:
    if not events:
        return None
    end = events[-1].match_clock_ms
    windowed = _window_events(events, end)
    for team_id in sorted({event.team_id for event in windowed} & set(match_state.attack_directions)):
        if any(i.type == "sustained_pressure" and i.team_id == team_id and end - i.window_end_ms < 120_000
               for i in existing_insights):
            continue
        snapshot = compute_snapshot(match_state.match_id, events, team_id)
        progressive = int(snapshot.metrics["progressive_actions"])
        entries = int(snapshot.metrics["final_third_entries"])
        shots = int(snapshot.metrics["shots"])
        if not (progressive >= 5 and entries >= 3 and shots >= 1):
            continue
        supporting = [event.event_id for event in windowed if event.team_id == team_id]
        return Insight(
            match_id=match_state.match_id, type="sustained_pressure", priority="high", confidence=0.9,
            team_id=team_id, window_start_ms=max(0, end - WINDOW_MS), window_end_ms=end,
            title="Pressure Building",
            summary=f"{match_state.team_display_names.get(team_id, team_id)} are sustaining pressure through repeated progression, final-third entries, and a shot.",
            evidence=[
                EvidenceItem(metric=metric, value=value, unit="count", supporting_event_ids=supporting)
                for metric, value in (("progressive_actions", progressive), ("final_third_entries", entries), ("shots", shots))
            ],
        )
    return None


def detect_rhythm_shift(match_state: MatchState, events: list[Event], existing_insights: list[Insight]) -> Insight | None:
    if len(events) < 6:
        return None

    end_ms = events[-1].match_clock_ms
    current_start = max(0, end_ms - 30_000)
    previous_start = max(0, current_start - 30_000)

    current_window = [e for e in events if current_start <= e.match_clock_ms <= end_ms]
    previous_window = [e for e in events if previous_start <= e.match_clock_ms < current_start]
    if not previous_window:
        return None

    current_intensity = len(current_window)
    previous_intensity = len(previous_window)

    progression: dict[str, int] = defaultdict(int)
    for event in current_window:
        if event.type in {"pass_completed", "carry"} and event.qualifiers.get("progressive"):
            progression[event.team_id] += 1
    eligible = [
        team for team in progression if team in match_state.attack_directions
        and not any(i.type == "rhythm_shift" and i.team_id == team and end_ms - i.window_end_ms < 180_000
                    for i in existing_insights)
    ]
    team_id = max(sorted(eligible), key=lambda team: progression[team], default=None)
    if not team_id or not (current_intensity >= previous_intensity + 3 and progression[team_id] >= 4):
        return None

    supporting = [e.event_id for e in current_window]
    evidence = [
        EvidenceItem(metric="current_window_events", value=current_intensity, unit="count", supporting_event_ids=supporting),
        EvidenceItem(metric="previous_window_events", value=previous_intensity, unit="count",
                     supporting_event_ids=[e.event_id for e in previous_window]),
        EvidenceItem(metric="progressive_actions", value=progression[team_id], unit="count",
                     supporting_event_ids=[e.event_id for e in current_window if e.team_id == team_id]),
    ]

    return Insight(
        match_id=match_state.match_id,
        type="rhythm_shift",
        priority="high",
        confidence=0.86,
        team_id=team_id,
        window_start_ms=previous_start,
        window_end_ms=end_ms,
        title="Momentum Shift",
        summary=f"{match_state.team_display_names.get(team_id, team_id)} are progressing more rapidly during a higher-intensity phase.",
        evidence=evidence,
    )


def detect_player_influence(match_state: MatchState, events: list[Event], existing_insights: list[Insight]) -> Insight | None:
    if not events:
        return None
    windowed = _window_events(events, events[-1].match_clock_ms)
    for team_id in sorted(match_state.attack_directions):
        team_events = [e for e in windowed if e.team_id == team_id and e.type in {"pass_completed", "carry"}
                       and bool(e.qualifiers.get("progressive"))]
        if len(team_events) < 5:
            continue
        players = sorted({e.player_id for e in team_events if e.player_id})
        for player_id in players:
            if any(i.type == "player_influence" and i.player_id == player_id
                   and events[-1].match_clock_ms - i.window_end_ms < 180_000 for i in existing_insights):
                continue
            player_events = [e for e in team_events if e.player_id == player_id]
            ratio = len(player_events) / len(team_events)
            shot = any(e.type == "shot" and e.team_id == team_id and e.qualifiers.get("created_by") == player_id
                       for e in windowed)
            if ratio < 0.6 or not shot:
                continue
            supporting = [e.event_id for e in windowed if e.team_id == team_id
                          and (e.player_id == player_id or e.qualifiers.get("created_by") == player_id)]
            return Insight(
                match_id=match_state.match_id, type="player_influence", priority="high", confidence=0.88,
                team_id=team_id, player_id=player_id,
                window_start_ms=max(0, events[-1].match_clock_ms - WINDOW_MS), window_end_ms=events[-1].match_clock_ms,
                title="Player Influence Rising",
                summary=f"Player {player_id} contributed most of {match_state.team_display_names.get(team_id, team_id)}'s progressive actions and helped create a shot.",
                evidence=[
                    EvidenceItem(metric=metric, value=value, unit=unit, supporting_event_ids=supporting)
                    for metric, value, unit in (
                        ("player_progressive_actions", len(player_events), "count"),
                        ("team_progressive_actions", len(team_events), "count"),
                        ("involvement_ratio", round(ratio, 2), "ratio"),
                    )
                ],
            )
    return None


def detect_player_speed_burst(match_state: MatchState, events: list[Event], existing_insights: list[Insight]) -> Insight | None:
    if not events:
        return None

    windowed = _window_events(events, events[-1].match_clock_ms)
    candidate_insights: list[tuple[Insight, float, int]] = []

    teams = ({event.team_id for event in events} | set(match_state.metrics_by_team)) & set(match_state.team_display_names)
    for team_id in sorted(teams):
        if any(i.type == "player_speed_burst" and i.team_id == team_id for i in existing_insights):
            continue

        current = match_state.metrics_by_team.get(team_id) if (
            match_state.last_sequence == events[-1].sequence and match_state.match_clock_ms == events[-1].match_clock_ms
        ) else None
        metrics = current if current is not None else compute_snapshot(match_state.match_id, events, team_id).metrics
        top_speed = float(metrics.get("top_player_speed_kmh", 0.0))
        high_speed_actions = int(metrics.get("player_high_speed_actions", 0))
        if not (top_speed >= 24.0 and high_speed_actions >= 1):
            continue

        supporting = [e.event_id for e in windowed if e.team_id == team_id]
        previous_frames: dict[str, Event] = {}
        for frame in events:
            if frame.type != "tracking_frame" or not events[-1].match_clock_ms - WINDOW_MS <= frame.match_clock_ms <= events[-1].match_clock_ms:
                continue
            tracking = frame.qualifiers.get("cv_frame", {})
            if not has_pitch_geometry(tracking):
                previous_frames.clear()
                continue
            for player in tracking.get("players", []):
                if player.get("team_id") != team_id or player.get("inferred"):
                    continue
                previous = previous_frames.get(player["track_id"])
                speed = player.get("speed_kmh")
                if isinstance(speed, (int, float)) and speed >= 24:
                    supporting.append(frame.event_id)
                    if (previous and frame.match_clock_ms - previous.match_clock_ms <= 750
                            and previous.qualifiers["cv_frame"].get("calibration", {}).get("revision")
                            == tracking.get("calibration", {}).get("revision")):
                        supporting.append(previous.event_id)
                previous_frames[player["track_id"]] = frame
        supporting = list(dict.fromkeys(supporting))
        evidence = [
            EvidenceItem(metric="top_player_speed_kmh", value=round(top_speed, 1), unit="km/h", supporting_event_ids=supporting),
            EvidenceItem(metric="player_high_speed_actions", value=high_speed_actions, unit="count", supporting_event_ids=supporting),
        ]

        candidate_insights.append(
            (
                Insight(
                    match_id=match_state.match_id,
                    type="player_speed_burst",
                    priority="medium",
                    confidence=0.82,
                    team_id=team_id,
                    window_start_ms=max(0, events[-1].match_clock_ms - WINDOW_MS),
                    window_end_ms=events[-1].match_clock_ms,
                    title="Speed Burst Detected",
                    summary="High-speed player movement samples were identified in this evidence window.",
                    evidence=evidence,
                ),
                top_speed,
                high_speed_actions,
            )
        )

    if not candidate_insights:
        return None

    candidate_insights.sort(key=lambda item: (item[1], item[2]), reverse=True)
    return candidate_insights[0][0]


def detect_turning_point(match_state: MatchState, events: list[Event], existing_insights: list[Insight]) -> Insight | None:
    if len(events) < 10:
        return None

    end_ms = events[-1].match_clock_ms
    current_start = max(0, end_ms - 20_000)
    previous_start = max(0, current_start - 20_000)

    current_window = [e for e in events if current_start <= e.match_clock_ms <= end_ms]
    previous_window = [e for e in events if previous_start <= e.match_clock_ms < current_start]
    if len(previous_window) < 4:
        return None

    current_teams = {e.team_id for e in current_window if e.team_id}
    previous_teams = {e.team_id for e in previous_window if e.team_id}
    if len(current_teams) < 2 and len(previous_teams) < 2:
        return None

    previous_possession = previous_window[-1].team_id
    current_possession = current_window[-1].team_id
    possession_swung = previous_possession != current_possession

    previous_progressive = sum(
        1 for e in previous_window if e.type in {"pass_completed", "carry"} and bool(e.qualifiers.get("progressive"))
    )
    current_progressive = sum(
        1 for e in current_window if e.type in {"pass_completed", "carry"} and bool(e.qualifiers.get("progressive"))
    )

    rhythm_spike = len(current_window) >= len(previous_window) + 3
    threat_spike = current_progressive >= previous_progressive + 2
    if not ((possession_swung and rhythm_spike) or threat_spike):
        return None

    if any(
        i.type == "turning_point" and abs(i.window_end_ms - end_ms) <= 25_000
        for i in existing_insights
    ):
        return None

    dominant_team = current_possession or current_window[-1].team_id
    current_supporting = [e.event_id for e in current_window]
    previous_supporting = [e.event_id for e in previous_window]
    evidence = [
        EvidenceItem(metric="current_window_events", value=len(current_window), unit="count", supporting_event_ids=current_supporting),
        EvidenceItem(metric="previous_window_events", value=len(previous_window), unit="count", supporting_event_ids=previous_supporting),
        EvidenceItem(metric="current_progressive_actions", value=current_progressive, unit="count", supporting_event_ids=current_supporting),
        EvidenceItem(metric="previous_progressive_actions", value=previous_progressive, unit="count", supporting_event_ids=previous_supporting),
    ]

    reason = "control_to_chaos" if possession_swung else "tempo_escalation"
    return Insight(
        match_id=match_state.match_id,
        type="turning_point",
        priority="high",
        confidence=0.84,
        team_id=dominant_team,
        window_start_ms=previous_start,
        window_end_ms=end_ms,
        title="Turning Point",
        summary=(
            "Match state has flipped into a high-impact phase with a clear shift in control and tempo."
            if reason == "control_to_chaos"
            else "Match tempo has escalated sharply, creating a decisive moment in game rhythm."
        ),
        evidence=evidence,
    )


def detect_insights(match_state: MatchState, events: list[Event], existing_insights: list[Insight]) -> list[Insight]:
    tactical = detect_tactical_insights(match_state, events, existing_insights)
    if not events or events[-1].type in {"tracking_frame", "motion_sample"}:
        speed = detect_player_speed_burst(match_state, events, existing_insights) if events else None
        if speed:
            tactical.append(speed)
        return tactical
    full_history = events
    events = [event for event in events if event.type not in {"tracking_frame", "motion_sample"}]
    candidates = [
        detect_sustained_pressure(match_state, events, existing_insights),
        detect_rhythm_shift(match_state, events, existing_insights),
        detect_player_influence(match_state, events, existing_insights),
        detect_player_speed_burst(match_state, full_history, existing_insights),
        detect_turning_point(match_state, events, existing_insights),
    ]
    return [c for c in candidates if c is not None] + tactical


def reduce_match_state(
    match_state: MatchState, event: Event, all_events: list[Event], value_model: ValueModel | None = None
) -> MatchState:
    match_state.match_clock_ms = event.match_clock_ms
    match_state.last_sequence = event.sequence
    if event.type in {"tracking_frame", "motion_sample"}:
        match_state.tracking_sample_count += 1
    else:
        match_state.action_count += 1

    if event.type in {"recovery", "pass_completed", "carry", "shot"}:
        match_state.possession_team_id = event.team_id

    if event.type == "goal" and event.team_id in match_state.score:
        match_state.score[event.team_id] = match_state.score.get(event.team_id, 0) + 1

    teams = {item.team_id for item in all_events} & set(match_state.team_display_names)
    teams |= {player.get("team_id") for player in event.qualifiers.get("cv_frame", {}).get("players", [])
              if player.get("team_id") in match_state.team_display_names}
    match_state.metrics_by_team = {
        team: compute_snapshot(match_state.match_id, all_events, team, event.match_clock_ms).metrics
        for team in sorted(teams)
    }
    if event.type == "tracking_frame":
        match_state.cv_live = deepcopy(event.qualifiers["cv_frame"])
        match_state.cv_live.update(_control_fields(match_state.metrics_by_team))
        match_state.possession_team_id = match_state.cv_live.get("possession_team_id")
    elif event.type == "motion_sample" or match_state.cv_live.get("tracking_source", "event_stream_v1") == "event_stream_v1":
        match_state.cv_live = _derive_cv_live(match_state, event, all_events)
    match_state.tactical_state = tactical_snapshot(match_state, all_events, value_model)

    return match_state
