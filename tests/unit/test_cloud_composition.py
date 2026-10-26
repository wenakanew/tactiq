from __future__ import annotations

import pytest

from tactiq.application.cloud import CloudAnalysisService, ScenarioRequest
from tactiq.runtime.cloud_composition import CloudSettings, compose_cloud


@pytest.mark.parametrize("origin", [
    "http://app.test", "https://app.test/path", "https://user:password@app.test",
    "https://app.test?key=secret", "*", "", "https://app.test#fragment",
])
def test_cloud_requires_exact_https_browser_origins(origin):
    with pytest.raises(ValueError):
        CloudSettings(origin)


@pytest.mark.parametrize("callback", [
    "http://api.test/api/v1/auth/microsoft/callback",
    "https://api.test/api/v1/matches",
    "https://user:secret@api.test/api/v1/auth/microsoft/callback",
    "https://api.test/api/v1/auth/microsoft/callback?redirect=evil",
])
def test_callback_cannot_exempt_other_write_routes_from_origin_check(callback):
    with pytest.raises(ValueError):
        CloudSettings("https://web.test", microsoft_redirect_uri=callback)


def test_cloud_dependencies_never_substitute_local_storage(monkeypatch):
    monkeypatch.delenv("TACTIQ_POSTGRES_DSN", raising=False)
    with pytest.raises(ValueError, match="TACTIQ_POSTGRES_DSN"):
        compose_cloud()


def test_cloud_admission_refuses_single_host_access_adapter():
    with pytest.raises(ValueError, match="atomic job admission"):
        CloudAnalysisService(object(), object(), object())


def test_native_processing_has_no_default_approved_allocation():
    assert CloudSettings("https://web.test").processing_limit_per_day is None


@pytest.mark.parametrize("limit", [0, -1, 10001, True])
def test_native_processing_allocation_is_explicit_and_bounded(limit):
    with pytest.raises(ValueError):
        CloudSettings("https://web.test", processing_limit_per_day=limit)


def test_scenario_validation_precedes_admission():
    with pytest.raises(ValueError):
        ScenarioRequest(scenario_id="arbitrary", seed=1)
    with pytest.raises(ValueError):
        ScenarioRequest(seed=True)
    with pytest.raises(ValueError):
        ScenarioRequest(recipe_version=10)


def test_frame_checkpoint_cache_never_replaces_authoritative_finish_or_fence():
    from types import SimpleNamespace
    from tactiq.runtime.cloud_execution import CheckpointExecutionStore

    clock = [0.]

    class Store:
        polls = 0
        stopped = False

        def cancelled(self, *_):
            self.polls += 1
            clock[0] += 2  # Transport latency must not consume the hint TTL.
            return self.stopped

        def finish(self, *_):
            return not self.stopped

    store = Store()
    adapter = CheckpointExecutionStore(store, clock=lambda: clock[0])
    claim = SimpleNamespace(tenant_id="t", job_id="j", fence=1)
    assert not adapter.cancelled(claim, "owner")
    for _ in range(100):
        assert not adapter.cancelled(claim, "owner")
    assert store.polls == 1
    store.stopped = True
    assert not adapter.finish(claim, "owner", {})
    clock[0] += .6
    assert adapter.cancelled(claim, "owner")
    assert store.polls == 2


def test_shared_connection_gate_rejects_overflow_and_recovers_without_opening_more_connections():
    from contextlib import contextmanager
    from concurrent.futures import ThreadPoolExecutor
    from tactiq.runtime.cloud_connections import BoundedPostgresConnections, DatabaseCapacityError

    opened = []

    @contextmanager
    def connect():
        opened.append(object())
        yield opened[-1]

    gate = BoundedPostgresConnections(connect, maximum=2)

    def overflow(_):
        with pytest.raises(DatabaseCapacityError):
            with gate():
                pytest.fail("Overflow must never open a database connection")
        return True

    with gate(), gate():
        with ThreadPoolExecutor(max_workers=12) as pool:
            assert all(pool.map(overflow, range(100)))
        assert len(opened) == 2
    with gate():
        assert len(opened) == 3


def test_connection_startup_failure_releases_the_shared_slot():
    from contextlib import contextmanager
    from tactiq.runtime.cloud_connections import BoundedPostgresConnections

    attempts = []

    @contextmanager
    def connect():
        attempts.append(True)
        if len(attempts) == 1:
            raise RuntimeError("Connection is unavailable")
        yield object()

    gate = BoundedPostgresConnections(connect, maximum=1)
    with pytest.raises(RuntimeError):
        with gate():
            pass
    with gate():
        assert len(attempts) == 2


def test_managed_identity_database_refreshes_token_for_each_connection(monkeypatch):
    import sys
    from contextlib import nullcontext
    from types import SimpleNamespace

    from tactiq.runtime.cloud_connections import BoundedPostgresConnections

    psycopg = pytest.importorskip("psycopg")
    tokens = iter(["token-one", "token-two"])
    scopes, connections, identities = [], [], []

    def credential(**kwargs):
        identities.append(kwargs)

        def get_token(scope):
            scopes.append(scope)
            return SimpleNamespace(token=next(tokens))

        return nullcontext(SimpleNamespace(get_token=get_token))

    monkeypatch.setitem(sys.modules, "azure.identity", SimpleNamespace(ManagedIdentityCredential=credential))
    monkeypatch.setattr(
        psycopg, "connect",
        lambda dsn, **kwargs: connections.append((dsn, kwargs)) or nullcontext(object()),
    )
    dsn = "host=test.postgres.database.azure.com dbname=tactiq user=api sslmode=verify-full"
    gate = BoundedPostgresConnections.from_dsn(dsn, managed_identity=True, client_id="identity")
    with gate():
        pass
    with gate():
        pass
    assert [kwargs["password"] for _, kwargs in connections] == ["token-one", "token-two"]
    assert scopes == ["https://ossrdbms-aad.database.windows.net/.default"] * 2
    assert identities == [{"client_id": "identity"}] * 2
    assert all(value == dsn for value, _ in connections)


@pytest.mark.parametrize("dsn", [
    "host=localhost user=api sslmode=verify-full",
    "host=test.postgres.database.azure.com user=api sslmode=require",
    "host=test.postgres.database.azure.com sslmode=verify-full",
    "host=test.postgres.database.azure.com user=api sslmode=verify-full password=secret",
    "host=test.postgres.database.azure.com user=api sslmode=verify-full passfile=private",
    "host=test.postgres.database.azure.com hostaddr=127.0.0.1 user=api sslmode=verify-full",
    "host=other,test.postgres.database.azure.com user=api sslmode=verify-full",
])
def test_managed_identity_database_rejects_unsafe_dsn_before_authentication(dsn):
    from tactiq.runtime.cloud_connections import BoundedPostgresConnections

    pytest.importorskip("psycopg")
    with pytest.raises(ValueError, match="password-free Azure DSN"):
        BoundedPostgresConnections.from_dsn(dsn, managed_identity=True)

@pytest.mark.parametrize("maximum", [0, 17, True])
def test_connection_admission_must_have_a_small_explicit_bound(maximum):
    from tactiq.runtime.cloud_connections import BoundedPostgresConnections

    with pytest.raises(ValueError):
        BoundedPostgresConnections(lambda: None, maximum=maximum)


@pytest.mark.parametrize("missing", ["TACTIQ_CLOUD_ANALYSES_PER_DAY", "TACTIQ_MICROSOFT_CLIENT_SECRET", "TACTIQ_BLOB_ACCOUNT_URL"])
def test_production_composition_fails_before_network_when_required_configuration_is_missing(monkeypatch, missing):
    values = {
        "TACTIQ_POSTGRES_DSN": "host=127.0.0.1 port=1 dbname=not_used",
        "TACTIQ_IP_HMAC_KEY_HEX": "6b" * 32,
        "TACTIQ_PUBLIC_ORIGIN": "https://web.test",
        "TACTIQ_CLOUD_ANALYSES_PER_DAY": "disabled",
        "TACTIQ_MICROSOFT_CLIENT_ID": "00000000-0000-0000-0000-000000000001",
        "TACTIQ_MICROSOFT_REDIRECT_URI": "https://api.test/api/v1/auth/microsoft/callback",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv(missing, raising=False)
    with pytest.raises(ValueError, match=missing):
        compose_cloud(include_identity=missing != "TACTIQ_BLOB_ACCOUNT_URL")


def test_private_media_startup_failure_closes_resources_instead_of_serving_local_data():
    from fastapi.testclient import TestClient
    from tactiq.presentation.api.cloud import create_cloud_app
    from tactiq.runtime.cloud_composition import CloudDependencies

    class Store:
        def healthcheck(self):
            return None

    class Media:
        closed = False

        def healthcheck(self):
            raise PermissionError("Private container is unavailable")

        def close(self):
            self.closed = True

    media = Media()
    dependencies = CloudDependencies(CloudSettings("https://web.test"), Store(), object(), media, owned=True)
    with pytest.raises(PermissionError):
        with TestClient(create_cloud_app(dependencies)):
            pytest.fail("An unverified public/unavailable container must not become ready")
    assert media.closed
