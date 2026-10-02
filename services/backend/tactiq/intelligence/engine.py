from __future__ import annotations

from collections import defaultdict

from tactiq.domain.models import Event, EvidenceItem, Insight, MatchState, MetricSnapshot


WINDOW_MS = 90_000


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
            },
        )

    end_ms = events[-1].match_clock_ms
    windowed = _window_events(events, end_ms)

    progressive = 0
    entries = 0
    shots = 0

    for event in windowed:
        if event.team_id != team_id:
            continue
        if event.type in {"pass_completed", "carry"} and bool(event.qualifiers.get("progressive")):
            progressive += 1
        if bool(event.qualifiers.get("final_third_entry")):
            entries += 1
        if event.type == "shot":
            shots += 1

    intensity = (len(windowed) / (WINDOW_MS / 60_000)) if WINDOW_MS else 0.0

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


def detect_insights(match_state: MatchState, events: list[Event], existing_insights: list[Insight]) -> list[Insight]:
    candidates = [
        detect_sustained_pressure(match_state, events, existing_insights),
        detect_rhythm_shift(match_state, events, existing_insights),
        detect_player_influence(match_state, events, existing_insights),
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
