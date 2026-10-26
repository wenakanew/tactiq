from __future__ import annotations

from collections import Counter, defaultdict
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator
from scipy.optimize import linear_sum_assignment

from tactiq.cv.calibration import project_point
from tactiq.cv.types import CameraCalibration, CvTrackPoint


class AnnotatedObject(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    object_id: str = Field(min_length=1)
    object_class: Literal["player", "ball"]
    bbox_xywh: tuple[float, float, float, float]
    team_id: str | None = None
    visible: bool = True

    @model_validator(mode="after")
    def validate_box(self) -> AnnotatedObject:
        if self.bbox_xywh[2] <= 0 or self.bbox_xywh[3] <= 0:
            raise ValueError("Annotated bounding boxes require positive dimensions")
        return self


class AnnotatedFrame(BaseModel):
    model_config = ConfigDict(extra="forbid")
    frame_idx: int = Field(ge=0)
    timestamp_ms: int = Field(ge=0)
    objects: list[AnnotatedObject]

    @model_validator(mode="after")
    def validate_identities(self) -> AnnotatedFrame:
        identities = [(item.object_class, item.object_id) for item in self.objects]
        if len(set(identities)) != len(identities):
            raise ValueError("An object identity may appear only once in an annotated frame")
        return self


def box_iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    intersection = max(0.0, min(ax + aw, bx + bw) - max(ax, bx)) * max(0.0, min(ay + ah, by + bh) - max(ay, by))
    union = max(0, aw) * max(0, ah) + max(0, bw) * max(0, bh) - intersection
    return intersection / union if union else 0.0


def evaluate_tracking(frames: list[AnnotatedFrame], predictions: list[CvTrackPoint]) -> dict:
    if not frames:
        raise ValueError("Tracking evaluation requires annotated frames, including empty frames")
    if [frame.frame_idx for frame in frames] != sorted({frame.frame_idx for frame in frames}):
        raise ValueError("Annotated frames must be unique and ordered")
    if [frame.timestamp_ms for frame in frames] != sorted({frame.timestamp_ms for frame in frames}):
        raise ValueError("Annotated timestamps must be unique and increasing")
    grouped = defaultdict(list)
    for point in predictions:
        if not point.inferred:
            grouped[point.frame_idx].append(point)
    true_count = predicted_count = matched_count = false_positive = false_negative = switches = 0
    team_total = team_correct = 0
    previous_id = {}
    identity_counts = Counter()
    player_identity_counts = Counter()
    player_truth = 0
    recovered_at = defaultdict(list)
    occlusion_endings = []
    occlusion_starts = defaultdict(list)
    was_hidden = set()
    for frame in frames:
        visible = [obj for obj in frame.objects if obj.visible]
        predicted = grouped[frame.frame_idx]
        for obj in frame.objects:
            if obj.object_class == "ball":
                if not obj.visible:
                    if obj.object_id not in was_hidden:
                        occlusion_starts[obj.object_id].append(frame.timestamp_ms)
                    was_hidden.add(obj.object_id)
                elif obj.object_id in was_hidden:
                    occlusion_endings.append((obj.object_id, frame.timestamp_ms))
                    was_hidden.remove(obj.object_id)
        true_count += len(visible)
        player_truth += sum(obj.object_class == "player" for obj in visible)
        predicted_count += len(predicted)
        costs = np.ones((len(visible), len(predicted)))
        for i, obj in enumerate(visible):
            for j, point in enumerate(predicted):
                if obj.object_class == point.object_class:
                    costs[i, j] = 1 - box_iou(obj.bbox_xywh, point.bbox_xywh)
        assigned = 0
        if costs.size:
            rows, cols = linear_sum_assignment(costs)
            for i, j in zip(rows, cols, strict=True):
                if costs[i, j] > 0.5:
                    continue
                obj, point = visible[int(i)], predicted[int(j)]
                assigned += 1
                truth_id = obj.object_class, obj.object_id
                predicted_id = point.object_class, point.track_id
                identity_counts[(truth_id, predicted_id)] += 1
                if obj.object_class == "player":
                    player_identity_counts[(truth_id, predicted_id)] += 1
                if truth_id in previous_id and previous_id[truth_id] != predicted_id:
                    switches += 1
                previous_id[truth_id] = predicted_id
                if obj.team_id is not None:
                    team_total += 1
                    team_correct += point.team_id == obj.team_id
                if obj.object_class == "ball":
                    recovered_at[obj.object_id].append(frame.timestamp_ms)
        matched_count += assigned
        false_negative += len(visible) - assigned
        false_positive += len(predicted) - assigned

    def identity_true_positives(counts: Counter) -> int:
        truth_ids = sorted({pair[0] for pair in counts})
        track_ids = sorted({pair[1] for pair in counts})
        if not truth_ids or not track_ids:
            return 0
        matrix = np.array([[counts[(truth, track)] for track in track_ids] for truth in truth_ids])
        rows, cols = linear_sum_assignment(-matrix)
        return int(matrix[rows, cols].sum())

    identity_correct = identity_true_positives(identity_counts)
    recovery_delays = []
    unrecovered = 0
    for object_id, ending in occlusion_endings:
        following_occlusion = min((start for start in occlusion_starts[object_id] if start > ending), default=float("inf"))
        later = [timestamp for timestamp in recovered_at[object_id] if ending <= timestamp < following_occlusion]
        if later:
            recovery_delays.append(min(later) - ending)
        else:
            unrecovered += 1
    duration = max(frame.timestamp_ms for frame in frames) - min(frame.timestamp_ms for frame in frames)
    return {
        "annotated_frames": len(frames), "duration_ms": duration,
        "ground_truth_observations": true_count, "prediction_observations": predicted_count,
        "matched": matched_count, "false_positives": false_positive, "false_negatives": false_negative,
        "identity_switches": switches,
        "precision": matched_count / predicted_count if predicted_count else 0,
        "recall": matched_count / true_count if true_count else 0,
        "idf1": 2 * identity_correct / (true_count + predicted_count) if true_count + predicted_count else 0,
        "mota": 1 - (false_negative + false_positive + switches) / true_count if true_count else 0,
        "player_id_consistency": identity_true_positives(player_identity_counts) / player_truth if player_truth else None,
        "team_classification_accuracy": team_correct / team_total if team_total else None,
        "team_labelled_observations": team_total,
        "occlusion_cases": len(occlusion_endings),
        "unrecovered_occlusions": unrecovered,
        "ball_recovery_max_ms": max(recovery_delays) if recovery_delays else None,
        "unscored_prediction_frames": len(set(grouped) - {frame.frame_idx for frame in frames}),
    }


def evaluate_calibration(calibration: CameraCalibration, holdouts: list[dict]) -> dict:
    if not holdouts:
        raise ValueError("Calibration evaluation requires independent held-out landmarks")
    errors = []
    for item in holdouts:
        point = project_point(calibration, *item["image_point"])
        if point is not None:
            expected = np.asarray(item["pitch_point"], dtype=float)
            errors.append(float(np.linalg.norm((np.asarray(point) - expected) * [1.05, 0.68])))
    return {
        "holdout_count": len(holdouts), "projected_count": len(errors),
        "coverage": len(errors) / len(holdouts),
        "rmse_m": float(np.sqrt(np.mean(np.square(errors)))) if errors else None,
        "max_error_m": max(errors) if errors else None,
    }
