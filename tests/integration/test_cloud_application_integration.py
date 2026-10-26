"""Real disposable PostgreSQL + in-memory Blob/broker boundaries; no Azure calls.

The explicit two-variable write gate matches the foundational PostgreSQL tests.
Every test owns a unique schema; existing application data is never touched.
"""
from __future__ import annotations

import os
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from queue import LifoQueue
from uuid import uuid4
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from tactiq.application.cloud import (
    AnalysisHandlers, CloudAnalysisService, CloudCapacityError, ProcessingUnavailable, ScenarioRequest,
    VideoRequest, expire_guest_data,
)
from tactiq.presentation.api.cloud import COOKIE, create_cloud_app
from tactiq.runtime.access import AccessDenied, DAY, IdentityKey
from tactiq.runtime.cloud_composition import CloudDependencies, CloudSettings
from tactiq.runtime.cloud_execution import create_analysis_worker
from tactiq.runtime.cloud_store import CloudApplicationStore
from tactiq.runtime.cloud_worker import CloudWorker, WorkReference, dispatch_once
from tactiq.runtime.contracts import MediaRef, RevisionConflictError
from tactiq.runtime.identity import MicrosoftSignIn
from tactiq.runtime.postgres_access import PostgresAccessStore

pytestmark = pytest.mark.skipif(
    os.environ.get("TACTIQ_ALLOW_POSTGRES_TEST_WRITES") != "1" or not os.environ.get("TACTIQ_TEST_POSTGRES_DSN"),
    reason="Cloud composition requires an explicitly approved disposable PostgreSQL database",
)


class MemoryMedia:
    """Boundary double, NOT verification of Azure SDK/provider behavior."""
    def __init__(self):
        self.blobs = {}
        self.uploads = 0
        self.fail_upload = False

    def upload(self, tenant_id, match_id, source, *, size, content_type):
        self.uploads += 1
        if self.fail_upload:
            raise RuntimeError("Boundary is unavailable")
        content = source.read()
        assert len(content) == size
        ref = MediaRef(tenant_id, match_id, uuid4().hex, size, content_type, '"fixture"')
        self.blobs[(tenant_id, match_id, ref.media_id)] = content
        return ref

    def download(self, reference, *, tenant_id, match_id, offset=0, length=None):
        assert (tenant_id, match_id) == (reference.tenant_id, reference.match_id)
        content = self.blobs[(tenant_id, match_id, reference.media_id)]
        yield content[offset:] if length is None else content[offset:offset + length]

    def delete(self, reference, *, tenant_id, match_id):
        assert (tenant_id, match_id) == (reference.tenant_id, reference.match_id)
        self.blobs.pop((tenant_id, match_id, reference.media_id), None)


class Delivery:
    def __init__(self, reference):
        self.body = reference.model_dump_json().encode()
        self.completed = self.abandoned = self.rejected = False

    def complete(self):
        self.completed = True

    def abandon(self):
        self.abandoned = True

    def dead_letter(self, reason):
        self.rejected = True


@pytest.fixture
def runtime():
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row

    dsn = os.environ["TACTIQ_TEST_POSTGRES_DSN"]
    schema = "tactiq_app_test_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True, connect_timeout=60) as db:
        db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))

    def connect():
        return Lease()

    # Native Windows PostgreSQL starts one backend PROCESS per connection.
    # Reuse at most eight real backends in this disposable test schema; each
    # operation still has its own transaction and exclusive connection lease.
    # This is a test fixture optimization, NOT a production pooling/SLO claim.
    connections = []
    available = LifoQueue()

    class Lease:
        def __enter__(self):
            self.db = available.get(timeout=60)
            return self.db

        def __exit__(self, error_type, error, traceback):
            try:
                if error_type is None:
                    self.db.commit()
                else:
                    self.db.rollback()
            finally:
                available.put(self.db)

    store = CloudApplicationStore(connect)
    clock = [1_000_000.0]
    access = PostgresAccessStore(connect, ip_hmac_key=b"k" * 32, clock=lambda: clock[0])
    try:
        for _ in range(8):
            db = psycopg.connect(dsn, row_factory=dict_row, connect_timeout=60)
            connections.append(db)
            db.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            db.commit()
            available.put(db)
        store.migrate()
        store.migrate()
        access.migrate()
        media = MemoryMedia()
        settings = CloudSettings("https://api.test", max_pending=100, processing_limit_per_day=100)
        deps = CloudDependencies(settings, store, access, media)
        yield deps, connect, clock
    finally:
        for db in connections:
            db.close()
        with psycopg.connect(dsn, autocommit=True, connect_timeout=60) as db:
            db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def client_for(deps):
    return TestClient(create_cloud_app(deps), base_url="https://api.test", client=("127.0.0.1", 4444))


def guest(client):
    response = client.post("/api/v1/session/guest", headers={"Origin": "https://api.test"})
    assert response.status_code == 200, response.text
    assert response.json()["kind"] == "guest"
    assert "token" not in response.json()
    assert "Secure" in response.headers["set-cookie"] and "HttpOnly" in response.headers["set-cookie"]
    return response.json()


def test_cloud_policy_errors_keep_cors_and_retry_headers(runtime):
    deps, _, _ = runtime
    deps.settings = replace(deps.settings, processing_limit_per_day=None)
    with client_for(deps) as client:
        guest(client)
        response = scenario(client)
        assert response.status_code == 503
        assert response.headers["access-control-allow-origin"] == "https://api.test"
        assert "retry-after" in response.headers["access-control-expose-headers"].lower()
        assert response.headers["retry-after"] == "60"


def test_shared_owned_generation_and_audio_survive_api_restart_without_paid_replay(runtime, monkeypatch):
    import base64
    import time
    from tactiq.agents.narrative import DeterministicNarrativeGenerator, FoundryNarrativeGenerator
    from tactiq.agents.speech import AzureSpeechSynthesizer, SpeechResult
    from tactiq.domain.models import EvidenceItem, Insight
    from tactiq.runtime.access import UsagePolicy
    from tactiq.runtime.cloud_generation import CloudGeneration, GenerationSettings

    deps, connect, clock = runtime
    now = time.time()
    clock[0] = now
    policy = UsagePolicy(now - 60, now + 86400, 10_000_000, 5_000_000)
    deps.access = PostgresAccessStore(connect, ip_hmac_key=b"k" * 32, usage_policy=policy, clock=lambda: clock[0])
    settings = GenerationSettings(True, True, policy, 1_000_000, 16_000_000)
    monkeypatch.setenv("FOUNDRY_ENABLED", "true")
    monkeypatch.setenv("AZURE_FOUNDRY_PROJECT_ENDPOINT", "https://offline.invalid")
    monkeypatch.setenv("AZURE_FOUNDRY_PROJECT_DEPLOYMENT_NAME", "offline-fixture")
    monkeypatch.setenv("AZURE_SPEECH_API_KEY", "offline-fixture")
    monkeypatch.setenv("AZURE_SPEECH_REGION", "offline-region")
    monkeypatch.setenv("AZURE_SPEECH_DEFAULT_VOICE", "en-GB-RyanNeural")
    model = FoundryNarrativeGenerator(DeterministicNarrativeGenerator(), metered=True)
    speech = AzureSpeechSynthesizer(metered=True)
    model_calls, speech_calls = [], []

    def explain(_):
        model_calls.append(True)
        return json.dumps({"title": "Supported passage", "body": "Team A has a supported shot.",
                           "claims": [], "entities": ["team_a"]})

    def speak(**_):
        speech_calls.append(True)
        return SpeechResult(base64.b64encode(b"offline boundary audio").decode(), "audio/mpeg", "offline-fixture")

    monkeypatch.setattr(model, "_call_foundry", explain)
    monkeypatch.setattr(speech, "_synthesize_with_rest", speak)
    deps.generation = CloudGeneration(settings, deps.store, deps.access, deps.media, narrative=model, speech=speech)
    with client_for(deps) as client:
        guest(client)
        proof = client.cookies.get(COOKIE)
        match = scenario(client).json()["match_id"]
        tenant = deps.access.require_analysis(proof, match).tenant_id
        insight = Insight(
            match_id=match, type="sustained_pressure", priority="high", confidence=.8, team_id="team_a",
            window_start_ms=0, window_end_ms=0, title="Pressure", summary="Supported passage.",
            evidence=[EvidenceItem(metric="shots", value=1, unit="count", supporting_event_ids=["e1"])],
        )
        script = deps.generation.script(proof, tenant, match, "single", insight, "en-GB", "fan", None, allow_cloud=True)
        assert script["provider"] == "foundry" and len(model_calls) == 1  # Mock transport, not live model evidence.
        body = {"match_id": match, "script_id": script["script_id"]}
        response = client.post("/api/v1/speech/synthesize", json=body, headers={"Origin": "https://api.test"})
        assert response.status_code == 200, response.text
        assert len(speech_calls) == 1
        assert response.json()["provider"] == "offline-fixture"
        assert deps.access.trial_allowance(proof, "127.0.0.1").remaining == 2
    deps.generation = CloudGeneration(settings, deps.store, deps.access, deps.media, narrative=model, speech=speech)
    with client_for(deps) as restarted:
        restarted.cookies.set(COOKIE, proof)
        assert deps.generation.script(proof, tenant, match, "single", insight, "en-GB", "fan", None, allow_cloud=True) == script
        repeated = restarted.post("/api/v1/speech/synthesize", json=body, headers={"Origin": "https://api.test"})
        assert repeated.status_code == 200, repeated.text
        assert len(model_calls) == len(speech_calls) == 1
        assert base64.b64decode(repeated.json()["audio_base64"]) == b"offline boundary audio"
    with client_for(deps) as stranger:
        guest(stranger)
        assert stranger.post("/api/v1/speech/synthesize", json=body, headers={"Origin": "https://api.test"}).status_code == 401
        assert len(speech_calls) == 1


def scenario(client, operation="operation_1", **payload):
    return client.post("/api/v1/matches", json=payload, headers={
        "Origin": "https://api.test", "Idempotency-Key": operation,
    })


def run_job(deps, match_id, scratch, *, handlers=None):
    principal = deps.access.require_analysis(
        # System tests normally discover tenant via the authoritative bounded metadata.
        deps.test_token, match_id,
    )
    worker = create_analysis_worker(
        deps.store, handlers or AnalysisHandlers(deps.store, deps.media, scratch).handlers(), "test_worker",
    )
    delivery = Delivery(WorkReference(tenant_id=principal.tenant_id, job_id=match_id))
    outcome = worker.process(delivery)
    return outcome, delivery


def test_full_guest_scenario_outbox_worker_pagination_scripts_and_isolation(runtime):
    deps, connect, _ = runtime
    root = Path("build") / ("cloud-app-" + uuid4().hex)
    try:
        with client_for(deps) as client:
            assert client.get("/api/v1/session").status_code == 401
            guest(client)
            deps.test_token = client.cookies.get(COOKIE)
            result = scenario(client)
            assert result.status_code == 202, result.text
            admitted = result.json()
            assert admitted["remaining_trial_uses"] == 2
            repeated = scenario(client).json()
            assert repeated["match_id"] == admitted["match_id"] and repeated["replayed"]
            assert scenario(client, seed=7).status_code == 409
            sent = []
            assert dispatch_once(deps.store, "dispatcher", lambda ref, message: sent.append((ref, message)))
            assert sent[0][0].job_id == admitted["job_id"]
            assert not dispatch_once(deps.store, "dispatcher", lambda *_: None)
            outcome, delivery = run_job(deps, admitted["match_id"], root)
            assert outcome == "completed" and delivery.completed
            match = admitted["match_id"]
            state = client.get(f"/api/v1/matches/{match}/state").json()
            assert state["status"] == "completed" and state["scenario_recipe_version"] == 2
            assert "_cloud" not in state and "insights" not in state
            response = client.get(f"/api/v1/jobs/{match}").json()
            assert response["status"] == "succeeded" and response["result"]["event_count"] > 100
            first = client.get(f"/api/v1/matches/{match}/replay?limit=7").json()
            assert len(first["records"]) == 7 and first["has_more"]
            assert first["source_url"] is None
            cursor = first["cursor"]
            second = client.get(
                f"/api/v1/matches/{match}/replay?limit=7&after={cursor['after']}&revision={cursor['revision']}"
            ).json()
            assert second["records"][0]["payload"]["sequence"] > first["records"][-1]["payload"]["sequence"]
            assert first["records"][0]["payload"]["timestamp_ms"] >= 0
            insights = client.get(f"/api/v1/matches/{match}/insights").json()["insights"]
            assert insights and all(item["match_id"] == match for item in insights)
            narration = client.post(f"/api/v1/matches/{match}/commentary/dual", json={"overview": True}, headers={"Origin": "https://api.test"})
            assert narration.status_code == 200, narration.text
            assert narration.json()["provider"] == "deterministic" and narration.json()["script_id"]
            recap = client.post(f"/api/v1/matches/{match}/recap", json={"language": "en-GB"}, headers={"Origin": "https://api.test"})
            assert recap.status_code == 200 and isinstance(recap.json()["chapters"], list), recap.text
            assert client.post("/api/v1/speech/synthesize", json={"text": "arbitrary paid proxy"}, headers={"Origin": "https://api.test"}).status_code == 422
            assert len(client.get("/api/v1/matches").json()["matches"]) == 1
            duplicate = Delivery(sent[0][0])
            assert CloudWorker(deps.store, {"scenario": lambda *_: pytest.fail("Duplicate computation")}, "duplicate").process(duplicate) == "completed"
            with client_for(deps) as other:
                guest(other)
                assert other.get(f"/api/v1/matches/{match}/state").status_code == 401
                assert other.get("/api/v1/matches").json()["matches"] == []
            with connect() as db:
                assert db.execute("SELECT count(*) AS n FROM tactiq_cloud_jobs").fetchone()["n"] == 1
    finally:
        import shutil
        if root.exists():
            shutil.rmtree(root)


def test_atomic_trial_and_job_rollback_capacity_and_three_shared_ip(runtime, monkeypatch):
    deps, connect, _ = runtime
    with client_for(deps) as client:
        guest(client)
        with monkeypatch.context() as patch:
            patch.setattr(deps.store, "_enqueue", lambda *_: (_ for _ in ()).throw(RuntimeError("outbox failure")))
            assert scenario(client).status_code == 503
        assert client.get("/api/v1/session").json()["trial"]["remaining"] == 3
        with connect() as db:
            assert db.execute("SELECT count(*) AS n FROM tactiq_cloud_jobs").fetchone()["n"] == 0
        for index in range(3):
            assert scenario(client, f"analysis_{index}").status_code == 202
        blocked = scenario(client, "fourth")
        assert blocked.status_code == 403 and blocked.json()["detail"]["code"] == "sign_in_required"
        assert scenario(client, "analysis_0").status_code == 202
        with client_for(deps) as fresh:
            guest(fresh)
            assert scenario(fresh, "fresh_browser").status_code == 403
        assert scenario(client, "malformed", scenario_id="not_real").status_code == 422


def test_native_processing_requires_explicit_policy_before_counted_admission(runtime):
    deps, connect, _ = runtime
    deps.settings = replace(deps.settings, processing_limit_per_day=None)
    with client_for(deps) as client:
        guest(client)
        response = scenario(client)
        assert response.status_code == 503 and response.headers["retry-after"] == "60"
        assert client.get("/api/v1/session").json()["trial"]["remaining"] == 3
        with connect() as db:
            assert db.execute("SELECT count(*) AS n FROM tactiq_cloud_jobs").fetchone()["n"] == 0
            assert db.execute("SELECT count(*) AS n FROM tactiq_cloud_processing_window").fetchone()["n"] == 0


def test_store_and_access_share_bounded_connections_and_overflow_returns_retryable_503(runtime):
    from tactiq.runtime.cloud_connections import BoundedPostgresConnections

    deps, connect, clock = runtime
    gate = BoundedPostgresConnections(connect, maximum=2)
    deps.store = CloudApplicationStore(gate)
    deps.access = PostgresAccessStore(gate, ip_hmac_key=b"k" * 32, clock=lambda: clock[0])
    assert deps.store._connect is deps.access._connect
    with client_for(deps) as client:
        guest(client)
        admitted = scenario(client).json()
        with gate(), gate():
            response = client.get(f"/api/v1/matches/{admitted['match_id']}/state")
            assert response.status_code == 503 and response.headers["retry-after"] == "1"
            assert response.json()["detail"]["code"] == "database_capacity"
            assert "trial_use_charged" not in response.json()["detail"]
        assert client.get(f"/api/v1/matches/{admitted['match_id']}/state").status_code == 200


def test_global_native_processing_window_is_shared_and_retry_does_not_reserve_twice(runtime):
    deps, connect, _ = runtime
    deps.settings = replace(deps.settings, processing_limit_per_day=1)
    with client_for(deps) as client:
        guest(client)
        first = scenario(client)
        assert first.status_code == 202
        blocked = scenario(client, "second_operation")
        assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) > 0
        assert blocked.json()["detail"]["code"] == "processing_limit_exhausted"
        assert client.get("/api/v1/session").json()["trial"]["remaining"] == 2
        conflicting = CloudAnalysisService(deps.store, deps.access, deps.media, processing_limit_per_day=2)
        with pytest.raises(ProcessingUnavailable):
            conflicting.admit(client.cookies.get(COOKIE), "127.0.0.1", "other_policy", "scenario", ScenarioRequest().model_dump())
        retry = scenario(client)
        assert retry.status_code == 202 and retry.json()["replayed"]
        with connect() as db:
            row = db.execute("SELECT admitted,approved_limit FROM tactiq_cloud_processing_window").fetchone()
            assert row == {"admitted": 1, "approved_limit": 1}
            assert db.execute("SELECT count(*) AS n FROM tactiq_cloud_jobs").fetchone()["n"] == 1
            db.execute("UPDATE tactiq_cloud_processing_window SET starts=clock_timestamp()-interval '25 hours'")
        assert scenario(client, "after_global_reset").status_code == 202
        assert client.get("/api/v1/session").json()["trial"]["remaining"] == 1


def test_global_native_processing_limit_serializes_across_independent_sessions(runtime):
    deps, connect, _ = runtime
    sessions = [deps.access.issue_guest().token for _ in range(12)]
    service = CloudAnalysisService(
        deps.store, deps.access, deps.media, max_pending=100, processing_limit_per_day=2,
    )

    def admit(index):
        try:
            service.admit(
                sessions[index], f"192.0.2.{index + 1}", f"op_{index}", "scenario", ScenarioRequest().model_dump(),
            )
            return True
        except CloudCapacityError:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(admit, range(12))) == 2
    with connect() as db:
        assert db.execute("SELECT admitted FROM tactiq_cloud_processing_window").fetchone()["admitted"] == 2
        assert db.execute("SELECT count(*) AS n FROM tactiq_cloud_jobs").fetchone()["n"] == 2
        assert db.execute("SELECT count(*) AS n FROM tactiq_cloud_outbox").fetchone()["n"] == 2


def test_atomic_admission_from_many_clients_counts_only_three(runtime):
    deps, connect, _ = runtime
    sessions = [deps.access.issue_guest().token for _ in range(12)]
    service = CloudAnalysisService(deps.store, deps.access, deps.media, max_pending=100, processing_limit_per_day=100)

    def admit(index):
        try:
            return service.admit(sessions[index], "192.0.2.3", f"op_{index}", "scenario", ScenarioRequest().model_dump())
        except AccessDenied:
            return None

    with ThreadPoolExecutor(max_workers=12) as pool:
        assert sum(result is not None for result in pool.map(admit, range(12))) == 3
    with connect() as db:
        assert db.execute("SELECT count(*) AS n FROM tactiq_cloud_jobs").fetchone()["n"] == 3
        assert db.execute("SELECT count(*) AS n FROM tactiq_cloud_outbox").fetchone()["n"] == 3


def test_pause_resume_cancellation_stale_fence_and_recovery(runtime):
    deps, connect, _ = runtime
    issued = deps.access.issue_guest()
    service = CloudAnalysisService(deps.store, deps.access, deps.media, processing_limit_per_day=100)
    reservation = service.admit(issued.token, "192.0.2.4", "pause_op", "scenario", ScenarioRequest().model_dump())
    tenant, match = reservation.tenant_id, reservation.analysis_id
    reference = WorkReference(tenant_id=tenant, job_id=match)
    old = deps.store.claim(reference, "old", 60)
    assert deps.store.pause(tenant, match)
    assert deps.store.terminal(reference)
    with pytest.raises(RevisionConflictError):
        deps.store.commit(tenant, match, {}, expected_revision=1, claim=old, owner="old")
    assert deps.store.resume(tenant, match)
    current = deps.store.claim(reference, "current", 60)
    assert current.fence > old.fence
    assert not deps.store.finish(old, "old", {})
    assert deps.store.request_cancel(tenant, match)
    assert deps.store.cancelled(current, "current")
    assert not deps.store.resume(tenant, match)
    service.admit(issued.token, "192.0.2.4", "recovery_op", "scenario", ScenarioRequest(seed=2).model_dump())
    with connect() as db:
        db.execute("UPDATE tactiq_cloud_outbox SET status='sent'")
    assert deps.store.recover() == 1
    assert deps.store.recover() == 0


def test_upload_no_quota_for_invalid_and_fourth_never_uploads_blob(runtime):
    deps, _, _ = runtime
    issued = deps.access.issue_guest()
    service = CloudAnalysisService(deps.store, deps.access, deps.media, processing_limit_per_day=100)
    with pytest.raises(ValueError):
        service.admit_upload(issued.token, "192.0.2.5", "bad", b"not a video", VideoRequest(rights_confirmed=True))
    assert deps.access.trial_allowance(issued.token, "192.0.2.5").remaining == 3
    for index in range(3):
        service.admit(issued.token, "192.0.2.5", f"scenario_{index}", "scenario", ScenarioRequest(seed=index).model_dump())
    # Valid bounded MP4 container header; no decoder/provider call in admission.
    content = b"\x00\x00\x00\x18ftypmp42" + b"\0" * 100
    with pytest.raises(AccessDenied):
        service.admit_upload(issued.token, "192.0.2.5", "blocked", content, VideoRequest(rights_confirmed=True))
    assert deps.media.uploads == 0


def test_upload_failure_retry_and_private_byte_ranges(runtime):
    deps, connect, _ = runtime
    content = b"\x00\x00\x00\x18ftypmp42" + b"\0" * 100
    with client_for(deps) as client:
        guest(client)
        headers = {"Origin": "https://api.test", "Idempotency-Key": "upload"}
        deps.media.fail_upload = True
        response = client.post("/api/v1/video/jobs", headers=headers,
                               files={"file": ("source.mp4", content)}, data={"rights_confirmed": "true"})
        assert response.status_code == 503
        assert client.get("/api/v1/session").json()["trial"]["remaining"] == 2
        with connect() as db:
            assert db.execute("SELECT count(*) AS n FROM tactiq_cloud_jobs").fetchone()["n"] == 1
            assert db.execute("SELECT count(*) AS n FROM tactiq_cloud_outbox").fetchone()["n"] == 0
        deps.media.fail_upload = False
        admitted = client.post("/api/v1/video/jobs", headers=headers, files={"file": ("source.mp4", content)},
                               data={"rights_confirmed": "true"}).json()
        assert admitted["replayed"] and admitted["remaining_trial_uses"] == 2
        match = admitted["match_id"]
        principal = deps.access.authenticate(client.cookies.get(COOKIE))
        aggregate = deps.store.load(principal.tenant_id, match)
        source_id = aggregate.state["_cloud"]["input_media_id"]
        deps.store.commit(
            principal.tenant_id, match,
            {**aggregate.state, "_cloud": {**aggregate.state["_cloud"], "source_media_id": source_id}},
            expected_revision=aggregate.revision,
        )
        ranged = client.get(f"/api/v1/matches/{match}/media/source", headers={"Range": "bytes=2-9"})
        assert ranged.status_code == 206 and ranged.content == content[2:10]
        assert ranged.headers["content-range"] == f"bytes 2-9/{len(content)}"
        suffix = client.get(f"/api/v1/matches/{match}/media/source", headers={"Range": "bytes=-3"})
        assert suffix.content == content[-3:]
        full = client.get(f"/api/v1/matches/{match}/media/source", headers={"Range": "bytes=2-9", "If-Range": '"changed"'})
        assert full.status_code == 200 and full.content == content
        assert client.head(f"/api/v1/matches/{match}/media/source").headers["content-length"] == str(len(content))
        assert client.get(f"/api/v1/matches/{match}/media/source", headers={"Range": "bytes=9999-"}).status_code == 416
        assert client.get(f"/api/v1/matches/{match}/media/source", headers={"Range": "bytes=0-2,5-7"}).status_code == 416
        assert len(deps.media.blobs) == 1


@pytest.mark.parametrize("callback_host", ["api.test", "private-api.internal"])
def test_microsoft_callback_promotes_only_owned_guest_and_expiry_cleanup(runtime, callback_host):
    deps, _, clock = runtime

    class Provider:
        def begin(self):
            return {"state": "fixture_state", "auth_uri": "https://login.microsoftonline.com/test",
                    "nonce": "nonce", "code_verifier": "verifier"}

        def complete(self, flow, response):
            assert flow["state"] == response["state"]
            return IdentityKey("https://login.microsoftonline.com/fixture/v2.0", "member")

    deps.settings = replace(deps.settings, microsoft_redirect_uri="https://api.test/api/v1/auth/microsoft/callback")
    deps.sign_in = MicrosoftSignIn(deps.access, Provider())
    with client_for(deps) as client:
        guest(client)
        old = client.cookies.get(COOKIE)
        match = scenario(client).json()["match_id"]
        assert client.get("/api/v1/auth/microsoft/login?return_to=https://evil.test").status_code == 400
        login = client.get("/api/v1/auth/microsoft/login", follow_redirects=False)
        assert login.status_code == 303 and "__Host-tactiq_oidc" in client.cookies
        callback = client.post("/api/v1/auth/microsoft/callback", data={"state": "fixture_state", "code": "test-only"},
                               headers={"Host": callback_host}, follow_redirects=False)
        assert callback.status_code == 303, callback.text
        assert client.get("/api/v1/session").json()["kind"] == "member"
        assert client.get(f"/api/v1/matches/{match}/state").status_code == 200
        retained_tenants = deps.access.authorized_tenants(client.cookies.get(COOKIE))
        with pytest.raises(AccessDenied):
            deps.access.authenticate(old)
        expired_guest = deps.access.issue_guest()
        service = CloudAnalysisService(deps.store, deps.access, deps.media, processing_limit_per_day=100)
        expired = service.admit(expired_guest.token, "192.0.2.99", "expiring", "scenario", ScenarioRequest().model_dump())
        clock[0] += DAY + 1
        assert expire_guest_data(deps.store, deps.access, deps.media) == 1
        assert deps.store.load(expired.tenant_id, expired.analysis_id) is None
        assert deps.store.load_owned(retained_tenants, match) is not None
        assert expire_guest_data(deps.store, deps.access, deps.media) == 0


def test_cv_worker_preserves_options_unknown_scores_source_clock_and_fenced_output(runtime, monkeypatch):
    from tactiq.application import service as service_module
    from tactiq.cv.types import CameraCalibration, CvIngestResult, CvOptions, CvTrackPoint
    from tactiq.video.media import NormalizedVideo

    deps, _, _ = runtime
    root = Path("build") / ("cloud-cv-" + uuid4().hex)
    content = b"\x00\x00\x00\x18ftypmp42" + b"\0" * 100
    issued = deps.access.issue_guest()
    request = VideoRequest(rights_confirmed=True, options=CvOptions(
        sample_fps=4, max_duration_seconds=2, attack_directions={"team_a": -1, "team_b": 1},
        team_colors_rgb={"team_a": (10, 20, 30), "team_b": (40, 50, 60)},
    ))
    service = CloudAnalysisService(deps.store, deps.access, deps.media, processing_limit_per_day=100)
    reservation = service.admit_upload(issued.token, "192.0.2.6", "video", content, request)
    observed = []

    def extract(video, name, options):
        observed.append(options)
        return CvIngestResult(
            extractor="offline-boundary", fps=4, width=100, height=100, frame_count=8, sampled_frames=3,
            detection_source="model", model_id="offline-only", notes="Fixture, not a validated model.",
            sampled_timestamps_ms=[0, 875, 1750], processed_until_ms=1750,
            calibration=CameraCalibration(method="unavailable", confidence=0),
            tracks=[CvTrackPoint(track_id="ball_1", object_class="ball", confidence=.9, frame_idx=0,
                                 timestamp_ms=875, bbox_xywh=(0, 0, 8, 8), image_position=(50, 50))],
        )

    monkeypatch.setattr(service_module, "normalize_video", lambda *_: NormalizedVideo(content, 2000, False))
    monkeypatch.setattr(service_module, "extract_cv_tracks_from_video_bytes", extract)
    monkeypatch.setattr(service_module.MatchService, "_resolve_video_duration_ms", lambda *_: 2000)
    deps.test_token = issued.token
    try:
        outcome, _ = run_job(deps, reservation.analysis_id, root)
        assert outcome == "completed"
        assert observed[0].model_dump() == request.options.model_dump()
        aggregate = deps.store.load(reservation.tenant_id, reservation.analysis_id)
        assert aggregate.state["score"] == {}
        assert aggregate.state["attack_directions"] == request.options.attack_directions
        assert aggregate.state["_cloud"]["duration_ms"] == 2000
        frames = deps.store.page(reservation.tenant_id, reservation.analysis_id, "replay").records
        assert [frame.payload["timestamp_ms"] for frame in frames] == [0, 875, 1750]
        assert all(frame.payload["score"] == {} for frame in frames)
        source = deps.store.load_media(
            reservation.tenant_id, reservation.analysis_id, aggregate.state["_cloud"]["source_media_id"],
        )
        assert b"".join(deps.media.download(source, tenant_id=source.tenant_id, match_id=source.match_id)) == content
        assert all(not child.is_dir() for child in root.iterdir())
    finally:
        import shutil
        if root.exists():
            shutil.rmtree(root)


def test_origin_and_forwarded_header_policy_fail_closed(runtime, monkeypatch):
    deps, _, _ = runtime
    with client_for(deps) as client:
        assert client.post("/api/v1/session/guest").status_code == 403
        assert client.post("/api/v1/session/guest", headers={"Origin": "https://evil.test"}).status_code == 403
        guest(client)
        assert scenario(client).status_code == 202
        # Untrusted socket peers cannot choose allowance identities via XFF.
        for index in range(2):
            assert client.post("/api/v1/matches", json={}, headers={
                "Origin": "https://api.test", "Idempotency-Key": f"forward_{index}", "X-Forwarded-For": f"192.0.2.{index}",
            }).status_code == 202
        assert scenario(client, "fourth").status_code == 403
        monkeypatch.setattr(deps.store, "load_owned", lambda *_: (_ for _ in ()).throw(RuntimeError("database down")))
        existing = client.get("/api/v1/matches").json()["matches"][0]["match_id"]
        response = client.get(f"/api/v1/matches/{existing}/state")
        assert response.status_code == 503 and "no local fallback" in response.text


@pytest.mark.skipif(not os.getenv("TACTIQ_TEST_AZURITE_CONFIG"), reason="Explicit loopback Azurite test configuration required")
def test_actual_azurite_cloud_video_request_job_source_and_range(runtime, monkeypatch):
    """Real PostgreSQL + real Blob SDK/Azurite; CV inference remains an offline double."""
    import shutil
    import cv2
    import numpy as np
    from azure.storage.blob import BlobServiceClient
    from tactiq.application import service as service_module
    from tactiq.cv.types import CameraCalibration, CvIngestResult
    from tactiq.runtime.blob import PrivateBlobStore
    from tactiq.video.media import NormalizedVideo

    config = json.loads(Path(os.environ["TACTIQ_TEST_AZURITE_CONFIG"]).read_text())
    endpoint = urlsplit(config["url"])
    if endpoint.scheme != "http" or endpoint.hostname != "127.0.0.1" or endpoint.port != 11000:
        pytest.fail("This test permits the approved loopback emulator only")
    deps, _, _ = runtime
    root = Path("build") / ("cloud-azurite-" + uuid4().hex)
    root.mkdir(parents=True)
    service = BlobServiceClient(config["url"], credential={
        "account_name": config["account"], "account_key": config["key"],
    }, retry_total=0)
    container = None
    try:
        container = service.create_container("tactiq-integration-" + uuid4().hex)
        deps.media = PrivateBlobStore(container)
        deps.media.healthcheck()
        source = root / "generated.mp4"
        writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"mp4v"), 4, (160, 100))
        assert writer.isOpened()
        for index in range(8):
            frame = np.zeros((100, 160, 3), dtype=np.uint8)
            cv2.rectangle(frame, (10 + index * 5, 25), (20 + index * 5, 65), (200, 50, 30), -1)
            writer.write(frame)
        writer.release()
        content = source.read_bytes()
        monkeypatch.setattr(service_module, "normalize_video", lambda *_: NormalizedVideo(content, 2000, False))
        monkeypatch.setattr(service_module, "extract_cv_tracks_from_video_bytes", lambda *_: CvIngestResult(
            extractor="offline-inference-double", fps=4, width=160, height=100, frame_count=8,
            sampled_frames=3, sampled_timestamps_ms=[0, 875, 1750], processed_until_ms=1750,
            detection_source="model", model_id="offline-only", notes="Storage/clock fixture, not a CV model validation.",
            tracks=[], calibration=CameraCalibration(method="unavailable", confidence=0),
        ))
        monkeypatch.setattr(service_module.MatchService, "_resolve_video_duration_ms", lambda *_: 2000)
        with client_for(deps) as client:
            guest(client)
            deps.test_token = client.cookies.get(COOKIE)
            response = client.post(
                "/api/v1/video/jobs", files={"file": ("generated.mp4", content, "video/mp4")},
                data={"rights_confirmed": "true", "analysis_mode": "cv", "options": '{"sample_fps":4}'},
                headers={"Origin": "https://api.test", "Idempotency-Key": "real_blob_video"},
            )
            assert response.status_code == 202, response.text
            match = response.json()["match_id"]
            outcome, delivery = run_job(deps, match, root / "worker")
            assert outcome == "completed" and delivery.completed
            job = client.get(f"/api/v1/jobs/{match}").json()
            assert job["status"] == "succeeded"
            media = f"/api/v1/matches/{match}/media/source"
            full = client.get(media)
            assert full.status_code == 200 and full.content == content
            ranged = client.get(media, headers={"Range": "bytes=8-31"})
            assert ranged.status_code == 206 and ranged.content == content[8:32]
            assert ranged.headers["content-range"] == f"bytes 8-31/{len(content)}"
            assert ranged.headers["etag"] == full.headers["etag"]
            replay = client.get(f"/api/v1/matches/{match}/replay").json()
            assert replay["duration_ms"] == 2000 and replay["source_url"] == media
            assert [record["payload"]["timestamp_ms"] for record in replay["records"]] == [0, 875, 1750]
            assert client.get(f"/api/v1/matches/{match}/state").json()["score"] == {}
            with client_for(deps) as outsider:
                guest(outsider)
                assert outsider.get(media).status_code == 401
        assert container.get_container_properties()["public_access"] is None
    finally:
        try:
            if container is not None:
                container.delete_container()
        finally:
            service.close()
            shutil.rmtree(root)
