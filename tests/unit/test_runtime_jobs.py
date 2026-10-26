from __future__ import annotations

import json
import logging
import os
import secrets
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event, Lock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from tactiq.runtime import (
    BackgroundJobs,
    RecordValidationError,
    RetryableJobError,
    SQLiteStore,
)


TERMINAL = {"succeeded", "failed", "cancelled", "dead_letter"}


@pytest.fixture
def store(tmp_path):
    with SQLiteStore(tmp_path / "jobs.sqlite3") as instance:
        yield instance


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        Event().wait(0.01)
    pytest.fail("Timed out waiting for bounded background work")


def finished(runner, job_id, tenant_id="tenant-a"):
    def terminal():
        job = runner.get(tenant_id, job_id)
        return job if job is not None and job.status in TERMINAL else None

    return wait_for(terminal)


def idle(runner):
    def no_futures():
        with runner._lock:
            return not runner._futures

    wait_for(no_futures)


def test_success_is_serializable_isolated_persistent_and_slots_are_released(store):
    with BackgroundJobs(store, {"work": lambda payload: {"value": payload["value"] + 1}}) as jobs:
        submitted = jobs.submit("tenant-a", "work", {"value": 4})
        assert submitted.job_id == submitted.id
        assert submitted.status == "queued"
        assert submitted.attempts == 0
        with pytest.raises(ValidationError):
            submitted.status = "succeeded"
        assert jobs.get("tenant-b", submitted.job_id) is None
        assert jobs.cancel("tenant-b", submitted.job_id) is None
        result = finished(jobs, submitted.job_id)
        assert result.status == "succeeded"
        assert result.result == {"value": 5}
        assert result.attempts == 1
        assert result.error is None
        assert result.started_at is not None
        assert result.finished_at is not None
        assert result.updated_at >= result.created_at
        json.dumps(result.model_dump(mode="json"), allow_nan=False)
        idle(jobs)
        assert jobs.cancel("tenant-a", submitted.job_id) == result
    assert not jobs._dispatcher.is_alive()
    assert all(not thread.is_alive() for thread in jobs._executor._threads)
    with BackgroundJobs(store, {}) as reopened:
        assert reopened.recover() == 0
        assert reopened.get("tenant-a", submitted.job_id) == result


def test_payload_is_persisted_before_execution_and_does_not_share_mutations(store):
    started, release = Event(), Event()
    payload = {"options": {"revision": 1}}

    def work(received):
        started.set()
        assert release.wait(10)
        assert received == {"options": {"revision": 1}}
        received["options"]["revision"] = 9
        return {"revision": received["options"]["revision"]}

    with BackgroundJobs(store, {"work": work}) as jobs:
        try:
            submitted = jobs.submit("tenant-a", "work", payload)
            assert started.wait(10)
            payload["options"]["revision"] = 2
            submitted.payload["options"]["revision"] = 3
            release.set()
            result = finished(jobs, submitted.job_id)
            assert result.payload == {"options": {"revision": 1}}
            assert result.result == {"revision": 9}
        finally:
            release.set()


def test_only_explicit_retryable_failure_retries_with_persisted_attempts(store):
    calls = []

    def work(payload):
        calls.append(payload["source_path"])
        if len(calls) == 1:
            raise RetryableJobError("Transient backend unavailability")
        return {"done": True}

    with BackgroundJobs(store, {"work": work}, max_pending=1, max_attempts=2) as jobs:
        submitted = jobs.submit("tenant-a", "work", {"source_path": "uploads\\video.mp4"})
        result = finished(jobs, submitted.job_id)
        assert result.status == "succeeded"
        assert result.attempts == 2
        assert result.error is None
        assert calls == ["uploads\\video.mp4"] * 2
        idle(jobs)
        assert finished(jobs, jobs.submit("tenant-a", "work", {"source_path": "next"}).id).status == "succeeded"


@pytest.mark.parametrize("error_type", [ValueError, SystemExit])
def test_permanent_and_unexpected_failures_are_safe_and_release_capacity(
    store, tmp_path, caplog, error_type
):
    secret = secrets.token_urlsafe(24)
    private_path = str(tmp_path / "private-source.mp4")

    def broken(_):
        raise error_type(f"{private_path}: {secret}")

    with caplog.at_level(logging.ERROR, logger="tactiq.runtime.jobs"):
        with BackgroundJobs(
            store, {"broken": broken, "ok": lambda _: {}}, max_pending=1, max_attempts=3
        ) as jobs:
            submitted = jobs.submit("tenant-a", "broken", {})
            result = finished(jobs, submitted.job_id)
            assert result.status == "failed"
            assert result.attempts == 1
            assert result.error == "Job execution failed."
            assert secret not in result.model_dump_json()
            assert private_path not in result.model_dump_json()
            idle(jobs)
            assert finished(jobs, jobs.submit("tenant-a", "ok", {}).id).status == "succeeded"
    assert any(record.exc_info for record in caplog.records)
    assert all("payload" not in record.__dict__ for record in caplog.records)


def test_retry_exhaustion_is_dead_letter_with_safe_public_error(store):
    calls = []
    private = secrets.token_urlsafe(24)

    def broken(_):
        calls.append(1)
        raise RetryableJobError(private)

    with BackgroundJobs(
        store, {"broken": broken, "ok": lambda _: {}}, max_pending=1, max_attempts=2
    ) as jobs:
        submitted = jobs.submit("tenant-a", "broken", {})
        result = finished(jobs, submitted.id)
        assert result.status == "dead_letter"
        assert result.attempts == result.max_attempts == 2
        assert len(calls) == 2
        assert result.error == "Job exhausted its retry budget."
        assert private not in result.model_dump_json()
        idle(jobs)
        assert finished(jobs, jobs.submit("tenant-a", "ok", {}).id).status == "succeeded"


@pytest.mark.parametrize("result", [None, [], {"n": float("nan")}, {"access_token": "redacted"}])
def test_non_json_or_credential_results_fail_without_retry_and_release_slots(store, result):
    with BackgroundJobs(
        store, {"bad": lambda _: result, "ok": lambda _: {}}, max_pending=1
    ) as jobs:
        submitted = jobs.submit("tenant-a", "bad", {})
        failed = finished(jobs, submitted.id)
        assert failed.status == "failed"
        assert failed.attempts == 1
        assert failed.result is None
        idle(jobs)
        assert finished(jobs, jobs.submit("tenant-a", "ok", {}).id).status == "succeeded"


def test_admission_is_bounded_and_saturation_does_not_persist_extra_work(store):
    started, release = Event(), Event()

    def work(_):
        started.set()
        assert release.wait(10)
        return {}

    with BackgroundJobs(store, {"work": work}, max_workers=1, max_pending=2) as jobs:
        try:
            first = jobs.submit("tenant-a", "work", {})
            assert started.wait(10)
            second = jobs.submit("tenant-a", "work", {})
            assert jobs.get("tenant-a", second.id).status == "queued"
            with pytest.raises(HTTPException) as error:
                jobs.submit("tenant-a", "work", {})
            assert error.value.status_code == 503
            assert error.value.headers == {"Retry-After": "1"}
            with store._read() as connection:
                assert connection.execute("SELECT COUNT(*) FROM runtime_jobs").fetchone()[0] == 2
            release.set()
            assert finished(jobs, first.id).status == "succeeded"
            assert finished(jobs, second.id).status == "succeeded"
        finally:
            release.set()


def test_concurrent_admission_does_not_exceed_capacity(store):
    barrier, release = Barrier(8), Event()

    def work(_):
        assert release.wait(10)
        return {}

    with BackgroundJobs(store, {"work": work}, max_workers=1, max_pending=2) as jobs:
        def submit(_):
            barrier.wait(timeout=10)
            try:
                return jobs.submit("tenant-a", "work", {})
            except HTTPException as error:
                assert error.status_code == 503
                return None

        try:
            with ThreadPoolExecutor(max_workers=8) as executor:
                outcomes = list(executor.map(submit, range(8)))
            accepted = [job for job in outcomes if job is not None]
            assert len(accepted) == 2
            release.set()
            assert all(finished(jobs, job.id).status == "succeeded" for job in accepted)
        finally:
            release.set()


def test_queued_cancellation_is_immediate_and_releases_admission_slot(store):
    started, release = Event(), Event()
    calls = []

    def work(payload):
        calls.append(payload["key"])
        if payload["key"] == "first":
            started.set()
            assert release.wait(10)
        return {}

    with BackgroundJobs(store, {"work": work}, max_workers=1, max_pending=2) as jobs:
        try:
            first = jobs.submit("tenant-a", "work", {"key": "first"})
            assert started.wait(10)
            second = jobs.submit("tenant-a", "work", {"key": "second"})
            cancelled = jobs.cancel("tenant-a", second.id)
            assert cancelled.status == "cancelled"
            assert cancelled.cancel_requested
            assert cancelled.attempts == 0
            assert cancelled.finished_at is not None
            replacement = jobs.submit("tenant-a", "work", {"key": "replacement"})
            release.set()
            assert finished(jobs, first.id).status == "succeeded"
            assert finished(jobs, replacement.id).status == "succeeded"
            assert jobs.cancel("tenant-a", second.id) == cancelled
            assert calls == ["first", "replacement"]
        finally:
            release.set()


@pytest.mark.parametrize("outcome", ["success", "retryable", "permanent"])
def test_running_cancellation_retains_slot_and_discards_result_without_retry(store, outcome):
    started, release = Event(), Event()
    calls = []

    def work(_):
        calls.append(1)
        started.set()
        assert release.wait(10)
        if outcome == "retryable":
            raise RetryableJobError("Try again")
        if outcome == "permanent":
            raise ValueError("Permanent failure")
        return {"should_be_discarded": True}

    with BackgroundJobs(store, {"work": work}, max_workers=1, max_pending=1) as jobs:
        try:
            submitted = jobs.submit("tenant-a", "work", {})
            assert started.wait(10)
            cancelled = jobs.cancel("tenant-a", submitted.id)
            assert cancelled.status == "running"
            assert cancelled.cancel_requested
            with pytest.raises(HTTPException) as error:
                jobs.submit("tenant-a", "work", {})
            assert error.value.status_code == 503
            release.set()
            result = finished(jobs, submitted.id)
            assert result.status == "cancelled"
            assert result.result is None
            assert result.error is None
            assert result.attempts == 1
            assert len(calls) == 1
            idle(jobs)
        finally:
            release.set()


def test_recovery_drains_backlog_beyond_capacity_and_handles_interrupted_states(store):
    queued = [
        store._create_job("tenant-a", "work", {"key": index}, 2)
        for index in range(5)
    ]
    interrupted = store._create_job("tenant-a", "work", {"key": "interrupted"}, 2)
    store._claim_job(interrupted.id, "previous-process")
    exhausted = store._create_job("tenant-a", "work", {"key": "exhausted"}, 1)
    store._claim_job(exhausted.id, "previous-process")
    cancelled = store._create_job("tenant-a", "work", {"key": "cancelled"}, 2)
    store._claim_job(cancelled.id, "previous-process")
    store._cancel_job("tenant-a", cancelled.id)
    calls, lock = Counter(), Lock()

    def work(payload):
        with lock:
            calls[payload["key"]] += 1
        return {"key": payload["key"]}

    with BackgroundJobs(store, {"work": work}, max_workers=2, max_pending=2) as jobs:
        assert jobs.recover() == 6
        for original in [*queued, interrupted]:
            result = finished(jobs, original.id)
            assert result.status == "succeeded"
            assert result.attempts == (2 if original.id == interrupted.id else 1)
        assert finished(jobs, exhausted.id).status == "dead_letter"
        assert finished(jobs, exhausted.id).attempts == 1
        assert finished(jobs, cancelled.id).status == "cancelled"
        assert calls == Counter({0: 1, 1: 1, 2: 1, 3: 1, 4: 1, "interrupted": 1})
        idle(jobs)
        assert jobs.recover() == 0


def test_recovery_is_idempotent_during_own_active_claim(store):
    started, release = Event(), Event()
    calls = []

    def work(_):
        calls.append(1)
        started.set()
        assert release.wait(10)
        return {}

    with BackgroundJobs(store, {"work": work}, max_pending=1) as jobs:
        try:
            submitted = jobs.submit("tenant-a", "work", {})
            assert started.wait(10)
            for _ in range(3):
                assert jobs.recover() == 0
            assert jobs.get("tenant-a", submitted.id).attempts == 1
            release.set()
            assert finished(jobs, submitted.id).attempts == 1
            assert calls == [1]
        finally:
            release.set()


@pytest.mark.parametrize(
    "status,owner,attempts,max_attempts,cancel_requested,expected",
    [
        ("queued", None, 0, 2, False, "queued"),
        ("queued", None, 2, 2, False, "dead_letter"),
        ("queued", None, 2, 2, True, "cancelled"),
        ("running", "current", 1, 2, False, "running"),
        ("running", "current", 1, 1, False, "running"),
        ("running", "current", 1, 2, True, "running"),
        ("running", "other", 1, 2, False, "queued"),
        ("running", None, 1, 2, False, "queued"),
        ("running", "other", 2, 2, False, "dead_letter"),
        ("running", "other", 2, 2, True, "cancelled"),
        ("running", None, 1, 2, True, "cancelled"),
        ("succeeded", None, 1, 2, False, "succeeded"),
        ("failed", None, 1, 2, False, "failed"),
        ("cancelled", None, 0, 2, True, "cancelled"),
        ("dead_letter", None, 2, 2, False, "dead_letter"),
    ],
)
def test_recovery_static_sql_preserves_state_and_owner_guards(
    store, status, owner, attempts, max_attempts, cancel_requested, expected
):
    current_owner = "current'; DROP TABLE runtime_jobs; --"
    stored_owner = current_owner if owner == "current" else owner
    queued = store._create_job("tenant-a", "work", {}, max_attempts)
    with store._write() as connection:
        connection.execute(
            """UPDATE runtime_jobs SET status = ?, owner_id = ?, attempts = ?,
                    cancel_requested = ? WHERE job_id = ?""",
            (status, stored_owner, attempts, int(cancel_requested), queued.id),
        )
    store._recover_jobs(current_owner)
    recovered = store._get_job("tenant-a", queued.id)
    assert recovered.status == expected
    assert recovered.attempts == attempts
    assert recovered.cancel_requested == cancel_requested
    if expected != status and expected == "dead_letter":
        assert recovered.error == "Job exhausted its retry budget."
    if expected != status and expected in {"cancelled", "dead_letter"}:
        assert recovered.finished_at is not None
    with store._read() as connection:
        owner_id = connection.execute(
            "SELECT owner_id FROM runtime_jobs WHERE job_id = ?", (queued.id,)
        ).fetchone()[0]
    assert owner_id == (stored_owner if status == expected == "running" else None)


def test_atomic_claims_cannot_double_execute_a_queued_record(store):
    queued = store._create_job("tenant-a", "work", {}, 2)
    barrier = Barrier(8)

    def claim(index):
        barrier.wait(timeout=10)
        return store._claim_job(queued.id, f"owner-{index}")

    with ThreadPoolExecutor(max_workers=8) as executor:
        claims = list(executor.map(claim, range(8)))
    assert len([claim for claim in claims if claim is not None]) == 1
    assert store._get_job("tenant-a", queued.id).attempts == 1


def test_recovery_of_unregistered_kind_fails_safely(store):
    queued = store._create_job("tenant-a", "removed-handler", {}, 2)
    with BackgroundJobs(store, {}, max_pending=1) as jobs:
        assert jobs.recover() == 1
        result = finished(jobs, queued.id)
        assert result.status == "failed"
        assert result.error == "Job handler is unavailable."
        assert result.attempts == 1
        idle(jobs)


def test_scheduling_failure_persists_safe_failure_and_releases_capacity(store, monkeypatch):
    with BackgroundJobs(store, {"work": lambda _: {}}, max_pending=1) as jobs:
        real_submit = jobs._executor.submit

        def unavailable(*_, **__):
            raise RuntimeError("Internal executor state")

        monkeypatch.setattr(jobs._executor, "submit", unavailable)
        with pytest.raises(HTTPException) as error:
            jobs.submit("tenant-a", "work", {})
        assert error.value.status_code == 503
        assert jobs._futures == {}
        with store._read() as connection:
            row = connection.execute("SELECT * FROM runtime_jobs").fetchone()
        assert row["status"] == "failed"
        assert row["attempts"] == 0
        assert row["error"] == "Job could not be scheduled."
        monkeypatch.setattr(jobs._executor, "submit", real_submit)
        assert finished(jobs, jobs.submit("tenant-a", "work", {}).id).status == "succeeded"


def test_second_runner_is_rejected_until_running_work_finishes_after_nonwaiting_close(store):
    started, release = Event(), Event()

    def blocking(_):
        started.set()
        assert release.wait(10)
        return {}

    jobs = BackgroundJobs(store, {"work": blocking}, max_workers=1, max_pending=2)
    try:
        first = jobs.submit("tenant-a", "work", {})
        assert started.wait(10)
        queued = jobs.submit("tenant-a", "work", {})
        jobs.close(wait=False)
        assert jobs.get("tenant-a", queued.id).status == "queued"
        with pytest.raises(RuntimeError, match="owns"):
            BackgroundJobs(store, {"work": lambda _: {}})
        with pytest.raises(HTTPException) as error:
            jobs.submit("tenant-a", "work", {})
        assert error.value.status_code == 503
        with pytest.raises(HTTPException) as error:
            jobs.recover()
        assert error.value.status_code == 503
        release.set()
        jobs.close(wait=True)
        assert jobs.get("tenant-a", first.id).status == "succeeded"
        assert not jobs._dispatcher.is_alive()
        with BackgroundJobs(store, {"work": lambda _: {"recovered": True}}) as reopened:
            assert reopened.recover() == 1
            assert finished(reopened, queued.id).result == {"recovered": True}
    finally:
        release.set()
        jobs.close()


def test_nonwaiting_shutdown_leaves_retryable_work_for_next_recovery(store):
    started, release = Event(), Event()

    def transient(_):
        started.set()
        assert release.wait(10)
        raise RetryableJobError("Interrupted during shutdown")

    jobs = BackgroundJobs(store, {"work": transient}, max_pending=1)
    try:
        submitted = jobs.submit("tenant-a", "work", {})
        assert started.wait(10)
        jobs.close(wait=False)
        release.set()
        jobs.close(wait=True)
        interrupted = jobs.get("tenant-a", submitted.id)
        assert interrupted.status == "queued"
        assert interrupted.attempts == 1
        with BackgroundJobs(store, {"work": lambda _: {}}) as reopened:
            assert reopened.recover() == 1
            assert finished(reopened, submitted.id).attempts == 2
    finally:
        release.set()
        jobs.close()


def test_actual_process_crash_releases_lease_and_recovers_persisted_claim(tmp_path):
    database = tmp_path / "crash.sqlite3"
    backend = Path(__file__).resolve().parents[2] / "services" / "backend"
    script = """
import os, sys, threading
from tactiq.runtime import BackgroundJobs, SQLiteStore
started = threading.Event()
def work(payload):
    started.set()
    threading.Event().wait(30)
    return {}
store = SQLiteStore(sys.argv[1])
runner = BackgroundJobs(store, {"work": work})
job = runner.submit("tenant-a", "work", {})
if not started.wait(10):
    os._exit(2)
print(job.job_id, flush=True)
os._exit(0)
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(database)],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(backend), "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    job_id = result.stdout.strip()
    assert len(job_id) == 32
    with SQLiteStore(database) as store:
        assert store._get_job("tenant-a", job_id).status == "running"
        with BackgroundJobs(store, {"work": lambda _: {"recovered": True}}) as jobs:
            assert jobs.recover() == 1
            recovered = finished(jobs, job_id)
            assert recovered.status == "succeeded"
            assert recovered.attempts == 2
            assert recovered.result == {"recovered": True}


@pytest.mark.parametrize("name", ["max_workers", "max_pending", "max_attempts"])
@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_invalid_runner_limits_fail_before_starting_resources(store, name, value):
    with pytest.raises(RecordValidationError):
        BackgroundJobs(store, {}, **{name: value})
    with BackgroundJobs(store, {}) as jobs:
        assert jobs.cancel("tenant-a", "missing") is None


def test_invalid_payload_and_unsupported_kind_do_not_create_records(store):
    with BackgroundJobs(store, {"work": lambda _: {}}) as jobs:
        with pytest.raises(HTTPException) as error:
            jobs.submit("tenant-a", "unknown", {})
        assert error.value.status_code == 400
        for payload in ([], {"access_token": "redacted"}, {"duration": float("inf")}):
            with pytest.raises(RecordValidationError):
                jobs.submit("tenant-a", "work", payload)
        with store._read() as connection:
            assert connection.execute("SELECT COUNT(*) FROM runtime_jobs").fetchone()[0] == 0
