"""Scripted boundary tests, NOT PostgreSQL SQL/concurrency integration proof."""
from __future__ import annotations

from contextlib import contextmanager

import pytest

from tactiq.runtime.cloud_worker import ExecutionClaim, WorkReference, dispatch_once
from tactiq.runtime.contracts import (
    MAX_RECORD_BYTES, CloudRecordError, RecordCursor, RevisionConflictError,
    StreamRecord, bounded_json,
)
from tactiq.runtime.postgres import PostgresStore


class Result:
    def __init__(self, value):
        self.value = value

    def fetchone(self):
        return self.value

    def fetchall(self):
        return self.value


class Connection:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True

    @contextmanager
    def transaction(self):
        try:
            yield self
        except BaseException:
            self.rolled_back = True
            raise
        else:
            self.committed = True

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if "set_config" in sql:
            return Result(None)
        assert self.results, f"Unexpected query: {sql}"
        value = self.results.pop(0)
        if isinstance(value, BaseException):
            raise value
        return Result(value)


def store_with(*results):
    connection = Connection(*results)
    return PostgresStore(lambda: connection), connection


@pytest.mark.parametrize("value", [
    {"source_video_path": "hidden"},
    {"nested": [{"audio_url": "opaque"}]},
    {"arbitrary": "https://storage.example/x?sig=secret"},
    {"arbitrary": "C:\\private\\media.mp4"},
    {"access_token": "credential"},
    {"arbitrary": float("inf")},
])
def test_cloud_metadata_rejects_locations_credentials_and_nonfinite(value):
    with pytest.raises(ValueError):
        bounded_json(value, MAX_RECORD_BYTES)


def test_cloud_payload_size_is_utf8_bytes():
    with pytest.raises(CloudRecordError):
        bounded_json({"text": "é" * 20}, 30)


def test_state_commit_appends_streams_in_same_transaction():
    store, connection = store_with(None, {"revision": 4}, {"position": 10}, None)
    result = store.commit(
        "tenant", "match", {"clock": 20}, expected_revision=3,
        events=(StreamRecord("event", {"units": "metres"}),),
    )
    assert result.revision == 4
    insertion = connection.calls[-1]
    assert insertion[1] == ("tenant", "match", "events", 11, 4, "event", '{"units":"metres"}')
    assert connection.committed and connection.closed
    assert "revision=%s" in connection.calls[3][0]


def test_cas_conflict_rolls_back_placeholder_and_never_writes_events():
    store, connection = store_with(None, None)
    with pytest.raises(RevisionConflictError):
        store.commit("tenant", "match", {}, expected_revision=5)
    assert connection.rolled_back and not connection.committed and connection.closed
    assert len(connection.calls) == 4


def test_stream_insert_failure_rolls_back_state():
    store, connection = store_with(None, {"revision": 1}, {"position": 0}, RuntimeError("duplicate"))
    with pytest.raises(RuntimeError, match="duplicate"):
        store.commit("tenant", "match", {}, expected_revision=0, events=(StreamRecord("e", {}),))
    assert connection.rolled_back


def test_worker_aggregate_writes_are_fenced_inside_same_transaction():
    claim = ExecutionClaim("tenant", "job", 7, "analyse", {})
    store, connection = store_with(None)
    with pytest.raises(RevisionConflictError, match="lease"):
        store.commit("tenant", "match", {}, expected_revision=0, claim=claim, owner="worker:1")
    sql, params = connection.calls[-1]
    assert "FOR UPDATE" in sql and "lease_until>clock_timestamp()" in sql
    assert params == ("tenant", "job", 7, "worker:1")
    assert connection.rolled_back


def test_worker_aggregate_write_rejects_other_tenant_before_connect():
    store = PostgresStore(lambda: pytest.fail("must not connect"))
    with pytest.raises(CloudRecordError):
        store.commit("other", "match", {}, expected_revision=0,
                     claim=ExecutionClaim("tenant", "job", 1, "analyse", {}), owner="worker")


def test_worker_lease_expiry_during_write_rolls_back_state():
    store, connection = store_with({"job_id": "job"}, None, {"revision": 1}, None)
    with pytest.raises(RevisionConflictError, match="expired"):
        store.commit(
            "tenant", "match", {}, expected_revision=0,
            claim=ExecutionClaim("tenant", "job", 1, "analyse", {}), owner="worker",
        )
    assert connection.rolled_back


def test_page_is_owner_scoped_revision_frozen_and_bounded():
    store, connection = store_with({"revision": 9}, {"position": 4}, [
        {"position": 3, "record_id": "a", "payload": {"clock": 1}},
        {"position": 4, "record_id": "b", "payload": {"clock": 2}},
    ])
    cursor = RecordCursor("tenant", "match", "events", 5, 2)
    page = store.page("tenant", "match", "events", cursor=cursor, limit=1)
    assert page.has_more and page.cursor == RecordCursor("tenant", "match", "events", 5, 3)
    assert page.records == (StreamRecord("a", {"clock": 1}),)
    assert connection.calls[-1][1] == ("tenant", "match", "events", 2, 4, 5, 2)
    assert "ORDER BY position LIMIT %s" in connection.calls[-1][0]


@pytest.mark.parametrize("cursor", [
    RecordCursor("other", "match", "events", 1),
    RecordCursor("tenant", "other", "events", 1),
    RecordCursor("tenant", "match", "replay", 1),
    RecordCursor("tenant", "match", "events", 0),
])
def test_page_rejects_cross_scope_or_invalid_cursor_before_connect(cursor):
    store = PostgresStore(lambda: pytest.fail("must not connect"))
    with pytest.raises(CloudRecordError):
        store.page("tenant", "match", "events", cursor=cursor)


def test_page_rejects_uncommitted_future_revision():
    store, _ = store_with({"revision": 1})
    with pytest.raises(CloudRecordError):
        store.page("tenant", "match", "events", cursor=RecordCursor("tenant", "match", "events", 2))


def test_first_admission_and_outbox_are_atomic_and_reference_only():
    store, connection = store_with({"job_id": "job"}, None)
    assert store.admit_job("tenant", "job", "analyse", {"match_id": "match"})
    assert connection.committed
    sql, params = connection.calls[-1]
    assert "tactiq_cloud_outbox" in sql
    assert params[1:3] == ("tenant", "job")
    assert '"version": 1' in params[3] and "match_id" not in params[3]


def test_outbox_failure_rolls_back_job_admission():
    store, connection = store_with({"job_id": "job"}, RuntimeError("outbox unavailable"))
    with pytest.raises(RuntimeError):
        store.admit_job("tenant", "job", "analyse", {})
    assert connection.rolled_back


def test_idempotent_admission_never_enqueues_twice():
    store, connection = store_with(None, {"kind": "analyse", "payload": {}, "max_attempts": 3})
    assert not store.admit_job("tenant", "job", "analyse", {})
    assert all("INSERT INTO tactiq_cloud_outbox" not in sql for sql, _ in connection.calls)


def test_conflicting_admission_is_not_treated_as_idempotent():
    store, connection = store_with(None, {"kind": "other", "payload": {}, "max_attempts": 3})
    with pytest.raises(RevisionConflictError):
        store.admit_job("tenant", "job", "analyse", {})
    assert connection.rolled_back


def test_claim_composes_with_cloud_worker_and_consumes_an_attempt():
    store, connection = store_with(None, {"fence": 2, "kind": "analyse", "payload": {}})
    claim = store.claim(WorkReference(tenant_id="tenant", job_id="job"), "worker", 60)
    assert claim == ExecutionClaim("tenant", "job", 2, "analyse", {})
    sql, params = connection.calls[-1]
    assert "attempts=attempts+1" in sql and "fence=fence+1" in sql
    assert "lease_until<=clock_timestamp()" in sql
    assert params == ("worker", 60, "tenant", "job")


@pytest.mark.parametrize("operation", ["renew", "finish", "fail", "cancelled"])
def test_worker_updates_bind_owner_tenant_job_fence_and_live_lease(operation):
    store, connection = store_with(None)
    claim = ExecutionClaim("tenant", "job", 8, "analyse", {})
    if operation == "renew":
        assert not store.renew(claim, "owner", 60)
    elif operation == "finish":
        assert not store.finish(claim, "owner", {})
    elif operation == "fail":
        assert not store.fail(claim, "owner", "raw secret exception")
    else:
        assert store.cancelled(claim, "owner")
    sql, params = connection.calls[-1]
    assert params[-4:] == ("tenant", "job", 8, "owner")
    assert "lease_until>clock_timestamp()" in sql and "NOT cancel_requested" in sql
    assert "raw secret" not in str(params)


def test_failure_retry_admits_new_outbox_in_same_transaction():
    store, connection = store_with({"status": "queued"}, None)
    assert store.fail(ExecutionClaim("tenant", "job", 1, "analyse", {}), "owner", "provider error")
    assert connection.committed and "INSERT INTO tactiq_cloud_outbox" in connection.calls[-1][0]


def test_terminal_failure_does_not_enqueue_again():
    store, connection = store_with({"status": "dead_letter"})
    assert store.fail(ExecutionClaim("tenant", "job", 1, "analyse", {}), "owner", "error")
    assert len(connection.calls) == 3


def test_cancellation_invalidates_running_fence():
    store, connection = store_with({"job_id": "job"})
    assert store.request_cancel("tenant", "job")
    sql, params = connection.calls[-1]
    assert "fence=fence+1" in sql and "status='cancelled'" in sql
    assert params == ("tenant", "job")


def test_outbox_claim_is_bounded_skip_locked_and_fenced():
    store, connection = store_with(
        {"message_id": "m", "tenant_id": "tenant", "job_id": "job", "fence": 2, "status": "pending"},
    )
    claim = store.claim_outbox("dispatcher")
    assert claim.operation_id == "m" and claim.fence == 2
    sql, params = connection.calls[-1]
    assert "FOR UPDATE SKIP LOCKED LIMIT 1" in sql and "LEAST(o.attempts+1,10)" in sql
    assert params == ("dispatcher", 60)


def test_outbox_ack_is_owner_revision_and_scope_fenced():
    store, _ = store_with(
        {"message_id": "m", "tenant_id": "tenant", "job_id": "job", "fence": 2, "status": "pending"},
    )
    claim = store.claim_outbox("dispatcher")
    store, connection = store_with(None)
    assert not store.acknowledge_outbox(claim, "dispatcher")
    sql, params = connection.calls[-1]
    assert "lease_until>clock_timestamp()" in sql
    assert params[-5:] == ("m", "tenant", "job", 2, "dispatcher")


def test_outbox_dispatch_composes_directly_with_parent_protocol():
    connections = [
        Connection({"message_id": "m", "tenant_id": "tenant", "job_id": "job", "fence": 2, "status": "pending"}),
        Connection({"message_id": "m"}),
    ]
    store = PostgresStore(lambda: connections.pop(0))
    sent = []
    assert dispatch_once(store, "dispatcher", lambda reference, operation: sent.append((reference, operation)))
    assert sent == [(WorkReference(tenant_id="tenant", job_id="job"), "m")]
    assert not connections


def test_exhausted_outbox_is_terminal_and_not_sent():
    store, connection = store_with(
        {"message_id": "m", "tenant_id": "tenant", "job_id": "job", "fence": 11, "status": "dead_letter"},
    )
    assert store.claim_outbox("dispatcher") is None
    assert connection.committed


def test_migration_is_explicit_and_has_transaction_lock():
    store, connection = store_with(None, {"name": None}, None)
    store.migrate()
    assert "pg_advisory_xact_lock" in connection.calls[2][0]
    assert "CREATE TABLE tactiq_cloud_matches" in connection.calls[-1][0]
    assert connection.committed


def test_database_errors_propagate_without_local_fallback():
    def unavailable():
        raise OSError("database offline")

    with pytest.raises(OSError, match="offline"):
        PostgresStore(unavailable).load("tenant", "match")


@pytest.mark.parametrize("limit", [0, 101, True, 1.5])
def test_page_limit_rejected_before_connect(limit):
    store = PostgresStore(lambda: pytest.fail("must not connect"))
    with pytest.raises(ValueError):
        store.page("tenant", "match", "events", limit=limit)
