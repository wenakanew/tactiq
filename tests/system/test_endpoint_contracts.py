"""HTTP contracts using real lifespan, tenant authentication, and SQLite storage."""

from __future__ import annotations

from collections import Counter
import json
import time

import pytest


HTTP_OPERATIONS = [
    ("GET", "/health/live"),
    ("GET", "/health/ready"),
    ("GET", "/api/v1/capabilities"),
    ("GET", "/api/v1/session"),
    ("POST", "/api/v1/session"),
    ("POST", "/api/v1/session/logout"),
    ("GET", "/api/v1/operations/metrics"),
    ("GET", "/api/v1/operations/status"),
    ("GET", "/api/v1/operations/prometheus"),
    ("GET", "/api/v1/matches"),
    ("POST", "/api/v1/matches"),
    ("GET", "/api/v1/scenarios"),
    ("POST", "/api/v1/matches/{match_id}/start"),
    ("POST", "/api/v1/matches/{match_id}/pause"),
    ("GET", "/api/v1/matches/{match_id}/replay"),
    ("DELETE", "/api/v1/matches/{match_id}"),
    ("GET", "/api/v1/matches/{match_id}/media/source"),
    ("GET", "/media/{match_id}/{filename}"),
    ("GET", "/api/v1/matches/{match_id}/state"),
    ("GET", "/api/v1/matches/{match_id}/events"),
    ("GET", "/api/v1/matches/{match_id}/insights"),
    ("POST", "/api/v1/matches/{match_id}/viewer-profile"),
    ("GET", "/api/v1/matches/{match_id}/overlay"),
    ("POST", "/api/v1/video/upload"),
    ("POST", "/api/v1/video/from-link"),
    ("POST", "/api/v1/cv/ingest/upload"),
    ("POST", "/api/v1/video/jobs"),
    ("POST", "/api/v1/video/jobs/from-link"),
    ("GET", "/api/v1/jobs/{job_id}"),
    ("POST", "/api/v1/jobs/{job_id}/cancel"),
    ("POST", "/api/v1/matches/{match_id}/narratives"),
    ("POST", "/api/v1/matches/{match_id}/recap"),
    ("POST", "/api/v1/speech/synthesize"),
    ("POST", "/api/v1/speech/synthesize-dual"),
    ("POST", "/api/v1/matches/{match_id}/commentary/dual"),
    ("GET", "/api/v1/matches/{match_id}/commentary/debug"),
]
PUBLIC = {"/health/live", "/health/ready", "/api/v1/capabilities"}
VIEWER_WRITES = {
    "/api/v1/session", "/api/v1/session/logout",
    "/api/v1/matches/{match_id}/narratives", "/api/v1/matches/{match_id}/recap",
    "/api/v1/matches/{match_id}/commentary/dual",
    "/api/v1/speech/synthesize", "/api/v1/speech/synthesize-dual",
}
ADMIN_OPERATIONS = [
    operation for operation in HTTP_OPERATIONS
    if operation[0] == "DELETE" or operation[1].startswith("/api/v1/operations/")
]
PRIVILEGED_OPERATIONS = [
    operation for operation in HTTP_OPERATIONS
    if operation in ADMIN_OPERATIONS
    or (operation[0] == "POST" and operation[1] not in VIEWER_WRITES)
    or operation[1].endswith("/commentary/debug")
]
MATCH_OPERATIONS = [operation for operation in HTTP_OPERATIONS if "{match_id}" in operation[1]]
JSON_OPERATIONS = [
    "/api/v1/matches", "/api/v1/matches/{match_id}/viewer-profile",
    "/api/v1/video/from-link", "/api/v1/video/jobs/from-link",
    "/api/v1/matches/{match_id}/narratives", "/api/v1/matches/{match_id}/recap",
    "/api/v1/matches/{match_id}/commentary/dual",
    "/api/v1/speech/synthesize", "/api/v1/speech/synthesize-dual",
]


def operation_id(operation):
    return f"{operation[0]}-{operation[1].strip('/').replace('/', '-')}"


def path_values(match_id="missing_match"):
    return {"match_id": match_id, "job_id": "missing_job", "filename": "a" * 32 + ".mp4"}


@pytest.mark.parametrize("method,route", HTTP_OPERATIONS, ids=list(map(operation_id, HTTP_OPERATIONS)))
def test_each_declared_http_operation_has_explicit_anonymous_admission(api, method, route):
    response = api.request(
        method, route, case="anonymous-admission", actor=None,
        status=200 if route in PUBLIC else 401, path_params=path_values(),
    )
    if route not in PUBLIC:
        assert response.headers["www-authenticate"] == "Bearer"
        assert "detail" in response.json()


@pytest.mark.parametrize(
    "method,route", [operation for operation in HTTP_OPERATIONS if operation[1] not in PUBLIC],
    ids=[operation_id(operation) for operation in HTTP_OPERATIONS if operation[1] not in PUBLIC],
)
def test_each_protected_http_operation_rejects_a_bad_bearer(api, method, route):
    api.request(
        method, route, case="invalid-bearer", status=401, actor=None,
        headers={"Authorization": "Bearer offline-not-configured"}, path_params=path_values(),
    )


@pytest.mark.parametrize("method,route", PRIVILEGED_OPERATIONS, ids=list(map(operation_id, PRIVILEGED_OPERATIONS)))
def test_every_privileged_operation_denies_viewers_before_resource_lookup(api, method, route):
    api.request(
        method, route, case="viewer-role-denial", actor="viewer", status=403,
        path_params=path_values(),
    )


@pytest.mark.parametrize("method,route", ADMIN_OPERATIONS, ids=list(map(operation_id, ADMIN_OPERATIONS)))
def test_admin_operations_deny_analysts(api, method, route):
    api.request(
        method, route, case="analyst-admin-denial", actor="analyst", status=403,
        path_params=path_values(),
    )


@pytest.mark.parametrize("method,route", MATCH_OPERATIONS, ids=list(map(operation_id, MATCH_OPERATIONS)))
def test_every_match_operation_hides_unknown_matches(api, method, route):
    api.request(method, route, case="unknown-match", status=404, path_params=path_values())


@pytest.mark.parametrize("method,route", MATCH_OPERATIONS, ids=list(map(operation_id, MATCH_OPERATIONS)))
def test_every_match_operation_hides_foreign_tenant_matches(api, match_id, method, route):
    api.request(
        method, route, case="foreign-tenant-match", status=404, actor="foreign",
        path_params=path_values(match_id),
    )
    assert api.service.matches[match_id].tenant_id == "audit_a"


@pytest.mark.parametrize("route", JSON_OPERATIONS, ids=lambda route: route.strip("/").replace("/", "-"))
@pytest.mark.parametrize("body_kind", ["missing", "malformed", "null", "array"])
def test_json_operations_reject_missing_or_malformed_bodies(api, match_id, route, body_kind):
    body = {
        "missing": {}, "malformed": {"content": b"{invalid", "headers": {"Content-Type": "application/json"}},
        "null": {"content": b"null", "headers": {"Content-Type": "application/json"}},
        "array": {"json": []},
    }[body_kind]
    api.request(
        "POST", route, case=f"invalid-json-{body_kind}", status=422,
        path_params={"match_id": match_id}, **body,
    )


@pytest.mark.parametrize("route,expected", [("/health/live", "ok"), ("/health/ready", "ready")])
def test_health_checks_are_live_and_ready_without_auth(api, route, expected):
    response = api.request("GET", route, case="healthy-runtime", actor=None)
    assert response.json() == {"status": expected}


def test_capabilities_are_public_but_explicit_about_missing_models(api):
    payload = api.request("GET", "/api/v1/capabilities", case="missing-cloud-capabilities", actor=None).json()
    assert payload["auth_enabled"] is True
    assert payload["cv_model_configured"] is False
    assert payload["value_model_status"] == "unavailable"
    assert payload["languages"] == ["en-GB", "sw-KE", "fr-FR", "es-ES", "pt-BR", "ar-SA", "hi-IN"]
    assert not any("key" in key or "endpoint" in key or "token" in key for key in payload)


@pytest.mark.parametrize("name", ["metrics", "status", "prometheus"])
def test_operations_have_real_offline_runtime_telemetry(api, name):
    api.request("GET", "/health/live", case="metrics-sample", actor=None)
    response = api.request("GET", f"/api/v1/operations/{name}", case="admin-operations-valid")
    if name == "prometheus":
        assert response.headers["content-type"].startswith("text/plain")
        assert "tactiq_http_requests_total " in response.text
    elif name == "metrics":
        assert {"http", "pipeline", "jobs"} == set(response.json())
        assert response.json()["http"]["requests_total"] >= 1
    else:
        assert response.json()["status"] == "insufficient_samples"
        assert response.json()["alerts"] == []


def test_current_session_returns_only_the_test_identity(api):
    response = api.request("GET", "/api/v1/session", case="viewer-session", actor="viewer")
    assert response.json() == {
        "auth_enabled": True, "tenant_id": "audit_a", "subject": "viewer", "role": "viewer",
    }
    assert response.headers["cache-control"] == "private, no-store"


def test_browser_cookie_exchange_origin_checks_and_logout(api, match_id):
    session = api.request("POST", "/api/v1/session", case="cookie-exchange", actor="analyst")
    cookie = session.headers["set-cookie"]
    assert all(part in cookie for part in ("HttpOnly", "Secure", "SameSite=lax", "Path=/", "Max-Age=3600"))
    api.request(
        "GET", "/api/v1/matches/{match_id}/state", case="cookie-read", actor=None,
        path_params={"match_id": match_id},
    )
    api.request("POST", "/api/v1/matches", case="cookie-write-without-origin", actor=None, status=403, json={})
    api.request(
        "POST", "/api/v1/matches", case="cookie-write-foreign-origin", actor=None, status=403,
        headers={"Origin": "https://untrusted.invalid"}, json={},
    )
    api.request(
        "POST", "/api/v1/matches", case="cookie-write-verified-origin", actor=None,
        headers={"Origin": "https://audit.example"}, json={},
    )
    api.request(
        "GET", "/api/v1/session", case="bad-header-cannot-fall-back-to-cookie", actor=None, status=401,
        headers={"Authorization": "Bearer offline-invalid"},
    )
    logout = api.request(
        "POST", "/api/v1/session/logout", case="logout-cookie", actor=None,
        headers={"Origin": "https://audit.example"},
    )
    assert logout.json() == {"status": "signed_out"}
    assert "Max-Age=0" in logout.headers["set-cookie"]
    api.request("GET", "/api/v1/session", case="cookie-removed-after-logout", actor=None, status=401)


@pytest.mark.parametrize("scheme", ["bearer", "BEARER", "bEaReR"])
def test_session_exchange_accepts_case_insensitive_bearer_scheme(api, scheme):
    api.request(
        "POST", "/api/v1/session", case=f"bearer-scheme-{scheme.lower()}", actor=None,
        headers={"Authorization": f"{scheme} offline-a-viewer"},
    )


def test_session_exchange_requires_bearer_even_with_a_valid_cookie(api):
    api.request("POST", "/api/v1/session", case="seed-browser-session", actor="viewer")
    api.request(
        "POST", "/api/v1/session", case="cookie-is-not-bearer-exchange", actor=None, status=401,
        headers={"Origin": "https://audit.example"},
    )


@pytest.mark.parametrize("route", ["/docs", "/redoc", "/docs/oauth2-redirect", "/openapi.json"])
@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_documentation_routes_are_authenticated_and_served(api, route, method):
    response = api.request(method, route, case="authenticated-api-documentation", actor="viewer")
    if method == "HEAD":
        assert response.content == b""
    elif route == "/openapi.json":
        document = response.json()
        declared = {
            (verb.upper(), path) for path, operations in document["paths"].items()
            for verb in operations if verb.upper() in {"GET", "POST", "DELETE"}
        }
        assert declared == set(HTTP_OPERATIONS)
    else:
        assert response.headers["content-type"].startswith("text/html")


@pytest.mark.parametrize("route", ["/docs", "/redoc", "/docs/oauth2-redirect", "/openapi.json"])
def test_documentation_does_not_bypass_authentication(api, route):
    api.request("GET", route, case="documentation-auth-denial", actor=None, status=401)


@pytest.mark.parametrize(
    "route,verb", [
        ("/api/v1/matches", "POST"),
        ("/api/v1/matches/{match_id}/media/source", "GET"),
        ("/media/{match_id}/{filename}", "GET"),
    ],
)
def test_cors_preflight_allows_expected_methods_headers_and_credentials(api, route, verb):
    response = api.request(
        "OPTIONS", route, case="cors-allowed-preflight", actor=None, policy_headers=False,
        path_params=path_values(), headers={
            "Origin": "https://audit.example", "Access-Control-Request-Method": verb,
            "Access-Control-Request-Headers": "Authorization,Content-Type,X-Request-ID,Range",
        },
    )
    assert response.headers["access-control-allow-origin"] == "https://audit.example"
    assert response.headers["access-control-allow-credentials"] == "true"
    assert verb in response.headers["access-control-allow-methods"]


def test_cors_rejects_untrusted_preflight_and_bearer_origin(api):
    api.request(
        "OPTIONS", "/api/v1/matches", case="cors-rejected-preflight", actor=None,
        status=400, policy_headers=False, headers={
            "Origin": "https://untrusted.invalid", "Access-Control-Request-Method": "POST",
        },
    )
    api.request(
        "GET", "/api/v1/session", case="bearer-origin-denial", status=403,
        headers={"Origin": "https://untrusted.invalid"},
    )


@pytest.mark.parametrize("scenario", ["sustained_pressure", "rhythm_shift", "player_influence"])
def test_each_scenario_creates_and_exposes_a_consistent_match(api, scenario):
    identifier = api.create(scenario=scenario, actor="analyst", seed=123)
    state = api.request(
        "GET", "/api/v1/matches/{match_id}/state", case="new-match-state", actor="viewer",
        path_params={"match_id": identifier},
    ).json()
    assert state["scenario_id"] == scenario and state["seed"] == 123
    assert state["status"] == "created" and state["last_sequence"] == 0
    assert state["tenant_id"] == "audit_a"


def test_empty_create_body_uses_declared_defaults_and_names_are_trimmed(api):
    created = api.request("POST", "/api/v1/matches", case="empty-object-defaults", json={}).json()
    assert created["scenario_id"] == "sustained_pressure" and created["seed"] == 42001
    named = api.request(
        "POST", "/api/v1/matches", case="team-display-names",
        json={"team_display_names": {"team_a": "  Fixture A  ", "team_b": "Fixture B"}},
    ).json()
    assert named["team_display_names"] == {"team_a": "Fixture A", "team_b": "Fixture B"}


@pytest.mark.parametrize(
    "payload",
    [
        {"scenario_id": "not_a_scenario"}, {"seed": []},
        {"team_display_names": {"team_a": " ", "team_b": "B"}},
        {"team_display_names": {"team_a": "A" * 81, "team_b": "B"}},
        {"team_display_names": {"team_a": "A", "team_b": "B", "extra": "C"}},
    ],
    ids=["unknown-scenario", "invalid-seed", "blank-name", "oversized-name", "unknown-name-field"],
)
def test_invalid_create_inputs_are_rejected_without_state_changes(api, payload):
    api.request("POST", "/api/v1/matches", case="invalid-create-input", status=422, json=payload)
    assert not api.service.matches


def test_list_matches_is_empty_then_tenant_scoped_and_bounded(api):
    assert api.request("GET", "/api/v1/matches", case="empty-match-list", actor="viewer").json() == []
    own = api.create()
    foreign = api.create(actor="foreign")
    records = api.request("GET", "/api/v1/matches", case="tenant-match-list", actor="viewer", params={"limit": 1}).json()
    assert len(records) == 1 and records[0]["match_id"] == own
    assert foreign not in {record["match_id"] for record in records}


@pytest.mark.parametrize("limit", ["0", "-1", "101", "invalid"])
@pytest.mark.parametrize("route", ["/api/v1/matches", "/api/v1/matches/{match_id}/commentary/debug"])
def test_list_query_limits_are_validated(api, match_id, route, limit):
    api.request(
        "GET", route, case="invalid-limit", status=422, params={"limit": limit},
        path_params={"match_id": match_id},
    )


def test_scenario_inventory_is_machine_readable(api):
    payload = api.request("GET", "/api/v1/scenarios", case="scenario-inventory", actor="viewer").json()
    assert {item["scenario_id"] for item in payload} == {"sustained_pressure", "rhythm_shift", "player_influence"}
    assert all(item["total_ms"] > 0 for item in payload)


@pytest.mark.parametrize("tail", ["state", "events", "insights", "overlay", "replay", "commentary/debug"])
def test_populated_match_read_contracts(api, populated_match, tail):
    route = f"/api/v1/matches/{{match_id}}/{tail}"
    payload = api.request(
        "GET", route, case="seeded-scenario-read", actor="analyst" if tail.endswith("debug") else "viewer",
        path_params={"match_id": populated_match}, provenance=("real-scenario-events",),
    ).json()
    if tail == "state":
        assert payload["match_id"] == populated_match and payload["status"] == "completed"
        assert payload["last_sequence"] == 8
    elif tail == "events":
        assert [event["sequence"] for event in payload] == list(range(1, 9))
        assert all(event["match_id"] == populated_match and event["synthetic"] for event in payload)
    elif tail == "insights":
        known = {event.event_id for event in api.service.events[populated_match]}
        assert payload and all(item["match_id"] == populated_match for item in payload)
        assert all(set(item["provenance"]["supporting_event_ids"]) <= known for item in payload)
        assert all(item["provenance"]["data_source"] == "synthetic" for item in payload)
    elif tail == "overlay":
        assert payload["match_id"] == populated_match and payload["status"] == "completed"
        assert payload["overlay_version"] == "1.0.0"
        assert len(payload["overlay_queue"]) <= payload["render_hints"]["queue_limit"]
    elif tail == "replay":
        assert payload["match_id"] == populated_match and payload["source_url"] is None
        assert len(payload["frames"]) == 8
        assert payload["processed_until_ms"] == payload["frames"][-1]["timestamp_ms"]
        assert payload["metadata"] == {}
    else:
        assert payload == {"match_id": populated_match, "count": 0, "entries": []}


def test_viewer_profile_updates_overlay_and_survives_restore(api, match_id):
    profile = {"favorite_team_id": "team_a", "favorite_player_id": "a_10", "focus_metric": "progressive_passes"}
    response = api.request(
        "POST", "/api/v1/matches/{match_id}/viewer-profile", case="viewer-profile-valid", actor="analyst",
        path_params={"match_id": match_id}, json=profile,
    )
    assert response.json() == profile
    api.service._forget_match(match_id)
    overlay = api.request(
        "GET", "/api/v1/matches/{match_id}/overlay", case="restored-profile-overlay", actor="viewer",
        path_params={"match_id": match_id},
    ).json()
    assert overlay["viewer_profile"] == profile
    assert overlay["current_insight"] is None and overlay["overlay_queue"] == []


def test_empty_viewer_profile_is_a_safe_noop(api, match_id):
    assert api.request(
        "POST", "/api/v1/matches/{match_id}/viewer-profile", case="empty-profile",
        path_params={"match_id": match_id}, json={},
    ).json() == {}


def test_match_lifecycle_start_pause_resume_complete_and_no_restart(api, match_id):
    from tactiq.simulator.scenarios import load_scenario

    state = api.service.matches[match_id]
    state.scenario_recipe_version = 1
    _, events = load_scenario(state.scenario_id, match_id, state.seed, version=1)
    for event in events[:-3]:
        api.service.record_event(event, persist=False)
    api.service.persist_match(match_id)
    path = {"match_id": match_id}
    api.request("POST", "/api/v1/matches/{match_id}/start", case="lifecycle-start", path_params=path, actor="analyst")
    api.request("POST", "/api/v1/matches/{match_id}/start", case="start-is-idempotent", path_params=path, actor="analyst")
    paused = api.request(
        "POST", "/api/v1/matches/{match_id}/pause", case="lifecycle-pause", path_params=path, actor="analyst",
    )
    assert paused.json() == {"status": "paused"}
    stopped_at = state.last_sequence
    time.sleep(0.05)
    assert state.last_sequence == stopped_at and match_id not in api.service.tasks
    api.request("POST", "/api/v1/matches/{match_id}/start", case="lifecycle-resume", path_params=path, actor="analyst")
    deadline = time.monotonic() + 4
    while state.status.value == "running" and time.monotonic() < deadline:
        time.sleep(0.02)
    assert state.status.value == "completed", "Scenario did not complete within its bounded fixture budget"
    assert [event.sequence for event in api.service.events[match_id]] == list(range(1, len(events) + 1))
    api.request("POST", "/api/v1/matches/{match_id}/start", case="completed-start-conflict", status=409, path_params=path)
    terminal = api.request(
        "POST", "/api/v1/matches/{match_id}/pause", case="completed-pause-is-safe", path_params=path,
    )
    assert terminal.json() == {"status": "completed"}


def test_admin_delete_removes_durable_state_and_is_not_repeatable(api, populated_match):
    path = {"match_id": populated_match}
    api.request("DELETE", "/api/v1/matches/{match_id}", case="admin-delete-valid", path_params=path)
    assert populated_match not in api.service.matches
    assert api.client.app.state.store.load_match("audit_a", populated_match) is None
    api.request("DELETE", "/api/v1/matches/{match_id}", case="delete-unknown-is-404", status=404, path_params=path)
    api.request("GET", "/api/v1/matches/{match_id}/state", case="deleted-state-is-404", status=404, path_params=path)


@pytest.mark.parametrize("identifier", [" leading-space", "x" * 257], ids=["leading-space", "overlength"])
@pytest.mark.parametrize("method,route", [
    ("GET", "/api/v1/matches/{match_id}/state"),
    ("GET", "/api/v1/jobs/{job_id}"),
    ("POST", "/api/v1/jobs/{job_id}/cancel"),
])
def test_malformed_identifiers_are_client_errors_not_server_errors(api, method, route, identifier):
    api.request(
        method, route, case="malformed-resource-id", status=(400, 404, 422),
        path_params={"match_id": identifier, "job_id": identifier},
    )


@pytest.mark.parametrize("method,route", [
    ("GET", "/api/v1/jobs/{job_id}"), ("POST", "/api/v1/jobs/{job_id}/cancel"),
])
def test_unknown_job_ids_are_not_found(api, method, route):
    api.request(method, route, case="unknown-job", status=404, path_params={"job_id": "missing_job"})


@pytest.mark.parametrize("mode", ["declared", "streamed"])
def test_oversized_json_is_rejected_before_a_match_is_created(api, mode):
    content = b"x" * (256 * 1024 + 1)
    api.request(
        "POST", "/api/v1/matches", case=f"body-limit-{mode}", status=413,
        content=content if mode == "declared" else iter([content]),
        headers={"Content-Type": "application/json"},
    )
    assert not api.service.matches


def test_invalid_content_length_and_request_correlation_are_sanitized(api):
    api.request(
        "POST", "/api/v1/matches", case="invalid-content-length", status=400, json={},
        headers={"Content-Length": "-1"},
    )
    response = api.request(
        "GET", "/api/v1/session", case="valid-request-id", actor="viewer",
        headers={"X-Request-ID": "offline-audit-123"},
    )
    assert response.headers["x-request-id"] == "offline-audit-123"
    rejected = api.request(
        "GET", "/api/v1/session", case="untrusted-request-id", actor="viewer",
        headers={"X-Request-ID": "<unsafe request id>"},
    )
    assert rejected.headers["x-request-id"] != "<unsafe request id>"
    assert len(rejected.headers["x-request-id"]) <= 128


def test_request_rate_limit_is_per_identity_and_keeps_health_available(api):
    api.client.app.state.limiter.general_limit = 1
    api.request("GET", "/api/v1/scenarios", case="rate-limit-first", actor="viewer")
    rejected = api.request("GET", "/api/v1/scenarios", case="rate-limit-exhausted", actor="viewer", status=429)
    assert rejected.headers["retry-after"] == "60"
    api.request("GET", "/api/v1/scenarios", case="different-identity-not-throttled", actor="analyst")
    api.request("GET", "/health/live", case="health-bypasses-throttle", actor=None)


def test_heavy_request_admission_is_bounded_and_released(api, match_id):
    semaphore = api.client.app.state.heavy_admission
    for _ in range(4):
        api.client.portal.call(semaphore.acquire)
    try:
        response = api.request(
            "POST", "/api/v1/matches/{match_id}/narratives", case="heavy-admission-full",
            actor="viewer", status=503, path_params={"match_id": match_id}, json={},
        )
        assert response.headers["retry-after"] == "5"
    finally:
        for _ in range(4):
            api.client.portal.call(semaphore.release)
    api.request(
        "POST", "/api/v1/matches/{match_id}/narratives", case="heavy-admission-recovered",
        actor="viewer", status=400, path_params={"match_id": match_id}, json={},
    )


@pytest.mark.parametrize("tail", ["events", "insights", "replay"])
def test_empty_match_collections_have_explicit_empty_shapes(api, match_id, tail):
    result = api.request(
        "GET", f"/api/v1/matches/{{match_id}}/{tail}", case="empty-match-collection",
        actor="viewer", path_params={"match_id": match_id},
    ).json()
    if tail == "replay":
        assert result["frames"] == [] and result["insights"] == []
        assert result["source_url"] is None and result["duration_ms"] == 0
    else:
        assert result == []


def test_machine_report_is_sanitized_and_does_not_claim_unexercised_coverage(api):
    api.request("POST", "/api/v1/session", case="report-sanitization-fixture", actor="viewer")
    report = json.dumps(api.audit.report(0))
    for forbidden in (
        "offline-a-viewer", "offline-a-admin", "Authorization", "Set-Cookie",
        "https://testserver", "https://audit.example", str(api.service.media_root),
    ):
        assert forbidden not in report
    incomplete = type(api.audit)(None)
    incomplete.discover(api.main.app)
    result = incomplete.report(0)
    assert result["coverage_complete"] is False and result["all_contracts_passed"] is False
    assert len(result["missing_operations"]) == 37


def test_zz_declared_operation_coverage_is_complete(api):
    discovered = {(method, route) for method, route in api.audit.operations if method != "WEBSOCKET"}
    assert discovered == set(HTTP_OPERATIONS), "Declared routes and offline contract inventory diverged"
    assert Counter(method for method, _ in discovered) == {"GET": 18, "POST": 17, "DELETE": 1}
    assert len(api.audit.operations) == 37
    missing, without_positive = api.audit.coverage()
    assert not missing, f"Declared operations not exercised: {sorted(missing)}"
    assert not without_positive, f"Declared operations without a passing positive contract: {sorted(without_positive)}"
    auxiliary = {(row["method"], row["route"]) for row in api.audit.records}
    assert api.audit.auxiliary <= auxiliary, "Built-in documentation operations lack explicit evidence"
    assert api.audit.blocked_network_attempts == 0
    assert json.loads(json.dumps(api.audit.report(0)))["coverage_complete"] is True
