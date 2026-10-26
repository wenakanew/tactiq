"""Generated-media, CV boundary, durable-job, and authorized byte-range contracts."""

from __future__ import annotations

import json
from threading import Event
from uuid import uuid4

import pytest


UPLOAD_ROUTES = ["/api/v1/video/upload", "/api/v1/cv/ingest/upload", "/api/v1/video/jobs"]
LINK_ROUTES = ["/api/v1/video/from-link", "/api/v1/video/jobs/from-link"]
SOURCE_ROUTE = "/api/v1/matches/{match_id}/media/source"
CLIP_ROUTE = "/media/{match_id}/{filename}"


def upload(content, name="fixture.mp4"):
    return {"file": (name, content, "video/mp4")}


def attach_source(api, match_id, content):
    folder = api.service.source_root / match_id
    folder.mkdir(exist_ok=True)
    path = folder / "fixture.mp4"
    path.write_bytes(content)
    api.service.match_video_source_path[match_id] = str(path)
    api.service.persist_match(match_id)
    return path


@pytest.mark.parametrize("route", UPLOAD_ROUTES)
@pytest.mark.parametrize("kind", ["missing", "empty", "no-filename"])
def test_upload_endpoints_reject_missing_empty_or_unnamed_files(api, route, kind):
    files = {} if kind == "missing" else upload(b"" if kind == "empty" else b"fixture", "" if kind == "no-filename" else "fixture.mp4")
    api.request(
        "POST", route, case=f"upload-invalid-{kind}", status=422 if kind == "missing" else (400, 422),
        files=files, actor="analyst",
    )
    assert not api.service.matches
    assert not list((api.service.media_root / "pending").glob("*.bin"))


@pytest.mark.parametrize("route", UPLOAD_ROUTES)
def test_upload_file_limit_is_enforced_without_leaking_pending_inputs(api, route):
    api.request(
        "POST", route, case="upload-file-limit", status=413,
        files=upload(b"x" * (api.main.MAX_VIDEO_UPLOAD_BYTES + 1)),
    )
    assert not list((api.service.media_root / "pending").glob("*.bin"))


def test_sync_upload_runs_real_media_storage_and_offline_cv_event_adapter(api, generated_media, fake_motion):
    payload = api.request(
        "POST", "/api/v1/video/upload", case="generated-video-upload", actor="analyst",
        files=upload(generated_media.video), data={"source_name": "Fixture Game.mp4", "analysis_mode": "events"},
        provenance=("local-generated-video", "fake-CV-event-extractor", "real-FFmpeg-normalization"),
    ).json()
    assert payload["source"] == "Fixture Game.mp4"
    assert payload["event_count"] == 8 and payload["state"]["status"] == "completed"
    assert payload["extractor"] == "offline-fixture-motion"
    assert payload["state"]["analysis_metadata"]["normalized_source"] is True
    match_id = payload["match_id"]
    source = api.request(
        "GET", SOURCE_ROUTE, case="normalized-upload-source", actor="viewer", path_params={"match_id": match_id},
    )
    assert source.headers["content-type"].startswith("video/mp4") and source.content[4:8] == b"ftyp"
    replay = api.request(
        "GET", "/api/v1/matches/{match_id}/replay", case="uploaded-replay-clock", actor="viewer",
        path_params={"match_id": match_id},
    ).json()
    assert replay["source_url"] == SOURCE_ROUTE.format(match_id=match_id)
    assert replay["frames"] and replay["processed_until_ms"] <= replay["duration_ms"]
    assert replay["duration_ms"] <= 1500


def test_sync_link_ingest_uses_only_fixture_download_bytes(api, monkeypatch, generated_media, fake_motion):
    monkeypatch.setattr(api.main, "_download_video_link_bounded", lambda *args: generated_media.video)
    result = api.request(
        "POST", LINK_ROUTES[0], case="generated-video-link", actor="analyst",
        json={"url": "https://media.fixture.invalid/game.mp4", "analysis_mode": "events"},
        provenance=("fake-download", "local-generated-video", "fake-CV-event-extractor"),
    ).json()
    assert result["source"] == "game.mp4"
    assert result["state"]["tenant_id"] == "audit_a" and result["state"]["status"] == "completed"
    assert "https:" not in json.dumps(result)


def test_sync_link_honors_its_declared_analysis_mode_and_team_names(
    api, monkeypatch, generated_media, fake_motion, fake_cv,
):
    monkeypatch.setattr(api.main, "_download_video_link_bounded", lambda *args: generated_media.video)
    result = api.request(
        "POST", LINK_ROUTES[0], case="sync-link-cv-mode-and-names",
        json={
            "url": "https://media.fixture.invalid/game.mp4", "analysis_mode": "cv",
            "team_display_names": {"team_a": "Fixture A", "team_b": "Fixture B"},
            "options": {"sample_fps": 5},
        },
        provenance=("fake-download", "local-generated-video", "fake-CV-track-extractor"),
    ).json()
    assert result["state"]["scenario_id"] == "cv_upload", "Declared CV mode was ignored"
    assert result["extractor"] == "offline-fixture-cv"
    assert result["state"]["team_display_names"] == {"team_a": "Fixture A", "team_b": "Fixture B"}


@pytest.mark.parametrize("route", LINK_ROUTES)
@pytest.mark.parametrize(
    "kind,url,status",
    [
        ("empty", "", 422), ("whitespace", " ", 400), ("file-scheme", "file:///fixture.mp4", 400),
        ("ftp-scheme", "ftp://media.fixture.invalid/fixture.mp4", 400),
        ("embedded-credentials", "https://fixture:password@media.fixture.invalid/fixture.mp4", 400),
        ("invalid-port", "https://media.fixture.invalid:invalid/fixture.mp4", 400),
        ("malformed-brackets", "https://[invalid/fixture.mp4", 400),
    ],
    ids=["empty", "whitespace", "file-scheme", "ftp-scheme", "embedded-credentials", "invalid-port", "malformed-brackets"],
)
def test_link_input_validation_does_not_attempt_external_io(api, route, kind, url, status):
    api.request("POST", route, case=f"invalid-link-{kind}", status=status, json={"url": url})


@pytest.mark.parametrize("route", LINK_ROUTES)
def test_link_private_network_denial_is_exercised_without_dns_or_sockets(api, monkeypatch, route):
    import socket
    from tactiq.video import download

    monkeypatch.setattr(download.socket, "getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
    ])
    api.request(
        "POST", route, case="private-network-link-denial", status=400,
        json={"url": "https://media.fixture.invalid/game.mp4", "analysis_mode": "events"}, provenance=("fake-DNS-private-address",),
    )


@pytest.mark.parametrize("route", LINK_ROUTES)
def test_downloader_value_errors_become_client_errors(api, monkeypatch, route):
    def unavailable(*args):
        raise ValueError("Offline fixture download was rejected")

    monkeypatch.setattr(api.main, "_download_video_link_bounded", unavailable)
    api.request(
        "POST", route, case="download-validation-error", status=400,
        json={"url": "https://media.fixture.invalid/game.mp4", "analysis_mode": "events"}, provenance=("fault-injected-download",),
    )


def test_cv_ingest_returns_typed_tracks_without_claiming_fixture_model_quality(api, generated_media, fake_cv):
    payload = api.request(
        "POST", "/api/v1/cv/ingest/upload", case="cv-ingest-fixture", actor="analyst",
        files=upload(generated_media.video), data={"options_json": '{"sample_fps": 5}'},
        provenance=("local-generated-video", "fake-CV-track-extractor"),
    ).json()
    assert payload["extractor"] == "offline-fixture-cv" and payload["detection_source"] == "heuristic"
    assert payload["track_count_total"] == payload["track_count_returned"] == 6
    assert payload["tracks_truncated"] is False
    assert payload["calibration"]["status"] == "unavailable"
    assert payload["coordinate_space"] == "image_percent"
    assert all(point["pitch_position"] is None for point in payload["tracks"])


def test_cv_ingest_reports_track_truncation_instead_of_silently_dropping_it(api, monkeypatch, fake_cv):
    result = fake_cv.model_copy(update={"tracks": fake_cv.tracks * 21})
    monkeypatch.setattr(api.main, "extract_cv_tracks_from_video_bytes", lambda *args: result)
    payload = api.request(
        "POST", "/api/v1/cv/ingest/upload", case="cv-track-truncation", files=upload(b"fixture"),
        params={"max_tracks": 100}, provenance=("fake-CV-track-extractor",),
    ).json()
    assert payload["track_count_total"] == 126 and payload["track_count_returned"] == 100
    assert len(payload["tracks"]) == 100 and payload["tracks_truncated"] is True


@pytest.mark.parametrize("limit", ["99", "10001", "invalid"])
def test_cv_max_tracks_validation(api, limit):
    api.request(
        "POST", "/api/v1/cv/ingest/upload", case="invalid-max-tracks", status=422,
        params={"max_tracks": limit}, files=upload(b"fixture"),
    )


@pytest.mark.parametrize("route", ["/api/v1/cv/ingest/upload", "/api/v1/video/jobs"])
@pytest.mark.parametrize(
    "options",
    [
        "{malformed", '{"sample_fps": 0}', '{"unknown": true}',
        '{"sample_fps": NaN}', '{"attack_directions": {"team_a": 1, "team_b": 1}}',
    ],
    ids=["malformed", "invalid-fps", "unknown-option", "nonfinite", "same-attack-directions"],
)
def test_cv_options_are_bounded_and_validated(api, route, options):
    api.request(
        "POST", route, case="invalid-cv-options", status=400,
        files=upload(b"fixture"), data={"options_json": options},
    )


@pytest.mark.parametrize("fault,status", [("value", 400), ("unavailable", 503)])
def test_sync_cv_extractor_failures_are_explicit(api, monkeypatch, fault, status):
    from tactiq.cv.detection import CvUnavailableError

    def fail(*args):
        raise ValueError("Offline fixture decode failure") if fault == "value" else CvUnavailableError("Offline CV unavailable")

    monkeypatch.setattr(api.main, "extract_cv_tracks_from_video_bytes", fail)
    api.request(
        "POST", "/api/v1/cv/ingest/upload", case=f"cv-extractor-{fault}", status=status,
        files=upload(b"fixture"), provenance=("fault-injected-CV-extractor",),
    )


def test_missing_cv_model_configuration_fails_before_decoding(api):
    response = api.request(
        "POST", "/api/v1/cv/ingest/upload", case="missing-cv-model", status=503,
        files=upload(b"fixture"),
    )
    assert "TACTIQ_CV_MODEL_PATH" in response.json()["detail"]


def test_bad_existing_cv_model_fails_explicitly_without_network(api, monkeypatch):
    model = api.service.media_root / "invalid.onnx"
    model.write_bytes(b"offline-fixture-invalid-onnx")
    monkeypatch.setenv("TACTIQ_CV_MODEL_PATH", str(model))
    api.request(
        "POST", "/api/v1/cv/ingest/upload", case="unreadable-model-configuration", status=503,
        files=upload(b"fixture"), provenance=("invalid-ONNX-fixture",),
    )


@pytest.mark.parametrize("backend", ["onnx", "motion", "unsupported"])
@pytest.mark.parametrize("route", ["/api/v1/video/jobs", "/api/v1/video/jobs/from-link"])
def test_cv_job_configuration_is_validated_before_admission(api, monkeypatch, backend, route):
    monkeypatch.setenv("TACTIQ_CV_DETECTOR", backend)
    kwargs = {"files": upload(b"fixture"), "data": {"analysis_mode": "cv"}} if not route.endswith("from-link") else {
        "json": {"url": "https://media.fixture.invalid/fixture.mp4", "analysis_mode": "cv"},
    }
    api.request("POST", route, case=f"bad-cv-configuration-{backend}", status=503, **kwargs)
    assert not list((api.service.media_root / "pending").glob("*.bin"))


@pytest.mark.parametrize("kind", ["invalid-mode", "long-team-name"])
def test_job_form_validation_rejects_invalid_inputs(api, kind):
    data = {"analysis_mode": "unknown"} if kind == "invalid-mode" else {"team_a_name": "x" * 81}
    api.request("POST", "/api/v1/video/jobs", case=f"job-form-{kind}", status=422, files=upload(b"fixture"), data=data)


@pytest.mark.parametrize("mode", ["events", "cv"])
def test_async_uploaded_analysis_has_durable_status_result_and_cleanup(api, generated_media, fake_motion, fake_cv, mode):
    response = api.request(
        "POST", "/api/v1/video/jobs", case=f"async-upload-{mode}", status=202, actor="analyst",
        files=upload(generated_media.video, "..\\fixture.mp4"),
        data={"analysis_mode": mode, "team_a_name": "Fixture A", "team_b_name": "Fixture B"},
        provenance=("local-generated-video", f"fake-{mode}-extractor", "real-bounded-job-runner"),
    ).json()
    assert "payload" not in response and "tenant_id" not in response
    assert response["status"] in {"queued", "running"}
    job = api.await_job(response["job_id"])
    result = job["result"]
    assert result["state"]["status"] == "completed" and result["state"]["tenant_id"] == "audit_a"
    assert result["state"]["team_display_names"] == {"team_a": "Fixture A", "team_b": "Fixture B"}
    assert result["event_count"] > 0 and result["source"] == "fixture.mp4"
    assert not list((api.service.media_root / "pending").glob("*.bin"))
    assert str(api.service.media_root) not in json.dumps(job)
    replay = api.request(
        "GET", "/api/v1/matches/{match_id}/replay", case=f"async-replay-{mode}", actor="viewer",
        path_params={"match_id": result["match_id"]},
    ).json()
    assert replay["frames"] and replay["processed_until_ms"] <= replay["duration_ms"]
    cancelled = api.request(
        "POST", "/api/v1/jobs/{job_id}/cancel", case="cancel-finished-job-is-idempotent",
        path_params={"job_id": response["job_id"]}, actor="analyst",
    ).json()
    assert cancelled["status"] == "succeeded"


@pytest.mark.parametrize("mode", ["events", "cv"])
def test_async_link_jobs_persist_bytes_not_urls_or_query_credentials(
    api, monkeypatch, generated_media, fake_motion, fake_cv, mode,
):
    monkeypatch.setattr(api.main, "_download_video_link_bounded", lambda *args: generated_media.video)
    submitted = api.request(
        "POST", "/api/v1/video/jobs/from-link", case=f"async-link-{mode}", status=202,
        json={"url": "https://media.fixture.invalid/game.mp4?sig=offline-only-query-marker", "analysis_mode": mode},
        provenance=("fake-download", "local-generated-video", f"fake-{mode}-extractor"),
    ).json()
    internal = api.client.app.state.jobs.get("audit_a", submitted["job_id"])
    assert "url" not in internal.payload and "offline-only-query-marker" not in json.dumps(internal.model_dump())
    completed = api.await_job(submitted["job_id"])
    assert completed["result"]["state"]["status"] == "completed"
    assert not list((api.service.media_root / "pending").glob("*.bin"))


@pytest.mark.parametrize("method,route", [("GET", "/api/v1/jobs/{job_id}"), ("POST", "/api/v1/jobs/{job_id}/cancel")])
def test_jobs_cannot_be_read_or_cancelled_by_a_foreign_tenant(api, generated_media, fake_motion, method, route):
    submitted = api.request(
        "POST", "/api/v1/video/jobs", case="owned-job-fixture", status=202, files=upload(generated_media.video),
        data={"analysis_mode": "events"},
        provenance=("local-generated-video", "fake-CV-event-extractor"),
    ).json()
    api.request(
        method, route, case="foreign-tenant-job", status=404, actor="foreign",
        path_params={"job_id": submitted["job_id"]},
    )
    completed = api.await_job(submitted["job_id"])
    assert completed["status"] == "succeeded" and completed["cancel_requested"] is False


def test_running_and_queued_job_cancellation_and_capacity_cleanup(api, monkeypatch, generated_media, fake_motion):
    entered, release = Event(), Event()

    def blocked(content, match_id):
        entered.set()
        assert release.wait(timeout=30), "Fixture release exceeded its budget"
        return fake_motion(content, match_id)

    monkeypatch.setattr(api.service_module, "extract_events_from_video_auto", blocked)
    identifiers = []
    try:
        first = api.request(
            "POST", "/api/v1/video/jobs", case="blocking-running-job", status=202, files=upload(generated_media.video),
            data={"analysis_mode": "events"},
            provenance=("local-generated-video", "controlled-CV-adapter-block",),
        ).json()
        identifiers.append(first["job_id"])
        assert entered.wait(timeout=30), "Real worker did not reach the CV fixture adapter"
        for _ in range(2):
            queued = api.request(
                "POST", "/api/v1/video/jobs", case="bounded-queued-job", status=202, files=upload(generated_media.video),
                data={"analysis_mode": "events"},
            ).json()
            identifiers.append(queued["job_id"])
        pending_before = set((api.service.media_root / "pending").glob("*.bin"))
        assert len(pending_before) == 3
        rejected = api.request(
            "POST", "/api/v1/video/jobs", case="job-capacity-exhausted", status=503, files=upload(generated_media.video),
            data={"analysis_mode": "events"},
        )
        assert rejected.headers["retry-after"] == "1"
        assert set((api.service.media_root / "pending").glob("*.bin")) == pending_before
        for job_id in identifiers[1:]:
            cancelled = api.request(
                "POST", "/api/v1/jobs/{job_id}/cancel", case="cancel-queued-job",
                path_params={"job_id": job_id}, actor="analyst",
            ).json()
            assert cancelled["status"] == "cancelled"
            assert cancelled["cancel_requested"] is True and cancelled["result"] is None
        running = api.request(
            "POST", "/api/v1/jobs/{job_id}/cancel", case="cancel-running-job",
            path_params={"job_id": identifiers[0]}, actor="analyst",
        ).json()
        assert running["status"] == "running" and running["cancel_requested"] is True
    finally:
        release.set()
    for job_id in identifiers:
        result = api.await_job(job_id, expected="cancelled")
        assert result["result"] is None
    assert not list((api.service.media_root / "pending").glob("*.bin"))


@pytest.mark.parametrize("fault", ["value", "unavailable"])
def test_cv_async_failure_is_durable_and_never_a_successful_result(api, monkeypatch, generated_media, fake_cv, fault):
    from tactiq.cv.detection import CvUnavailableError

    def fail(*args, **kwargs):
        raise ValueError("Offline fixture bad CV input") if fault == "value" else CvUnavailableError("Offline fixture CV unavailable")

    monkeypatch.setattr(api.service_module, "extract_cv_tracks_from_video_bytes", fail)
    submitted = api.request(
        "POST", "/api/v1/video/jobs", case=f"cv-failing-job-{fault}", status=202,
        files=upload(generated_media.video), data={"analysis_mode": "cv"},
        provenance=("local-generated-video", "fault-injected-CV-adapter"),
    ).json()
    job = api.await_job(submitted["job_id"], expected="failed")
    assert job["result"] is None and job["error"] == "Job execution failed."
    assert not list((api.service.media_root / "pending").glob("*.bin"))
    assert "fixture" not in job["error"]


@pytest.mark.parametrize("route", ["/api/v1/video/upload", "/api/v1/video/from-link"])
def test_non_video_bytes_are_not_reported_as_successful_video_analysis(api, monkeypatch, route):
    kwargs = {"files": upload(b"offline-fixture-not-video"), "data": {"analysis_mode": "events"}} if route.endswith("/upload") else {
        "json": {"url": "https://media.fixture.invalid/game.mp4", "analysis_mode": "events"},
    }
    if route.endswith("from-link"):
        monkeypatch.setattr(api.main, "_download_video_link_bounded", lambda *args: b"offline-fixture-not-video")
    api.request(
        "POST", route, case="non-video-must-not-be-success", status=400,
        provenance=("invalid-media-fixture",), **kwargs,
    )


def test_async_non_video_input_is_failed_not_a_completed_synthetic_match(api):
    submitted = api.request(
        "POST", "/api/v1/video/jobs", case="async-invalid-media-admission", status=202,
        data={"analysis_mode": "events"},
        files=upload(b"offline-fixture-not-video"), provenance=("invalid-media-fixture",),
    ).json()
    result = api.await_job(submitted["job_id"], expected="failed")
    assert result["result"] is None


def test_unavailable_motion_fallback_is_labelled_and_stays_within_source_clock(api, monkeypatch, generated_media):
    def unavailable(**kwargs):
        raise ValueError("Offline fixture event extractor unavailable")

    monkeypatch.setattr(api.service_module, "extract_events_from_video_auto", unavailable)
    result = api.request(
        "POST", "/api/v1/video/upload", case="labelled-motion-fallback", files=upload(generated_media.video),
        data={"analysis_mode": "events"},
        provenance=("local-generated-video", "fault-injected-CV-event-extractor"),
    ).json()
    assert result["extractor"] == "video-stub-v1"
    metadata = result["state"]["analysis_metadata"]
    assert metadata["detection_source"] == "synthetic"
    assert metadata["source_timed_synthetic_preview"] is True
    assert metadata["extractor_confidence"] == result["extractor_confidence"] == 0.0
    assert metadata["notes"] == result["notes"] and "not observations" in result["notes"]
    assert metadata["warnings"]
    assert all(insight["confidence"] == 0.0 for insight in result["insights"])
    assert result["clips_generated"] == 0
    assert all(
        insight["clip"] == {"status": "unavailable", "reason": "synthetic_events_are_not_video_evidence"}
        for insight in result["insights"]
    )
    replay = api.request(
        "GET", "/api/v1/matches/{match_id}/replay", case="fallback-replay-source-clock",
        path_params={"match_id": result["match_id"]},
    ).json()
    assert replay["metadata"] == metadata
    assert replay["processed_until_ms"] <= replay["duration_ms"], "Synthetic fallback clock exceeds playable source duration"
    assert all(frame["timestamp_ms"] <= replay["duration_ms"] for frame in replay["frames"])
    assert all(event.qualifiers["source_confidence"] == 0.0 for event in api.service.events[result["match_id"]])

@pytest.mark.parametrize("route", [SOURCE_ROUTE, CLIP_ROUTE])
def test_ready_media_is_private_and_supports_valid_byte_ranges(api, populated_match, generated_media, route):
    if route == SOURCE_ROUTE:
        attach_source(api, populated_match, generated_media.video)
        path_params = {"match_id": populated_match}
    else:
        insight_id = api.service.insights[populated_match][0].insight_id
        folder = api.service.clips_root / populated_match
        folder.mkdir()
        (folder / f"{insight_id}.mp4").write_bytes(generated_media.video)
        api.service.insight_clip_map[populated_match][insight_id] = {"status": "ready"}
        path_params = {"match_id": populated_match, "filename": f"{insight_id}.mp4"}
    complete = api.request(
        "GET", route, case="private-media-complete", actor="viewer", path_params=path_params,
        provenance=("local-generated-video", "fixture-media-association"),
    )
    assert complete.content == generated_media.video
    assert complete.headers["cache-control"] == "private, no-store"
    assert complete.headers["accept-ranges"] == "bytes"
    assert complete.headers.get("content-disposition", "inline").split(";", 1)[0] == "inline"
    for label, header, start, end in (
        ("bounded", "bytes=10-19", 10, 19),
        ("suffix", "bytes=-11", len(generated_media.video) - 11, len(generated_media.video) - 1),
        ("open", "bytes=10-", 10, len(generated_media.video) - 1),
    ):
        partial = api.request(
            "GET", route, case=f"media-range-{label}", actor="viewer", status=206,
            path_params=path_params, headers={"Range": header},
        )
        assert partial.content == generated_media.video[start:end + 1]
        assert partial.headers["content-range"] == f"bytes {start}-{end}/{len(generated_media.video)}"
    unsatisfiable = api.request(
        "GET", route, case="media-range-unsatisfiable", actor="viewer", status=416,
        path_params=path_params, headers={"Range": f"bytes={len(generated_media.video)}-"},
    )
    assert unsatisfiable.headers["content-range"] == f"bytes */{len(generated_media.video)}"
    api.request(
        "GET", route, case="media-range-malformed", actor="viewer", status=400,
        path_params=path_params, headers={"Range": "bytes=invalid"},
    )
    unchanged = api.request(
        "GET", route, case="if-range-matching", status=206, path_params=path_params,
        headers={"Range": "bytes=0-9", "If-Range": complete.headers["etag"]},
    )
    assert unchanged.content == generated_media.video[:10]
    changed = api.request(
        "GET", route, case="if-range-mismatch", path_params=path_params,
        headers={"Range": "bytes=0-9", "If-Range": '"offline-nonmatching-etag"'},
    )
    assert changed.content == generated_media.video
    multipart = api.request(
        "GET", route, case="multiple-media-ranges", status=206, path_params=path_params,
        headers={"Range": "bytes=0-4,10-14"},
    )
    assert multipart.headers["content-type"].startswith("multipart/byteranges; boundary=")
    assert f"Content-Range: bytes 0-4/{len(generated_media.video)}".encode("ascii") in multipart.content
    assert f"Content-Range: bytes 10-14/{len(generated_media.video)}".encode("ascii") in multipart.content


def test_missing_source_and_missing_source_file_are_404(api, match_id, generated_media):
    path_params = {"match_id": match_id}
    api.request("GET", SOURCE_ROUTE, case="source-not-associated", status=404, path_params=path_params)
    path = attach_source(api, match_id, generated_media.video)
    path.unlink()
    api.request("GET", SOURCE_ROUTE, case="source-file-missing", status=404, path_params=path_params)


@pytest.mark.parametrize("kind", ["unknown", "invalid-name", "not-ready", "missing-file"])
def test_clips_require_a_ready_owned_file(api, match_id, kind):
    insight = uuid4().hex
    filename = f"{insight}.mp4" if kind != "invalid-name" else "not-an-insight.txt"
    if kind in {"not-ready", "missing-file"}:
        api.service.insight_clip_map[match_id][insight] = {"status": "pending" if kind == "not-ready" else "ready"}
    api.request(
        "GET", CLIP_ROUTE, case=f"clip-{kind}", status=404,
        path_params={"match_id": match_id, "filename": filename},
    )


def test_source_path_outside_media_root_is_never_served(api, match_id, tmp_path, generated_media):
    outside = tmp_path / "outside-source.mp4"
    outside.write_bytes(generated_media.video)
    api.service.match_video_source_path[match_id] = str(outside)
    api.request("GET", SOURCE_ROUTE, case="source-outside-root", status=404, path_params={"match_id": match_id})


def test_source_association_cannot_serve_another_tenants_media(api, match_id, generated_media):
    foreign_match = api.create(actor="foreign")
    foreign_path = attach_source(api, foreign_match, generated_media.video)
    api.service.match_video_source_path[match_id] = str(foreign_path)
    api.service.persist_match(match_id)
    api.service._forget_match(match_id)
    api.request(
        "GET", SOURCE_ROUTE, case="persisted-cross-tenant-source-association", status=404, actor="viewer",
        path_params={"match_id": match_id}, provenance=("fault-injected-persisted-media-association",),
    )


def test_clip_symlink_cannot_escape_its_match_directory(api, match_id, generated_media):
    foreign = api.create(actor="foreign")
    filename = f"{uuid4().hex}.mp4"
    foreign_folder = api.service.clips_root / foreign
    foreign_folder.mkdir()
    target = foreign_folder / filename
    target.write_bytes(generated_media.video)
    own_folder = api.service.clips_root / match_id
    own_folder.mkdir()
    alias = own_folder / filename
    try:
        alias.symlink_to(target)
    except OSError:
        pytest.skip("This host does not permit isolated file-symlink fixtures")
    try:
        api.service.insight_clip_map[match_id][filename[:-4]] = {"status": "ready"}
        api.request(
            "GET", CLIP_ROUTE, case="cross-match-clip-symlink", status=404,
            path_params={"match_id": match_id, "filename": filename},
            provenance=("fault-injected-cross-match-symlink",),
        )
    finally:
        alias.unlink(missing_ok=True)
