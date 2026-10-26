from __future__ import annotations

import numpy as np
from pydantic import BaseModel, Field, model_validator
from scipy.optimize import linear_sum_assignment

from tactiq.domain.models import Insight


class LabelledInsightWindow(BaseModel):
    insight_type: str
    team_id: str
    player_id: str | None = None
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_window(self) -> LabelledInsightWindow:
        if self.end_ms <= self.start_ms:
            raise ValueError("Labelled insight windows must have positive duration")
        return self


def evaluate_insights(expected: list[LabelledInsightWindow], predicted: list[Insight]) -> dict:
    overlap = np.zeros((len(expected), len(predicted)))
    for i, label in enumerate(expected):
        for j, insight in enumerate(predicted):
            if label.insight_type != insight.type or label.team_id != insight.team_id:
                continue
            if label.player_id is not None and label.player_id != insight.player_id:
                continue
            intersection = max(0, min(label.end_ms, insight.window_end_ms) - max(label.start_ms, insight.window_start_ms))
            union = max(label.end_ms, insight.window_end_ms) - min(label.start_ms, insight.window_start_ms)
            overlap[i, j] = intersection / union if union else 0
    matched = 0
    if overlap.size:
        rows, cols = linear_sum_assignment(-overlap)
        matched = sum(overlap[row, col] >= 0.5 for row, col in zip(rows, cols, strict=True))
    return {
        "labelled_windows": len(expected), "predicted_windows": len(predicted), "matched": int(matched),
        "false_positives": len(predicted) - int(matched), "false_negatives": len(expected) - int(matched),
        "precision": matched / len(predicted) if predicted else 0,
        "recall": matched / len(expected) if expected else 0,
        "matching": "type/team/player and temporal IoU >= 0.5",
    }
