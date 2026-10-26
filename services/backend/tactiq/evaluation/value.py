from __future__ import annotations

from typing import Literal

import numpy as np
from pydantic import BaseModel, Field
from scipy.optimize import minimize
from scipy.special import expit

from tactiq.domain.models import Position
from tactiq.intelligence.value import ValueModel, shot_features


class LabelledAction(BaseModel):
    match_id: str = Field(min_length=1)
    kind: Literal["move", "shot", "turnover"]
    position: Position
    end_position: Position | None = None
    attack_direction: Literal[-1, 1] = 1
    goal: bool = False


def fit_value_model(actions: list[LabelledAction], version: str, training_source: str) -> ValueModel:
    shots = [action for action in actions if action.kind == "shot"]
    labels = np.array([int(action.goal) for action in shots], dtype=float)
    if len(shots) < 20 or labels.sum() < 3 or (1 - labels).sum() < 3:
        raise ValueError("Value-model training needs at least 20 labelled shots and both outcome classes")
    features = np.array([[1.0, *shot_features(action.position, action.attack_direction)] for action in shots])

    def loss(weights: np.ndarray) -> tuple[float, np.ndarray]:
        logits = features @ weights
        penalty = 0.001 * np.sum(weights[1:] ** 2)
        objective = np.mean(np.logaddexp(0, logits) - labels * logits) + penalty
        gradient = features.T @ (expit(logits) - labels) / len(labels)
        gradient[1:] += 0.002 * weights[1:]
        return float(objective), gradient

    fitted = minimize(loss, [-1.0, -0.09, 1.2], jac=True, method="L-BFGS-B",
                      bounds=[(-20, 20), (-2, 0), (0, 20)])
    if not fitted.success or not np.all(np.isfinite(fitted.x)):
        raise ValueError("Value-model optimization did not converge")
    rows, cols = 8, 12
    cells = rows * cols
    totals = np.zeros(cells)
    shot_counts = np.zeros(cells)
    movements = np.zeros((cells, cells))

    def cell(point: Position, direction: int) -> int:
        x = point.x if direction == 1 else 100 - point.x
        return min(rows - 1, int(point.y / 100 * rows)) * cols + min(cols - 1, int(x / 100 * cols))

    for action in actions:
        start = cell(action.position, action.attack_direction)
        totals[start] += 1
        if action.kind == "shot":
            shot_counts[start] += 1
        elif action.kind == "move":
            if action.end_position is None:
                raise ValueError("Move training samples require an end position")
            movements[start, cell(action.end_position, action.attack_direction)] += 1
    shoot_probability = shot_counts / np.maximum(1, totals)
    transition = movements / np.maximum(1, totals)[:, None]
    scoring_probability = np.array([
        expit(np.dot(fitted.x, [1, *shot_features(Position(x=(col + 0.5) / cols * 100,
                                                         y=(row + 0.5) / rows * 100), 1)]))
        for row in range(rows) for col in range(cols)
    ])
    threat = np.zeros(cells)
    for _ in range(1000):
        following = shoot_probability * scoring_probability + 0.99 * transition @ threat
        if np.max(np.abs(following - threat)) < 1e-10:
            threat = following
            break
        threat = following
    return ValueModel(
        version=version, training_source=training_source, validated=False,
        training_match_ids=sorted({action.match_id for action in actions}), training_sample_count=len(actions),
        xt_grid=np.clip(threat.reshape(rows, cols), 0, 1).tolist(),
        xg_intercept=float(fitted.x[0]), xg_distance=float(fitted.x[1]), xg_angle=float(fitted.x[2]),
    )


def score_value_model(model: ValueModel, actions: list[LabelledAction]) -> dict:
    if set(model.training_match_ids).intersection(action.match_id for action in actions):
        raise ValueError("Validation matches must be disjoint from training matches")
    shots = [action for action in actions if action.kind == "shot"]
    if not shots:
        raise ValueError("Value-model evaluation requires held-out labelled shots")
    probabilities = np.array([model.shot_probability(action.position, action.attack_direction) for action in shots])
    outcomes = np.array([int(action.goal) for action in shots])
    calibration_error = 0.0
    for lower in np.arange(0, 1, 0.1):
        mask = (probabilities >= lower) & (probabilities < lower + 0.1)
        if mask.any():
            calibration_error += float(mask.mean() * abs(probabilities[mask].mean() - outcomes[mask].mean()))
    return {
        "model_version": model.version, "heldout_matches": sorted({action.match_id for action in actions}),
        "shot_samples": len(shots), "brier_score": float(np.mean((probabilities - outcomes) ** 2)),
        "expected_calibration_error": calibration_error,
        "model_validated": False,
        "note": "A held-out score is evidence for review, not automatic approval for production.",
    }
