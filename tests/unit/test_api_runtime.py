import hashlib
import json
import time
import asyncio
import subprocess
import shutil
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from tactiq.application.service import MatchService
from tactiq.presentation.api import main


@pytest.fixture
def local_api(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("TACTIQ_AUTH_ENABLED", "false")
    monkeypatch.setenv("TACTIQ_MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.setenv("TACTIQ_DATABASE_PATH", str(tmp_path / "matches.sqlite3"))
    monkeypatch.setenv("FOUNDRY_ENABLED", "false")
    monkeypatch.setenv("TACTIQ_EXPENSIVE_REQUESTS_PER_MINUTE", "100")
    instance = MatchService()
    monkeypatch.setattr(main, "service", instance)
    with TestClient(main.app) as client:
        yield client, instance


def test_match_state_persists_and_replay_contains_same_events(local_api):
    client, service = local_api
    created = client.post("/api/v1/matches", json={"scenario_id": "sustained_pressure", "seed": 1})
    assert created.status_code == 200, created.text
    match_id = created.json()["match_id"]
    assert client.get("/health/ready").status_code == 200
    assert client.get("/api/v1/matches").json()[0]["match_id"] == match_id
    service.matches.clear()
    restored = client.get(f"/api/v1/matches/{match_id}/state")
    assert restored.status_code == 200
    assert restored.json()["seed"] == 1
    assert client.get(f"/api/v1/matches/{match_id}/replay").json()["frames"] == []
    assert created.headers["x-request-id"]
    assert client.get("/api/v1/operations/metrics").status_code == 200


def test_replay_cursor_returns_only_new_frames_and_replaces_updated_timestamps(local_api):
    from tactiq.domain.models import Event as MatchEvent

    client, service = local_api
    match_id = client.post("/api/v1/matches", json={}).json()["match_id"]
    for sequence, clock in ((1, 0), (2, 1000)):
        service.record_event(MatchEvent(match_id=match_id, sequence=sequence, match_clock_ms=clock,
                                        type="recovery", team_id="team_a"))
    url = f"/api/v1/matches/{match_id}/replay"
    first = client.get(url).json()
    assert [frame["sequence"] for frame in first["frames"]] == [1, 2]
    service.record_event(MatchEvent(match_id=match_id, sequence=3, match_clock_ms=1000,
                                    type="pass_completed", team_id="team_a"))
    incremental = client.get(url, params={"after_sequence": 2}).json()
    assert incremental["after_sequence"] == 2
    assert [(frame["sequence"], frame["timestamp_ms"]) for frame in incremental["frames"]] == [(3, 1000)]
    assert client.get(url, params={"after_sequence": 3}).json()["frames"] == []
    assert client.get(url, params={"after_sequence": 4}).status_code == 400
    assert client.get(url, params={"after_sequence": -1}).status_code == 422


def test_video_job_is_durable_and_public_response_hides_disk_paths(local_api, tmp_path):
    client, service = local_api
    path = tmp_path / "generated.mp4"
    subprocess.run([shutil.which("ffmpeg"), "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=green:s=160x100:r=10:d=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
                   check=True, timeout=15)
    response = client.post("/api/v1/video/jobs", files={"file": ("upload.mp4", path.read_bytes(), "video/mp4")},
                           data={"analysis_mode": "events"})
    assert response.status_code == 202, response.text
    identifier = response.json()["job_id"]
    assert "payload" not in response.json()
    for _ in range(500):
        job = client.get(f"/api/v1/jobs/{identifier}").json()
        if job["status"] in {"succeeded", "failed", "dead_letter"}:
            break
        time.sleep(0.02)
    assert job["status"] == "succeeded", job
    match_id = job["result"]["match_id"]
    assert service.matches[match_id].tenant_id == "local"
    assert client.get(f"/api/v1/matches/{match_id}/replay").json()["frames"]
    assert list((service.media_root / "pending").iterdir()) == []


def test_signed_source_urls_are_not_persisted_in_durable_jobs(local_api, monkeypatch):
    client, _ = local_api
    monkeypatch.setattr(main, "_download_video_link_bounded", lambda *args: b"synthetic-fallback-fixture")
    response = client.post("/api/v1/video/jobs/from-link", json={"url": "https://media.example/clip.mp4?sig=never-store-this",
                                                               "analysis_mode": "events"})
    assert response.status_code == 202
    job = client.app.state.jobs.get("local", response.json()["job_id"])
    assert "url" not in job.payload
    assert "never-store-this" not in json.dumps(job.model_dump())


def test_missing_cv_model_fails_explicitly_not_with_synthetic_success(local_api, monkeypatch):
    client, _ = local_api
    monkeypatch.delenv("TACTIQ_CV_MODEL_PATH", raising=False)
    monkeypatch.setenv("TACTIQ_CV_DETECTOR", "onnx")
    response = client.post("/api/v1/cv/ingest/upload", files={"file": ("a.mp4", b"video", "video/mp4")})
    assert response.status_code == 503
    assert "TACTIQ_CV_MODEL_PATH" in response.json()["detail"]


def test_websocket_disconnect_releases_subscriber(local_api):
    client, service = local_api
    match_id = client.post("/api/v1/matches", json={}).json()["match_id"]
    with client.websocket_connect(f"/api/v1/ws/matches/{match_id}") as websocket:
        assert websocket.receive_json()["type"] == "match.snapshot"
        assert websocket.receive_json()["type"] == "insights.snapshot"
    assert len(service.subscribers[match_id]) == 0


def test_body_limit_and_origin_rejection(local_api):
    client, _ = local_api
    large = client.post("/api/v1/matches", content=b"x" * (256 * 1024 + 1), headers={"Content-Type": "application/json"})
    assert large.status_code == 413
    blocked = client.post("/api/v1/matches", json={}, headers={"Origin": "https://untrusted.example"})
    assert blocked.status_code == 403
    streamed = client.post("/api/v1/matches", content=iter([b"x" * (256 * 1024 + 1)]),
                           headers={"Content-Type": "application/json"})
    assert streamed.status_code == 413, streamed.text


def test_authorized_source_supports_byte_ranges_for_video_seek(local_api):
    client, service = local_api
    match_id = client.post("/api/v1/matches", json={}).json()["match_id"]
    directory = service.source_root / match_id
    directory.mkdir()
    source = directory / "source.mp4"
    source.write_bytes(b"0123456789" * 100)
    service.match_video_source_path[match_id] = str(source)
    response = client.get(f"/api/v1/matches/{match_id}/media/source", headers={"Range": "bytes=10-19"})
    assert response.status_code == 206
    assert response.headers["content-range"] == "bytes 10-19/1000"
    assert response.content == b"0123456789"


def test_cache_eviction_restores_durable_matches_and_admin_delete_removes_record(local_api):
    client, service = local_api
    service.max_cached_matches = 2
    identifiers = [client.post("/api/v1/matches", json={}).json()["match_id"] for _ in range(4)]
    assert len(service.matches) == 2
    assert client.get(f"/api/v1/matches/{identifiers[0]}/state").status_code == 200
    assert len(service.matches) == 2
    assert client.delete(f"/api/v1/matches/{identifiers[0]}").status_code == 200
    assert client.get(f"/api/v1/matches/{identifiers[0]}/state").status_code == 404


def test_media_stays_pinned_until_the_response_body_finishes(local_api, monkeypatch):
    client, service = local_api
    match_id = client.post("/api/v1/matches", json={}).json()["match_id"]
    folder = service.source_root / match_id
    folder.mkdir()
    source = folder / "source.mp4"
    source.write_bytes(b"source-fixture")
    service.match_video_source_path[match_id] = str(source)
    headers_sent = Event()
    allow_body = Event()
    original = main.FileResponse

    class DelayedResponse(original):
        async def __call__(self, scope, receive, send):
            async def gated_send(message):
                await send(message)
                if message["type"] == "http.response.start":
                    headers_sent.set()
                    while not allow_body.is_set():
                        await asyncio.sleep(0.01)
            await super().__call__(scope, receive, gated_send)

    monkeypatch.setattr(main, "FileResponse", DelayedResponse)
    with ThreadPoolExecutor(max_workers=1) as executor:
        response = executor.submit(client.get, f"/api/v1/matches/{match_id}/media/source")
        try:
            assert headers_sent.wait(timeout=5)
            blocked_delete = client.delete(f"/api/v1/matches/{match_id}")
            assert blocked_delete.status_code == 503, blocked_delete.text
            assert source.exists()
        finally:
            allow_body.set()
        assert response.result(timeout=5).content == b"source-fixture"
    assert service.in_use.get(match_id, 0) == 0
    assert client.delete(f"/api/v1/matches/{match_id}").status_code == 200


def test_queued_video_cancellation_cleans_its_pending_input(local_api):
    client, service = local_api
    workers_ready = Barrier(3)
    release = Event()

    def occupied(payload):
        workers_ready.wait(timeout=5)
        assert release.wait(timeout=10)
        return {}

    jobs = client.app.state.jobs
    jobs._handlers["video"] = occupied
    try:
        for _ in range(2):
            jobs.submit("local", "video", {})
        workers_ready.wait(timeout=5)
        submitted = client.post("/api/v1/video/jobs", files={"file": ("queued.mp4", b"fixture", "video/mp4")},
                                data={"analysis_mode": "events"})
        assert submitted.status_code == 202, submitted.text
        job_id = submitted.json()["job_id"]
        job = jobs.get("local", job_id)
        pending = main._pending_video_path(job.payload)
        assert pending.is_file()
        cancelled = client.post(f"/api/v1/jobs/{job_id}/cancel")
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "cancelled"
        assert not pending.exists()
    finally:
        release.set()


def test_startup_cleanup_keeps_only_inputs_for_unfinished_jobs(local_api):
    _, service = local_api
    pending = service.media_root / "pending"
    active_operation, cancelled_operation, orphan_operation = [uuid4().hex for _ in range(3)]
    for operation in (active_operation, cancelled_operation, orphan_operation):
        (pending / f"{operation}.bin").write_bytes(b"fixture")
    unknown = pending / "operator-notes.txt"
    unknown.write_text("keep")
    with main.SQLiteStore(service.media_root / "cleanup-fixture.sqlite3") as store:
        for operation, cancelled in ((active_operation, False), (cancelled_operation, True)):
            job = store._create_job("local", "video", {
                "operation_id": operation, "input_path": str(pending / f"{operation}.bin")
            }, 2)
            if cancelled:
                store._cancel_job("local", job.job_id)
        main._cleanup_inactive_video_inputs(store)
    assert (pending / f"{active_operation}.bin").exists()
    assert not (pending / f"{cancelled_operation}.bin").exists()
    assert not (pending / f"{orphan_operation}.bin").exists()
    assert unknown.exists()


def test_authenticated_tenants_roles_cookie_exchange_and_private_media(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("TACTIQ_AUTH_ENABLED", "true")
    monkeypatch.setenv("TACTIQ_COOKIE_SECURE", "true")
    monkeypatch.setenv("TACTIQ_MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.setenv("TACTIQ_DATABASE_PATH", str(tmp_path / "auth.sqlite3"))
    records = [
        {"token_sha256": hashlib.sha256(token.encode()).hexdigest(), "tenant_id": tenant, "subject": role, "role": role}
        for token, tenant, role in (("analyst-a", "a", "analyst"), ("viewer-a", "a", "viewer"), ("admin-b", "b", "admin"))
    ]
    monkeypatch.setenv("TACTIQ_API_TOKENS_JSON", json.dumps(records))
    instance = MatchService()
    monkeypatch.setattr(main, "service", instance)
    with TestClient(main.app, base_url="https://testserver") as client:
        assert client.post("/api/v1/matches", json={}).status_code == 401
        created = client.post("/api/v1/matches", json={}, headers={"Authorization": "Bearer analyst-a"})
        assert created.status_code == 200, created.text
        match_id = created.json()["match_id"]
        assert client.get(f"/api/v1/matches/{match_id}/state", headers={"Authorization": "Bearer admin-b"}).status_code == 404
        assert client.post("/api/v1/matches", json={}, headers={"Authorization": "Bearer viewer-a"}).status_code == 403
        session = client.post("/api/v1/session", headers={"Authorization": "Bearer viewer-a"})
        assert session.status_code == 200, session.text
        assert "HttpOnly" in session.headers["set-cookie"] and "Secure" in session.headers["set-cookie"]
        assert client.get(f"/api/v1/matches/{match_id}/state").status_code == 200
        assert client.post("/api/v1/matches", json={}).status_code == 403
        assert client.get("/api/v1/operations/metrics").status_code == 403
        assert client.get(f"/api/v1/matches/{match_id}/media/source").status_code == 404
