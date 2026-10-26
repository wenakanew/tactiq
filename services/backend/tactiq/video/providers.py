from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
import importlib.util
import ipaddress
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Literal
from urllib.parse import parse_qs, urlsplit

from tactiq.video.download import download_video
from tactiq.video.resolver_process import run_resolver_process
from tactiq.runtime.jobs import PublicJobError


class VideoProviderUnavailable(PublicJobError, RuntimeError):
    pass


class ProviderInputError(PublicJobError, ValueError):
    pass


@dataclass(frozen=True)
class VideoSource:
    provider: Literal["direct", "youtube"]
    url: str = field(repr=False)
    video_id: str | None = None


@dataclass(frozen=True)
class MediaFormat:
    format_id: str
    url: str = field(repr=False)
    has_video: bool = True
    has_audio: bool = False


def parse_video_source(url: str) -> VideoSource:
    try:
        parsed = urlsplit(url.strip())
        port = parsed.port
    except ValueError as exc:
        raise ValueError("The video link is malformed.") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Use an HTTP(S) video link without embedded credentials.")
    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost"):
        raise ValueError("Video links must use a public media host.")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None and not literal.is_global:
        raise ValueError("Video links must use a public media address.")
    if host not in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be"}:
        return VideoSource("direct", url.strip())
    if port not in {None, 80, 443}:
        raise ValueError("YouTube links must use a standard web port.")
    parts = parsed.path.strip("/").split("/")
    if host in {"youtu.be", "www.youtu.be"} and len(parts) == 1:
        video_id = parts[0]
    elif len(parts) == 2 and parts[0] in {"shorts", "embed"}:
        video_id = parts[1]
    elif parts == ["watch"]:
        ids = parse_qs(parsed.query).get("v", [])
        video_id = ids[0] if len(ids) == 1 else ""
    else:
        video_id = ""
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise ValueError("Use a single YouTube watch or Shorts video link, not a playlist, channel, or redirect page.")
    return VideoSource("youtube", f"https://www.youtube.com/watch?v={video_id}", video_id)


@lru_cache(maxsize=8)
def _supported_node(binary: str) -> bool:
    try:
        version = run_resolver_process([binary, "--version"], time.monotonic() + 3).strip()
    except (ValueError, OSError, RuntimeError):
        return False
    match = re.fullmatch(r"v(\d+)\.\d+\.\d+", version)
    return bool(match and int(match[1]) >= 22)


def youtube_available() -> bool:
    binary = shutil.which("node")
    return bool(binary and importlib.util.find_spec("yt_dlp") and importlib.util.find_spec("yt_dlp_ejs") and _supported_node(binary))


def _safe_format(item: dict, max_bytes: int) -> bool:
    try:
        parsed = urlsplit(item.get("url", ""))
        host = (parsed.hostname or "").lower()
        port = parsed.port
        size = item.get("filesize") or item.get("filesize_approx")
        for name in ("filesize", "filesize_approx", "width", "height", "fps", "abr", "tbr"):
            value = item.get(name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                      or not math.isfinite(value) or value < 0):
                return False
        return (
            item.get("protocol") == "https" and parsed.scheme == "https" and port in {None, 443}
            and not parsed.username and not parsed.password
            and (host == "googlevideo.com" or host.endswith(".googlevideo.com"))
            and not item.get("has_drm") and not item.get("fragments")
            and (size is None or (isinstance(size, (int, float)) and math.isfinite(size) and 0 < size <= max_bytes))
        )
    except (TypeError, ValueError):
        return False


def select_youtube_media(info: dict, max_bytes: int) -> list[MediaFormat]:
    duration = info.get("duration")
    if info.get("is_live") or info.get("live_status") in {"is_live", "is_upcoming", "post_live"}:
        raise ProviderInputError("Live or upcoming YouTube streams are not supported. Use a finite video you are authorized to analyze.")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or not 0 < duration <= 600:
        raise ProviderInputError("Import a finite YouTube clip of at most ten minutes, or upload a trimmed original.")
    formats = [item for item in info.get("formats", []) if isinstance(item, dict) and _safe_format(item, max_bytes)]
    videos = [item for item in formats if item.get("vcodec") not in {None, "none"}]
    audios = [item for item in formats if item.get("acodec") not in {None, "none"} and item.get("vcodec") in {None, "none"}]
    # Prefer useful source detail without selecting extreme-resolution streams.
    videos = [item for item in videos if 0 < (item.get("width") or 0) * (item.get("height") or 0) <= 1920 * 1080]
    videos.sort(key=lambda item: ((item.get("width") or 0) * (item.get("height") or 0),
                                 item.get("fps") or 0, item.get("acodec") not in {None, "none"}), reverse=True)
    audios.sort(key=lambda item: item.get("abr") or item.get("tbr") or 0, reverse=True)
    for video in videos:
        result = [MediaFormat(str(video.get("format_id", "video")), video["url"], True,
                              video.get("acodec") not in {None, "none"})]
        if not result[0].has_audio and audios:
            audio = audios[0]
            sizes = [(part.get("filesize") or part.get("filesize_approx") or 0) for part in (video, audio)]
            if sum(sizes) > max_bytes:
                continue
            result.append(MediaFormat(str(audio.get("format_id", "audio")), audio["url"], False, True))
        return result
    raise ProviderInputError("YouTube did not provide an accessible, unprotected direct media format within the import limits. Upload an authorized original instead.")


def resolve_youtube(video_id: str, deadline: float) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise ValueError("Invalid YouTube video identifier.")
    if not youtube_available():
        raise VideoProviderUnavailable("YouTube import requires the locked yt-dlp/EJS packages and Node 22 or newer.")
    try:
        output = run_resolver_process(
            [sys.executable, "-I", "-B", "-m", "tactiq.video.youtube_resolver", f"--video-id={video_id}"],
            deadline=min(deadline, time.monotonic() + 45),
        )
        info = json.loads(output)
    except (OSError, ValueError, RuntimeError) as exc:
        raise VideoProviderUnavailable("The YouTube resolver did not finish within its safety limits. Upload an authorized original or retry later.") from exc
    if not isinstance(info, dict):
        raise VideoProviderUnavailable("The YouTube resolver returned an invalid response.")
    errors = {
        "access_required": "YouTube requires sign-in or additional access for this video. Upload an authorized original; browser cookies are not imported.",
        "unavailable": "This YouTube video is unavailable to the import service. Check access or upload an authorized original.",
        "protected": "Protected or DRM-restricted YouTube media cannot be imported.",
        "network_policy": "The provider attempted a connection outside the permitted public-network policy.",
        "resolver_failed": "YouTube could not be resolved by the configured provider. Upload an authorized original or retry later.",
    }
    if info.get("error"):
        code = info["error"]
        raise ProviderInputError(errors.get(code, errors["resolver_failed"]) if isinstance(code, str) else errors["resolver_failed"])
    if info.get("id") != video_id:
        raise ValueError("YouTube returned a different video than the one requested.")
    return info


def download_youtube(video_id: str, max_bytes: int) -> bytes:
    deadline = time.monotonic() + 90
    formats = select_youtube_media(resolve_youtube(video_id, deadline), max_bytes)
    parts: list[bytes] = []
    remaining = max_bytes
    for item in formats:
        try:
            content = download_video(item.url, remaining, allow_audio=not item.has_video, deadline=deadline,
                                     allowed_host_suffix="googlevideo.com")
        except ValueError as exc:
            raise ProviderInputError("YouTube media could not be fetched within the public-host, size or time limits. Upload a trimmed authorized original instead.") from exc
        parts.append(content)
        remaining -= len(content)
    if len(parts) == 1:
        return parts[0]
    binary = shutil.which("ffmpeg")
    if not binary:
        raise VideoProviderUnavailable("FFmpeg is required to combine YouTube video and audio.")
    with tempfile.TemporaryDirectory(prefix="tactiq-provider-") as directory:
        root = Path(directory)
        video, audio, target = root / "video.bin", root / "audio.bin", root / "combined.mkv"
        video.write_bytes(parts[0])
        audio.write_bytes(parts[1])
        remaining_time = deadline - time.monotonic()
        if remaining_time <= 0:
            raise ProviderInputError("YouTube import exceeded its time budget. Upload a trimmed authorized original instead.")
        try:
            completed = subprocess.run(
                [binary, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                 "-protocol_whitelist", "file,pipe", "-i", str(video),
                 "-protocol_whitelist", "file,pipe", "-i", str(audio),
                 "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", "-shortest",
                 "-fs", str(max_bytes), "-f", "matroska", str(target)],
                capture_output=True, timeout=min(30, remaining_time), check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise VideoProviderUnavailable("YouTube video/audio composition did not complete.") from exc
        if completed.returncode or not target.is_file() or not target.stat().st_size:
            raise ProviderInputError("The selected YouTube streams could not be combined. Upload an authorized original instead.")
        if target.stat().st_size >= max_bytes:
            raise ProviderInputError("Combined YouTube media exceeds the import size limit. Upload a trimmed original instead.")
        return target.read_bytes()
