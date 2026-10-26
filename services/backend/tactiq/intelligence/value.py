from __future__ import annotations

import json
from math import atan2, exp, hypot, pi
import os
from pathlib import Path

import numpy as np
from pydantic import BaseModel, Field, model_validator

from tactiq.domain.models import Position


def shot_features(position: Position, direction: int) -> tuple[float, float]:
    x = position.x if direction == 1 else 100 - position.x
    distance_x = max(0.01, (100 - x) * 1.05)
    distance_y = (position.y - 50) * 0.68
    angle = abs(atan2(3.66 - distance_y, distance_x) - atan2(-3.66 - distance_y, distance_x))
    return hypot(distance_x, distance_y), min(angle, 2 * pi - angle)


class ValueModel(BaseModel):
    version: str
    training_source: str
    validated: bool = False
    validation_reference: str | None = None
    training_match_ids: list[str] = Field(default_factory=list)
    training_sample_count: int = Field(default=0, ge=0)
    xt_grid: list[list[float]] = Field(min_length=2)
    xg_intercept: float = Field(allow_inf_nan=False)
    xg_distance: float = Field(allow_inf_nan=False)
    xg_angle: float = Field(allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_model(self) -> ValueModel:
        grid = np.asarray(self.xt_grid, dtype=float)
        if grid.ndim != 2 or min(grid.shape) < 2 or not np.all(np.isfinite(grid)) or np.any((grid < 0) | (grid > 1)):
            raise ValueError("xT grid must be a rectangular probability grid in [0, 1]")
        if self.validated and not self.validation_reference:
            raise ValueError("A validated value model requires a benchmark report reference")
        return self

    def threat(self, position: Position, direction: int) -> float:
        x = position.x if direction == 1 else 100 - position.x
        row = min(len(self.xt_grid) - 1, int(position.y / 100 * len(self.xt_grid)))
        col = min(len(self.xt_grid[0]) - 1, int(x / 100 * len(self.xt_grid[0])))
        return self.xt_grid[row][col]

    def shot_probability(self, position: Position, direction: int) -> float:
        distance, angle = shot_features(position, direction)
        logit = self.xg_intercept + self.xg_distance * distance + self.xg_angle * angle
        return 1 / (1 + exp(-max(-40, min(40, logit))))

    @classmethod
    def reference(cls) -> ValueModel:
        # Deliberately unvalidated geometric reference, never a learned league model.
        grid = []
        for row in range(8):
            centrality = 1 - abs((row + 0.5) / 8 * 2 - 1)
            grid.append([round(0.002 + ((col + 0.5) / 12) ** 4 * (0.06 + centrality * 0.12), 5) for col in range(12)])
        return cls(
            version="geometric-reference-v1", training_source="untrained-geometric-reference",
            xt_grid=grid, xg_intercept=-1.0, xg_distance=-0.09, xg_angle=1.2,
        )


def configured_value_model() -> ValueModel | None:
    path = os.getenv("TACTIQ_VALUE_MODEL_PATH", "").strip()
    if path:
        model = ValueModel.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))
        if os.getenv("APP_ENV", "local").lower() == "production" and not model.validated:
            raise ValueError("Production requires a benchmarked value model")
        return model
    if os.getenv("APP_ENV", "local").lower() == "production":
        return None
    return ValueModel.reference()
