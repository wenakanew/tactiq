from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tempfile

import cv2  # type: ignore[import-not-found]
import numpy as np

from tactiq.cv.types import CameraCalibration, CvIngestResult, CvTrackPoint


@dataclass
class _TrackState:
    track_id: str
    object_class: str
    cx: float
    cy: float
    vx: float = 0.0
    vy: float = 0.0
    last_frame: int = 0
    team_votes_a: int = 0
    team_votes_b: int = 0


def map_to_pitch_percent(x_px: float, y_px: float, width: int, height: int) -> tuple[float, float]:
    if width <= 0 or height <= 0:
        return 50.0, 50.0
    return (
        float(max(0.0, min(100.0, (x_px / width) * 100.0))),
        float(max(0.0, min(100.0, (y_px / height) * 100.0))),
    )


def classify_team_from_patch(patch_bgr: np.ndarray) -> str:
    if patch_bgr.size == 0:
        return "unknown"

    hsv = cv2.cvtColor(patch_bgr, cv2.COLOR_BGR2HSV)
    sat = float(np.mean(hsv[:, :, 1]))
    if sat < 28.0:
        return "unknown"

    mean_bgr = np.mean(patch_bgr.reshape(-1, 3), axis=0)
    blue, green, red = float(mean_bgr[0]), float(mean_bgr[1]), float(mean_bgr[2])

    if abs(red - blue) < 8 and green > min(red, blue) - 5:
        return "unknown"
    return "team_a" if blue > red else "team_b"


def _calibrate_broadcast(frame: np.ndarray) -> CameraCalibration:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 60, 140)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=80, minLineLength=80, maxLineGap=16)

    confidence = 0.2
    if lines is not None:
        confidence = min(0.9, 0.3 + (len(lines) / 60.0))

    return CameraCalibration(
        method="line-keypoint-homography",
        confidence=float(round(confidence, 2)),
        homography=[
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
        ],
    )


def extract_cv_tracks_from_video_bytes(content: bytes, source_name: str = "uploaded_video") -> CvIngestResult:
    if not content:
        raise ValueError("Empty video content")

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp:
            tmp.write(content)
            temp_path = Path(tmp.name)

        cap = cv2.VideoCapture(str(temp_path))
        if not cap.isOpened():
            raise ValueError("Unable to open video for cv ingest")

        fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1)
        sample_step = max(1, int(round(fps / 8.0)))

        background = cv2.createBackgroundSubtractorMOG2(history=240, varThreshold=24, detectShadows=False)

        next_track_id = 1
        tracks: dict[str, _TrackState] = {}
        ball_track_id: str | None = None
        ball_history: list[tuple[int, float, float]] = []
        observations: list[CvTrackPoint] = []

        frame_idx = 0
        sampled_frames = 0
        first_frame: np.ndarray | None = None

        while True:
            ok, frame = cap.read()
            if not ok:
                break

            frame_idx += 1
            if frame_idx % sample_step != 0:
                continue

            sampled_frames += 1
            if first_frame is None:
                first_frame = frame.copy()

            mask = background.apply(frame)
            kernel = np.ones((3, 3), np.uint8)
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
            mask = cv2.morphologyEx(mask, cv2.MORPH_DILATE, kernel, iterations=2)

            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            detections: list[dict[str, float | str | tuple[float, float, float, float]]] = []
            for contour in contours:
                area = float(cv2.contourArea(contour))
                if area < 24 or area > (width * height * 0.2):
                    continue

                x, y, w, h = cv2.boundingRect(contour)
                x_f, y_f, w_f, h_f = float(x), float(y), float(w), float(h)
                cx, cy = x_f + (w_f / 2.0), y_f + (h_f / 2.0)

                object_class = "player"
                if area < 95 and w_f < 26 and h_f < 26:
                    object_class = "ball"

                team_id: str | None = None
                if object_class == "player":
                    x2 = int(min(width, x + w))
                    y2 = int(min(height, y + h))
                    patch = frame[int(max(0, y)):y2, int(max(0, x)):x2]
                    inferred_team = classify_team_from_patch(patch)
                    team_id = inferred_team if inferred_team in {"team_a", "team_b"} else None

                confidence = min(0.99, 0.35 + (area / max(1.0, width * height * 0.02)))
                detections.append(
                    {
                        "object_class": object_class,
                        "team_id": team_id or "",
                        "confidence": float(round(confidence, 3)),
                        "bbox": (x_f, y_f, w_f, h_f),
                        "cx": cx,
                        "cy": cy,
                        "area": area,
                    }
                )

            assigned_tracks: set[str] = set()
            for det in sorted(detections, key=lambda d: (d["object_class"] == "ball", -float(d["confidence"])), reverse=True):
                det_class = str(det["object_class"])
                det_cx = float(det["cx"])
                det_cy = float(det["cy"])

                best_track: _TrackState | None = None
                best_distance = 1e9
                for state in tracks.values():
                    if state.object_class != det_class or state.track_id in assigned_tracks:
                        continue
                    distance = float(np.hypot(state.cx - det_cx, state.cy - det_cy))
                    if distance < best_distance and distance < 60.0:
                        best_distance = distance
                        best_track = state

                if best_track is None:
                    track_id = f"{det_class}_{next_track_id:04d}"
                    next_track_id += 1
                    best_track = _TrackState(
                        track_id=track_id,
                        object_class=det_class,
                        cx=det_cx,
                        cy=det_cy,
                        last_frame=frame_idx,
                    )
                    tracks[best_track.track_id] = best_track
                else:
                    dt = max(1.0, frame_idx - best_track.last_frame)
                    best_track.vx = (det_cx - best_track.cx) / dt
                    best_track.vy = (det_cy - best_track.cy) / dt
                    best_track.cx = det_cx
                    best_track.cy = det_cy
                    best_track.last_frame = frame_idx

                assigned_tracks.add(best_track.track_id)

                team_id = str(det.get("team_id") or "")
                if best_track.object_class == "player":
                    if team_id == "team_a":
                        best_track.team_votes_a += 1
                    elif team_id == "team_b":
                        best_track.team_votes_b += 1

                resolved_team = None
                if best_track.object_class == "player":
                    if best_track.team_votes_a > best_track.team_votes_b:
                        resolved_team = "team_a"
                    elif best_track.team_votes_b > best_track.team_votes_a:
                        resolved_team = "team_b"

                bbox = det["bbox"]
                pitch = map_to_pitch_percent(det_cx, det_cy, width, height)
                observations.append(
                    CvTrackPoint(
                        track_id=best_track.track_id,
                        object_class=best_track.object_class,  # type: ignore[arg-type]
                        team_id=resolved_team,
                        confidence=float(det["confidence"]),
                        frame_idx=frame_idx,
                        timestamp_ms=int((frame_idx / max(0.1, fps)) * 1000),
                        bbox_xywh=(float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])),
                        pitch_position=pitch,
                        inferred=False,
                    )
                )

                if best_track.object_class == "ball":
                    ball_track_id = best_track.track_id
                    ball_history.append((frame_idx, det_cx, det_cy))
                    if len(ball_history) > 6:
                        ball_history = ball_history[-6:]

            # Ball occlusion fallback (short horizon only)
            if ball_track_id and all(d["object_class"] != "ball" for d in detections):
                state = tracks.get(ball_track_id)
                if state and frame_idx - state.last_frame <= max(2, sample_step * 2):
                    pred_x = state.cx + state.vx
                    pred_y = state.cy + state.vy
                    pred_x = float(max(0.0, min(width - 1.0, pred_x)))
                    pred_y = float(max(0.0, min(height - 1.0, pred_y)))
                    state.cx = pred_x
                    state.cy = pred_y
                    state.last_frame = frame_idx
                    pitch = map_to_pitch_percent(pred_x, pred_y, width, height)
                    observations.append(
                        CvTrackPoint(
                            track_id=state.track_id,
                            object_class="ball",
                            team_id=None,
                            confidence=0.35,
                            frame_idx=frame_idx,
                            timestamp_ms=int((frame_idx / max(0.1, fps)) * 1000),
                            bbox_xywh=(pred_x - 4.0, pred_y - 4.0, 8.0, 8.0),
                            pitch_position=pitch,
                            inferred=True,
                        )
                    )

            if sampled_frames >= 450:
                break

        cap.release()

        if first_frame is None:
            raise ValueError("Video did not provide readable frames")

        calibration = _calibrate_broadcast(first_frame)
        notes = (
            f"cv-ingest-v1 processed source={source_name}; sampled_frames={sampled_frames}; "
            f"tracks={len(observations)}"
        )

        return CvIngestResult(
            extractor="cv-ingest-v1",
            fps=float(round(fps, 3)),
            width=width,
            height=height,
            frame_count=frame_count,
            sampled_frames=sampled_frames,
            tracks=observations,
            calibration=calibration,
            notes=notes,
        )
    finally:
        if temp_path and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
