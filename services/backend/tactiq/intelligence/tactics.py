from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from functools import lru_cache
from math import hypot
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import numpy as np

from tactiq.domain.models import Event, EvidenceItem, Insight, MatchState
from tactiq.intelligence.value import ValueModel
from tactiq.intelligence.geometry import has_pitch_geometry

CONTROL_EVENTS = {"recovery", "interception", "pass_completed", "carry", "shot", "goal", "tackle"}


def possession_chains(
    events: list[Event], directions: dict[str, int], value_model: ValueModel | None, team_ids: set[str] | None = None
) -> list[dict[str, Any]]:
    chains: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for event in events:
        if event.type == "tracking_frame":
            continue
        if current and (event.match_clock_ms - current["end_ms"] > 15_000 or event.type in {"period_end", "period_start"}):
            current["terminal_event"] = "gap" if event.type not in {"period_end", "period_start"} else event.type
            current = None
        if event.type in {"turnover", "pass_incomplete"}:
            if current and event.team_id == current["team_id"]:
                current["event_ids"].append(event.event_id)
                current["end_ms"] = event.match_clock_ms
                current["terminal_event"] = event.type
                current = None
            continue
        if event.type not in CONTROL_EVENTS or event.team_id not in (team_ids if team_ids is not None else directions):
            continue
        if current is None or current["team_id"] != event.team_id:
            if current:
                current["terminal_event"] = "possession_change"
            current = {
                "chain_id": str(uuid5(NAMESPACE_URL, f"{event.match_id}:{event.sequence}")),
                "team_id": event.team_id,
                "start_ms": event.match_clock_ms, "end_ms": event.match_clock_ms,
                "event_ids": [], "actions": 0, "completed_passes": 0,
                "progression_m": 0.0 if event.team_id in directions else None,
                "xt_gain_estimate": 0.0 if value_model and event.team_id in directions else None,
                "xg_estimate": 0.0 if value_model and event.team_id in directions else None,
                "started_with_recovery": event.type in {"recovery", "interception", "tackle"},
                "terminal_event": None,
                "last_ball_x": None,
            }
            chains.append(current)
        current["event_ids"].append(event.event_id)
        current["end_ms"] = event.match_clock_ms
        current["actions"] += 1
        direction = directions.get(event.team_id)
        if event.type == "pass_completed":
            current["completed_passes"] += 1
        if direction is not None and event.position and event.end_position and event.type in {"pass_completed", "carry"}:
            current["progression_m"] += (event.end_position.x - event.position.x) * 1.05 * direction
            if value_model:
                current["xt_gain_estimate"] += (
                    value_model.threat(event.end_position, direction) - value_model.threat(event.position, direction)
                )
        if event.type == "shot" and event.position and value_model and direction is not None:
            current["xg_estimate"] += value_model.shot_probability(event.position, direction)
        point = event.end_position or event.position
        if point and direction is not None:
            current["last_ball_x"] = point.x if direction == 1 else 100 - point.x
        duration = current["end_ms"] - current["start_ms"]
        if direction is None:
            current["phase"] = "unknown"
        elif current["started_with_recovery"] and duration <= 10_000 and current["progression_m"] >= 15:
            current["phase"] = "transition"
        elif current["progression_m"] <= -10:
            current["phase"] = "defensive_reset"
        elif duration >= 10_000 and (current["last_ball_x"] or 0) >= 60:
            current["phase"] = "settled_attack"
        else:
            current["phase"] = "build_up"
        if event.type in {"shot", "goal"}:
            current["terminal_event"] = event.type
            current = None
    for chain in chains:
        for key in ("progression_m", "xt_gain_estimate", "xg_estimate"):
            if chain[key] is not None:
                chain[key] = round(chain[key], 5)
    return chains


def _shape(players: list[dict], direction: int | None) -> dict[str, Any] | None:
    observed = [p for p in players if p.get("position") and not p.get("inferred")]
    if len(observed) < 7:
        return None
    points = tuple((p["position"]["x"], p["position"]["y"]) for p in observed)
    return deepcopy(_shape_geometry(points, direction))


@lru_cache(maxsize=2048)
def _shape_geometry(points: tuple[tuple[float, float], ...], direction: int | None) -> dict[str, Any]:
    coordinates = np.array([[x * 1.05, y * .68] for x, y in points], dtype=float)
    x = coordinates[:, 0] if direction == 1 else 105 - coordinates[:, 0]
    ordered = np.sort(x)
    # Only remove a goalkeeper candidate if the observed depth separates it.
    keeper_separated = direction is not None and len(ordered) >= 10 and ordered[1] - ordered[0] >= 7
    outfield = ordered[1:] if keeper_separated else ordered
    formation = None
    if keeper_separated and len(outfield) == 10:
        centers = np.quantile(outfield, [0.15, 0.5, 0.85])
        labels = np.zeros(len(outfield), dtype=int)
        for _ in range(12):
            labels = np.argmin(np.abs(outfield[:, None] - centers[None]), axis=1)
            if any(np.sum(labels == i) == 0 for i in range(3)):
                break
            centers = np.array([np.mean(outfield[labels == i]) for i in range(3)])
        if all(np.sum(labels == i) > 0 for i in range(3)) and min(np.diff(np.sort(centers))) >= 5:
            formation = "-".join(str(int(np.sum(labels == i))) for i in np.argsort(centers))
    return {
        "observed_players": len(points),
        "centroid": {"x": round(float(np.mean(coordinates[:, 0])) / 1.05, 2),
                     "y": round(float(np.mean(coordinates[:, 1])) / 0.68, 2)},
        "width_m": round(float(np.ptp(coordinates[:, 1])), 2),
        "depth_m": round(float(np.ptp(coordinates[:, 0])), 2),
        "formation_estimate": formation,
        "formation_status": "single_frame_estimate" if formation else "insufficient_coverage",
    }


def tactical_snapshot(
    state: MatchState, events: list[Event], value_model: ValueModel | None
) -> dict[str, Any]:
    chains = possession_chains(events, state.attack_directions, value_model, set(state.team_display_names))
    totals: dict[str, dict] = defaultdict(lambda: {"chains": 0, "xt_gain_estimate": None, "xg_estimate": None})
    for chain in chains:
        total = totals[chain["team_id"]]
        total["chains"] += 1
        for metric in ("xt_gain_estimate", "xg_estimate"):
            if chain[metric] is not None:
                total[metric] = round((total[metric] or 0) + chain[metric], 5)
    cv = state.cv_live
    shapes = {}
    lanes = {}
    if has_pitch_geometry(cv):
        players = cv.get("players", [])
        recent_frames = [event for event in events[-80:] if event.type == "tracking_frame"
                         and state.match_clock_ms - 2500 <= event.match_clock_ms <= state.match_clock_ms
                         and has_pitch_geometry(event.qualifiers.get("cv_frame", {}))]
        for team in state.team_display_names:
            own = [p for p in players if p.get("team_id") == team and p.get("position") and not p.get("inferred")]
            opponents = [p for p in players if p.get("team_id") in state.team_display_names and p.get("team_id") != team
                         and p.get("position") and not p.get("inferred")]
            shape = _shape(own, state.attack_directions.get(team))
            if shape:
                history = []
                for sample in recent_frames:
                    positions = [player for player in sample.qualifiers["cv_frame"].get("players", [])
                                 if player.get("team_id") == team]
                    measured = _shape(positions, state.attack_directions.get(team))
                    if measured:
                        history.append((sample, measured))
                if history:
                    shape["width_m"] = round(float(np.median([item["width_m"] for _, item in history])), 2)
                    shape["depth_m"] = round(float(np.median([item["depth_m"] for _, item in history])), 2)
                candidate = shape["formation_estimate"]
                agreeing = [(sample, item) for sample, item in history if candidate and item["formation_estimate"] == candidate]
                stable = len(agreeing) >= 3 and agreeing[-1][0].match_clock_ms - agreeing[0][0].match_clock_ms >= 1000
                shape["formation_estimate"] = candidate if stable else None
                shape["formation_status"] = ("attack_direction_unknown" if team not in state.attack_directions
                                              else "stable_observed_estimate" if stable else "insufficient_temporal_support")
                shape["supporting_event_ids"] = [sample.event_id for sample, _ in history]
                shape["possession_phase"] = "in_possession" if state.possession_team_id == team else "out_of_possession"
                shapes[team] = shape
            lanes[team] = [
                {"lane": name,
                 "attackers": sum(1 for p in own if low <= p["position"]["y"] < high),
                 "defenders": sum(1 for p in opponents if low <= p["position"]["y"] < high)}
                for name, low, high in (("upper", 0, 33.34), ("central", 33.34, 66.67), ("lower", 66.67, 100.01))
            ]
    return {
        "version": "tactics-v1", "phase": chains[-1].get("phase", "unknown") if chains else "unknown",
        "current_chain": chains[-1] if chains and chains[-1]["terminal_event"] is None else None,
        "recent_chains": chains[-12:], "chain_count": len(chains), "team_totals": dict(totals),
        "team_shapes": shapes, "lanes": lanes,
        "geometry_status": "simulation" if cv.get("geometry_source") == "simulation" else "calibrated" if cv.get("calibrated") else "unavailable",
        "value_model": {
            "version": value_model.version if value_model else None,
            "status": "validated" if value_model and value_model.validated else "reference_unvalidated" if value_model else "unavailable",
            "training_source": value_model.training_source if value_model else None,
            "validation_reference": value_model.validation_reference if value_model else None,
        },
    }


def detect_tactical_insights(state: MatchState, events: list[Event], existing: list[Insight]) -> list[Insight]:
    if not events:
        return []
    output = []
    end = events[-1].match_clock_ms
    current = state.tactical_state

    def eligible(kind: str, team: str) -> bool:
        return not any(i.type == kind and i.team_id == team and end - i.window_end_ms < 30_000 for i in existing)

    chains = current.get("recent_chains", [])
    for chain in chains[-1:]:
        team = chain["team_id"]
        if (chain.get("phase") == "transition" and chain["actions"] >= 3 and chain["progression_m"] >= 25
                and eligible("transition_attack", team)):
            output.append(Insight(
                match_id=state.match_id, type="transition_attack", team_id=team, priority="high", confidence=0.8,
                window_start_ms=chain["start_ms"], window_end_ms=chain["end_ms"],
                title="Fast Transition", summary=f"{state.team_display_names.get(team, team)} advanced rapidly after regaining possession.",
                evidence=[
                    EvidenceItem(metric="forward_progression_m", value=chain["progression_m"], unit="m", supporting_event_ids=chain["event_ids"]),
                    EvidenceItem(metric="chain_actions", value=chain["actions"], unit="count", supporting_event_ids=chain["event_ids"]),
                    EvidenceItem(metric="chain_duration_ms", value=chain["end_ms"] - chain["start_ms"], unit="ms", supporting_event_ids=chain["event_ids"]),
                ],
            ))
    window = [event for event in events if event.type == "tracking_frame" and end - 2000 <= event.match_clock_ms <= end]
    if len(window) < 3:
        return output
    team = state.possession_team_id
    if team not in state.team_display_names:
        return output
    counts = Counter()
    samples = defaultdict(list)
    for event in window:
        frame = event.qualifiers.get("cv_frame", {})
        ball = frame.get("ball_position")
        if not has_pitch_geometry(frame) or not ball or frame.get("possession_team_id") != team:
            continue
        coverage = Counter(p.get("team_id") for p in frame.get("players", [])
                           if p.get("position") and not p.get("inferred"))
        if coverage[team] < 5 or sum(value for key, value in coverage.items()
                                    if key in state.team_display_names and key != team) < 5:
            continue
        lane = "upper" if ball["y"] < 33.34 else "lower" if ball["y"] >= 66.67 else "central"
        attackers = defenders = 0
        for player in frame.get("players", []):
            point = player.get("position")
            if not point or player.get("inferred"):
                continue
            if hypot((point["x"] - ball["x"]) * 1.05, (point["y"] - ball["y"]) * 0.68) > 15:
                continue
            if player.get("team_id") == team:
                attackers += 1
            elif player.get("team_id") in state.team_display_names:
                defenders += 1
        if attackers >= 3 and attackers - defenders >= 2:
            counts[lane] += 1
            samples[lane].append((event, attackers, defenders))
    if counts:
        lane, count = counts.most_common(1)[0]
        supporting = samples[lane]
        if count >= 3 and supporting[-1][0].match_clock_ms - supporting[0][0].match_clock_ms >= 500 and eligible("local_overload", team):
            output.append(Insight(
                match_id=state.match_id, type="local_overload", team_id=team, priority="medium", confidence=0.75,
                window_start_ms=supporting[0][0].match_clock_ms, window_end_ms=end, title="Local Numerical Overload",
                summary=f"{state.team_display_names.get(team, team)} have more observed players near the ball in the {lane} channel.",
                evidence=[
                    EvidenceItem(metric="nearby_attackers", value=supporting[-1][1], unit="count",
                                 supporting_event_ids=[sample[0].event_id for sample in supporting]),
                    EvidenceItem(metric="nearby_defenders", value=supporting[-1][2], unit="count",
                                 supporting_event_ids=[sample[0].event_id for sample in supporting]),
                ],
            ))
    # A pressure convergence is only called a trap after a subsequent turnover.
    recoveries = [event for event in events if event.type == "recovery" and end - 2000 <= event.match_clock_ms <= end]
    for recovery in recoveries[-1:]:
        pressing_team = recovery.team_id
        if not eligible("pressing_trap", pressing_team):
            continue
        supporters = []
        for event in window:
            if event.match_clock_ms >= recovery.match_clock_ms:
                continue
            frame = event.qualifiers.get("cv_frame", {})
            ball = frame.get("ball_position")
            if not has_pitch_geometry(frame) or not ball or frame.get("possession_team_id") in {None, pressing_team}:
                continue
            nearby = sum(1 for p in frame.get("players", [])
                         if p.get("team_id") == pressing_team and p.get("position") and not p.get("inferred")
                         and hypot((p["position"]["x"] - ball["x"]) * 1.05, (p["position"]["y"] - ball["y"]) * 0.68) <= 8)
            if nearby >= 3:
                supporters.append(event)
        if len(supporters) >= 2:
            ids = [event.event_id for event in supporters] + [recovery.event_id]
            output.append(Insight(
                match_id=state.match_id, type="pressing_trap", team_id=pressing_team, priority="high", confidence=0.76,
                window_start_ms=supporters[0].match_clock_ms, window_end_ms=recovery.match_clock_ms,
                title="Pressing Convergence", summary="Several observed defenders converged near the ball before possession changed.",
                evidence=[
                    EvidenceItem(metric="convergence_samples", value=len(supporters), unit="count", supporting_event_ids=ids),
                    EvidenceItem(metric="subsequent_recoveries", value=1, unit="count", supporting_event_ids=[recovery.event_id]),
                ],
            ))
    return output
