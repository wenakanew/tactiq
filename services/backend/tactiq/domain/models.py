from __future__ import annotations

from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class MatchStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"


class Position(BaseModel):
    x: float = Field(ge=0, le=100)
    y: float = Field(ge=0, le=100)


class Event(BaseModel):
    schema_version: str = "1.0.0"
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    match_id: str
    sequence: int = Field(ge=1)
    match_clock_ms: int = Field(ge=0)
    type: str
    team_id: str
    player_id: str | None = None
    target_player_id: str | None = None
    position: Position | None = None
    end_position: Position | None = None
    qualifiers: dict[str, Any] = Field(default_factory=dict)
    synthetic: bool = True


class MetricSnapshot(BaseModel):
    schema_version: str = "1.0.0"
    match_id: str
    as_of_sequence: int
    as_of_match_clock_ms: int
    window_ms: int = 90_000
    team_id: str
    metrics: dict[str, float | int]


class EvidenceItem(BaseModel):
    metric: str
    value: float | int
    unit: str
    supporting_event_ids: list[str]


class Insight(BaseModel):
    schema_version: str = "1.0.0"
    insight_id: str = Field(default_factory=lambda: str(uuid4()))
    match_id: str
    type: str
    priority: str
    confidence: float = Field(ge=0.0, le=1.0)
    team_id: str
    window_start_ms: int
    window_end_ms: int
    title: str
    summary: str
    evidence: list[EvidenceItem]


class MatchState(BaseModel):
    schema_version: str = "1.0.0"
    match_id: str
    scenario_id: str
    seed: int
    status: MatchStatus = MatchStatus.CREATED
    match_clock_ms: int = 0
    score: dict[str, int] = Field(default_factory=lambda: {"team_a": 0, "team_b": 0})
    possession_team_id: str | None = None
    last_sequence: int = 0
    metrics_by_team: dict[str, dict[str, float | int]] = Field(default_factory=dict)
