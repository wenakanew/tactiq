from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator


class CalibrationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp_ms: int = Field(default=0, ge=0)
    image_points: list[tuple[FiniteFloat, FiniteFloat]] = Field(min_length=4, max_length=64)
    pitch_points: list[tuple[FiniteFloat, FiniteFloat]] = Field(min_length=4, max_length=64)

    @model_validator(mode="after")
    def validate_correspondences(self) -> CalibrationInput:
        if len(self.image_points) != len(self.pitch_points):
            raise ValueError("Image and pitch control points must have equal length")
        if len(set(self.image_points)) < 4 or len(set(self.pitch_points)) < 4:
            raise ValueError("At least four distinct control points are required")
        if any(not (0 <= x <= 100 and 0 <= y <= 100) for x, y in self.pitch_points):
            raise ValueError("Pitch control points use percentages in [0, 100]")
        return self


class CvOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_fps: float = Field(default=8.0, ge=1, le=15, allow_inf_nan=False)
    max_duration_seconds: float = Field(default=180, ge=1, le=600, allow_inf_nan=False)
    calibrations: list[CalibrationInput] = Field(default_factory=list, max_length=100)
    team_colors_rgb: dict[str, tuple[int, int, int]] = Field(default_factory=dict)
    attack_directions: dict[str, Literal[-1, 1]] | None = None

    @model_validator(mode="after")
    def validate_options(self) -> CvOptions:
        times = [item.timestamp_ms for item in self.calibrations]
        if times != sorted(set(times)):
            raise ValueError("Calibration timestamps must be unique and increasing")
        if self.team_colors_rgb and set(self.team_colors_rgb) != {"team_a", "team_b"}:
            raise ValueError("Specify both team_a and team_b jersey colors")
        if any(not 0 <= channel <= 255 for color in self.team_colors_rgb.values() for channel in color):
            raise ValueError("Jersey colors must be RGB values in [0, 255]")
        if self.attack_directions is not None and (
            set(self.attack_directions) != {"team_a", "team_b"} or len(set(self.attack_directions.values())) != 2
        ):
            raise ValueError("Specify opposite, known attack directions for both teams, or leave them unknown")
        return self


class CvTrackPoint(BaseModel):
    track_id: str
    object_class: Literal["player", "ball"]
    team_id: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    frame_idx: int = Field(ge=0)
    timestamp_ms: int = Field(ge=0)
    bbox_xywh: tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat]
    image_position: tuple[FiniteFloat, FiniteFloat] | None = None
    pitch_position: tuple[FiniteFloat, FiniteFloat] | None = None
    team_confidence: float = Field(default=0, ge=0, le=1)
    speed_kmh: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    inferred: bool = False
    scene_id: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_geometry(self) -> CvTrackPoint:
        if self.bbox_xywh[2] <= 0 or self.bbox_xywh[3] <= 0:
            raise ValueError("Tracking bounding boxes must have positive dimensions")
        for point in (self.image_position, self.pitch_position):
            if point is not None and any(not 0 <= coordinate <= 100 for coordinate in point):
                raise ValueError("Normalized positions must lie in [0, 100]")
        return self


class CameraCalibration(BaseModel):
    method: str
    confidence: float = Field(ge=0.0, le=1.0)
    homography: list[list[float]] | None = None
    status: Literal["calibrated", "unavailable"] = "unavailable"
    timestamp_ms: int = 0
    reprojection_error_m: float | None = None
    inlier_count: int = 0
    reason: str | None = None
    revision: int = Field(default=0, ge=0)


class CvIngestResult(BaseModel):
    schema_version: str = "1.0.0"
    extractor: str
    fps: float = Field(gt=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    frame_count: int = Field(ge=0)
    sampled_frames: int = Field(ge=0)
    tracks: list[CvTrackPoint]
    calibration: CameraCalibration
    notes: str
    detection_source: Literal["model", "heuristic"] = "heuristic"
    model_id: str | None = None
    coordinate_space: Literal["pitch_percent", "image_percent", "mixed"] = "image_percent"
    sampled_timestamps_ms: list[int] = Field(default_factory=list)
    calibration_history: list[CameraCalibration] = Field(default_factory=list)
    processed_until_ms: int = 0
    truncated: bool = False
    warnings: list[str] = Field(default_factory=list)
    timing_source: Literal["presentation_timestamps", "frame_rate_approximation"] = "presentation_timestamps"
    scene_changes: list[int] = Field(default_factory=list)
    tracking_quality: dict[str, int | float] = Field(default_factory=dict)
