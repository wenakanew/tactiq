from __future__ import annotations

import cv2
import numpy as np

from tactiq.cv.types import CalibrationInput, CameraCalibration

PITCH_METRES = np.array([105.0, 68.0], dtype=np.float64)


def fit_calibration(points: CalibrationInput, width: int, height: int) -> CameraCalibration:
    image = np.asarray(points.image_points, dtype=np.float64)
    pitch = np.asarray(points.pitch_points, dtype=np.float64) * PITCH_METRES / 100.0
    if np.any(image < 0) or np.any(image[:, 0] > width) or np.any(image[:, 1] > height):
        raise ValueError("Image control points must lie within the video frame")
    for coordinates in (image, pitch):
        if np.linalg.matrix_rank(coordinates - coordinates.mean(axis=0)) < 2:
            raise ValueError("Calibration control points must not be collinear")
    matrix, mask = cv2.findHomography(image, pitch, cv2.RANSAC, 1.0)
    if matrix is None or mask is None or not np.all(np.isfinite(matrix)):
        raise ValueError("Unable to estimate camera homography")
    inliers = mask.ravel().astype(bool)
    if inliers.sum() < 4 or inliers.mean() < 0.75 or np.linalg.cond(matrix) > 1e10:
        raise ValueError("Camera control points do not support a stable homography")
    projected = cv2.perspectiveTransform(image.reshape(-1, 1, 2), matrix).reshape(-1, 2)
    error = float(np.sqrt(np.mean(np.sum((projected[inliers] - pitch[inliers]) ** 2, axis=1))))
    if error > 1.0:
        raise ValueError("Camera calibration reprojection error exceeds one metre")
    percent_matrix = np.diag([100.0 / 105.0, 100.0 / 68.0, 1.0]) @ matrix
    return CameraCalibration(
        method="control-point-homography",
        confidence=round(float(inliers.mean()) / (1.0 + error), 3),
        homography=percent_matrix.tolist(),
        status="calibrated",
        timestamp_ms=points.timestamp_ms,
        reprojection_error_m=round(error, 4),
        inlier_count=int(inliers.sum()),
    )


def project_point(calibration: CameraCalibration, x: float, y: float) -> tuple[float, float] | None:
    if calibration.status != "calibrated" or calibration.homography is None:
        return None
    result = cv2.perspectiveTransform(
        np.array([[[x, y]]], dtype=np.float64), np.asarray(calibration.homography, dtype=np.float64)
    )[0, 0]
    if not np.all(np.isfinite(result)) or np.any(result < -2) or np.any(result > 102):
        return None
    return float(np.clip(result[0], 0, 100)), float(np.clip(result[1], 0, 100))


class CameraProjector:
    """Keep a surveyed pitch transform aligned across sampled broadcast frames."""

    def __init__(self, control_points: list[CalibrationInput]) -> None:
        self.pending = list(control_points)
        self.current = CameraCalibration(method="unavailable", confidence=0, reason="control_points_required")
        self.previous_gray: np.ndarray | None = None
        self.previous_points: np.ndarray | None = None
        self.revision = 0

    def reset_for_cut(self, timestamp_ms: int) -> None:
        self.revision += 1
        self.current = CameraCalibration(
            method="unavailable", confidence=0, timestamp_ms=timestamp_ms,
            reason="scene_cut_requires_survey", revision=self.revision,
        )
        self.previous_gray = None
        self.previous_points = None

    def update(self, frame: np.ndarray, timestamp_ms: int) -> CameraCalibration:
        height, width = frame.shape[:2]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        explicit = None
        while self.pending and self.pending[0].timestamp_ms <= timestamp_ms:
            explicit = self.pending.pop(0)
        if explicit is not None:
            self.current = fit_calibration(explicit, width, height)
            self.revision += 1
        elif self.current.status == "calibrated" and self.previous_gray is not None:
            self._follow_camera(gray, timestamp_ms)
        self.current = self.current.model_copy(update={"timestamp_ms": timestamp_ms, "revision": self.revision})
        self.previous_gray = gray
        # Pitch-line features are less affected by independently moving players.
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        line_mask = cv2.inRange(hsv, np.array([0, 0, 160]), np.array([180, 85, 255]))
        self.previous_points = cv2.goodFeaturesToTrack(
            gray, maxCorners=160, qualityLevel=0.02, minDistance=10, mask=line_mask
        )
        return self.current

    def _follow_camera(self, gray: np.ndarray, timestamp_ms: int) -> None:
        assert self.previous_gray is not None
        difference = float(np.mean(cv2.absdiff(gray, self.previous_gray)))
        if difference < 1.0:
            return
        if self.previous_points is None or len(self.previous_points) < 8:
            self._invalidate(timestamp_ms, "camera_alignment_unavailable")
            return
        moved, status, _ = cv2.calcOpticalFlowPyrLK(self.previous_gray, gray, self.previous_points, None)
        if moved is None or status is None:
            self._invalidate(timestamp_ms, "camera_tracking_lost")
            return
        valid = status.ravel() == 1
        before = self.previous_points[valid]
        after = moved[valid]
        if len(before) < 8:
            self._invalidate(timestamp_ms, "insufficient_camera_features")
            return
        transform, inliers = cv2.findHomography(before, after, cv2.RANSAC, 2.0)
        if transform is None or inliers is None or inliers.mean() < 0.7 or np.linalg.cond(transform) > 1e8:
            self._invalidate(timestamp_ms, "camera_cut_or_unreliable_motion")
            return
        projected = cv2.perspectiveTransform(before, transform)
        residual = float(np.median(np.linalg.norm(projected - after, axis=2)[inliers.ravel() == 1]))
        if residual > 2.0:
            self._invalidate(timestamp_ms, "camera_alignment_error")
            return
        assert self.current.homography is not None
        matrix = np.asarray(self.current.homography) @ np.linalg.inv(transform)
        if not np.all(np.isfinite(matrix)) or abs(matrix[2, 2]) < 1e-12:
            self._invalidate(timestamp_ms, "camera_transform_invalid")
            return
        self.current = self.current.model_copy(
            update={"method": "optical-flow-homography", "homography": (matrix / matrix[2, 2]).tolist()}
        )

    def _invalidate(self, timestamp_ms: int, reason: str) -> None:
        self.current = CameraCalibration(
            method="unavailable", confidence=0, timestamp_ms=timestamp_ms, reason=reason
        )
