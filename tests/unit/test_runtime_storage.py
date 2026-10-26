from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from threading import Barrier, Event
from types import SimpleNamespace

import pytest

from tactiq.runtime import (
    RecordValidationError,
    SQLiteStore,
    StorageCorruptionError,
    StorageVersionError,
    TenantConflictError,
)
from tactiq.runtime import storage as storage_module


@pytest.fixture
def store(tmp_path):
    with SQLiteStore(tmp_path / "runtime.sqlite3") as instance:
        yield instance


@pytest.fixture
def initialization_clock(monkeypatch):
    clock = SimpleNamespace(now=0.0, sleeps=[])

    def sleep(delay):
        clock.sleeps.append(delay)
        clock.now += delay

    monkeypatch.setattr(
        storage_module, "time", SimpleNamespace(monotonic=lambda: clock.now, sleep=sleep)
    )
    return clock


def test_aggregate_round_trip_survives_restart_and_is_detached(tmp_path):
    database = tmp_path / "nested" / "runtime.sqlite3"
    snapshot = {
        "match_id": "match-1",
        "state": {"clock": 123.5, "score": [1, 0]},
        "events": [{"kind": "pass", "time": 121}],
        "insights": [{"reason": "sustained pressure", "confidence": 0.8}],
        "viewer_profile": {"mode": "analyst"},
        "commentary_audit": [{"text": "A sustained attack", "event_ids": ["e-1"]}],
        "commentary_memory": {"recent": ["pressure"]},
        "source_video_path": "uploads\\video.mp4",
        "duration": 180.25,
        "clip_map": {"e-1": {"start": 120, "end": 125}},
        "replay": {"version": 1, "frames": []},
        "cv_metadata": {"provider": "opencv", "frames": 15},
    }
    expected = json.loads(json.dumps(snapshot))
    with SQLiteStore(database) as original:
        original.save_match("tenant-a", "match-1", snapshot)
        snapshot["events"].clear()
        loaded = original.load_match("tenant-a", "match-1")
        assert loaded == expected
        loaded["state"]["clock"] = 0
        assert original.load_match("tenant-a", "match-1") == expected
    with SQLiteStore(database) as reopened:
        assert reopened.load_match("tenant-a", "match-1") == expected
        assert reopened.healthcheck() == {
            "ok": True,
            "schema_version": 1,
            "journal_mode": "wal",
            "foreign_keys": True,
        }


def test_tenant_isolation_includes_overwrites_deletes_and_listing(store):
    store.save_match("tenant-a", "shared-id", {"state": {"score": 1}})
    assert store.load_match("tenant-b", "shared-id") is None
    assert store.list_matches("tenant-b") == []
    assert not store.delete_match("tenant-b", "shared-id")
    with pytest.raises(TenantConflictError, match="unavailable"):
        store.save_match("tenant-b", "shared-id", {"state": {"score": 9}})
    assert store.load_match("tenant-a", "shared-id") == {"state": {"score": 1}}
    store.save_match("tenant-b", "another-id", {})
    assert [item["match_id"] for item in store.list_matches("tenant-b")] == ["another-id"]
    assert store.delete_match("tenant-a", "shared-id")
    assert not store.delete_match("tenant-a", "shared-id")
    assert store.load_match("tenant-a", "shared-id") is None


def test_metadata_listing_is_bounded_ordered_and_preserves_created_at(store):
    store.save_match("tenant-a", "older", {"revision": 0})
    created = store.list_matches("tenant-a")[0]["created_at"]
    store.save_match("tenant-a", "newer", {"revision": 0})
    assert [item["match_id"] for item in store.list_matches("tenant-a", limit=1)] == ["newer"]
    store.save_match("tenant-a", "older", {"revision": 1})
    metadata = store.list_matches("tenant-a")
    assert [item["match_id"] for item in metadata] == ["older", "newer"]
    assert set(metadata[0]) == {"match_id", "tenant_id", "created_at", "updated_at"}
    assert metadata[0]["created_at"] == created
    assert datetime.fromisoformat(metadata[0]["updated_at"]).tzinfo is not None
    assert store.list_matches("tenant-a", limit=0) == []


@pytest.mark.parametrize("limit", [-1, 1001, 1.5, True, None])
def test_invalid_list_limits_are_explicit(store, limit):
    with pytest.raises(RecordValidationError):
        store.list_matches("tenant-a", limit=limit)


@pytest.mark.parametrize(
    "snapshot",
    [
        [],
        None,
        {"number": float("nan")},
        {"number": float("inf")},
        {1: "non-string key"},
        {"tuple": (1, 2)},
        {"set": {1}},
        {"bytes": b"media"},
        {"nested": {"Authorization": "not-a-credential"}},
        {"nested": [{"api-key": "not-a-credential"}]},
        {"nested": {"token_sha256": "not-a-credential"}},
        {"cookie": "not-a-credential"},
        {"bad_unicode": "\ud800"},
    ],
)
def test_invalid_snapshot_does_not_overwrite_valid_aggregate(store, snapshot):
    store.save_match("tenant-a", "match-1", {"revision": 1})
    with pytest.raises(RecordValidationError):
        store.save_match("tenant-a", "match-1", snapshot)
    assert store.load_match("tenant-a", "match-1") == {"revision": 1}


def test_cycles_and_excessively_nested_records_are_explicit(store):
    cyclic = {}
    cyclic["self"] = cyclic
    with pytest.raises(RecordValidationError, match="nesting"):
        store.save_match("tenant-a", "match-1", cyclic)


@pytest.mark.parametrize("identity", ["", " ", " tenant", "tenant\n", "a" * 257, None, 1])
def test_invalid_identity_is_explicit(store, identity):
    with pytest.raises(RecordValidationError):
        store.save_match(identity, "match-1", {})
    with pytest.raises(RecordValidationError):
        store.load_match("tenant-a", identity)
    with pytest.raises(RecordValidationError):
        store.delete_match(identity, "match-1")


@pytest.mark.parametrize(
    "snapshot", [{"match_id": "different"}, {"tenant_id": "tenant-b"}, {"match_id": None}]
)
def test_snapshot_identity_cannot_disagree_with_record_identity(store, snapshot):
    with pytest.raises(RecordValidationError, match="identity"):
        store.save_match("tenant-a", "match-1", snapshot)
    assert store.load_match("tenant-a", "match-1") is None


def test_concurrent_writers_save_whole_snapshots(store):
    def write(revision):
        snapshot = {"revision": revision, "events": [{"revision": revision}] * 10}
        store.save_match("tenant-a", "shared", snapshot)
        observed = store.load_match("tenant-a", "shared")
        assert all(event["revision"] == observed["revision"] for event in observed["events"])
        store.save_match("tenant-a", f"match-{revision}", snapshot)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(write, range(24)))
    assert len(store.list_matches("tenant-a")) == 25


def test_concurrent_tenant_collision_has_exactly_one_owner(store):
    barrier = Barrier(2)

    def save(tenant):
        barrier.wait(timeout=5)
        try:
            store.save_match(tenant, "same-id", {"owner": tenant})
        except TenantConflictError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(save, ["tenant-a", "tenant-b"]))
    assert sorted(outcomes) == [False, True]
    owners = [tenant for tenant in ("tenant-a", "tenant-b") if store.load_match(tenant, "same-id")]
    assert len(owners) == 1


def test_connections_enforce_foreign_keys_and_failed_transaction_rolls_back(store):
    with pytest.raises(sqlite3.IntegrityError):
        with store._write() as connection:
            connection.execute(
                """INSERT INTO runtime_matches
                    (match_id, tenant_id, snapshot_json, created_at, updated_at)
                    VALUES ('orphan', 'missing-tenant', '{}', 'now', 'now')"""
            )
    store.save_match("tenant-a", "valid", {})
    assert len(store.list_matches("tenant-a")) == 1


@pytest.mark.parametrize("encoded", ["[]", "{", '{"n": NaN}', '{"access_token": "redacted"}'])
def test_corrupt_records_fail_explicitly(store, encoded):
    store.save_match("tenant-a", "match-1", {})
    with store._write() as connection:
        connection.execute(
            "UPDATE runtime_matches SET snapshot_json = ? WHERE match_id = 'match-1'",
            (encoded,),
        )
    with pytest.raises(StorageCorruptionError, match="validation"):
        store.load_match("tenant-a", "match-1")
    assert store.load_match("tenant-b", "match-1") is None


def test_newer_schema_is_not_downgraded(tmp_path):
    database = tmp_path / "newer.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA user_version = 99")
    with pytest.raises(StorageVersionError):
        SQLiteStore(database)
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 99


@pytest.mark.parametrize("round_number", range(3))
def test_concurrent_initializers_share_one_schema(tmp_path, round_number):
    database = tmp_path / "runtime.sqlite3"
    barrier = Barrier(8)

    def initialize(index):
        barrier.wait(timeout=5)
        with SQLiteStore(database) as instance:
            instance.save_match("tenant-a", f"thread-{index}", {"round": round_number})
            return instance.healthcheck()["schema_version"]

    with ThreadPoolExecutor(max_workers=8) as executor:
        assert list(executor.map(initialize, range(8))) == [1] * 8
    with SQLiteStore(database) as instance:
        assert len(instance.list_matches("tenant-a")) == 8


def test_concurrent_process_initializers_share_one_schema(tmp_path):
    database = tmp_path / "processes.sqlite3"
    gate = tmp_path / "initialize"
    backend = Path(__file__).resolve().parents[2] / "services" / "backend"
    environment = {
        **os.environ,
        "PYTHONPATH": str(backend),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    script = """
import pathlib, sys, time
from tactiq.runtime import SQLiteStore
database, ready, gate, index = sys.argv[1:]
pathlib.Path(ready).touch()
deadline = time.monotonic() + 20
while not pathlib.Path(gate).exists():
    if time.monotonic() >= deadline:
        raise TimeoutError("Initialization gate timed out")
    time.sleep(0.01)
with SQLiteStore(database) as store:
    store.save_match("tenant-a", "process-" + index, {"index": int(index)})
    print(store.healthcheck()["schema_version"])
"""

    def initialize(index):
        return subprocess.run(
            [
                sys.executable, "-B", "-c", script, str(database),
                str(tmp_path / f"ready-{index}"), str(gate), str(index),
            ],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(initialize, index) for index in range(4)]
        try:
            deadline = time.monotonic() + 20
            while not all((tmp_path / f"ready-{index}").exists() for index in range(4)):
                for future in futures:
                    if future.done():
                        pytest.fail(f"Initializer exited before its gate: {future.result().stderr}")
                if time.monotonic() >= deadline:
                    pytest.fail("Timed out waiting for process initialization")
                time.sleep(0.01)
            gate.touch()
            for future in futures:
                result = future.result(timeout=30)
                assert result.returncode == 0, result.stderr
                assert result.stdout.strip() == "1"
        finally:
            gate.touch()
    with SQLiteStore(database) as instance:
        assert len(instance.list_matches("tenant-a")) == 4


@pytest.mark.parametrize(
    "code",
    [
        sqlite3.SQLITE_BUSY,
        sqlite3.SQLITE_LOCKED,
        sqlite3.SQLITE_BUSY_SNAPSHOT,
        sqlite3.SQLITE_LOCKED_SHAREDCACHE,
    ],
)
def test_initialization_retries_only_busy_and_locked_codes(
    tmp_path, monkeypatch, initialization_clock, code
):
    original = SQLiteStore._initialize_once
    attempts = []
    error = sqlite3.OperationalError("Transient initialization lock")
    error.sqlite_errorcode = code

    def attempt(instance):
        attempts.append(1)
        if len(attempts) <= 2:
            raise error
        original(instance)

    monkeypatch.setattr(SQLiteStore, "_initialize_once", attempt)
    with SQLiteStore(tmp_path / "retry.sqlite3") as instance:
        assert instance.healthcheck()["schema_version"] == 1
    assert len(attempts) == 3
    assert initialization_clock.sleeps == [0.01, 0.02]


@pytest.mark.parametrize(
    "code", [sqlite3.SQLITE_ERROR, sqlite3.SQLITE_READONLY, sqlite3.SQLITE_CANTOPEN, None]
)
def test_initialization_preserves_non_lock_failures_without_retry(
    tmp_path, monkeypatch, initialization_clock, code
):
    attempts = []
    error = sqlite3.OperationalError("Explicit initialization failure")
    if code is not None:
        error.sqlite_errorcode = code

    def attempt(_):
        attempts.append(1)
        raise error

    monkeypatch.setattr(SQLiteStore, "_initialize_once", attempt)
    with pytest.raises(sqlite3.OperationalError) as captured:
        SQLiteStore(tmp_path / "failure.sqlite3")
    assert captured.value is error
    assert len(attempts) == 1
    assert initialization_clock.sleeps == []


def test_initialization_lock_retry_budget_is_bounded(
    tmp_path, monkeypatch, initialization_clock
):
    monkeypatch.setattr(storage_module, "_INITIALIZATION_TIMEOUT_SECONDS", 0.05)
    attempts = []
    error = sqlite3.OperationalError("database is locked")
    error.sqlite_errorcode = sqlite3.SQLITE_BUSY

    def attempt(_):
        attempts.append(1)
        raise error

    monkeypatch.setattr(SQLiteStore, "_initialize_once", attempt)
    with pytest.raises(sqlite3.OperationalError) as captured:
        SQLiteStore(tmp_path / "bounded.sqlite3")
    assert captured.value is error
    assert len(attempts) == 3
    assert initialization_clock.now == pytest.approx(0.05)
    assert initialization_clock.sleeps == pytest.approx([0.01, 0.02, 0.02])


def test_initialization_retries_a_real_exclusive_lock_then_succeeds(tmp_path, monkeypatch):
    database = tmp_path / "locked.sqlite3"
    blocked = Event()
    original = SQLiteStore._initialize_once

    def attempt(instance):
        try:
            original(instance)
        except sqlite3.OperationalError as error:
            if error.sqlite_errorcode & 0xFF in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                blocked.set()
            raise

    monkeypatch.setattr(SQLiteStore, "_initialize_once", attempt)
    connection = sqlite3.connect(database, isolation_level=None)
    try:
        connection.execute("BEGIN EXCLUSIVE")
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(SQLiteStore, database)
            try:
                assert blocked.wait(timeout=5)
                assert not future.done()
            finally:
                connection.rollback()
            with future.result(timeout=5) as instance:
                assert instance.healthcheck()["journal_mode"] == "wal"
    finally:
        connection.close()


def test_initialization_persistent_lock_fails_explicitly_and_releases_connections(
    tmp_path, monkeypatch
):
    database = tmp_path / "still-locked.sqlite3"
    monkeypatch.setattr(storage_module, "_INITIALIZATION_TIMEOUT_SECONDS", 0.05)
    connection = sqlite3.connect(database, isolation_level=None)
    try:
        connection.execute("BEGIN EXCLUSIVE")
        with pytest.raises(sqlite3.OperationalError) as captured:
            SQLiteStore(database)
        assert captured.value.sqlite_errorcode & 0xFF in {
            sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED
        }
        connection.rollback()
    finally:
        connection.close()
    with SQLiteStore(database) as instance:
        assert instance.healthcheck()["schema_version"] == 1


def test_close_is_explicit_and_idempotent(store):
    store.close()
    store.close()
    with pytest.raises(RuntimeError, match="closed"):
        store.load_match("tenant-a", "match-1")


@pytest.mark.parametrize("path", ["", " ", ":memory:", "bad\0path"])
def test_store_requires_a_real_file_path(path):
    with pytest.raises(RecordValidationError):
        SQLiteStore(path)


def test_runtime_import_does_not_touch_disk_or_import_existing_application(tmp_path):
    backend = Path(__file__).resolve().parents[2] / "services" / "backend"
    environment = {
        **os.environ,
        "PYTHONPATH": str(backend),
        "PYTHONDONTWRITEBYTECODE": "1",
        "APP_ENV": "production",
        "TACTIQ_AUTH_ENABLED": "false",
    }
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            "import pathlib, sys; import tactiq.runtime; "
            "assert 'tactiq.application.service' not in sys.modules; "
            "assert 'tactiq.presentation.main' not in sys.modules; "
            "assert not list(pathlib.Path('.').iterdir())",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
