"""Tenant-isolated aggregate snapshots and durable job metadata in SQLite.

Constructing a store initializes/migrates its database. Importing this module
does not touch disk. Every operation owns its connection and writes use
BEGIN IMMEDIATE transactions. Match IDs have one global tenant owner: an ID
collision cannot replace or disclose another tenant's snapshot.

Snapshots are intentionally aggregate-shaped, not coupled to domain models:
state, events, insights, viewer_profile, commentary audit/memory, source video
path/duration, clips, replay and CV metadata can all be saved in one transaction.
Credential-labelled fields are rejected; callers must not hide credentials in
free text. Media bytes remain outside the metadata database.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from ._validation import (
    RecordValidationError,
    decode_object,
    encode_object,
    identifier,
    positive_integer,
)
from .models import Job


SCHEMA_VERSION = 1
_INITIALIZATION_TIMEOUT_SECONDS = 10.0


class TenantConflictError(PermissionError):
    """A globally owned identifier cannot be assigned to another tenant."""


class StorageVersionError(RuntimeError):
    """The database schema is newer than this runtime supports."""


class StorageCorruptionError(RuntimeError):
    """A stored record failed validation."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SQLiteStore:
    """File-backed storage. Close job runners before closing their store.

    Initialization retries SQLite busy/locked errors for at most ten seconds.
    SQLite's write transaction serializes migrations across threads/processes;
    WAL negotiation can bypass its busy handler and needs explicit retries.
    Other initialization failures propagate immediately.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        raw_path = os.fspath(path)
        if not isinstance(raw_path, str) or not raw_path.strip() or raw_path == ":memory:":
            raise RecordValidationError("SQLiteStore requires a persistent database file path")
        if "\x00" in raw_path:
            raise RecordValidationError("Database path must not contain null characters")
        self.path = Path(raw_path).expanduser().resolve()
        self._state_lock = threading.Lock()
        self._closed = False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self, *, timeout: float = 10.0) -> sqlite3.Connection:
        with self._state_lock:
            if self._closed:
                raise RuntimeError("SQLiteStore is closed")
        connection = sqlite3.connect(str(self.path), timeout=timeout, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA synchronous = FULL")
        except BaseException:
            connection.close()
            raise
        return connection

    @contextmanager
    def _read(self, *, timeout: float = 10.0) -> Iterator[sqlite3.Connection]:
        connection = self._connect(timeout=timeout)
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _write(self, *, timeout: float = 10.0) -> Iterator[sqlite3.Connection]:
        with self._read(timeout=timeout) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def _initialize(self) -> None:
        deadline = time.monotonic() + _INITIALIZATION_TIMEOUT_SECONDS
        delay = 0.01
        while True:
            try:
                self._initialize_once()
                return
            except sqlite3.OperationalError as exc:
                code = getattr(exc, "sqlite_errorcode", None)
                remaining = deadline - time.monotonic()
                if (
                    code is None
                    or code & 0xFF not in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                    or remaining <= 0
                ):
                    raise
                time.sleep(min(delay, remaining))
                if time.monotonic() >= deadline:
                    raise
                delay = min(delay * 2, 0.1)

    def _initialize_once(self) -> None:
        # Nonblocking attempts leave the single deadline in control of lock waits.
        # Contexts roll back/close before any retry, including WAL/commit failures.
        with self._read(timeout=0.0) as connection:
            mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            if mode.lower() != "wal":
                raise RuntimeError("SQLiteStore requires WAL journal mode")
        with self._write(timeout=0.0) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise StorageVersionError("Database schema is newer than this runtime")
            if version == 0:
                statements = (
                    """CREATE TABLE IF NOT EXISTS runtime_tenants (
                        tenant_id TEXT PRIMARY KEY,
                        created_at TEXT NOT NULL
                    )""",
                    """CREATE TABLE IF NOT EXISTS runtime_matches (
                        match_id TEXT PRIMARY KEY,
                        tenant_id TEXT NOT NULL REFERENCES runtime_tenants(tenant_id),
                        snapshot_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )""",
                    """CREATE INDEX IF NOT EXISTS runtime_matches_by_tenant
                        ON runtime_matches(tenant_id, updated_at DESC, match_id)""",
                    """CREATE TABLE IF NOT EXISTS runtime_jobs (
                        job_id TEXT PRIMARY KEY,
                        tenant_id TEXT NOT NULL REFERENCES runtime_tenants(tenant_id),
                        kind TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        status TEXT NOT NULL CHECK (
                            status IN ('queued', 'running', 'succeeded', 'failed',
                                       'cancelled', 'dead_letter')
                        ),
                        result_json TEXT,
                        error TEXT,
                        attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
                        max_attempts INTEGER NOT NULL CHECK (max_attempts >= 1),
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        started_at TEXT,
                        finished_at TEXT,
                        owner_id TEXT,
                        cancel_requested INTEGER NOT NULL DEFAULT 0
                            CHECK (cancel_requested IN (0, 1)),
                        CHECK (attempts <= max_attempts)
                    )""",
                    """CREATE INDEX IF NOT EXISTS runtime_jobs_by_status
                        ON runtime_jobs(status, created_at, job_id)""",
                    """CREATE INDEX IF NOT EXISTS runtime_jobs_by_tenant
                        ON runtime_jobs(tenant_id, job_id)""",
                )
                for statement in statements:
                    connection.execute(statement)
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @staticmethod
    def _tenant(connection: sqlite3.Connection, tenant_id: str, now: str) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO runtime_tenants (tenant_id, created_at) VALUES (?, ?)",
            (tenant_id, now),
        )

    def save_match(self, tenant_id: str, match_id: str, snapshot: dict[str, Any]) -> None:
        identifier(tenant_id, "tenant_id")
        identifier(match_id, "match_id")
        encoded = encode_object(snapshot)
        for field, expected in (("match_id", match_id), ("tenant_id", tenant_id)):
            if field in snapshot and snapshot[field] != expected:
                raise RecordValidationError("Snapshot identity does not match its storage identity")
        now = _now()
        with self._write() as connection:
            owner = connection.execute(
                "SELECT tenant_id FROM runtime_matches WHERE match_id = ?", (match_id,)
            ).fetchone()
            if owner is not None and owner["tenant_id"] != tenant_id:
                raise TenantConflictError("Match identifier is unavailable")
            self._tenant(connection, tenant_id, now)
            connection.execute(
                """INSERT INTO runtime_matches
                    (match_id, tenant_id, snapshot_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(match_id) DO UPDATE SET
                        snapshot_json = excluded.snapshot_json,
                        updated_at = excluded.updated_at
                    WHERE runtime_matches.tenant_id = excluded.tenant_id""",
                (match_id, tenant_id, encoded, now, now),
            )

    @staticmethod
    def _decode(encoded: str) -> dict[str, Any]:
        try:
            return decode_object(encoded)
        except RecordValidationError as exc:
            raise StorageCorruptionError("Stored runtime record failed validation") from exc

    def load_match(self, tenant_id: str, match_id: str) -> dict[str, Any] | None:
        identifier(tenant_id, "tenant_id")
        identifier(match_id, "match_id")
        with self._read() as connection:
            row = connection.execute(
                """SELECT snapshot_json FROM runtime_matches
                    WHERE tenant_id = ? AND match_id = ?""",
                (tenant_id, match_id),
            ).fetchone()
        return None if row is None else self._decode(row["snapshot_json"])

    def list_matches(self, tenant_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """Return metadata, newest first; use load_match for aggregate contents."""
        identifier(tenant_id, "tenant_id")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 0 <= limit <= 1000:
            raise RecordValidationError("limit must be an integer between 0 and 1000")
        with self._read() as connection:
            rows = connection.execute(
                """SELECT tenant_id, match_id, created_at, updated_at FROM runtime_matches
                    WHERE tenant_id = ? ORDER BY updated_at DESC, match_id LIMIT ?""",
                (tenant_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_match(self, tenant_id: str, match_id: str) -> bool:
        identifier(tenant_id, "tenant_id")
        identifier(match_id, "match_id")
        with self._write() as connection:
            deleted = connection.execute(
                "DELETE FROM runtime_matches WHERE tenant_id = ? AND match_id = ?",
                (tenant_id, match_id),
            ).rowcount
        return bool(deleted)

    def healthcheck(self) -> dict[str, Any]:
        with self._read() as connection:
            return {
                "ok": connection.execute("SELECT 1").fetchone()[0] == 1,
                "schema_version": connection.execute("PRAGMA user_version").fetchone()[0],
                "journal_mode": connection.execute("PRAGMA journal_mode").fetchone()[0],
                "foreign_keys": bool(connection.execute("PRAGMA foreign_keys").fetchone()[0]),
            }

    def close(self) -> None:
        """Disallow new operations; connections already in flight finish normally."""
        with self._state_lock:
            self._closed = True

    def __enter__(self) -> SQLiteStore:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    @classmethod
    def _job(cls, row: sqlite3.Row | None) -> Job | None:
        if row is None:
            return None
        try:
            return Job(
                job_id=row["job_id"],
                tenant_id=row["tenant_id"],
                kind=row["kind"],
                payload=cls._decode(row["payload_json"]),
                status=row["status"],
                attempts=row["attempts"],
                max_attempts=row["max_attempts"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                started_at=row["started_at"],
                finished_at=row["finished_at"],
                result=None if row["result_json"] is None else cls._decode(row["result_json"]),
                error=row["error"],
                cancel_requested=bool(row["cancel_requested"]),
            )
        except (ValidationError, KeyError, TypeError) as exc:
            raise StorageCorruptionError("Stored job failed validation") from exc

    def _create_job(
        self,
        tenant_id: str,
        kind: str,
        payload: dict[str, Any],
        max_attempts: int,
        *,
        job_id: str | None = None,
    ) -> Job:
        identifier(tenant_id, "tenant_id")
        identifier(kind, "kind")
        positive_integer(max_attempts, "max_attempts")
        job_id = uuid4().hex if job_id is None else identifier(job_id, "job_id")
        encoded = encode_object(payload)
        now = _now()
        with self._write() as connection:
            self._tenant(connection, tenant_id, now)
            connection.execute(
                """INSERT INTO runtime_jobs
                    (job_id, tenant_id, kind, payload_json, status, max_attempts,
                     created_at, updated_at)
                    VALUES (?, ?, ?, ?, 'queued', ?, ?, ?)""",
                (job_id, tenant_id, kind, encoded, max_attempts, now, now),
            )
            job = self._job(
                connection.execute(
                    "SELECT * FROM runtime_jobs WHERE job_id = ?", (job_id,)
                ).fetchone()
            )
        assert job is not None
        return job

    def _get_job(self, tenant_id: str, job_id: str) -> Job | None:
        identifier(tenant_id, "tenant_id")
        identifier(job_id, "job_id")
        with self._read() as connection:
            return self._job(
                connection.execute(
                    "SELECT * FROM runtime_jobs WHERE tenant_id = ? AND job_id = ?",
                    (tenant_id, job_id),
                ).fetchone()
            )

    def _queued_jobs(self, limit: int) -> list[Job]:
        with self._read() as connection:
            rows = connection.execute(
                """SELECT * FROM runtime_jobs WHERE status = 'queued'
                    ORDER BY created_at, job_id LIMIT ?""",
                (limit,),
            ).fetchall()
        return [job for row in rows if (job := self._job(row)) is not None]

    def active_video_inputs(self) -> set[str]:
        with self._read() as connection:
            rows = connection.execute(
                """SELECT payload_json FROM runtime_jobs
                   WHERE kind = 'video' AND status IN ('queued', 'running')"""
            ).fetchall()
        return {
            payload["input_path"]
            for row in rows if isinstance((payload := self._decode(row["payload_json"])).get("input_path"), str)
        }

    def _queued_count(self) -> int:
        with self._read() as connection:
            return connection.execute(
                "SELECT COUNT(*) FROM runtime_jobs WHERE status = 'queued'"
            ).fetchone()[0]

    def _claim_job(self, job_id: str, owner_id: str) -> Job | None:
        now = _now()
        with self._write() as connection:
            claimed = connection.execute(
                """UPDATE runtime_jobs SET status = 'running', attempts = attempts + 1,
                        owner_id = ?, started_at = COALESCE(started_at, ?), updated_at = ?,
                        error = NULL
                    WHERE job_id = ? AND status = 'queued'
                        AND cancel_requested = 0 AND attempts < max_attempts""",
                (owner_id, now, now, job_id),
            ).rowcount
            if not claimed:
                return None
            return self._job(
                connection.execute(
                    "SELECT * FROM runtime_jobs WHERE job_id = ?", (job_id,)
                ).fetchone()
            )

    def _finish_job(
        self,
        job_id: str,
        owner_id: str,
        *,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> Job | None:
        if status not in {"succeeded", "failed", "dead_letter"}:
            raise RecordValidationError("Invalid terminal job status")
        if status == "succeeded" and result is None:
            raise RecordValidationError("Successful jobs must have a JSON object result")
        encoded = None if result is None else encode_object(result)
        now = _now()
        with self._write() as connection:
            row = connection.execute(
                """SELECT * FROM runtime_jobs
                    WHERE job_id = ? AND owner_id = ? AND status = 'running'""",
                (job_id, owner_id),
            ).fetchone()
            if row is None:
                return None
            if row["cancel_requested"]:
                status, encoded, error = "cancelled", None, None
            connection.execute(
                """UPDATE runtime_jobs SET status = ?, result_json = ?, error = ?,
                        updated_at = ?, finished_at = ?, owner_id = NULL WHERE job_id = ?""",
                (status, encoded, error, now, now, job_id),
            )
            return self._job(
                connection.execute(
                    "SELECT * FROM runtime_jobs WHERE job_id = ?", (job_id,)
                ).fetchone()
            )

    def _retry_job(self, job_id: str, owner_id: str) -> Job | None:
        now = _now()
        with self._write() as connection:
            row = connection.execute(
                """SELECT * FROM runtime_jobs
                    WHERE job_id = ? AND owner_id = ? AND status = 'running'""",
                (job_id, owner_id),
            ).fetchone()
            if row is None:
                return None
            if row["cancel_requested"]:
                status, error = "cancelled", None
            elif row["attempts"] >= row["max_attempts"]:
                status, error = "dead_letter", "Job exhausted its retry budget."
            else:
                status, error = "queued", None
            connection.execute(
                """UPDATE runtime_jobs SET status = ?, error = ?, owner_id = NULL,
                        updated_at = ?, finished_at = ? WHERE job_id = ?""",
                (status, error, now, None if status == "queued" else now, job_id),
            )
            return self._job(
                connection.execute(
                    "SELECT * FROM runtime_jobs WHERE job_id = ?", (job_id,)
                ).fetchone()
            )

    def _cancel_job(self, tenant_id: str, job_id: str) -> Job | None:
        identifier(tenant_id, "tenant_id")
        identifier(job_id, "job_id")
        now = _now()
        with self._write() as connection:
            row = connection.execute(
                "SELECT * FROM runtime_jobs WHERE tenant_id = ? AND job_id = ?",
                (tenant_id, job_id),
            ).fetchone()
            if row is None:
                return None
            if row["status"] in {"queued", "running"}:
                queued = row["status"] == "queued"
                connection.execute(
                    """UPDATE runtime_jobs SET cancel_requested = 1, status = ?,
                            updated_at = ?, finished_at = ?, owner_id = ?
                        WHERE tenant_id = ? AND job_id = ?""",
                    (
                        "cancelled" if queued else "running",
                        now,
                        now if queued else None,
                        None if queued else row["owner_id"],
                        tenant_id,
                        job_id,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM runtime_jobs WHERE tenant_id = ? AND job_id = ?",
                    (tenant_id, job_id),
                ).fetchone()
            return self._job(row)

    def _fail_queued_job(self, job_id: str) -> None:
        now = _now()
        with self._write() as connection:
            connection.execute(
                """UPDATE runtime_jobs SET status = 'failed', updated_at = ?, finished_at = ?,
                        error = 'Job could not be scheduled.', owner_id = NULL
                    WHERE job_id = ? AND status = 'queued'""",
                (now, now, job_id),
            )

    def _recover_jobs(self, owner_id: str) -> None:
        """Caller must hold the exclusive local runner lease."""
        now = _now()
        with self._write() as connection:
            connection.execute(
                """UPDATE runtime_jobs SET status = 'cancelled', owner_id = NULL,
                        updated_at = ?, finished_at = ?
                    WHERE (status = 'queued' OR
                        (status = 'running' AND (owner_id IS NULL OR owner_id != ?)))
                        AND cancel_requested = 1""",
                (now, now, owner_id),
            )
            connection.execute(
                """UPDATE runtime_jobs SET status = 'dead_letter', owner_id = NULL,
                        updated_at = ?, finished_at = ?,
                        error = 'Job exhausted its retry budget.'
                    WHERE (status = 'queued' OR
                        (status = 'running' AND (owner_id IS NULL OR owner_id != ?)))
                        AND attempts >= max_attempts""",
                (now, now, owner_id),
            )
            connection.execute(
                """UPDATE runtime_jobs SET status = 'queued', owner_id = NULL,
                        updated_at = ?, finished_at = NULL, error = NULL
                    WHERE status = 'running' AND (owner_id IS NULL OR owner_id != ?)
                        AND cancel_requested = 0 AND attempts < max_attempts""",
                (now, owner_id),
            )
