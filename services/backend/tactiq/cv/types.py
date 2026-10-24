from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CvTrackPoint(BaseModel):
    track_id: str
    object_class: Literal["player", "ball"]
    team_id: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    frame_idx: int = Field(ge=0)
    timestamp_ms: int = Field(ge=0)
    bbox_xywh: tuple[float, float, float, float]
    pitch_position: tuple[float, float] | None = None
    inferred: bool = False


class CameraCalibration(BaseModel):
    method: str
    confidence: float = Field(ge=0.0, le=1.0)
    homography: list[list[float]] | None = None


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
