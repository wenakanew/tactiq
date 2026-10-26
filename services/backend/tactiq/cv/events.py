from __future__ import annotations

from collections import defaultdict
from math import hypot

from tactiq.cv.types import CvIngestResult, CvTrackPoint
from tactiq.domain.models import Event, Position


def tracks_to_events(result: CvIngestResult, match_id: str, attack_directions: dict[str, int] | None = None) -> list[Event]:
    grouped: dict[int, list[CvTrackPoint]] = defaultdict(list)
    for point in result.tracks:
        grouped[point.timestamp_ms].append(point)
    calibrations = {item.timestamp_ms: item for item in result.calibration_history}
    events: list[Event] = []
    previous_owner: tuple[str, str, int, tuple[float, float]] | None = None
    candidate_owner: str | None = None
    owner_since = 0
    previous_scene = 0
    for timestamp in result.sampled_timestamps_ms or sorted(grouped):
        if previous_owner and timestamp - previous_owner[2] > 4000:
            previous_owner = None
        points = grouped[timestamp]
        scene = points[0].scene_id if points else sum(change <= timestamp for change in result.scene_changes)
        if scene != previous_scene:
            previous_owner = None
            candidate_owner = None
            owner_since = timestamp
        previous_scene = scene
        players = [point for point in points if point.object_class == "player"]
        ball = next((point for point in points if point.object_class == "ball"), None)
        calibrated = calibrations.get(timestamp, result.calibration).status == "calibrated"
        if not calibrated:
            previous_owner = None
        owner: CvTrackPoint | None = None
        if ball and ball.pitch_position and not ball.inferred:
            eligible = [point for point in players if point.pitch_position and point.team_id and not point.inferred]
            if eligible:
                owner = min(eligible, key=lambda point: _distance(point.pitch_position, ball.pitch_position))
                if _distance(owner.pitch_position, ball.pitch_position) > 2.5:
                    owner = None
        owner_id = owner.track_id if owner else None
        if owner_id != candidate_owner:
            candidate_owner, owner_since = owner_id, timestamp
        confirmed = owner is not None and timestamp - owner_since >= 250
        team = owner.team_id if confirmed and owner else None
        ball_position = _position(ball.pitch_position) if ball and ball.pitch_position else None
        payload = {
            "tracking_source": result.extractor,
            "detection_source": result.detection_source,
            "model_id": result.model_id,
            "coordinate_space": "pitch_percent" if calibrated else "image_percent",
            "calibrated": calibrated,
            "geometry_source": "camera" if calibrated else "image",
            "frame_width": result.width, "frame_height": result.height,
            "calibration": calibrations.get(timestamp, result.calibration).model_dump(),
            "players": [_entity(point) for point in players],
            "ball": _entity(ball) if ball else None,
            "ball_position": ball_position.model_dump() if ball_position else None,
            "raw_ball_position": None,
            "smoothed_ball_position": ball_position.model_dump() if ball_position else None,
            "ball_speed_kmh": ball.speed_kmh if ball else None,
            "top_player_speed_kmh": max((point.speed_kmh for point in players if point.speed_kmh is not None), default=None),
            "tracked_players": len([point for point in players if not point.inferred]),
            "possession_team_id": team,
            "possession_player_id": owner_id if confirmed else None,
            "possession_transition_confidence": min(owner.confidence, owner.team_confidence) if confirmed and owner else 0,
            "updated_at_ms": timestamp,
            "scene_id": scene,
        }
        events.append(Event(
            match_id=match_id, sequence=len(events) + 1, match_clock_ms=timestamp,
            type="tracking_frame", team_id=team or "unknown",
            player_id=owner_id if confirmed else None, position=ball_position,
            qualifiers={"cv_frame": payload, "source": result.extractor},
            synthetic=result.detection_source != "model",
        ))
        if confirmed and owner and ball and ball.pitch_position and team:
            current = owner.track_id, team, timestamp, ball.pitch_position
            if previous_owner and previous_owner[0] != owner.track_id:
                previous_player, previous_team, previous_time, previous_position = previous_owner
                elapsed = timestamp - previous_time
                kind = "recovery" if previous_team != team else "pass_completed"
                if kind == "recovery" or (elapsed <= 4000 and _distance(previous_position, ball.pitch_position) >= 3):
                    start = _position(previous_position)
                    end = _position(ball.pitch_position)
                    direction = (attack_directions or {}).get(team)
                    forward = (end.x - start.x) * direction if direction is not None else 0
                    events.append(Event(
                        match_id=match_id, sequence=len(events) + 1, match_clock_ms=timestamp,
                        type=kind, team_id=team,
                        player_id=previous_player if kind == "pass_completed" else owner.track_id,
                        target_player_id=owner.track_id if kind == "pass_completed" else None,
                        position=start, end_position=end,
                        qualifiers={
                            "source": result.extractor, "inferred": True,
                            "source_confidence": min(owner.confidence, owner.team_confidence, ball.confidence),
                            "progressive": forward >= 10,
                            "final_third_entry": (start.x < 66.67 <= end.x) if direction == 1
                                else (start.x > 33.33 >= end.x) if direction == -1 else False,
                            "travel_ms": elapsed,
                        },
                        synthetic=result.detection_source != "model",
                    ))
            previous_owner = current
    return events


def _distance(a: tuple[float, float] | None, b: tuple[float, float] | None) -> float:
    if a is None or b is None:
        return float("inf")
    return hypot((a[0] - b[0]) * 1.05, (a[1] - b[1]) * 0.68)


def _position(point: tuple[float, float]) -> Position:
    return Position(x=round(point[0], 3), y=round(point[1], 3))


def _entity(point: CvTrackPoint) -> dict:
    return {
        "track_id": point.track_id,
        "team_id": point.team_id,
        "confidence": point.confidence,
        "team_confidence": point.team_confidence,
        "position": _position(point.pitch_position).model_dump() if point.pitch_position else None,
        "image_position": _position(point.image_position).model_dump() if point.image_position else None,
        "speed_kmh": point.speed_kmh,
        "inferred": point.inferred,
    }
