from tactiq.application.service import MatchService
from tactiq.video.extractor import extract_events_from_video_stub


def test_extract_events_from_video_stub_generates_events() -> None:
    result = extract_events_from_video_stub(content=b"fake-video-binary", match_id="match_video")

    assert result.extractor == "video-stub-v1"
    assert 0.0 <= result.confidence <= 1.0
    assert len(result.events) >= 6
    assert all(event.match_id == "match_video" for event in result.events)


def test_create_match_from_video_runs_pipeline() -> None:
    service = MatchService()

    summary = service.create_match_from_video(video_bytes=b"clip-data", source_name="upload.mp4")

    assert summary["match_id"].startswith("match_")
    assert summary["source"] == "upload.mp4"
    assert summary["extractor"] in {"video-auto-v1", "video-stub-v1"}
    assert summary["event_count"] >= 6
    assert summary["state"]["status"] == "completed"
    assert isinstance(summary["insights"], list)
