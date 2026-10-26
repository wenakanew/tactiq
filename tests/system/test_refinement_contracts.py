import base64
import json

import pytest


@pytest.mark.parametrize("route", ["/api/v1/video/from-link", "/api/v1/video/jobs/from-link"])
def test_youtube_requires_permission_before_resolving_or_creating_work(api, route):
    response = api.request("POST", route, case="youtube-permission-required", status=400,
                           json={"url": "https://youtube.com/shorts/y02mOQudT3E"})
    assert "permission" in response.json()["detail"]
    assert not api.service.matches
    assert not list((api.service.media_root / "pending").iterdir())


def test_youtube_default_cv_job_keeps_only_a_canonical_id_and_cleans_managed_inputs(
    api, monkeypatch, generated_media, fake_cv,
):
    monkeypatch.setattr(api.main, "youtube_available", lambda: True)
    calls = []

    def download(video_id, maximum):
        calls.append((video_id, maximum))
        return generated_media.video

    monkeypatch.setattr(api.main, "download_youtube", download)
    submitted = api.request(
        "POST", "/api/v1/video/jobs/from-link", case="authorized-youtube-default-cv", status=202,
        json={"url": "https://youtube.com/shorts/y02mOQudT3E?si=never-persist-this", "rights_confirmed": True},
        provenance=("fake-provider-download", "fake-CV-track-extractor", "real-bounded-job-runner"),
    ).json()
    internal = api.client.app.state.jobs.get("audit_a", submitted["job_id"])
    assert internal.payload["analysis_mode"] == "cv"
    assert internal.payload["youtube_video_id"] == "y02mOQudT3E"
    assert "url" not in internal.payload and "never-persist-this" not in json.dumps(internal.model_dump())
    completed = api.await_job(submitted["job_id"])
    metadata = completed["result"]["state"]["analysis_metadata"]
    assert metadata["input_provider"] == "youtube" and metadata["provider_video_id"] == "y02mOQudT3E"
    assert calls == [("y02mOQudT3E", api.main.MAX_VIDEO_UPLOAD_BYTES)]
    assert not list((api.service.media_root / "pending").iterdir())


def test_provider_access_failure_is_actionable_without_leaking_external_error_details(api, monkeypatch, fake_cv):
    from tactiq.video.providers import ProviderInputError

    monkeypatch.setattr(api.main, "youtube_available", lambda: True)

    def denied(*args):
        raise ProviderInputError("YouTube requires sign-in. Upload an authorized original instead.")

    monkeypatch.setattr(api.main, "download_youtube", denied)
    submitted = api.request(
        "POST", "/api/v1/video/jobs/from-link", case="provider-denied-job-admission", status=202,
        json={"url": "https://youtu.be/y02mOQudT3E", "rights_confirmed": True},
        provenance=("curated-provider-denial",),
    ).json()
    failed = api.await_job(submitted["job_id"], expected="failed")
    assert failed["result"] is None and "sign-in" in failed["error"] and "authorized original" in failed["error"]
    assert not list((api.service.media_root / "pending").iterdir())


@pytest.mark.parametrize("route", ["/api/v1/video/upload", "/api/v1/video/jobs",
                                  "/api/v1/video/from-link", "/api/v1/video/jobs/from-link"])
def test_default_video_mode_requires_a_real_configured_detector(api, route):
    kwargs = {"json": {"url": "https://media.fixture.invalid/game.mp4"}} if route.endswith("from-link") else {
        "files": {"file": ("fixture.mp4", b"fixture", "video/mp4")},
    }
    response = api.request("POST", route, case="default-cv-never-synthetic-success", status=503, **kwargs)
    assert "TACTIQ_CV_MODEL_PATH" in response.json()["detail"]
    assert not api.service.matches


def test_new_matches_persist_the_richer_recipe_and_do_not_claim_camera_calibration(api):
    match = api.create()
    state = api.request("GET", "/api/v1/matches/{match_id}/state", case="v2-recipe-identity",
                        path_params={"match_id": match}).json()
    assert state["scenario_recipe_version"] == 2
    assert state["analysis_metadata"]["geometry_source"] == "simulation"
    assert state["action_count"] == state["tracking_sample_count"] == 0


def test_ordered_speech_contract_keeps_role_order_and_tenant_scope(api, monkeypatch, generated_media):
    from tactiq.agents.speech import SpeechResult

    captured = []

    def render(turns, language, primary_voice, secondary_voice, *, tenant_id):
        captured.append((turns, language, tenant_id))
        return SpeechResult(base64.b64encode(generated_media.audio).decode(), "audio/mpeg", "offline-ordered-fixture")

    monkeypatch.setattr(api.service.speech_synthesizer, "synthesize_turns", render)
    turns = [{"role": role, "text": text} for role, text in (
        ("lead", "The selected passage."), ("analyst", "Its supporting measurements."),
        ("lead", "The recorded sequence."), ("analyst", "Its evidence limitations."),
    )]
    response = api.request("POST", "/api/v1/speech/synthesize-dual", case="ordered-four-turn-speech",
                           actor="viewer", json={"turns": turns, "language": "en-GB"},
                           provenance=("fake-speech-renderer", "locally-generated-MP3")).json()
    assert response["provider"] == "offline-ordered-fixture"
    assert [turn.role for turn in captured[0][0]] == ["lead", "analyst", "lead", "analyst"]
    assert captured[0][1:] == ("en-GB", "audit_a")


@pytest.mark.parametrize("turns", [
    [{"role": "lead", "text": "Only one turn."}],
    [{"role": "lead", "text": "Lead."}, {"role": "lead", "text": "Not a distinct analyst turn."}],
    [{"role": "unknown", "text": "Unsupported."}, {"role": "analyst", "text": "Analyst."}],
])
def test_invalid_ordered_speech_is_rejected_before_synthesis(api, turns):
    api.request("POST", "/api/v1/speech/synthesize-dual", case="invalid-ordered-speech", status=422,
                json={"turns": turns})
