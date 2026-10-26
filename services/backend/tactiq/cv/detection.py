from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
from typing import Literal, Protocol

import cv2
import numpy as np

ObjectClass = Literal["player", "ball"]


class CvUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class Detection:
    object_class: ObjectClass
    confidence: float
    bbox: tuple[float, float, float, float]


class Detector(Protocol):
    name: str
    source: Literal["model", "heuristic"]
    model_id: str | None

    def detect(self, frame: np.ndarray) -> list[Detection]: ...


class OnnxDetector:
    source: Literal["model", "heuristic"] = "model"
    name = "opencv-dnn-onnx/ball-roi-v1"

    def __init__(self, path: str, model_format: str = "yolox", input_size: int = 416) -> None:
        model = Path(path)
        if not model.is_file() or model.suffix.lower() != ".onnx":
            raise CvUnavailableError("TACTIQ_CV_MODEL_PATH must identify a readable ONNX model")
        if model_format not in {"yolox", "yolox-decoded", "yolov8"}:
            raise CvUnavailableError("TACTIQ_CV_MODEL_FORMAT must be yolox, yolox-decoded, or yolov8")
        if input_size < 128 or input_size > 1280 or input_size % 32:
            raise CvUnavailableError("TACTIQ_CV_INPUT_SIZE must be a multiple of 32 in [128, 1280]")
        self.model_id = hashlib.sha256(model.read_bytes()).hexdigest()
        expected = os.getenv("TACTIQ_CV_MODEL_SHA256", "").strip().lower()
        if expected and self.model_id != expected:
            raise CvUnavailableError("CV model SHA256 verification failed")
        try:
            self.net = cv2.dnn.readNetFromONNX(str(model))
        except cv2.error as exc:
            raise CvUnavailableError("Unable to load the configured ONNX detector") from exc
        self.model_format = model_format
        self.input_size = input_size
        try:
            self.person_class = int(os.getenv("TACTIQ_CV_PERSON_CLASS_ID", "0"))
            self.ball_class = int(os.getenv("TACTIQ_CV_BALL_CLASS_ID", "32"))
        except ValueError as exc:
            raise CvUnavailableError("CV class IDs must be integers") from exc
        if min(self.person_class, self.ball_class) < 0 or self.person_class == self.ball_class:
            raise CvUnavailableError("CV player and ball class IDs must be distinct non-negative integers")
        self.reset_tracking()

    def detect(self, frame: np.ndarray) -> list[Detection]:
        full = self._infer(frame)
        players = [item for item in full if item.object_class == "player"]
        balls = [item for item in full if item.object_class == "ball"]
        self._frames += 1
        height, width = frame.shape[:2]
        seed = max(balls, key=lambda item: item.confidence, default=None)
        if self._last_ball is not None and self._misses < 4:
            px, py, pw, ph = self._last_ball
            predicted = (px + pw / 2 + self._velocity[0], py + ph / 2 + self._velocity[1])
            nearby = [item for item in balls if np.hypot(
                item.bbox[0] + item.bbox[2] / 2 - predicted[0], item.bbox[1] + item.bbox[3] / 2 - predicted[1]
            ) <= max(50, np.hypot(*self._velocity) * 2)]
            seed = max(nearby, key=lambda item: item.confidence, default=None)
        if seed:
            x, y, w, h = seed.bbox
            center = (x + w / 2, y + h / 2)
            side = min(180 if max(w, h) <= 24 else 280 if max(w, h) <= 45 else 360, min(width, height))
        elif self._last_ball is not None and self._misses < 4:
            x, y, w, h = self._last_ball
            center = (x + w / 2 + self._velocity[0], y + h / 2 + self._velocity[1])
            side = min(280, min(width, height))
        elif players and self._frames % 3:
            candidates = sorted(players, key=lambda item: item.bbox[2] * item.bbox[3], reverse=True)
            player = candidates[(self._frames // 3) % min(4, len(candidates))]
            x, y, w, h = player.bbox
            center = x + w / 2, y + h
            side = min(280, min(width, height))
        else:
            side = min(360, min(width, height))
            columns = max(1, int(np.ceil(width / (side * .85))))
            rows = max(1, int(np.ceil(height / (side * .85))))
            cell = self._frames % (columns * rows)
            center = (side / 2 + (width - side) * (cell % columns) / max(1, columns - 1),
                      side / 2 + (height - side) * (cell // columns) / max(1, rows - 1))
        left = int(np.clip(center[0] - side / 2, 0, width - side))
        top = int(np.clip(center[1] - side / 2, 0, height - side))
        region = frame[top:top + side, left:left + side]
        refined = [
            Detection("ball", item.confidence, (item.bbox[0] + left, item.bbox[1] + top, item.bbox[2], item.bbox[3]))
            for item in self._infer(region) if item.object_class == "ball"
            and item.confidence >= (.3 if self._last_ball is not None and self._misses < 4 else .45)
            and .45 <= item.bbox[2] / item.bbox[3] <= 2.2
        ]
        if not refined:
            refined = [item for item in balls if item.confidence >= .7]
        confirmed = bool(refined)
        if not refined and seed and self._last_ball is not None and self._frames - self._last_confirmed <= 2:
            x, y, w, h = seed.bbox
            px, py, pw, ph = self._last_ball
            if (seed.confidence >= .3 and .65 <= w / h <= 1.6 and .5 <= w / pw <= 2
                    and .5 <= h / ph <= 2 and np.hypot(x + w / 2 - px - pw / 2, y + h / 2 - py - ph / 2)
                    <= max(30, np.hypot(*self._velocity) * 1.5)):
                refined = [seed]
        selected = max(refined, key=lambda item: item.confidence, default=None)
        if selected:
            x, y, w, h = selected.bbox
            if self._last_ball is not None:
                px, py, pw, ph = self._last_ball
                self._velocity = (float(np.clip(x + w / 2 - px - pw / 2, -side / 2, side / 2)),
                                  float(np.clip(y + h / 2 - py - ph / 2, -side / 2, side / 2)))
            self._last_ball = selected.bbox
            if confirmed:
                self._last_confirmed = self._frames
            self._misses = 0
            return [*players, selected]
        self._misses += 1
        if self._misses >= 4:
            self._last_ball = None
            self._velocity = (0.0, 0.0)
        return players

    def reset_tracking(self) -> None:
        self._last_ball: tuple[float, float, float, float] | None = None
        self._velocity = (0.0, 0.0)
        self._misses = self._frames = 0
        self._last_confirmed = -10

    def _infer(self, frame: np.ndarray) -> list[Detection]:
        height, width = frame.shape[:2]
        scale = min(self.input_size / width, self.input_size / height)
        resized = cv2.resize(frame, (max(1, round(width * scale)), max(1, round(height * scale))))
        canvas = np.full((self.input_size, self.input_size, 3), 114, dtype=np.uint8)
        left = top = 0
        if self.model_format == "yolov8":
            left = (self.input_size - resized.shape[1]) // 2
            top = (self.input_size - resized.shape[0]) // 2
        canvas[top:top + resized.shape[0], left:left + resized.shape[1]] = resized
        blob = cv2.dnn.blobFromImage(
            canvas, 1.0 / 255 if self.model_format == "yolov8" else 1.0,
            swapRB=self.model_format == "yolov8", crop=False,
        )
        try:
            self.net.setInput(blob)
            output = np.asarray(self.net.forward()).squeeze(axis=0)
        except cv2.error as exc:
            raise CvUnavailableError("Configured detector failed during inference") from exc
        if output.ndim != 2 or not np.all(np.isfinite(output)):
            raise CvUnavailableError("Detector returned an unsupported or non-finite tensor")
        if self.model_format == "yolov8":
            output = output.T
            scores = output[:, 4:]
        else:
            output = output.copy()
            if self.model_format == "yolox":
                grids = []
                strides = []
                for stride in (8, 16, 32):
                    side = self.input_size // stride
                    yy, xx = np.meshgrid(np.arange(side), np.arange(side), indexing="ij")
                    grids.append(np.stack((xx, yy), axis=-1).reshape(-1, 2))
                    strides.append(np.full((side * side, 1), stride))
                grid = np.concatenate(grids)
                stride_values = np.concatenate(strides)
                if len(output) != len(grid):
                    raise CvUnavailableError("YOLOX output grid does not match the configured input size")
                output[:, :2] = (output[:, :2] + grid) * stride_values
                output[:, 2:4] = np.exp(np.clip(output[:, 2:4], -20, 20)) * stride_values
            scores = output[:, 5:] * output[:, 4:5]
        if scores.shape[1] <= max(self.person_class, self.ball_class):
            raise CvUnavailableError("Configured class IDs are not present in the detector output")
        detections: list[Detection] = []
        for class_id, object_class, threshold in (
            (self.person_class, "player", 0.35), (self.ball_class, "ball", 0.2)
        ):
            indices = np.flatnonzero(scores[:, class_id] >= threshold)
            boxes: list[tuple[float, float, float, float]] = []
            confidences: list[float] = []
            for index in indices:
                cx, cy, w, h = output[index, :4]
                x1 = float(np.clip((cx - w / 2 - left) / scale, 0, width))
                y1 = float(np.clip((cy - h / 2 - top) / scale, 0, height))
                x2 = float(np.clip((cx + w / 2 - left) / scale, 0, width))
                y2 = float(np.clip((cy + h / 2 - top) / scale, 0, height))
                if x2 <= x1 or y2 <= y1:
                    continue
                boxes.append((x1, y1, x2 - x1, y2 - y1))
                confidences.append(float(np.clip(scores[index, class_id], 0, 1)))
            retained = cv2.dnn.NMSBoxes(boxes, confidences, threshold, 0.45)
            for index in np.asarray(retained).reshape(-1):
                detections.append(Detection(object_class, confidences[int(index)], boxes[int(index)]))
        return detections


class MotionDetector:
    name = "motion-baseline"
    source: Literal["model", "heuristic"] = "heuristic"
    model_id = None

    def __init__(self) -> None:
        self.background = cv2.createBackgroundSubtractorMOG2(history=240, varThreshold=24, detectShadows=False)
        self.frames = 0

    def detect(self, frame: np.ndarray) -> list[Detection]:
        mask = self.background.apply(frame)
        self.frames += 1
        if self.frames <= 2:
            return []
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        detections = []
        for contour in contours:
            area = cv2.contourArea(contour)
            if not 12 <= area <= frame.shape[0] * frame.shape[1] * 0.05:
                continue
            x, y, w, h = cv2.boundingRect(contour)
            kind: ObjectClass = "ball" if area < 95 and max(w, h) < 26 else "player"
            detections.append(Detection(kind, 0.4, (float(x), float(y), float(w), float(h))))
        return detections


def configured_detector() -> Detector:
    backend = os.getenv("TACTIQ_CV_DETECTOR", "onnx").strip().lower()
    if backend == "motion":
        if os.getenv("APP_ENV", "local").lower() == "production":
            raise CvUnavailableError("Heuristic CV is not allowed in production")
        return MotionDetector()
    if backend != "onnx":
        raise CvUnavailableError("TACTIQ_CV_DETECTOR must be onnx or motion")
    path = os.getenv("TACTIQ_CV_MODEL_PATH", "").strip()
    if not path:
        raise CvUnavailableError("Configure TACTIQ_CV_MODEL_PATH before using model-based video tracking")
    if os.getenv("APP_ENV", "local").lower() == "production" and not os.getenv("TACTIQ_CV_MODEL_SHA256", "").strip():
        raise CvUnavailableError("Production CV requires TACTIQ_CV_MODEL_SHA256 for artifact verification")
    try:
        size = int(os.getenv("TACTIQ_CV_INPUT_SIZE", "416"))
    except ValueError as exc:
        raise CvUnavailableError("TACTIQ_CV_INPUT_SIZE must be an integer") from exc
    return OnnxDetector(path, os.getenv("TACTIQ_CV_MODEL_FORMAT", "yolox"), size)
