"""Opt-in real PostgreSQL checks; require a separately approved disposable DB.

Set BOTH TACTIQ_ALLOW_POSTGRES_TEST_WRITES=1 and TACTIQ_TEST_POSTGRES_DSN.
Each test creates/drops only its uniquely named schema. These tests do not
provision a database, discover credentials, or use an application's live DB.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from tactiq.runtime.cloud_worker import ExecutionClaim, WorkReference
from tactiq.runtime.contracts import RevisionConflictError, StreamRecord
from tactiq.runtime.postgres import PostgresStore

pytestmark = pytest.mark.skipif(
    os.environ.get("TACTIQ_ALLOW_POSTGRES_TEST_WRITES") != "1"
    or not os.environ.get("TACTIQ_TEST_POSTGRES_DSN"),
    reason="Real PostgreSQL tests require an explicitly approved disposable database",
)


@pytest.fixture
def database():
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql
    from psycopg.rows import dict_row

    dsn = os.environ["TACTIQ_TEST_POSTGRES_DSN"]
    schema = "tactiq_test_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))

    def connect():
        connection = psycopg.connect(dsn, row_factory=dict_row, connect_timeout=10)
        try:
            connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            connection.commit()
        except BaseException:
            connection.close()
            raise
        return connection

    store = PostgresStore(connect)
    try:
        store.migrate()
        store.migrate()
        yield store, connect
    finally:
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_pg_atomic_cas_streams_tenant_scope_and_frozen_cursor(database):
    store, _ = database
    store.commit("tenant_a", "match", {"clock": 1}, expected_revision=0, events=(
        StreamRecord("e1", {"clock": 1}), StreamRecord("e2", {"clock": 2}),
    ))
    first = store.page("tenant_a", "match", "events", limit=1)
    store.commit("tenant_a", "match", {"clock": 3}, expected_revision=1, events=(StreamRecord("e3", {"clock": 3}),))
    second = store.page("tenant_a", "match", "events", cursor=first.cursor, limit=1)
    assert [item.record_id for item in second.records] == ["e2"] and not second.has_more
    assert store.load("tenant_b", "match") is None
    store.commit("tenant_b", "match", {"clock": 99}, expected_revision=0)
    assert store.load("tenant_a", "match").state == {"clock": 3}
    assert store.load("tenant_b", "match").state == {"clock": 99}
    with pytest.raises(RevisionConflictError):
        store.commit("tenant_a", "match", {}, expected_revision=1)


def test_pg_concurrent_writers_cannot_commit_the_same_revision(database):
    store, _ = database
    store.commit("tenant", "match", {}, expected_revision=0)

    def write(index):
        try:
            store.commit("tenant", "match", {"writer": index}, expected_revision=1)
            return "committed"
        except RevisionConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(write, [1, 2])) == ["committed", "conflict"]
    assert store.load("tenant", "match").revision == 2


def test_pg_global_capacity_serializes_claims_across_tenants(database):
    _, connect = database
    store = PostgresStore(connect, max_running=1)
    references = [WorkReference(tenant_id=f"tenant_{i}", job_id="job") for i in range(2)]
    for ref in references:
        store.admit_job(ref.tenant_id, ref.job_id, "analyse", {})

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda ref: store.claim(ref, ref.tenant_id, 60), references))
    assert sum(claim is not None for claim in claims) == 1
    winner = next(claim for claim in claims if claim is not None)
    assert store.finish(winner, winner.tenant_id, {})
    loser = next(ref for ref, claim in zip(references, claims) if claim is None)
    assert store.claim(loser, loser.tenant_id, 60) is not None


def test_pg_job_claim_lease_recovery_fencing_retry_and_cancellation(database):
    store, connect = database
    reference = WorkReference(tenant_id="tenant", job_id="job")
    assert store.admit_job("tenant", "job", "analyse", {"match_id": "match"})
    assert not store.admit_job("tenant", "job", "analyse", {"match_id": "match"})
    first = store.claim(reference, "worker_1", 60)
    assert first and store.claim(reference, "worker_2", 60) is None
    assert store.renew(first, "worker_1", 60)
    with connect() as connection:
        connection.execute(
            "UPDATE tactiq_cloud_jobs SET lease_until=clock_timestamp()-interval '1 second'"
            " WHERE tenant_id=%s AND job_id=%s", ("tenant", "job"),
        )
    second = store.claim(reference, "worker_2", 60)
    assert second and second.fence > first.fence
    assert not store.finish(first, "worker_1", {})
    assert not store.renew(first, "worker_1", 60)
    with pytest.raises(RevisionConflictError):
        store.commit("tenant", "match", {}, expected_revision=0, claim=first, owner="worker_1")
    assert store.load("tenant", "match") is None
    store.commit("tenant", "match", {}, expected_revision=0, claim=second, owner="worker_2")
    assert store.fail(second, "worker_2", "Do not persist provider secrets")
    third = store.claim(reference, "worker_3", 60)
    assert third and store.request_cancel("tenant", "job")
    assert store.cancelled(third, "worker_3")
    assert not store.finish(third, "worker_3", {}) and store.terminal(reference)


def test_pg_job_and_outbox_admission_rollback_and_disjoint_claims(database, monkeypatch):
    store, _ = database

    def fail_enqueue(*args):
        raise RuntimeError("simulate outbox insertion failure")

    with monkeypatch.context() as patch:
        patch.setattr(store, "_enqueue", fail_enqueue)
        with pytest.raises(RuntimeError):
            store.admit_job("tenant", "rolled_back", "analyse", {})
    assert store.claim(WorkReference(tenant_id="tenant", job_id="rolled_back"), "worker", 60) is None
    for index in range(2):
        store.admit_job("tenant", f"job_{index}", "analyse", {})
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda owner: store.claim_outbox(owner), ["sender_1", "sender_2"]))
    assert len({claim.operation_id for claim in claims}) == 2
    assert store.acknowledge_outbox(claims[0], "sender_1")
    assert not store.acknowledge_outbox(claims[0], "sender_1")
    assert store.release_outbox(claims[1], "sender_2", delay_seconds=0)
    reclaimed = store.claim_outbox("sender_3")
    assert reclaimed.operation_id == claims[1].operation_id and reclaimed.fence > claims[1].fence
    assert not store.acknowledge_outbox(claims[1], "sender_2")


def test_pg_final_crashed_attempt_becomes_dead_letter(database):
    store, connect = database
    store.admit_job("tenant", "job", "analyse", {}, max_attempts=1)
    reference = WorkReference(tenant_id="tenant", job_id="job")
    assert isinstance(store.claim(reference, "worker", 60), ExecutionClaim)
    with connect() as connection:
        connection.execute(
            "UPDATE tactiq_cloud_jobs SET lease_until=clock_timestamp()-interval '1 second'"
            " WHERE tenant_id=%s AND job_id=%s", ("tenant", "job"),
        )
    assert store.claim(reference, "replacement", 60) is None
    assert store.terminal(reference)
