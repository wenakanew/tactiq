"""Offline endpoint audit infrastructure; never loads dotenv or contacts a cloud.

Run with FOUNDRY_ENABLED=false and optionally TACTIQ_AUDIT_REPORT_PATH pointing
to a JSON artifact. Pytest scratch data stays below this test directory.
"""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time

import anyio
from fastapi.routing import APIRoute, APIWebSocketRoute
from fastapi.testclient import TestClient
import httpx
import pytest
import requests
from starlette.websockets import WebSocketDisconnect


TEST_ROOT = Path(__file__).resolve().parent
WS_ROUTE = "/api/v1/ws/matches/{match_id}"
TOKENS = {
    "admin": ("offline-a-admin", "audit_a", "admin"),
    "analyst": ("offline-a-analyst", "audit_a", "analyst"),
    "viewer": ("offline-a-viewer", "audit_a", "viewer"),
    "foreign": ("offline-b-admin", "audit_b", "admin"),
}


class AuditState:
    def __init__(self, report_path: str | None):
        self.report_path = Path(report_path) if report_path else None
        self.operations: set[tuple[str, str]] = set()
        self.auxiliary: set[tuple[str, str]] = set()
        self.records: list[dict] = []
        self.test_outcomes: dict[str, str] = {}
        self.failures: dict[str, dict] = {}
        self.collected: set[str] = set()
        self.blocked_network_attempts = 0

    def discover(self, app):
        discovered = set()
        auxiliary = set()
        for route in app.routes:
            if isinstance(route, APIRoute):
                discovered.update((method, route.path) for method in route.methods)
            elif isinstance(route, APIWebSocketRoute):
                discovered.add(("WEBSOCKET", route.path))
            else:
                auxiliary.update((method, route.path) for method in getattr(route, "methods", ()))
        assert not self.operations or self.operations == discovered, "Route inventory changed during the audit"
        self.operations, self.auxiliary = discovered, auxiliary

    def record(self, nodeid, method, route, case, expected, provenance):
        record = {
            "test": nodeid,
            "method": method,
            "route": route,
            "case": case,
            "expected_status": list(expected),
            "status": None,
            "assertion": "status-and-pytest-contract",
            "status_assertion_passed": False,
            "fixture_provenance": ["offline/fixture", *provenance],
        }
        self.records.append(record)
        return record

    def verdict(self, record):
        outcome = self.test_outcomes.get(record["test"], "not_run")
        return "pass" if record["status_assertion_passed"] and outcome == "passed" else (
            "skip" if outcome == "skipped" else "fail"
        )

    def coverage(self):
        exercised = {(row["method"], row["route"]) for row in self.records if row["status"] is not None}
        positive = {
            (row["method"], row["route"]) for row in self.records
            if isinstance(row["status"], int)
            and (200 <= row["status"] < 300 or row["status"] == 101)
            and self.verdict(row) == "pass"
        }
        return self.operations - exercised, self.operations - positive

    def report(self, exitstatus):
        missing, without_positive = self.coverage()
        records = [
            {**row, "result": self.verdict(row),
             "pytest_outcome": self.test_outcomes.get(row["test"], "not_run")}
            for row in self.records
        ]
        operations = []
        for method, route in sorted(self.operations):
            cases = [row for row in records if (row["method"], row["route"]) == (method, route)]
            operations.append({
                "method": method, "route": route, "case_count": len(cases),
                "pass_count": sum(row["result"] == "pass" for row in cases),
                "fail_count": sum(row["result"] == "fail" for row in cases),
                "skip_count": sum(row["result"] == "skip" for row in cases),
            })
        counts = Counter(method for method, _ in self.operations if method != "WEBSOCKET")
        return {
            "schema_version": "1.0",
            "audit_mode": "offline/fixture",
            "cloud_enabled": False,
            "blocked_network_attempts": self.blocked_network_attempts,
            "pytest_exit_status": int(exitstatus),
            "coverage_complete": bool(self.operations) and not missing and not without_positive,
            "all_contracts_passed": bool(records) and not missing and not without_positive
                and all(row["result"] == "pass" for row in records)
                and all(self.test_outcomes.get(test) == "passed" for test in self.collected),
            "declared_http_operation_count": sum(counts.values()),
            "declared_http_method_counts": dict(sorted(counts.items())),
            "websocket_operation_count": sum(method == "WEBSOCKET" for method, _ in self.operations),
            "test_counts": dict(Counter(self.test_outcomes.get(test, "not_run") for test in self.collected)),
            "case_counts": dict(Counter(row["result"] for row in records)),
            "missing_operations": [{"method": method, "route": route} for method, route in sorted(missing)],
            "operations_without_passing_positive_case": [
                {"method": method, "route": route} for method, route in sorted(without_positive)
            ],
            "operations": operations,
            "auxiliary_operations": [{"method": method, "route": route} for method, route in sorted(self.auxiliary)],
            "cases": records,
            "failures": [
                {"test": nodeid, **failure} for nodeid, failure in sorted(self.failures.items())
            ],
            "limitations": [
                "No live Azure, Foundry, Speech, external download, or CV-model quality validation.",
                "Generated media and explicit adapter fixtures are contract evidence, not real-match evidence.",
                "Authentication uses test-only digests; storage is isolated per test.",
            ],
        }


_STATE: AuditState | None = None


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    global _STATE
    _STATE = AuditState(os.getenv("TACTIQ_AUDIT_REPORT_PATH"))
    if not config.option.basetemp:
        config.option.basetemp = str(TEST_ROOT / ".offline-audit-work")
    base = Path(config.option.basetemp).resolve()
    if not base.is_relative_to(TEST_ROOT):
        raise pytest.UsageError("Offline audit --basetemp must be inside tests\\system")
    factory = getattr(config, "_tmp_path_factory", None)
    if factory is not None and factory._basetemp is None:
        factory._given_basetemp = base


def pytest_collection_modifyitems(items):
    assert _STATE is not None
    for item in items:
        if Path(item.path).resolve().is_relative_to(TEST_ROOT):
            _STATE.collected.add(item.nodeid)
    items.sort(key=lambda item: item.name == "test_zz_declared_operation_coverage_is_complete")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    result = yield
    report = result.get_result()
    assert _STATE is not None
    if item.nodeid not in _STATE.collected:
        return
    previous = _STATE.test_outcomes.get(item.nodeid)
    if report.failed:
        _STATE.test_outcomes[item.nodeid] = "failed"
        frame = call.excinfo.traceback[-1] if call.excinfo is not None else None
        _STATE.failures[item.nodeid] = {
            "phase": report.when,
            "exception_type": call.excinfo.type.__name__ if call.excinfo is not None else "Unknown",
            "assertion_file": Path(str(frame.path)).name if frame is not None else None,
            "assertion_line": frame.lineno + 1 if frame is not None else None,
        }
    elif previous != "failed" and report.skipped:
        _STATE.test_outcomes[item.nodeid] = "skipped"
    elif report.when == "call" and previous not in {"failed", "skipped"}:
        _STATE.test_outcomes[item.nodeid] = "passed"


def pytest_sessionfinish(session, exitstatus):
    assert _STATE is not None
    missing, without_positive = _STATE.coverage()
    if (not _STATE.operations or missing or without_positive) and int(session.exitstatus) == 0:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
    if _STATE.report_path:
        _STATE.report_path.parent.mkdir(parents=True, exist_ok=True)
        _STATE.report_path.write_text(
            json.dumps(_STATE.report(session.exitstatus), indent=2, sort_keys=True), encoding="utf-8"
        )


class OfflineAPI:
    def __init__(self, client, service, main, service_module, audit, nodeid):
        self.client, self.service, self.main = client, service, main
        self.service_module, self.audit, self.nodeid = service_module, audit, nodeid

    @staticmethod
    def headers(actor):
        return {} if actor is None else {"Authorization": f"Bearer {TOKENS[actor][0]}"}

    def request(self, method, route, *, case, status=200, actor="admin",
                path_params=None, provenance=(), policy_headers=True, **kwargs):
        expected = (status,) if isinstance(status, int) else tuple(status)
        if method == "OPTIONS":
            self.audit.auxiliary.add((method, route))
        record = self.audit.record(self.nodeid, method, route, case, expected, provenance)
        path = route.format(**(path_params or {}))
        headers = {**self.headers(actor), **kwargs.pop("headers", {})}
        try:
            response = self.client.request(method, path, headers=headers, **kwargs)
        except Exception:
            record["assertion"] = "unexpected-runtime-exception"
            raise
        record["status"] = response.status_code
        record["status_assertion_passed"] = response.status_code in expected
        assert response.status_code in expected, (
            f"{method} {route} [{case}]: expected {expected}, got {response.status_code}"
        )
        if policy_headers:
            assert response.headers.get("x-request-id"), "Missing correlation identifier"
            assert response.headers.get("x-content-type-options") == "nosniff"
        return response

    def create(self, *, scenario="sustained_pressure", actor="admin", seed=1):
        return self.request(
            "POST", "/api/v1/matches", case="valid-create", actor=actor,
            json={"scenario_id": scenario, "seed": seed},
        ).json()["match_id"]

    def populate(self, match_id, *, complete=True):
        from tactiq.domain.models import MatchStatus
        from tactiq.simulator.scenarios import load_scenario

        state = self.service.matches[match_id]
        _, events = load_scenario(state.scenario_id, match_id, state.seed, version=1)
        state.scenario_recipe_version = 1
        state.analysis_metadata = {}
        for event in events:
            self.service.record_event(event, persist=False)
        if complete:
            state.status = MatchStatus.COMPLETED
        self.service.persist_match(match_id)
        assert self.service.insights[match_id], "Scenario fixture did not produce insights"
        return match_id

    def await_job(self, job_id, *, expected="succeeded"):
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            result = self.request(
                "GET", "/api/v1/jobs/{job_id}", path_params={"job_id": job_id},
                case="job-poll", provenance=("real-bounded-job-runner",),
            ).json()
            if result["status"] in {"succeeded", "failed", "cancelled", "dead_letter"}:
                assert result["status"] == expected, f"Job expected {expected}, got {result['status']}"
                return result
            time.sleep(0.02)
        pytest.fail("Offline job exceeded its 45-second fixture budget")

    def _websocket_session(self, match_id, actor, headers):
        session = self.client.websocket_connect(
            "wss://testserver" + WS_ROUTE.format(match_id=match_id),
            headers={**self.headers(actor), **(headers or {})},
        )

        def bounded_receive():
            async def receive():
                with anyio.fail_after(3):
                    return await session._send_rx.receive()
            return session.portal.call(receive)

        session.receive = bounded_receive
        return session

    @contextmanager
    def websocket(self, match_id, *, case, actor="viewer", headers=None):
        record = self.audit.record(self.nodeid, "WEBSOCKET", WS_ROUTE, case, (101,), ())
        with self._websocket_session(match_id, actor, headers) as session:
            record["status"], record["status_assertion_passed"] = 101, True
            yield session

    def websocket_denied(self, match_id, *, case, code, actor="viewer", headers=None):
        record = self.audit.record(self.nodeid, "WEBSOCKET", WS_ROUTE, case, (code,), ())
        try:
            with self._websocket_session(match_id, actor, headers):
                pytest.fail("WebSocket unexpectedly admitted")
        except WebSocketDisconnect as exc:
            record["status"] = exc.code
            record["status_assertion_passed"] = exc.code == code
            assert exc.code == code, f"WebSocket [{case}] expected {code}, got {exc.code}"


@pytest.fixture
def api(tmp_path, monkeypatch, request):
    assert _STATE is not None
    for name in tuple(os.environ):
        if name.startswith(("AZURE_", "FOUNDRY_", "TACTIQ_")) and name != "TACTIQ_AUDIT_REPORT_PATH":
            monkeypatch.delenv(name, raising=False)
    settings = {
        "APP_ENV": "production", "FOUNDRY_ENABLED": "false",
        "TACTIQ_AUTH_ENABLED": "true", "TACTIQ_COOKIE_SECURE": "true",
        "TACTIQ_AUTH_COOKIE_NAME": "offline_audit_session",
        "TACTIQ_CORS_ALLOW_ORIGINS": "https://audit.example",
        "TACTIQ_MEDIA_DIR": str(tmp_path / "media"),
        "TACTIQ_DATABASE_PATH": str(tmp_path / "matches.sqlite3"),
        "TACTIQ_REQUESTS_PER_MINUTE": "10000",
        "TACTIQ_EXPENSIVE_REQUESTS_PER_MINUTE": "10000",
        "TACTIQ_JOB_WORKERS": "1", "TACTIQ_JOB_CAPACITY": "3",
        "TACTIQ_CLIP_MAX_PER_MATCH": "0",
        "VIDEO_UPLOAD_MAX_BYTES": str(2 * 1024 * 1024),
    }
    for name, value in settings.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("TACTIQ_API_TOKENS_JSON", json.dumps([
        {
            "token_sha256": hashlib.sha256(token.encode("ascii")).hexdigest(),
            "tenant_id": tenant, "subject": actor, "role": role,
        }
        for actor, (token, tenant, role) in TOKENS.items()
    ]))
    scratch = tmp_path / "adapter-work"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    for name in ("TMP", "TEMP", "TMPDIR"):
        monkeypatch.setenv(name, str(scratch))

    blocked_before = _STATE.blocked_network_attempts

    def deny_network(*args, **kwargs):
        _STATE.blocked_network_attempts += 1
        raise AssertionError("External I/O is forbidden in the offline endpoint audit")

    monkeypatch.setattr(requests.sessions.Session, "request", deny_network)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", deny_network)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", deny_network)
    original_connect = socket.socket.connect
    original_resolve = socket.getaddrinfo

    def local_connect(connection, address):
        if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1", "localhost"}:
            return original_connect(connection, address)
        return deny_network()

    monkeypatch.setattr(socket.socket, "connect", local_connect)
    monkeypatch.setattr(socket, "create_connection", deny_network)

    def local_resolve(host, *args, **kwargs):
        if host in {None, "", "localhost", "127.0.0.1", "::1", b"localhost", b"127.0.0.1", b"::1"}:
            return original_resolve(host, *args, **kwargs)
        return deny_network()

    monkeypatch.setattr(socket, "getaddrinfo", local_resolve)
    service_module = importlib.import_module("tactiq.application.service")
    # Unit collection may already have imported the API with different startup settings.
    source = importlib.util.find_spec("tactiq.presentation.api.main")
    assert source is not None and source.origin is not None
    spec = importlib.util.spec_from_file_location("tactiq.presentation.api._offline_audit_main", source.origin)
    assert spec is not None and spec.loader is not None
    main = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, main)
    spec.loader.exec_module(main)
    narrative = importlib.import_module("tactiq.agents.narrative")
    speech = importlib.import_module("tactiq.agents.speech")
    monkeypatch.setattr(narrative.FoundryNarrativeGenerator, "_ensure_client", deny_network)
    monkeypatch.setattr(narrative.FoundryNarrativeGenerator, "_ensure_agents_client", deny_network)
    monkeypatch.setattr(speech.AzureSpeechSynthesizer, "_synthesize_with_rest", deny_network)
    monkeypatch.setattr(speech.AzureSpeechSynthesizer, "_synthesize_with_sdk", deny_network)
    instance = service_module.MatchService()
    monkeypatch.setattr(main, "service", instance)
    _STATE.discover(main.app)
    with TestClient(main.app, base_url="https://testserver", raise_server_exceptions=False) as client:
        yield OfflineAPI(client, instance, main, service_module, _STATE, request.node.nodeid)
    assert not instance.tasks, "Lifespan leaked a scenario task"
    assert not any(instance.subscribers.values()), "WebSocket subscriber leaked"
    assert not any(instance.in_use.values()), "Match pin leaked"
    assert instance.store is None, "Lifespan did not release the store"
    assert _STATE.blocked_network_attempts == blocked_before, "A test attempted external I/O"


@pytest.fixture
def match_id(api):
    return api.create()


@pytest.fixture
def populated_match(api, match_id):
    return api.populate(match_id)


@dataclass(frozen=True)
class GeneratedMedia:
    video: bytes
    audio: bytes


@pytest.fixture(scope="session")
def generated_media(tmp_path_factory):
    root = tmp_path_factory.mktemp("generated-media")
    binary = shutil.which("ffmpeg")
    assert binary, "Existing FFmpeg is required for offline media contracts"
    video, audio = root / "fixture.mp4", root / "fixture.mp3"
    commands = [
        [
            binary, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=96x64:rate=10", "-t", "1.2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(video),
        ],
        [
            binary, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=0.15",
            "-c:a", "libmp3lame", "-b:a", "48k", str(audio),
        ],
    ]
    for command in commands:
        result = subprocess.run(command, capture_output=True, check=False, timeout=20)
        assert result.returncode == 0, "Unable to generate local fixture media"
    return GeneratedMedia(video.read_bytes(), audio.read_bytes())


@pytest.fixture
def fake_motion(api, monkeypatch):
    from tactiq.simulator.scenarios import load_scenario
    from tactiq.video.extractor import VideoExtractionResult

    def extract(content, match_id):
        assert content
        _, events = load_scenario("sustained_pressure", match_id, 1, version=1)
        for index, event in enumerate(events):
            event.match_clock_ms = (index + 1) * 100
        return VideoExtractionResult(events, "offline-fixture-motion", 0.85, "offline/fixture CV event adapter")

    monkeypatch.setattr(api.service_module, "extract_events_from_video_auto", extract)
    return extract


@pytest.fixture
def fake_cv(api, monkeypatch):
    from tactiq.cv.types import CameraCalibration, CvIngestResult, CvTrackPoint

    tracks = []
    for index, timestamp in enumerate((0, 500, 1000)):
        for identifier, kind, position in (
            ("fixture_player", "player", (50.0, 50.0)), ("fixture_ball", "ball", (51.0, 51.0)),
        ):
            tracks.append(CvTrackPoint(
                track_id=identifier, object_class=kind, confidence=0.85, frame_idx=index * 5,
                timestamp_ms=timestamp, bbox_xywh=(1, 1, 4, 8),
                image_position=position, team_id="team_a" if kind == "player" else None,
            ))
    result = CvIngestResult(
        extractor="offline-fixture-cv", fps=10, width=96, height=64, frame_count=12,
        sampled_frames=3, tracks=tracks,
        calibration=CameraCalibration(method="offline/fixture", confidence=0, status="unavailable"),
        notes="offline/fixture CV adapter; no model inference",
        sampled_timestamps_ms=[0, 500, 1000], processed_until_ms=1000,
        warnings=["Offline fixture, not measured CV quality."],
    )

    def extract(*args, **kwargs):
        return result.model_copy(deep=True)

    monkeypatch.setattr(api.main, "extract_cv_tracks_from_video_bytes", extract)
    monkeypatch.setattr(api.service_module, "extract_cv_tracks_from_video_bytes", extract)
    model = api.service.media_root / "fixture.onnx"
    model.write_bytes(b"offline-adapter-fixture-not-a-real-model")
    monkeypatch.setenv("TACTIQ_CV_MODEL_PATH", str(model))
    return result


@pytest.fixture
def fake_speech(api, monkeypatch, generated_media):
    from tactiq.agents.speech import SpeechResult

    synthesizer = api.service.speech_synthesizer
    monkeypatch.setattr(synthesizer, "_key", "offline-fixture-not-a-credential")
    monkeypatch.setattr(synthesizer, "_region", "offline-fixture")
    monkeypatch.setattr(synthesizer, "_default_voice", "en-GB-FixtureLeadNeural")
    monkeypatch.setattr(synthesizer, "_default_commentator_a_voice", "en-GB-FixtureLeadNeural")
    monkeypatch.setattr(synthesizer, "_default_commentator_b_voice", "en-GB-FixtureAnalystNeural")
    languages = ("en-GB", "sw-KE", "fr-FR", "es-ES", "pt-BR", "ar-SA", "hi-IN")
    monkeypatch.setattr(synthesizer, "_default_voice_by_language", {
        language: f"{language}-FixtureLeadNeural" for language in languages
    })
    monkeypatch.setattr(synthesizer, "_default_commentator_pair_by_language", {
        language: (f"{language}-FixtureLeadNeural", f"{language}-FixtureAnalystNeural")
        for language in languages
    })
    calls = []

    def synthesize(*, ssml, provider):
        calls.append(ssml)
        import base64
        return SpeechResult(
            base64.b64encode(generated_media.audio).decode("ascii"), "audio/mpeg", "offline-fixture-speech"
        )

    monkeypatch.setattr(synthesizer, "_synthesize_with_rest", synthesize)
    return calls
