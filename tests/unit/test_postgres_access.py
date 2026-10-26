from __future__ import annotations

import builtins

import pytest

from tactiq.runtime.postgres_access import PostgresAccessStore


def test_postgres_access_constructor_never_connects_or_silently_uses_sqlite():
    calls = []

    def unavailable():
        calls.append(True)
        raise ConnectionError("database unavailable")

    store = PostgresAccessStore(
        unavailable, ip_hmac_key=b"test-only-private-ip-hmac-key-at-least-32",
    )
    assert calls == []
    with pytest.raises(ConnectionError, match="database unavailable"):
        store.issue_guest()
    assert calls == [True]


def test_postgres_access_optional_dependency_failure_is_explicit(monkeypatch):
    real_import = builtins.__import__

    def no_psycopg(name, *args, **kwargs):
        if name == "psycopg":
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_psycopg)
    with pytest.raises(RuntimeError, match="optional PostgreSQL"):
        PostgresAccessStore.from_dsn(
            "unused", ip_hmac_key=b"test-only-private-ip-hmac-key-at-least-32",
        )


def test_postgres_access_rejects_unsafe_configuration_without_connecting():
    def never():
        raise AssertionError("unexpected connection")

    with pytest.raises(ValueError, match="HMAC"):
        PostgresAccessStore(never, ip_hmac_key=b"short")
    with pytest.raises(ValueError, match="validated usage policy"):
        PostgresAccessStore(
            never, ip_hmac_key=b"test-only-private-ip-hmac-key-at-least-32",
            usage_policy={"global_limit": 150},
        )


def test_postgres_access_from_dsn_connects_only_on_explicit_operation(monkeypatch):
    psycopg = pytest.importorskip("psycopg")
    calls = []

    def unavailable(dsn, **kwargs):
        calls.append((dsn, kwargs))
        raise ConnectionError("database unavailable")

    monkeypatch.setattr(psycopg, "connect", unavailable)
    store = PostgresAccessStore.from_dsn(
        "host=unavailable.invalid", ip_hmac_key=b"test-only-private-ip-hmac-key-at-least-32",
    )
    assert calls == []
    with pytest.raises(ConnectionError):
        store.migrate()
    assert len(calls) == 1 and calls[0][1]["connect_timeout"] == 10
    with pytest.raises(ConnectionError):
        PostgresAccessStore.from_dsn(
            "host=unavailable.invalid", ip_hmac_key=b"test-only-private-ip-hmac-key-at-least-32",
            migrate=True,
        )
    assert len(calls) == 2
