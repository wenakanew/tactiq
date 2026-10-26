from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from tactiq.video.validation import validate_video_container


@dataclass(frozen=True)
class NormalizedVideo:
    content: bytes
    original_duration_ms: int | None
    truncated: bool


def normalize_video(content: bytes, max_duration_seconds: float, max_output_bytes: int = 200 * 1024 * 1024) -> NormalizedVideo:
    validate_video_container(content)
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise RuntimeError("FFmpeg and FFprobe are required for browser-compatible synchronized replay")
    with tempfile.TemporaryDirectory(prefix="tactiq-media-") as directory:
        root = Path(directory)
        source, target = root / "input.bin", root / "replay.mp4"
        source.write_bytes(content)
        try:
            probe = subprocess.run(
                [ffprobe, "-v", "error", "-protocol_whitelist", "file,pipe", "-show_entries",
                 "format=duration", "-of", "json", str(source)],
                capture_output=True, timeout=10, check=False,
            )
            if probe.returncode:
                raise ValueError("Uploaded bytes are not a decodable supported video")
            duration = json.loads(probe.stdout).get("format", {}).get("duration")
            original_duration = round(float(duration) * 1000) if duration is not None else None
            result = subprocess.run(
                [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                 "-protocol_whitelist", "file,pipe", "-i", str(source), "-t", str(max_duration_seconds),
                 "-map", "0:v:0", "-map", "0:a:0?", "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2,setsar=1",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                 "-fps_mode", "vfr", "-c:a", "aac", "-movflags", "+faststart",
                 "-fs", str(max_output_bytes), str(target)],
                capture_output=True, timeout=90, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Video normalization exceeded its processing budget") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("Unable to prepare the source video") from exc
        if result.returncode or not target.is_file() or not target.stat().st_size:
            raise ValueError("Unable to normalize the uploaded video for playback")
        if target.stat().st_size >= max_output_bytes:
            raise ValueError("Normalized video exceeds the configured media size limit")
        return NormalizedVideo(
            target.read_bytes(), original_duration,
            original_duration is not None and original_duration > max_duration_seconds * 1000,
        )
