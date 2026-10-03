from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import os
import tempfile

from tactiq.domain.models import Event, Position


@dataclass(frozen=True)
class VideoExtractionResult:
    events: list[Event]
    extractor: str
    confidence: float
    notes: str


def extract_events_from_video_auto(content: bytes, match_id: str) -> VideoExtractionResult:
    """
    Lightweight model-based auto-eventing over uploaded video bytes.

    This implementation uses frame-difference motion analysis to infer football-like
    event candidates (passes, carries, shots, pressure moments, possession changes)
    and adapts them to the existing Event schema.
    """
    if not content:
        raise ValueError("Empty video content")

    try:
        import cv2  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("OpenCV is not installed for auto-eventing") from exc

    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp:
            tmp.write(content)
            temp_path = tmp.name

        cap = cv2.VideoCapture(temp_path)
        if not cap.isOpened():
            raise RuntimeError("Unable to open uploaded video")

        fps = cap.get(cv2.CAP_PROP_FPS)
        if not fps or fps <= 0:
            fps = 25.0
        sample_step = max(1, int(round(fps / 5.0)))  # ~5 FPS sampling

        observations: list[tuple[int, float, float, float]] = []
        prev_gray = None
        prev_center_x = 0.5
        frame_idx = 0

        while True:
            ok, frame = cap.read()
            if not ok:
                break

            frame_idx += 1
            if frame_idx % sample_step != 0:
                continue

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            gray = cv2.GaussianBlur(gray, (5, 5), 0)

            if prev_gray is None:
                prev_gray = gray
                continue

            delta = cv2.absdiff(prev_gray, gray)
            _, thresh = cv2.threshold(delta, 22, 255, cv2.THRESH_BINARY)
            motion_pixels = cv2.countNonZero(thresh)
            total_pixels = thresh.shape[0] * thresh.shape[1]
            motion_ratio = (motion_pixels / total_pixels) if total_pixels else 0.0

            moments = cv2.moments(thresh)
            if moments["m00"] > 0:
                center_x = (moments["m10"] / moments["m00"]) / thresh.shape[1]
            else:
                center_x = prev_center_x

            dt = sample_step / fps
            # x-normalized movement converted into rough km/h proxy on 105m pitch length.
            speed_kmh = abs(center_x - prev_center_x) * 105.0 / max(0.05, dt) * 3.6
            speed_kmh = max(0.0, min(130.0, speed_kmh))

            clock_ms = int((frame_idx / fps) * 1000)
            observations.append((clock_ms, motion_ratio, center_x, speed_kmh))

            prev_gray = gray
            prev_center_x = center_x

        cap.release()

        if len(observations) < 4:
            raise RuntimeError("Insufficient motion observations for auto-eventing")

        events = _observations_to_events(match_id=match_id, observations=observations)
        if len(events) < 6:
            # Guardrail: keep pipeline useful even for low-quality or static clips.
            fallback = extract_events_from_video_stub(content=content, match_id=match_id)
            return replace(
                fallback,
                notes="Auto-eventing produced too few events; fell back to deterministic stub extraction.",
            )

        confidence = min(0.86, 0.45 + (len(events) / 40.0))
        return VideoExtractionResult(
            events=events,
            extractor="video-auto-v1",
            confidence=round(confidence, 2),
            notes="Motion-model auto-eventing extracted football event candidates from uploaded video.",
        )
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


def extract_events_from_video_stub(content: bytes, match_id: str) -> VideoExtractionResult:
    """
    Stub extractor for hackathon use.

    Converts uploaded video bytes into synthetic-but-football-realistic event candidates,
    normalized to the core `Event` schema so the existing intelligence pipeline can run
    without changes.
    """
    if not content:
        raise ValueError("Empty video content")

    digest = sha256(content).hexdigest()
    seed = int(digest[:8], 16)

    team_primary = "team_a" if seed % 2 == 0 else "team_b"
    team_secondary = "team_b" if team_primary == "team_a" else "team_a"

    base_clock = 42_000 + (seed % 6_000)
    pace = 700 + (seed % 250)

    # Deterministic event template derived from file hash.
    template = [
        ("recovery", team_primary, "p_06", 41.0, 44.0, 44.0, 44.0, {}),
        (
            "pass_completed",
            team_primary,
            "p_08",
            44.0,
            44.0,
            57.0,
            41.0,
            {"progressive": True, "travel_ms": 620, "pressured": True},
        ),
        (
            "carry",
            team_primary,
            "p_10",
            57.0,
            41.0,
            67.0,
            38.0,
            {"progressive": True, "travel_ms": 640},
        ),
        (
            "pass_completed",
            team_primary,
            "p_10",
            67.0,
            38.0,
            75.0,
            34.0,
            {"progressive": True, "travel_ms": 560},
        ),
        (
            "pass_completed",
            team_primary,
            "p_11",
            75.0,
            34.0,
            82.0,
            31.0,
            {"progressive": True, "travel_ms": 540, "final_third_entry": True},
        ),
        (
            "shot",
            team_primary,
            "p_09",
            82.0,
            31.0,
            91.0,
            30.0,
            {"shot_speed_kmh": 99.4, "created_by": "p_10"},
        ),
        (
            "turnover",
            team_primary,
            "p_07",
            70.0,
            35.0,
            68.0,
            37.0,
            {},
        ),
        (
            "recovery",
            team_secondary,
            "q_06",
            68.0,
            37.0,
            69.0,
            36.0,
            {},
        ),
        (
            "pass_completed",
            team_secondary,
            "q_08",
            69.0,
            36.0,
            78.0,
            33.0,
            {"progressive": True, "travel_ms": 600},
        ),
    ]

    events: list[Event] = []
    for i, (event_type, team_id, player_id, sx, sy, ex, ey, qualifiers) in enumerate(template, start=1):
        entry_qualifiers = dict(qualifiers)
        if sx < 66.67 <= ex and event_type in {"pass_completed", "carry"}:
            entry_qualifiers.setdefault("final_third_entry", True)

        events.append(
            Event(
                match_id=match_id,
                sequence=i,
                match_clock_ms=base_clock + (i * pace),
                type=event_type,
                team_id=team_id,
                player_id=player_id,
                position=Position(x=sx, y=sy),
                end_position=Position(x=ex, y=ey),
                qualifiers=entry_qualifiers,
            )
        )

    return VideoExtractionResult(
        events=events,
        extractor="video-stub-v1",
        confidence=0.62,
        notes="Stub extraction generated deterministic event candidates from uploaded video hash.",
    )


def _observations_to_events(match_id: str, observations: list[tuple[int, float, float, float]]) -> list[Event]:
    events: list[Event] = []
    seq = 1
    prev_center_x = observations[0][2]
    prev_dir = 0
    last_team = "team_a"

    def add_event(
        clock_ms: int,
        event_type: str,
        team_id: str,
        player_id: str,
        sx: float,
        sy: float,
        ex: float,
        ey: float,
        qualifiers: dict,
    ) -> None:
        nonlocal seq
        event_qualifiers = dict(qualifiers)
        if sx < 66.67 <= ex and event_type in {"pass_completed", "carry"}:
            event_qualifiers.setdefault("final_third_entry", True)

        events.append(
            Event(
                match_id=match_id,
                sequence=seq,
                match_clock_ms=max(0, clock_ms),
                type=event_type,
                team_id=team_id,
                player_id=player_id,
                position=Position(x=max(0.0, min(100.0, sx)), y=max(0.0, min(100.0, sy))),
                end_position=Position(x=max(0.0, min(100.0, ex)), y=max(0.0, min(100.0, ey))),
                qualifiers=event_qualifiers,
            )
        )
        seq += 1

    for idx, (clock_ms, motion_ratio, center_x, speed_kmh) in enumerate(observations[1:], start=1):
        direction = 1 if center_x >= prev_center_x else -1
        attacking_team = "team_a" if direction >= 0 else "team_b"
        defending_team = "team_b" if attacking_team == "team_a" else "team_a"

        sx = prev_center_x * 100.0
        ex = center_x * 100.0
        # Keep vertical motion plausible with a deterministic mild wave.
        sy = 35.0 + ((idx % 5) * 3.0)
        ey = max(20.0, min(80.0, sy + (2.5 if direction > 0 else -2.5)))

        if prev_dir != 0 and direction != prev_dir and speed_kmh > 8.0:
            add_event(clock_ms - 180, "turnover", last_team, f"{'a' if last_team == 'team_a' else 'b'}_07", sx, sy, ex, ey, {})
            add_event(clock_ms - 90, "recovery", attacking_team, f"{'a' if attacking_team == 'team_a' else 'b'}_06", sx, sy, ex, ey, {})

        if motion_ratio > 0.006 and speed_kmh >= 10.0 and speed_kmh < 62.0:
            add_event(
                clock_ms,
                "pass_completed",
                attacking_team,
                f"{'a' if attacking_team == 'team_a' else 'b'}_08",
                sx,
                sy,
                ex,
                ey,
                {
                    "progressive": speed_kmh > 20.0,
                    "travel_ms": int(max(220, min(900, 1800 / max(1.0, speed_kmh / 12.0)))),
                    "ball_speed_kmh": round(min(95.0, speed_kmh * 1.25), 1),
                    "pass_difficulty": round(min(0.95, 0.35 + (motion_ratio * 20)), 2),
                    "pressured": motion_ratio > 0.012,
                },
            )
        elif motion_ratio > 0.003 and 4.0 <= speed_kmh < 18.0:
            add_event(
                clock_ms,
                "carry",
                attacking_team,
                f"{'a' if attacking_team == 'team_a' else 'b'}_10",
                sx,
                sy,
                ex,
                ey,
                {
                    "progressive": speed_kmh > 9.0,
                    "travel_ms": int(max(280, min(1100, 2200 / max(1.0, speed_kmh / 8.0)))),
                },
            )

        if speed_kmh >= 65.0 and motion_ratio > 0.006:
            add_event(
                clock_ms + 120,
                "shot",
                attacking_team,
                f"{'a' if attacking_team == 'team_a' else 'b'}_09",
                ex,
                ey,
                min(100.0, ex + 8.0),
                ey,
                {"shot_speed_kmh": round(min(125.0, speed_kmh * 1.15), 1)},
            )

        if motion_ratio > 0.014:
            add_event(
                clock_ms + 60,
                "pressure_event",
                defending_team,
                f"{'a' if defending_team == 'team_a' else 'b'}_05",
                sx,
                sy,
                ex,
                ey,
                {"intensity": round(min(1.0, motion_ratio * 45), 2)},
            )

        prev_center_x = center_x
        prev_dir = direction
        last_team = attacking_team

    # Ensure strict sequence order by clock time.
    events.sort(key=lambda item: (item.match_clock_ms, item.sequence))
    for index, event in enumerate(events, start=1):
        event.sequence = index

    return events
