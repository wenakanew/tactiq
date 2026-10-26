import os
from pathlib import Path
import shutil

import pytest

from tactiq.application.service import MatchService
from tactiq.cv.types import CvOptions
from tactiq.runtime import SQLiteStore


@pytest.mark.skipif(not os.getenv("TACTIQ_TEST_ONNX_MODEL"), reason="Requires an explicitly supplied licensed model asset")
def test_real_onnx_observations_reach_durable_match_and_replay(tmp_path, monkeypatch):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("Media runtime is required")
    configured_source = os.getenv("TACTIQ_TEST_FOOTBALL_CLIP", "").strip()
    if not configured_source:
        pytest.skip("Supply TACTIQ_TEST_FOOTBALL_CLIP explicitly; private footage is not a repository fixture")
    source = Path(configured_source)
    if not source.is_file():
        pytest.skip("Optional locally supplied football clip is not present")
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("TACTIQ_CV_DETECTOR", "onnx")
    monkeypatch.setenv("TACTIQ_CV_MODEL_PATH", os.environ["TACTIQ_TEST_ONNX_MODEL"])
    monkeypatch.setenv("TACTIQ_CV_MODEL_FORMAT", "yolox")
    monkeypatch.setenv("TACTIQ_CV_INPUT_SIZE", "416")
    monkeypatch.setenv("TACTIQ_MEDIA_DIR", str(tmp_path / "media"))
    with SQLiteStore(tmp_path / "model.sqlite3") as store:
        service = MatchService()
        service.configure_storage(store)
        result = service.create_match_from_cv(source.read_bytes(), "source.mp4",
                                               CvOptions(sample_fps=2, max_duration_seconds=2))
        match_id = result["match_id"]
        replay = service.get_replay(match_id)
        assert result["state"]["status"] == "completed"
        assert result["state"]["analysis_metadata"]["detection_source"] == "model"
        assert result["state"]["score"] == {}
        assert result["state"]["attack_directions"] == {}
        assert any(frame["tracking"]["players"] for frame in replay["frames"])
        assert all(frame["tracking"]["ball_speed_kmh"] is None for frame in replay["frames"])
        assert replay["source_url"].endswith("/media/source")
        recovered = MatchService()
        recovered.configure_storage(store)
        assert recovered.restore_match("local", match_id)
        assert recovered.get_replay(match_id)["frames"] == replay["frames"]
