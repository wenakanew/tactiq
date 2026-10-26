"""HTTP origin-policy checks without starting the database-backed lifespan."""
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from tactiq.presentation.api.cloud import create_cloud_app
from tactiq.runtime.cloud_composition import CloudSettings


@pytest.fixture
def client(monkeypatch):
    origin = "https://web.test"
    monkeypatch.setenv("TACTIQ_PUBLIC_ORIGIN", origin)
    monkeypatch.setenv("TACTIQ_CORS_ALLOW_ORIGINS", origin)
    app = create_cloud_app()
    app.state.cloud = SimpleNamespace(settings=CloudSettings(origin))
    # No lifespan: these requests exercise middleware, not storage or sign-in.
    client = TestClient(app, base_url=origin)
    yield client
    client.close()


def test_cloud_preflight_allows_only_the_configured_browser_origin(client):
    headers = {
        "Origin": "https://web.test",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,idempotency-key",
    }
    response = client.options("/api/v1/session/guest", headers=headers)
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://web.test"
    assert response.headers["access-control-allow-credentials"] == "true"

    response = client.options(
        "/api/v1/session/guest", headers={**headers, "Origin": "https://untrusted.test"},
    )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_cloud_auth_errors_preserve_credentialed_cors_headers(client):
    response = client.get("/api/v1/session", headers={"Origin": "https://web.test"})
    assert response.status_code == 401
    assert response.headers["access-control-allow-origin"] == "https://web.test"
    assert response.headers["access-control-allow-credentials"] == "true"
    assert "retry-after" in response.headers["access-control-expose-headers"].lower()


@pytest.mark.parametrize("origin", [None, "https://untrusted.test"])
def test_cloud_writes_reject_missing_or_untrusted_origin_before_storage(client, origin):
    response = client.post(
        "/api/v1/session/guest", headers={"Origin": origin} if origin else {},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "A verified browser Origin is required"
