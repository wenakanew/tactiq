"""Bounded cloud records. Tenant IDs are internal workspaces, not Entra directories.

These contracts do not replace the SQLite/local runtime. Cloud errors propagate;
callers must never recover by silently selecting local storage.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, BinaryIO, Literal, Protocol

from ._validation import RecordValidationError, encode_object, identifier

MAX_STATE_BYTES = 1_048_576
MAX_RECORD_BYTES = 65_536
MAX_BATCH_RECORDS = 100
MAX_PAGE_RECORDS = 100
MAX_PAGE_BYTES = MAX_RECORD_BYTES * MAX_PAGE_RECORDS


class RevisionConflictError(RuntimeError):
    """A stale writer or a conflicting idempotency key was rejected."""


class CloudRecordError(RecordValidationError):
    pass


def bounded_integer(value: int, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise CloudRecordError(f"{label} must be between {minimum} and {maximum}")
    return value


def cloud_identifier(value: str, label: str) -> str:
    identifier(value, label)
    if not all(char.isascii() and (char.isalnum() or char in "_-") for char in value):
        raise CloudRecordError(f"{label} must contain only ASCII letters, digits, _ or -")
    return value


def bounded_json(value: dict[str, Any], maximum: int) -> str:
    encoded = encode_object(value)
    if len(encoded.encode("utf-8")) > maximum:
        raise CloudRecordError("Cloud record exceeds its byte limit")
    _reject_locations(value)
    return encoded


def _reject_locations(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = key.casefold().replace("-", "_")
            if (
                (normalized.endswith(("_path", "_url", "_uri"))
                 and normalized not in {"orchestration_path", "attempted_orchestration_path"})
                or normalized in {"path", "url", "uri", "sas", "connection_string"}
            ):
                raise CloudRecordError("Persist opaque media IDs, not locations or credentials")
            _reject_locations(item)
    elif isinstance(value, list):
        for item in value:
            _reject_locations(item)
    elif isinstance(value, str):
        lowered = value.casefold()
        if (
            "://" in value or "\\\\" in value or ":\\" in value
            or lowered.startswith(("/", "\\"))
            or "sig=" in lowered or "sharedaccesssignature=" in lowered
        ):
            raise CloudRecordError("Cloud records cannot contain raw locations or SAS credentials")


@dataclass(frozen=True)
class Aggregate:
    tenant_id: str
    match_id: str
    revision: int
    state: dict[str, Any]


@dataclass(frozen=True)
class StreamRecord:
    record_id: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class RecordCursor:
    """Scope-bound append-only cursor; revision is frozen on the first page."""

    tenant_id: str
    match_id: str
    stream: Literal["events", "replay"]
    revision: int
    after: int = 0


@dataclass(frozen=True)
class RecordPage:
    records: tuple[StreamRecord, ...]
    cursor: RecordCursor
    has_more: bool


@dataclass(frozen=True)
class MediaRef:
    """Trusted metadata loaded by owner, never an object URL or filesystem path."""

    tenant_id: str
    match_id: str
    media_id: str
    size: int
    content_type: str
    etag: str


class AggregateStore(Protocol):
    def load(self, tenant_id: str, match_id: str) -> Aggregate | None: ...
    def commit(
        self, tenant_id: str, match_id: str, state: dict[str, Any], *,
        expected_revision: int, events: tuple[StreamRecord, ...] = (),
        replay: tuple[StreamRecord, ...] = (),
    ) -> Aggregate: ...
    def page(
        self, tenant_id: str, match_id: str, stream: Literal["events", "replay"], *,
        cursor: RecordCursor | None = None, limit: int = 100,
    ) -> RecordPage | None: ...


class MediaStore(Protocol):
    def upload(
        self, tenant_id: str, match_id: str, source: BinaryIO, *, size: int, content_type: str,
    ) -> MediaRef: ...
    def download(
        self, reference: MediaRef, *, tenant_id: str, match_id: str,
        offset: int = 0, length: int | None = None,
    ) -> Iterator[bytes]: ...
    def delete(self, reference: MediaRef, *, tenant_id: str, match_id: str) -> None: ...
