from __future__ import annotations


def validate_video_container(content: bytes) -> None:
    iso_media = len(content) >= 12 and content[4:8] == b"ftyp"
    avi = len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"AVI "
    ebml = content.startswith(b"\x1a\x45\xdf\xa3")
    if not (iso_media or avi or ebml):
        raise ValueError("Only MP4/MOV, AVI, and WebM/Matroska video containers are supported; playlists are not accepted")
