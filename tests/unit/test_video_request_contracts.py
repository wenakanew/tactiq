from types import SimpleNamespace

import pytest

from tactiq.presentation.api import main
from tactiq.cv.detection import CvUnavailableError


def test_direct_link_honors_requested_analysis_mode_and_team_metadata(monkeypatch, tmp_path):
    calls = []
    model = tmp_path / "fixture.onnx"
    model.write_bytes(b"fixture model; adapter is mocked")
    monkeypatch.setenv("TACTIQ_CV_MODEL_PATH", str(model))

    def cv(**arguments):
        calls.append(arguments)
        return {"extractor": "cv-fixture"}

    monkeypatch.setattr(main, "_download_video_link_bounded", lambda *args: b"fixture")
    monkeypatch.setattr(main.service, "create_match_from_cv", cv)
    monkeypatch.setattr(main.service, "create_match_from_video", lambda **kwargs: {"extractor": "event-fixture"})
    payload = main.VideoLinkRequest(
        url="https://video.example/match.mp4", analysis_mode="cv",
        team_display_names={"team_a": "Home", "team_b": "Away"},
        options={"sample_fps": 4},
    )
    request = SimpleNamespace(state=SimpleNamespace(principal=SimpleNamespace(tenant_id="studio")))
    result = main.create_from_video_link(payload, request)
    assert result["extractor"] == "cv-fixture"
    assert calls[0]["options"].sample_fps == 4
    assert calls[0]["team_display_names"] == {"team_a": "Home", "team_b": "Away"}
    assert calls[0]["tenant_id"] == "studio"


def test_model_unavailability_on_the_link_path_is_explicit(monkeypatch, tmp_path):
    model = tmp_path / "fixture.onnx"
    model.write_bytes(b"fixture model; adapter is mocked")
    monkeypatch.setenv("TACTIQ_CV_MODEL_PATH", str(model))
    def unavailable(**arguments):
        raise CvUnavailableError("CV model unavailable")

    monkeypatch.setattr(main, "_download_video_link_bounded", lambda *args: b"fixture")
    monkeypatch.setattr(main.service, "create_match_from_cv", unavailable)
    monkeypatch.setattr(main.service, "create_match_from_video", lambda **kwargs: {"extractor": "event-fixture"})
    request = SimpleNamespace(state=SimpleNamespace(principal=SimpleNamespace(tenant_id="studio")))
    with pytest.raises(main.HTTPException) as error:
        main.create_from_video_link(main.VideoLinkRequest(url="https://video.example/match.mp4", analysis_mode="cv"), request)
    assert error.value.status_code == 503
