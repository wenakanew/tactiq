"""Serializable public models without application/service imports."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled", "dead_letter"]


class Job(BaseModel):
    """An immutable job envelope; payload/result are detached JSON snapshots."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str
    tenant_id: str
    kind: str
    payload: dict[str, Any]
    status: JobStatus
    attempts: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    created_at: str
    updated_at: str
    started_at: str | None = None
    finished_at: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    cancel_requested: bool = False

    @model_validator(mode="after")
    def validate_attempts(self) -> Job:
        if self.attempts > self.max_attempts:
            raise ValueError("Job attempts cannot exceed max_attempts")
        return self

    @property
    def id(self) -> str:
        return self.job_id
