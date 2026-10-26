import pytest

from tactiq.video.providers import parse_video_source, select_youtube_media


@pytest.mark.parametrize("url", [
    "https://youtube.com/shorts/abcdefghijk?si=discard",
    "https://www.youtube.com/watch?v=abcdefghijk&list=discard",
    "https://youtu.be/abcdefghijk?t=12",
    "https://m.youtube.com/watch?v=abcdefghijk",
])
def test_authorized_youtube_video_forms_normalize_without_unrelated_parameters(url):
    source = parse_video_source(url)
    assert source.provider == "youtube"
    assert source.video_id == "abcdefghijk"
    assert source.url == "https://www.youtube.com/watch?v=abcdefghijk"


@pytest.mark.parametrize("url", [
    "https://youtube.com/playlist?list=abcdef",
    "https://youtube.com/redirect?q=http://127.0.0.1",
    "https://youtube.com/shorts/not-an-id",
    "https://youtube.com/watch?v=abcdefghijk&v=12345678901",
    "https://user:password@youtube.com/watch?v=abcdefghijk",
    "file:///private/video.mp4",
])
def test_invalid_provider_inputs_are_explicit_errors(url):
    with pytest.raises(ValueError):
        parse_video_source(url)


def test_direct_signed_urls_remain_ephemeral_and_are_not_printed_by_source_repr():
    url = "https://media.fixture.invalid/clip.mp4?signature=private-fixture"
    source = parse_video_source(url)
    assert source.provider == "direct" and source.url == url
    assert "signature" not in repr(source)
    assert parse_video_source("https://youtube.com.attacker.invalid/clip.mp4").provider == "direct"


def media(format_id, height, *, video=True, audio=False, size=1000):
    return {
        "format_id": format_id, "url": f"https://r1.googlevideo.com/{format_id}",
        "protocol": "https", "ext": "mp4" if video else "m4a",
        "width": round(height * 16 / 9), "height": height,
        "vcodec": "avc1" if video else "none", "acodec": "aac" if audio else "none",
        "filesize": size,
    }


def test_direct_video_and_audio_formats_are_selected_without_manifests_or_drm():
    info = {"duration": 40, "formats": [
        media("muxed", 360, audio=True), media("video", 720), media("audio", 0, video=False, audio=True),
        {**media("drm", 1080), "has_drm": True},
        {**media("playlist", 1080), "protocol": "m3u8_native"},
    ]}
    selected = select_youtube_media(info, max_bytes=100_000)
    assert [item.format_id for item in selected] == ["video", "audio"]


@pytest.mark.parametrize("info", [
    {"is_live": True, "duration": 40, "formats": [media("video", 360, audio=True)]},
    {"duration": 40, "formats": [{**media("video", 360, audio=True), "url": "https://127.0.0.1/private"}]},
    {"duration": 40, "formats": [{**media("video", 360, audio=True), "has_drm": True}]},
    {"duration": 40, "formats": [media("video", 360, audio=True, size=200_000)]},
])
def test_unavailable_unsafe_and_oversized_provider_media_are_not_success_fallbacks(info):
    with pytest.raises(ValueError):
        select_youtube_media(info, max_bytes=100_000)


@pytest.mark.parametrize("field,value", [("width", "large"), ("fps", float("nan")), ("filesize", True)])
def test_malformed_provider_numbers_do_not_reach_sorting_or_download(field, value):
    with pytest.raises(ValueError):
        select_youtube_media({"duration": 20, "formats": [{**media("video", 360, audio=True), field: value}]}, 100000)


def test_runtime_capability_requires_node_22_without_inheriting_machine_configuration(monkeypatch):
    from tactiq.video import providers

    providers._supported_node.cache_clear()
    monkeypatch.setattr(providers, "run_resolver_process", lambda *args, **kwargs: "v20.19.0")
    assert not providers._supported_node("node-version-fixture")
    providers._supported_node.cache_clear()
    monkeypatch.setattr(providers, "run_resolver_process", lambda *args, **kwargs: "v22.13.1")
    assert providers._supported_node("node-version-fixture")
    providers._supported_node.cache_clear()


def test_unresponsive_node_is_an_unconfigured_capability_not_an_api_crash(monkeypatch):
    from tactiq.video import providers

    def timeout(*args, **kwargs):
        raise RuntimeError("Provider resolution exceeded its time budget.")

    providers._supported_node.cache_clear()
    monkeypatch.setattr(providers, "run_resolver_process", timeout)
    assert not providers._supported_node("blocked-node-fixture")
    providers._supported_node.cache_clear()


def test_bounded_resolver_timeout_becomes_a_curated_provider_failure(monkeypatch):
    import time
    from tactiq.video import providers

    def timeout(*args, **kwargs):
        raise RuntimeError("Provider resolution exceeded its time budget.")

    monkeypatch.setattr(providers, "youtube_available", lambda: True)
    monkeypatch.setattr(providers, "run_resolver_process", timeout)
    with pytest.raises(providers.VideoProviderUnavailable, match="safety limits"):
        providers.resolve_youtube("abcdefghijk", time.monotonic() + 3)


def test_raw_extractor_formats_receive_the_protocol_normally_added_by_postprocessing():
    from tactiq.video.youtube_resolver import format_metadata

    raw = media("video", 360, audio=True)
    raw.pop("protocol")
    normalized = format_metadata(raw)
    assert normalized["protocol"] == "https"
    assert select_youtube_media({"duration": 9, "formats": [normalized]}, 100000)[0].format_id == "video"
    manifest = format_metadata({**raw, "url": "https://r1.googlevideo.com/playlist.m3u8",
                                "protocol": "m3u8_native"})
    with pytest.raises(ValueError):
        select_youtube_media({"duration": 9, "formats": [manifest]}, 100000)
