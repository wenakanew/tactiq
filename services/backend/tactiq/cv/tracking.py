from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np
from scipy.optimize import linear_sum_assignment

from tactiq.cv.detection import Detection, ObjectClass


class SceneContinuity:
    def __init__(self) -> None:
        self.previous: np.ndarray | None = None
        self.histogram: np.ndarray | None = None

    def update(self, frame: np.ndarray) -> bool:
        small = cv2.resize(frame, (96, 64))
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        histogram = cv2.calcHist([hsv], [0, 1], None, [24, 16], [0, 180, 0, 256])
        cv2.normalize(histogram, histogram, norm_type=cv2.NORM_L1)
        difference = float(np.mean(cv2.absdiff(small, self.previous))) if self.previous is not None else 0
        color_change = cv2.compareHist(self.histogram, histogram, cv2.HISTCMP_BHATTACHARYYA) if self.histogram is not None else 0
        self.previous, self.histogram = small, histogram
        return bool((color_change > .62 and difference > 30) or difference > 95)


def jersey_color(frame: np.ndarray, bbox: tuple[float, float, float, float]) -> np.ndarray | None:
    x, y, width, height = bbox
    x1, x2 = max(0, int(x + width * 0.2)), min(frame.shape[1], int(x + width * 0.8))
    y1, y2 = max(0, int(y + height * 0.15)), min(frame.shape[0], int(y + height * 0.5))
    patch = frame[y1:y2, x1:x2]
    if patch.size == 0:
        return None
    return np.median(cv2.cvtColor(patch, cv2.COLOR_BGR2LAB).reshape(-1, 3), axis=0).astype(float)


class TeamClassifier:
    def __init__(self, colors_rgb: dict[str, tuple[int, int, int]] | None = None) -> None:
        self.palette: np.ndarray | None = None
        self.samples: list[np.ndarray] = []
        if colors_rgb:
            pixels = np.array([[colors_rgb["team_a"][::-1], colors_rgb["team_b"][::-1]]], dtype=np.uint8)
            self.palette = cv2.cvtColor(pixels, cv2.COLOR_BGR2LAB)[0].astype(float)
            if np.linalg.norm(self.palette[0] - self.palette[1]) < 25:
                raise ValueError("Team jersey colors are too similar to distinguish reliably")

    def learn(self, colors: list[np.ndarray]) -> None:
        if self.palette is not None:
            return
        self.samples.extend(colors)
        self.samples = self.samples[-100:]
        if len(self.samples) < 8:
            return
        values = np.asarray(self.samples)
        distances = np.linalg.norm(values[:, None] - values[None, :], axis=2)
        a, b = np.unravel_index(np.argmax(distances), distances.shape)
        centers = values[[a, b]].copy()
        for _ in range(10):
            labels = np.argmin(np.linalg.norm(values[:, None] - centers[None], axis=2), axis=1)
            if min(np.sum(labels == 0), np.sum(labels == 1)) < 2:
                return
            centers = np.array([np.median(values[labels == i], axis=0) for i in (0, 1)])
        if np.linalg.norm(centers[0] - centers[1]) >= 35:
            # Stable cluster labels are team hints, never inferred club identities.
            self.palette = centers[np.argsort(centers[:, 2])]

    def classify(self, color: np.ndarray | None) -> tuple[str | None, float]:
        if color is None or self.palette is None:
            return None, 0.0
        distances = np.linalg.norm(self.palette - color, axis=1)
        nearest = int(np.argmin(distances))
        margin = float(abs(distances[0] - distances[1]) / max(1.0, np.sum(distances)))
        confidence = margin * max(0.0, 1.0 - float(distances[nearest]) / 100)
        if confidence < 0.35:
            return None, confidence
        return ("team_a" if nearest == 0 else "team_b"), min(1.0, confidence)


@dataclass
class Track:
    track_id: str
    object_class: ObjectClass
    state: np.ndarray
    covariance: np.ndarray
    bbox_size: tuple[float, float]
    timestamp_ms: int
    last_observed_ms: int
    confidence: float
    color: np.ndarray | None = None
    team_votes: dict[str, float] = field(default_factory=lambda: {"team_a": 0.0, "team_b": 0.0})
    inferred: bool = False
    observed_bbox: tuple[float, float, float, float] | None = None

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        if not self.inferred and self.observed_bbox is not None:
            return self.observed_bbox
        width, height = self.bbox_size
        return float(self.state[0] - width / 2), float(self.state[1] - height / 2), width, height

    @property
    def team(self) -> tuple[str | None, float]:
        total = sum(self.team_votes.values())
        if total <= 0:
            return None, 0.0
        winner = max(self.team_votes, key=self.team_votes.get)
        confidence = self.team_votes[winner] / total
        return (winner, confidence) if confidence >= 0.7 else (None, confidence)


class MultiObjectTracker:
    def __init__(self, max_age_ms: int = 1500) -> None:
        self.max_age_ms = max_age_ms
        self.tracks: dict[str, Track] = {}
        self.next_id = 1

    def reset(self) -> None:
        self.tracks.clear()

    def update(
        self, detections: list[Detection], frame: np.ndarray, timestamp_ms: int, teams: TeamClassifier
    ) -> list[Track]:
        self.tracks = {
            key: track for key, track in self.tracks.items()
            if timestamp_ms - track.last_observed_ms <= self.max_age_ms
        }
        current = list(self.tracks.values())
        for track in current:
            dt = max(0.0, (timestamp_ms - track.timestamp_ms) / 1000)
            transition = np.array([[1, 0, dt, 0], [0, 1, 0, dt], [0, 0, 1, 0], [0, 0, 0, 1]], dtype=float)
            track.state = transition @ track.state
            acceleration = 800.0 if track.object_class == "ball" else 120.0
            noise = np.array([[dt**4/4, 0, dt**3/2, 0], [0, dt**4/4, 0, dt**3/2],
                              [dt**3/2, 0, dt**2, 0], [0, dt**3/2, 0, dt**2]]) * acceleration**2
            track.covariance = transition @ track.covariance @ transition.T + noise + np.eye(4) * .1
            track.timestamp_ms = timestamp_ms
            track.inferred = True
        colors = [jersey_color(frame, item.bbox) if item.object_class == "player" else None for item in detections]
        teams.learn([color for color in colors if color is not None])
        costs = np.full((len(current), len(detections)), 1e6, dtype=float)
        diagonal = float(np.hypot(*frame.shape[:2]))
        for i, track in enumerate(current):
            for j, detection in enumerate(detections):
                if track.object_class != detection.object_class:
                    continue
                x, y, w, h = detection.bbox
                distance = float(np.linalg.norm(track.state[:2] - [x + w / 2, y + h / 2])) / diagonal
                if distance > 0.12:
                    continue
                appearance = 0.0
                if track.color is not None and colors[j] is not None:
                    appearance = float(np.linalg.norm(track.color - colors[j])) / 255
                    if appearance > 0.45:
                        continue
                costs[i, j] = distance * 4 + appearance
        matched: set[int] = set()
        if costs.size:
            rows, cols = linear_sum_assignment(costs)
            for i, j in zip(rows, cols, strict=True):
                if costs[i, j] >= 1e6:
                    continue
                track = current[int(i)]
                detection = detections[int(j)]
                self._observe(track, detection, colors[int(j)], timestamp_ms)
                matched.add(int(j))
        for j, detection in enumerate(detections):
            if j in matched:
                continue
            x, y, w, h = detection.bbox
            track_id = f"{detection.object_class}_{self.next_id:04d}"
            self.next_id += 1
            self.tracks[track_id] = Track(
                track_id, detection.object_class,
                np.array([x + w / 2, y + h / 2, 0, 0], dtype=float),
                np.diag([16.0, 16.0, 2500.0, 2500.0]), (w, h),
                timestamp_ms, timestamp_ms, detection.confidence, colors[j],
                observed_bbox=detection.bbox,
            )
            if detection.object_class == "ball":
                self.tracks = {key: value for key, value in self.tracks.items()
                               if value.object_class != "ball" or key == track_id}
        for track in self.tracks.values():
            if track.object_class == "player" and not track.inferred:
                team, confidence = teams.classify(track.color)
                track.team_votes = {key: value * 0.9 for key, value in track.team_votes.items()}
                if team:
                    track.team_votes[team] += confidence
        return sorted(self.tracks.values(), key=lambda item: item.track_id)

    @staticmethod
    def _observe(track: Track, detection: Detection, color: np.ndarray | None, timestamp_ms: int) -> None:
        x, y, width, height = detection.bbox
        measurement = np.array([x + width / 2, y + height / 2])
        observation = np.array([[1, 0, 0, 0], [0, 1, 0, 0]], dtype=float)
        noise = np.eye(2) * 4.0
        residual = measurement - observation @ track.state
        innovation = observation @ track.covariance @ observation.T + noise
        gain = track.covariance @ observation.T @ np.linalg.inv(innovation)
        track.state += gain @ residual
        track.covariance = (np.eye(4) - gain @ observation) @ track.covariance
        track.bbox_size = width, height
        track.confidence = detection.confidence
        track.last_observed_ms = timestamp_ms
        track.inferred = False
        track.observed_bbox = detection.bbox
        if color is not None:
            track.color = color if track.color is None else track.color * 0.7 + color * 0.3
