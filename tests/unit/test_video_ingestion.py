from tactiq.application.service import MatchService
from tactiq.video.extractor import extract_events_from_video_stub
from tactiq.domain.models import MatchStatus
import pytest
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event
from tactiq.runtime import SQLiteStore


def test_extract_events_from_video_stub_generates_events() -> None:
    result = extract_events_from_video_stub(content=b"fake-video-binary", match_id="match_video")

    assert result.extractor == "video-stub-v1"
    assert 0.0 <= result.confidence <= 1.0
    assert len(result.events) >= 6
    assert all(event.match_id == "match_video" for event in result.events)


def test_create_match_from_video_runs_pipeline() -> None:
    service = MatchService()

    summary = service.create_match_from_video(video_bytes=b"clip-data", source_name="upload.mp4", use_auto_eventing=False)

    assert summary["match_id"].startswith("match_")
    assert summary["source"] == "upload.mp4"
    assert summary["extractor"] in {"video-auto-v1", "video-stub-v1"}
    assert summary["event_count"] >= 6
    assert summary["state"]["status"] == "completed"
    assert isinstance(summary["insights"], list)


def test_decode_failure_does_not_leave_a_running_match(tmp_path, monkeypatch):
    monkeypatch.setenv("TACTIQ_MEDIA_DIR", str(tmp_path))
    service = MatchService()
    def invalid_media(*args):
        raise ValueError("invalid media")
    monkeypatch.setattr("tactiq.application.service.normalize_video", invalid_media)
    with pytest.raises(ValueError, match="invalid media"):
        service.create_match_from_video(b"\x00\x00\x00\x18ftyp" + b"unsupported", "bad.mp4")
    assert all(match.status == MatchStatus.FAILED for match in service.matches.values())


def test_completed_analysis_cannot_be_evicted_before_its_result_is_detached(tmp_path, monkeypatch):
    monkeypatch.setenv("TACTIQ_MEDIA_DIR", str(tmp_path / "media"))
    service = MatchService()
    service.max_cached_matches = 2
    with SQLiteStore(tmp_path / "matches.sqlite3") as store:
        service.configure_storage(store)
        other = service.create_match("sustained_pressure", 1)
        other.status = MatchStatus.RUNNING
        summary_started, allow_summary = Event(), Event()
        original = service._video_summary

        def gated_summary(*args, **kwargs):
            summary_started.set()
            assert allow_summary.wait(timeout=5)
            return original(*args, **kwargs)

        monkeypatch.setattr(service, "_video_summary", gated_summary)
        with ThreadPoolExecutor(max_workers=2) as executor:
            analysis = executor.submit(service.create_match_from_video, b"synthetic-fixture", "fixture.mp4", False)
            assert summary_started.wait(timeout=5)
            admission = executor.submit(service.create_match, "sustained_pressure", 2)
            try:
                with pytest.raises(TimeoutError):
                    admission.result(timeout=0.1)
            finally:
                allow_summary.set()
            summary = analysis.result(timeout=5)
            admission.result(timeout=5)
        assert summary["event_count"] >= 6
        assert summary["state"]["status"] == "completed"
        assert store.load_match("local", summary["match_id"])["state"]["status"] == "completed"
