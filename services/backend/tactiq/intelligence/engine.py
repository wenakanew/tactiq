from __future__ import annotations

from collections import defaultdict
from math import sqrt

from tactiq.domain.models import Event, EvidenceItem, Insight, MatchState, MetricSnapshot


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
    return [e for e in events if start <= e.match_clock_ms <= end_ms]


def compute_snapshot(match_id: str, events: list[Event], team_id: str) -> MetricSnapshot:
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

    end_ms = events[-1].match_clock_ms
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

    for event in windowed:
        if event.team_id != team_id:
            continue

        player_id = event.player_id
        if player_id:
            previous = previous_by_player.get(player_id)
            if previous and previous.position and event.position:
                dx = ((event.position.x - previous.position.x) / 100.0) * PITCH_LENGTH_M
                dy = ((event.position.y - previous.position.y) / 100.0) * PITCH_WIDTH_M
                segment_m = sqrt((dx * dx) + (dy * dy))
                delta_s = max(0.2, (event.match_clock_ms - previous.match_clock_ms) / 1000.0)
                speed_kmh = (segment_m / delta_s) * 3.6
                player_distance_m[player_id] += segment_m
                player_speed_kmh_max[player_id] = max(player_speed_kmh_max[player_id], speed_kmh)
                if speed_kmh >= 24.0:
                    high_speed_actions += 1
            previous_by_player[player_id] = event

        event_distance = _event_distance_m(event)
        if event_distance > 0:
            delta_s = max(0.2, float(event.qualifiers.get("travel_ms", 600)) / 1000.0)
            estimated_speed = (event_distance / delta_s) * 3.6
            ball_speed = float(event.qualifiers.get("ball_speed_kmh", estimated_speed))
            ball_speed_max = max(ball_speed_max, ball_speed)

        if event.type in {"pass_completed", "carry"} and bool(event.qualifiers.get("progressive")):
            progressive += 1
        if bool(event.qualifiers.get("final_third_entry")):
            entries += 1
        if event.type == "shot":
            shots += 1
            shot_speed = float(event.qualifiers.get("shot_speed_kmh", ball_speed_max))
            shot_speed_max = max(shot_speed_max, shot_speed)

        if event.type in {"pass_completed", "pass_incomplete"}:
            pass_attempts += 1
            pass_difficulty_total += _difficulty_rating(event, event_distance)
            pass_difficulty_count += 1
            if event.type == "pass_completed":
                pass_completed += 1
                pass_distance_total += event_distance

    intensity = (len(windowed) / (WINDOW_MS / 60_000)) if WINDOW_MS else 0.0
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

    if any(i.type == "sustained_pressure" for i in existing_insights):
        return None

    team_id = "team_a"
    snapshot = compute_snapshot(match_state.match_id, events, team_id)

    progressive = int(snapshot.metrics["progressive_actions"])
    entries = int(snapshot.metrics["final_third_entries"])
    shots = int(snapshot.metrics["shots"])

    if not (progressive >= 5 and entries >= 3 and shots >= 1):
        return None

    windowed = _window_events(events, events[-1].match_clock_ms)
    supporting = [e.event_id for e in windowed if e.team_id == team_id]

    evidence = [
        EvidenceItem(metric="progressive_actions", value=progressive, unit="count", supporting_event_ids=supporting),
        EvidenceItem(metric="final_third_entries", value=entries, unit="count", supporting_event_ids=supporting),
        EvidenceItem(metric="shots", value=shots, unit="count", supporting_event_ids=supporting),
    ]

    return Insight(
        match_id=match_state.match_id,
        type="sustained_pressure",
        priority="high",
        confidence=0.9,
        team_id=team_id,
        window_start_ms=max(0, events[-1].match_clock_ms - WINDOW_MS),
        window_end_ms=events[-1].match_clock_ms,
        title="Pressure Building",
        summary="Team A are beginning to pin Team B back with repeated progressive actions and final-third entries.",
        evidence=evidence,
    )


def detect_rhythm_shift(match_state: MatchState, events: list[Event], existing_insights: list[Insight]) -> Insight | None:
    if len(events) < 6 or any(i.type == "rhythm_shift" for i in existing_insights):
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

    current_team_b_progressive = sum(
        1
        for e in current_window
        if e.team_id == "team_b" and e.type in {"pass_completed", "carry"} and bool(e.qualifiers.get("progressive"))
    )

    if not (current_intensity >= previous_intensity + 3 and current_team_b_progressive >= 4):
        return None

    supporting = [e.event_id for e in current_window if e.team_id == "team_b"]
    evidence = [
        EvidenceItem(metric="current_window_events", value=current_intensity, unit="count", supporting_event_ids=supporting),
        EvidenceItem(metric="previous_window_events", value=previous_intensity, unit="count", supporting_event_ids=supporting),
        EvidenceItem(metric="team_b_progressive_actions", value=current_team_b_progressive, unit="count", supporting_event_ids=supporting),
    ]

    return Insight(
        match_id=match_state.match_id,
        type="rhythm_shift",
        priority="high",
        confidence=0.86,
        team_id="team_b",
        window_start_ms=current_start,
        window_end_ms=end_ms,
        title="Momentum Shift",
        summary="Team B have raised the tempo and are now driving the match with rapid progressive actions.",
        evidence=evidence,
    )


def detect_player_influence(match_state: MatchState, events: list[Event], existing_insights: list[Insight]) -> Insight | None:
    if not events or any(i.type == "player_influence" for i in existing_insights):
        return None

    team_id = "team_a"
    player_id = "a_10"
    windowed = _window_events(events, events[-1].match_clock_ms)

    team_progressive_events = [
        e
        for e in windowed
        if e.team_id == team_id and e.type in {"pass_completed", "carry"} and bool(e.qualifiers.get("progressive"))
    ]
    if len(team_progressive_events) < 5:
        return None

    player_progressive = [e for e in team_progressive_events if e.player_id == player_id]
    involvement_ratio = len(player_progressive) / len(team_progressive_events)

    shot_created = any(e.type == "shot" and e.qualifiers.get("created_by") == player_id for e in windowed)
    if not (involvement_ratio >= 0.6 and shot_created):
        return None

    supporting = [e.event_id for e in windowed if e.team_id == team_id and (e.player_id == player_id or e.type == "shot")]
    evidence = [
        EvidenceItem(metric="player_progressive_actions", value=len(player_progressive), unit="count", supporting_event_ids=supporting),
        EvidenceItem(metric="team_progressive_actions", value=len(team_progressive_events), unit="count", supporting_event_ids=supporting),
        EvidenceItem(metric="involvement_ratio", value=round(involvement_ratio, 2), unit="ratio", supporting_event_ids=supporting),
    ]

    return Insight(
        match_id=match_state.match_id,
        type="player_influence",
        priority="high",
        confidence=0.88,
        team_id=team_id,
        window_start_ms=max(0, events[-1].match_clock_ms - WINDOW_MS),
        window_end_ms=events[-1].match_clock_ms,
        title="Player Influence Rising",
        summary="Player a_10 is driving Team A progression and helped create the latest shot.",
        evidence=evidence,
    )


def detect_player_speed_burst(match_state: MatchState, events: list[Event], existing_insights: list[Insight]) -> Insight | None:
    if not events:
        return None

    windowed = _window_events(events, events[-1].match_clock_ms)
    candidate_insights: list[tuple[Insight, float, int]] = []

    for team_id in {event.team_id for event in events}:
        if any(i.type == "player_speed_burst" and i.team_id == team_id for i in existing_insights):
            continue

        snapshot = compute_snapshot(match_state.match_id, events, team_id)
        top_speed = float(snapshot.metrics.get("top_player_speed_kmh", 0.0))
        high_speed_actions = int(snapshot.metrics.get("player_high_speed_actions", 0))
        if not (top_speed >= 24.0 and high_speed_actions >= 1):
            continue

        supporting = [e.event_id for e in windowed if e.team_id == team_id]
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
                    summary="A rapid acceleration phase has emerged, increasing attacking threat and transition risk.",
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
        window_start_ms=current_start,
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
    candidates = [
        detect_sustained_pressure(match_state, events, existing_insights),
        detect_rhythm_shift(match_state, events, existing_insights),
        detect_player_influence(match_state, events, existing_insights),
        detect_player_speed_burst(match_state, events, existing_insights),
        detect_turning_point(match_state, events, existing_insights),
    ]
    return [c for c in candidates if c is not None]


def reduce_match_state(match_state: MatchState, event: Event, all_events: list[Event]) -> MatchState:
    match_state.match_clock_ms = event.match_clock_ms
    match_state.last_sequence = event.sequence

    if event.type in {"recovery", "pass_completed", "carry", "shot"}:
        match_state.possession_team_id = event.team_id

    if event.type == "goal":
        match_state.score[event.team_id] = match_state.score.get(event.team_id, 0) + 1

    grouped: dict[str, list[Event]] = defaultdict(list)
    for item in all_events:
        grouped[item.team_id].append(item)

    match_state.metrics_by_team = {
        team: compute_snapshot(match_state.match_id, team_events, team).metrics
        for team, team_events in grouped.items()
    }

    return match_state
