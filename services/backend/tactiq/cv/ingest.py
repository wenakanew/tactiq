from __future__ import annotations

import logging
from pathlib import Path
import tempfile

import cv2
import numpy as np

from tactiq.cv.calibration import CameraProjector, project_point
from tactiq.cv.detection import Detector, OnnxDetector, configured_detector
from tactiq.cv.tracking import MultiObjectTracker, SceneContinuity, TeamClassifier
from tactiq.cv.types import CameraCalibration, CvIngestResult, CvOptions, CvTrackPoint
from tactiq.video.validation import validate_video_container

logger = logging.getLogger(__name__)


def map_to_pitch_percent(x_px: float, y_px: float, width: int, height: int) -> tuple[float, float]:
    """Image normalization only; metric pitch coordinates require calibration."""
    if width <= 0 or height <= 0:
        raise ValueError("Image dimensions must be positive")
    return float(np.clip(x_px / width * 100, 0, 100)), float(np.clip(y_px / height * 100, 0, 100))


def classify_team_from_patch(patch_bgr: np.ndarray) -> str:
    if patch_bgr.size == 0:
        return "unknown"
    hsv = cv2.cvtColor(patch_bgr, cv2.COLOR_BGR2HSV)
    if float(np.mean(hsv[:, :, 1])) < 28:
        return "unknown"
    blue, green, red = np.mean(patch_bgr.reshape(-1, 3), axis=0)
    if abs(red - blue) < 8 and green > min(red, blue) - 5:
        return "unknown"
    return "team_a" if blue > red else "team_b"


def extract_cv_tracks_from_video_bytes(
    content: bytes,
    source_name: str = "uploaded_video",
    options: CvOptions | None = None,
    *,
    detector: Detector | None = None,
) -> CvIngestResult:
    if not content:
        raise ValueError("Empty video content")
    options = options or CvOptions()
    detector = detector or configured_detector()
    validate_video_container(content)
    temporary: Path | None = None
    capture = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as handle:
            handle.write(content)
            temporary = Path(handle.name)
        capture = cv2.VideoCapture(str(temporary))
        if not capture.isOpened():
            raise ValueError("Unable to decode the uploaded video")
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if not np.isfinite(fps) or fps <= 0 or width <= 0 or height <= 0:
            raise ValueError("Video must provide valid dimensions and timing")
        if width * height > 16_000_000:
            raise ValueError("Video resolution exceeds the supported decode limit")
        step = max(1, round(fps / options.sample_fps))
        projector = CameraProjector(options.calibrations)
        tracker = MultiObjectTracker()
        scenes = SceneContinuity()
        scene_id = 0
        scene_changes: list[int] = []
        teams = TeamClassifier(options.team_colors_rgb)
        observations: list[CvTrackPoint] = []
        timestamps: list[int] = []
        calibrations: list[CameraCalibration] = []
        previous_positions: dict[str, tuple[int, tuple[float, float]]] = {}
        warnings: set[str] = set()
        if detector.source == "heuristic":
            warnings.add("Heuristic detections are a development baseline, not validated player/ball recognition.")
        if not options.calibrations:
            warnings.add("No surveyed control points: image positions only; metric speeds and tactical geometry unavailable.")
        index = -1
        truncated = False
        previous_decode_time = -1
        approximate_timing = False
        previous_calibration_revision = 0
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            index += 1
            reported = capture.get(cv2.CAP_PROP_POS_MSEC)
            if not np.isfinite(reported) or (index > 0 and reported <= previous_decode_time):
                timestamp = max(round(index / fps * 1000), previous_decode_time + max(1, round(1000 / fps)))
                approximate_timing = True
            else:
                timestamp = round(reported)
            previous_decode_time = timestamp
            if timestamp > options.max_duration_seconds * 1000:
                truncated = True
                break
            if index % step:
                continue
            if scenes.update(frame):
                scene_id += 1
                scene_changes.append(timestamp)
                tracker.reset()
                previous_positions.clear()
                projector.reset_for_cut(timestamp)
                if isinstance(detector, OnnxDetector):
                    detector.reset_tracking()
            calibration = projector.update(frame, timestamp)
            if projector.revision != previous_calibration_revision or calibration.status != "calibrated":
                previous_positions.clear()
                previous_calibration_revision = projector.revision
            calibrations.append(calibration)
            timestamps.append(timestamp)
            detections = detector.detect(frame)
            if len(detections) > 64:
                warnings.add("Dense-frame detections were capped at 64 objects.")
                balls = sorted((item for item in detections if item.object_class == "ball"), key=lambda item: item.confidence, reverse=True)[:4]
                players = sorted((item for item in detections if item.object_class == "player"), key=lambda item: item.confidence, reverse=True)
                detections = [*balls, *players[:64-len(balls)]]
            active = tracker.update(detections, frame, timestamp, teams)
            active_ids = {track.track_id for track in active}
            previous_positions = {key: value for key, value in previous_positions.items() if key in active_ids}
            ball_candidates = [item for item in active if item.object_class == "ball"]
            best_ball = max(
                ball_candidates,
                key=lambda item: (not item.inferred, item.confidence, item.last_observed_ms),
                default=None,
            )
            for track in active:
                if track.object_class == "ball" and track is not best_ball:
                    continue
                x, y, w, h = track.bbox
                anchor_x = x + w / 2
                anchor_y = y + h if track.object_class == "player" else y + h / 2
                if track.inferred and not (0 <= anchor_x <= width and 0 <= anchor_y <= height):
                    previous_positions.pop(track.track_id, None)
                    continue
                point = project_point(calibration, anchor_x, anchor_y)
                team_id, team_confidence = track.team
                speed = None
                previous = previous_positions.get(track.track_id)
                if point is not None and not track.inferred:
                    if previous is not None:
                        previous_time, previous_point = previous
                        dt = (timestamp - previous_time) / 1000
                        if 0 < dt <= 0.75:
                            displacement = (np.array(point) - previous_point) * np.array([1.05, 0.68])
                            candidate = float(np.linalg.norm(displacement) / dt * 3.6)
                            if candidate <= (50 if track.object_class == "player" else 200):
                                speed = round(candidate, 2)
                            else:
                                warnings.add("Implausible speed samples were rejected rather than clamped.")
                    previous_positions[track.track_id] = timestamp, point
                elif point is None or track.inferred:
                    previous_positions.pop(track.track_id, None)
                age = timestamp - track.last_observed_ms
                confidence = track.confidence * (max(0, 1 - age / tracker.max_age_ms) if track.inferred else 1)
                observations.append(CvTrackPoint(
                    track_id=track.track_id,
                    object_class=track.object_class,
                    team_id=team_id if track.object_class == "player" else None,
                    team_confidence=round(team_confidence, 3),
                    confidence=round(confidence, 3),
                    frame_idx=index,
                    timestamp_ms=timestamp,
                    bbox_xywh=track.bbox,
                    image_position=map_to_pitch_percent(anchor_x, anchor_y, width, height),
                    pitch_position=point,
                    speed_kmh=speed,
                    inferred=track.inferred,
                    scene_id=scene_id,
                ))
        if not timestamps:
            raise ValueError("Video did not provide readable sampled frames")
        if any(item.status != "calibrated" for item in calibrations) and options.calibrations:
            warnings.add("Camera calibration was unavailable in some frames; affected pitch coordinates were withheld.")
        if approximate_timing:
            warnings.add("The decoder did not provide reliable presentation timestamps; timing is frame-rate-derived.")
        has_pitch = any(item.pitch_position is not None for item in observations)
        has_image_only = any(item.pitch_position is None for item in observations)
        return CvIngestResult(
            extractor=f"cv-ingest-v2/{detector.name}",
            detection_source=detector.source,
            model_id=detector.model_id,
            fps=fps, width=width, height=height, frame_count=count,
            sampled_frames=len(timestamps), tracks=observations,
            calibration=calibrations[-1],
            calibration_history=calibrations,
            sampled_timestamps_ms=timestamps,
            coordinate_space="mixed" if has_pitch and has_image_only else "pitch_percent" if has_pitch else "image_percent",
            processed_until_ms=timestamps[-1],
            truncated=truncated,
            warnings=sorted(warnings),
            timing_source="frame_rate_approximation" if approximate_timing else "presentation_timestamps",
            scene_changes=scene_changes,
            tracking_quality={
                "sampled_frames": len(timestamps),
                "ball_observed_frames": sum(point.object_class == "ball" and not point.inferred for point in observations),
                "ball_predicted_frames": sum(point.object_class == "ball" and point.inferred for point in observations),
                "player_observed_samples": sum(point.object_class == "player" and not point.inferred for point in observations),
                "scene_changes": len(scene_changes),
            },
            notes=f"Processed {len(timestamps)} sampled frames; club names require supplied match metadata.",
        )
    finally:
        if capture is not None:
            capture.release()
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                logger.exception("Unable to remove temporary CV input")
