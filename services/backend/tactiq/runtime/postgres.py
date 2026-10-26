"""PostgreSQL authoritative, bounded storage and CloudWorker execution adapter.

Migration is explicit. Each operation owns a fresh connection/transaction.
Database failure propagates; this module never touches SQLite or local media.
Workers must be idempotent: fencing protects database completion, not external
effects already performed by a worker that subsequently lost its lease.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from ._validation import identifier
from .cloud_worker import ExecutionClaim, OutboxClaim, WorkReference
from .contracts import (
    MAX_BATCH_RECORDS, MAX_PAGE_RECORDS, MAX_RECORD_BYTES, MAX_STATE_BYTES,
    Aggregate, CloudRecordError, MediaRef, RecordCursor, RecordPage,
    RevisionConflictError, StreamRecord, bounded_integer, bounded_json, cloud_identifier,
)


class PostgresStore:
    def __init__(self, connect: Callable[[], Any], *, max_running: int | None = None) -> None:
        """connect returns a NEW psycopg3 connection with dict_row row_factory.

        An injected factory allows managed-identity token refresh at connection
        creation. It must not return an existing transaction or autocommit=False
        connection shared with another operation.
        """
        if max_running is not None:
            bounded_integer(max_running, "max_running", 1, 100)
        self._connect = connect
        self._max_running = max_running

    @classmethod
    def from_dsn(cls, dsn: str) -> PostgresStore:
        """DSN is private configuration, never persisted in a runtime record."""
        from psycopg import connect
        from psycopg.rows import dict_row

        return cls(lambda: connect(dsn, row_factory=dict_row, connect_timeout=10))

    @contextmanager
    def _transaction(self) -> Iterator[Any]:
        with self._connect() as connection:
            with connection.transaction():
                connection.execute("SELECT set_config('statement_timeout', %s, true)", ("15000",))
                connection.execute("SELECT set_config('lock_timeout', %s, true)", ("5000",))
                yield connection

    def migrate(self) -> None:
        """Run only using an explicitly approved migration-capable identity."""
        with self._transaction() as connection:
            connection.execute("SELECT pg_advisory_xact_lock(854917020601)")
            exists = connection.execute(
                "SELECT to_regclass('tactiq_cloud_schema') AS name"
            ).fetchone()["name"]
            if exists:
                row = connection.execute("SELECT version FROM tactiq_cloud_schema").fetchone()
                if row is None or row["version"] != 1:
                    raise RuntimeError("Unsupported cloud schema version")
                return
            migration = Path(__file__).with_name("migrations") / "001_cloud.sql"
            connection.execute(migration.read_text(encoding="utf-8"))

    @staticmethod
    def _scope(tenant_id: str, match_id: str) -> None:
        cloud_identifier(tenant_id, "tenant_id")
        cloud_identifier(match_id, "match_id")

    def load(self, tenant_id: str, match_id: str) -> Aggregate | None:
        self._scope(tenant_id, match_id)
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT revision, state FROM tactiq_cloud_matches WHERE tenant_id=%s AND match_id=%s",
                (tenant_id, match_id),
            ).fetchone()
        return None if row is None else Aggregate(tenant_id, match_id, row["revision"], row["state"])

    def commit(
        self, tenant_id: str, match_id: str, state: dict[str, Any], *,
        expected_revision: int, events: tuple[StreamRecord, ...] = (),
        replay: tuple[StreamRecord, ...] = (),
        claim: ExecutionClaim | None = None, owner: str | None = None,
    ) -> Aggregate:
        """Create with revision=0; append and CAS state in one transaction.

        State excludes events/replay so reads never restore the full history.
        Duplicate record IDs fail atomically, including the state update.
        Workers MUST supply claim+owner to fence aggregate writes as well as
        completion. A checkpoint alone cannot close an expired-lease race.
        """
        self._scope(tenant_id, match_id)
        bounded_integer(expected_revision, "expected_revision", 0, 2**63 - 2)
        if "events" in state or "replay" in state:
            raise CloudRecordError("Use bounded event/replay streams, not aggregate arrays")
        encoded = bounded_json(state, MAX_STATE_BYTES)
        if (claim is None) != (owner is None):
            raise CloudRecordError("Worker claim and owner must be provided together")
        if claim is not None and claim.tenant_id != tenant_id:
            raise CloudRecordError("Worker claim does not own this workspace")
        encoded_streams = {}
        for stream, records in (("events", events), ("replay", replay)):
            bounded_integer(len(records), "record batch", 0, MAX_BATCH_RECORDS)
            encoded_streams[stream] = [
                (cloud_identifier(record.record_id, "record_id"), bounded_json(record.payload, MAX_RECORD_BYTES))
                for record in records
            ]
        with self._transaction() as connection:
            if claim is not None:
                params = self._claim_params(claim, owner)
                owned = connection.execute(
                    """SELECT job_id FROM tactiq_cloud_jobs
                       WHERE tenant_id=%s AND job_id=%s AND fence=%s AND lease_owner=%s
                       AND status='running' AND NOT cancel_requested AND lease_until>clock_timestamp()
                       FOR UPDATE""", params,
                ).fetchone()
                if owned is None:
                    raise RevisionConflictError("Worker aggregate write lost its lease")
            connection.execute(
                """INSERT INTO tactiq_cloud_matches(tenant_id,match_id,revision,state)
                   VALUES (%s,%s,0,'{}'::jsonb) ON CONFLICT DO NOTHING""",
                (tenant_id, match_id),
            )
            row = connection.execute(
                """UPDATE tactiq_cloud_matches SET state=%s::jsonb, revision=revision+1,
                   updated_at=clock_timestamp()
                   WHERE tenant_id=%s AND match_id=%s AND revision=%s RETURNING revision""",
                (encoded, tenant_id, match_id, expected_revision),
            ).fetchone()
            if row is None:
                raise RevisionConflictError("Aggregate revision changed")
            revision = row["revision"]
            for stream, records in encoded_streams.items():
                if not records:
                    continue
                position = connection.execute(
                    """SELECT COALESCE(MAX(position),0) AS position FROM tactiq_cloud_records
                       WHERE tenant_id=%s AND match_id=%s AND stream=%s""",
                    (tenant_id, match_id, stream),
                ).fetchone()["position"]
                for offset, (record_id, payload) in enumerate(records, 1):
                    connection.execute(
                        """INSERT INTO tactiq_cloud_records
                           (tenant_id,match_id,stream,position,revision,record_id,payload)
                           VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb)""",
                        (tenant_id, match_id, stream, position + offset, revision, record_id, payload),
                    )
            if claim is not None:
                live = connection.execute(
                    """SELECT job_id FROM tactiq_cloud_jobs
                       WHERE tenant_id=%s AND job_id=%s AND fence=%s AND lease_owner=%s
                       AND status='running' AND NOT cancel_requested AND lease_until>clock_timestamp()""",
                    self._claim_params(claim, owner),
                ).fetchone()
                if live is None:
                    raise RevisionConflictError("Worker lease expired during aggregate write")
        return Aggregate(tenant_id, match_id, revision, json.loads(encoded))

    def page(
        self, tenant_id: str, match_id: str, stream: Literal["events", "replay"], *,
        cursor: RecordCursor | None = None, limit: int = 100,
    ) -> RecordPage | None:
        self._scope(tenant_id, match_id)
        bounded_integer(limit, "limit", 1, MAX_PAGE_RECORDS)
        if stream not in ("events", "replay"):
            raise CloudRecordError("Unknown stream")
        if cursor is not None:
            if (cursor.tenant_id, cursor.match_id, cursor.stream) != (tenant_id, match_id, stream):
                raise CloudRecordError("Cursor does not belong to this stream owner")
            bounded_integer(cursor.revision, "cursor revision", 1, 2**63 - 1)
            bounded_integer(cursor.after, "cursor position", 0, 2**63 - 1)
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT revision FROM tactiq_cloud_matches WHERE tenant_id=%s AND match_id=%s",
                (tenant_id, match_id),
            ).fetchone()
            if row is None:
                return None
            revision = row["revision"] if cursor is None else cursor.revision
            if revision > row["revision"]:
                raise CloudRecordError("Cursor revision is ahead of committed state")
            after = 0 if cursor is None else cursor.after
            # Bound the index scan too: a completed frozen cursor must not scan
            # every record appended by other writers after its frozen revision.
            highest = connection.execute(
                """SELECT position FROM tactiq_cloud_records
                   WHERE tenant_id=%s AND match_id=%s AND stream=%s AND revision<=%s
                   ORDER BY revision DESC,position DESC LIMIT 1""",
                (tenant_id, match_id, stream, revision),
            ).fetchone()
            through = 0 if highest is None else highest["position"]
            rows = connection.execute(
                """SELECT position,record_id,payload FROM tactiq_cloud_records
                   WHERE tenant_id=%s AND match_id=%s AND stream=%s
                   AND position>%s AND position<=%s AND revision<=%s
                   ORDER BY position LIMIT %s""",
                (tenant_id, match_id, stream, after, through, revision, limit + 1),
            ).fetchall()
        selected = rows[:limit]
        next_cursor = RecordCursor(
            tenant_id, match_id, stream, revision, selected[-1]["position"] if selected else after,
        )
        return RecordPage(
            tuple(StreamRecord(row["record_id"], row["payload"]) for row in selected),
            next_cursor, len(rows) > limit,
        )

    @staticmethod
    def _enqueue(connection: Any, tenant_id: str, job_id: str) -> None:
        payload = json.dumps({"version": 1, "tenant_id": tenant_id, "job_id": job_id})
        connection.execute(
            """INSERT INTO tactiq_cloud_outbox(message_id,tenant_id,job_id,payload)
               VALUES (%s,%s,%s,%s::jsonb)""",
            (uuid4().hex, tenant_id, job_id, payload),
        )

    def admit_job(
        self, tenant_id: str, job_id: str, kind: str, payload: dict[str, Any], *,
        max_attempts: int = 3,
    ) -> bool:
        """True for first admission, False for an identical idempotent retry."""
        self._scope(tenant_id, job_id)
        cloud_identifier(kind, "kind")
        bounded_integer(max_attempts, "max_attempts", 1, 10)
        encoded = bounded_json(payload, MAX_RECORD_BYTES)
        with self._transaction() as connection:
            row = connection.execute(
                """INSERT INTO tactiq_cloud_jobs(tenant_id,job_id,kind,payload,max_attempts)
                   VALUES (%s,%s,%s,%s::jsonb,%s) ON CONFLICT DO NOTHING RETURNING job_id""",
                (tenant_id, job_id, kind, encoded, max_attempts),
            ).fetchone()
            if row is None:
                existing = connection.execute(
                    """SELECT kind,payload,max_attempts FROM tactiq_cloud_jobs
                       WHERE tenant_id=%s AND job_id=%s""", (tenant_id, job_id),
                ).fetchone()
                if existing != {"kind": kind, "payload": json.loads(encoded), "max_attempts": max_attempts}:
                    raise RevisionConflictError("Job idempotency key already has different input")
                return False
            self._enqueue(connection, tenant_id, job_id)
            return True

    def claim(self, reference: WorkReference, owner: str, lease_seconds: int) -> ExecutionClaim | None:
        self._scope(reference.tenant_id, reference.job_id)
        identifier(owner, "owner")
        bounded_integer(lease_seconds, "lease_seconds", 15, 300)
        with self._transaction() as connection:
            if self._max_running is not None:
                connection.execute("SELECT pg_advisory_xact_lock(854917020605)")
                running = connection.execute(
                    """SELECT count(*) AS total FROM tactiq_cloud_jobs
                       WHERE status='running' AND lease_until>clock_timestamp()"""
                ).fetchone()
                if running["total"] >= self._max_running:
                    return None
            # Exhausted, crashed claims must become terminal on redelivery.
            connection.execute(
                """UPDATE tactiq_cloud_jobs SET status='dead_letter',lease_owner=NULL,lease_until=NULL
                   WHERE tenant_id=%s AND job_id=%s AND status='running'
                   AND lease_until<=clock_timestamp() AND attempts>=max_attempts""",
                (reference.tenant_id, reference.job_id),
            )
            row = connection.execute(
                """UPDATE tactiq_cloud_jobs SET status='running', attempts=attempts+1, fence=fence+1,
                   lease_owner=%s, lease_until=clock_timestamp()+(%s * interval '1 second'),
                   updated_at=clock_timestamp()
                   WHERE tenant_id=%s AND job_id=%s AND NOT cancel_requested AND attempts<max_attempts
                   AND (status='queued' OR (status='running' AND lease_until<=clock_timestamp()))
                   RETURNING fence,kind,payload""",
                (owner, lease_seconds, reference.tenant_id, reference.job_id),
            ).fetchone()
        if row is None:
            return None
        return ExecutionClaim(reference.tenant_id, reference.job_id, row["fence"], row["kind"], row["payload"])

    def terminal(self, reference: WorkReference) -> bool:
        self._scope(reference.tenant_id, reference.job_id)
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT status FROM tactiq_cloud_jobs WHERE tenant_id=%s AND job_id=%s",
                (reference.tenant_id, reference.job_id),
            ).fetchone()
        return row is None or row["status"] in {"succeeded", "cancelled", "dead_letter"}

    @staticmethod
    def _claim_params(claim: ExecutionClaim, owner: str) -> tuple[Any, ...]:
        PostgresStore._scope(claim.tenant_id, claim.job_id)
        identifier(owner, "owner")
        bounded_integer(claim.fence, "fence", 1, 2**63 - 1)
        return claim.tenant_id, claim.job_id, claim.fence, owner

    def renew(self, claim: ExecutionClaim, owner: str, lease_seconds: int) -> bool:
        params = self._claim_params(claim, owner)
        bounded_integer(lease_seconds, "lease_seconds", 15, 300)
        with self._transaction() as connection:
            row = connection.execute(
                """UPDATE tactiq_cloud_jobs SET lease_until=clock_timestamp()+(%s * interval '1 second')
                   WHERE tenant_id=%s AND job_id=%s AND fence=%s AND lease_owner=%s
                   AND status='running' AND NOT cancel_requested AND lease_until>clock_timestamp()
                   RETURNING job_id""", (lease_seconds, *params),
            ).fetchone()
        return row is not None

    def cancelled(self, claim: ExecutionClaim, owner: str) -> bool:
        params = self._claim_params(claim, owner)
        with self._transaction() as connection:
            row = connection.execute(
                """SELECT job_id FROM tactiq_cloud_jobs
                   WHERE tenant_id=%s AND job_id=%s AND fence=%s AND lease_owner=%s
                   AND status='running' AND NOT cancel_requested AND lease_until>clock_timestamp()""",
                params,
            ).fetchone()
        return row is None

    def finish(self, claim: ExecutionClaim, owner: str, result: dict[str, Any]) -> bool:
        params = self._claim_params(claim, owner)
        encoded = bounded_json(result, MAX_RECORD_BYTES)
        with self._transaction() as connection:
            row = connection.execute(
                """UPDATE tactiq_cloud_jobs SET status='succeeded',result=%s::jsonb,
                   lease_owner=NULL,lease_until=NULL,updated_at=clock_timestamp()
                   WHERE tenant_id=%s AND job_id=%s AND fence=%s AND lease_owner=%s
                   AND status='running' AND NOT cancel_requested AND lease_until>clock_timestamp()
                   RETURNING job_id""", (encoded, *params),
            ).fetchone()
        return row is not None

    def fail(self, claim: ExecutionClaim, owner: str, reason: str) -> bool:
        """Retry by admitting a NEW outbox message before acknowledging delivery."""
        params = self._claim_params(claim, owner)
        # Provider exception strings may contain credentials or private content.
        safe_reason = "Analysis execution failed."
        with self._transaction() as connection:
            row = connection.execute(
                """UPDATE tactiq_cloud_jobs
                   SET status=CASE WHEN attempts<max_attempts THEN 'queued' ELSE 'dead_letter' END,
                   error=%s,lease_owner=NULL,lease_until=NULL,updated_at=clock_timestamp()
                   WHERE tenant_id=%s AND job_id=%s AND fence=%s AND lease_owner=%s
                   AND status='running' AND NOT cancel_requested AND lease_until>clock_timestamp()
                   RETURNING status""", (safe_reason, *params),
            ).fetchone()
            if row is not None and row["status"] == "queued":
                self._enqueue(connection, claim.tenant_id, claim.job_id)
        return row is not None

    def request_cancel(self, tenant_id: str, job_id: str) -> bool:
        self._scope(tenant_id, job_id)
        with self._transaction() as connection:
            row = connection.execute(
                """UPDATE tactiq_cloud_jobs SET cancel_requested=true,status='cancelled',fence=fence+1,
                   lease_owner=NULL,lease_until=NULL,updated_at=clock_timestamp()
                   WHERE tenant_id=%s AND job_id=%s AND status IN ('queued','running')
                   RETURNING job_id""", (tenant_id, job_id),
            ).fetchone()
        return row is not None

    def claim_outbox(self, owner: str, lease_seconds: int = 60) -> OutboxClaim | None:
        """System dispatcher only; claims use SKIP LOCKED, not tenant API access."""
        identifier(owner, "outbox owner")
        bounded_integer(lease_seconds, "lease_seconds", 15, 300)
        with self._transaction() as connection:
            row = connection.execute(
                """WITH picked AS (
                     SELECT message_id FROM tactiq_cloud_outbox
                     WHERE status='pending' AND available_at<=clock_timestamp()
                     AND (lease_until IS NULL OR lease_until<=clock_timestamp())
                     ORDER BY available_at,message_id FOR UPDATE SKIP LOCKED LIMIT 1
                   )
                   UPDATE tactiq_cloud_outbox AS o
                   SET status=CASE WHEN o.attempts>=10 THEN 'dead_letter' ELSE 'pending' END,
                   attempts=LEAST(o.attempts+1,10),fence=o.fence+1,lease_owner=%s,
                   lease_until=clock_timestamp()+(%s * interval '1 second')
                   FROM picked WHERE o.message_id=picked.message_id
                   RETURNING o.message_id,o.tenant_id,o.job_id,o.fence,o.status""", (owner, lease_seconds),
            ).fetchone()
        if row is None or row["status"] != "pending":
            return None
        return OutboxClaim(
            row["message_id"], WorkReference(tenant_id=row["tenant_id"], job_id=row["job_id"]), row["fence"],
        )

    def acknowledge_outbox(self, claim: OutboxClaim, owner: str) -> bool:
        return self._settle_outbox(claim, owner, sent=True, delay_seconds=0)

    def release_outbox(self, claim: OutboxClaim, owner: str, *, delay_seconds: int = 30) -> bool:
        bounded_integer(delay_seconds, "delay_seconds", 0, 3600)
        return self._settle_outbox(claim, owner, sent=False, delay_seconds=delay_seconds)

    def _settle_outbox(self, claim: OutboxClaim, owner: str, *, sent: bool, delay_seconds: int) -> bool:
        self._scope(claim.reference.tenant_id, claim.reference.job_id)
        cloud_identifier(claim.operation_id, "outbox operation")
        bounded_integer(claim.fence, "outbox fence", 1, 2**63 - 1)
        identifier(owner, "outbox owner")
        with self._transaction() as connection:
            row = connection.execute(
                """UPDATE tactiq_cloud_outbox
                   SET status=CASE WHEN %s THEN 'sent' WHEN attempts>=10 THEN 'dead_letter' ELSE 'pending' END,
                   lease_owner=NULL,lease_until=NULL,available_at=clock_timestamp()+(%s * interval '1 second')
                   WHERE message_id=%s AND tenant_id=%s AND job_id=%s AND fence=%s AND lease_owner=%s
                   AND status='pending' AND lease_until>clock_timestamp() RETURNING message_id""",
                (sent, delay_seconds, claim.operation_id, claim.reference.tenant_id, claim.reference.job_id,
                 claim.fence, owner),
            ).fetchone()
        return row is not None

    def register_media(self, reference: MediaRef) -> None:
        from .blob import validate_reference

        validate_reference(reference)
        with self._transaction() as connection:
            row = connection.execute(
                """INSERT INTO tactiq_cloud_media(tenant_id,match_id,media_id,size,content_type,etag)
                   VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING media_id""",
                (reference.tenant_id, reference.match_id, reference.media_id, reference.size,
                 reference.content_type, reference.etag),
            ).fetchone()
            if row is None:
                raise RevisionConflictError("Media ID is already registered")

    def load_media(self, tenant_id: str, match_id: str, media_id: str) -> MediaRef | None:
        self._scope(tenant_id, match_id)
        cloud_identifier(media_id, "media_id")
        with self._transaction() as connection:
            row = connection.execute(
                """SELECT size,content_type,etag FROM tactiq_cloud_media
                   WHERE tenant_id=%s AND match_id=%s AND media_id=%s""",
                (tenant_id, match_id, media_id),
            ).fetchone()
        return None if row is None else MediaRef(tenant_id, match_id, media_id, row["size"], row["content_type"], row["etag"])
