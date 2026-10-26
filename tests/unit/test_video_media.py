import shutil
import subprocess

import pytest

from tactiq.video.media import normalize_video
from tactiq.video.validation import validate_video_container


def test_playlists_never_reach_native_video_decoders():
    for payload in (b"#EXTM3U\nhttp://127.0.0.1/private", b"file:///private", b"not-a-video"):
        with pytest.raises(ValueError, match="playlists"):
            validate_video_container(payload)


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="Requires the media runtime")
def test_normalized_replay_is_mp4_and_discloses_duration_limit(tmp_path):
    source = tmp_path / "generated.avi"
    subprocess.run([shutil.which("ffmpeg"), "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=green:s=160x100:r=10:d=1", "-c:v", "mjpeg", str(source)],
                   check=True, timeout=10)
    result = normalize_video(source.read_bytes(), 0.5)
    assert result.content[4:8] == b"ftyp"
    assert result.truncated
    assert result.original_duration_ms >= 900
