from __future__ import annotations

import os
import ipaddress
import socket
from urllib.parse import urljoin, urlparse

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import requests
from starlette.concurrency import run_in_threadpool

from tactiq.application.service import service
from tactiq.simulator.scenarios import list_scenarios


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


MAX_VIDEO_UPLOAD_BYTES = _env_int("VIDEO_UPLOAD_MAX_BYTES", 200 * 1024 * 1024)
MAX_VIDEO_REDIRECTS = _env_int("TACTIQ_VIDEO_MAX_REDIRECTS", 4)


def _cors_allow_origins() -> list[str]:
    raw = (os.getenv("TACTIQ_CORS_ALLOW_ORIGINS") or "").strip()
    if not raw:
        return []
    return [origin.strip() for origin in raw.split(",") if origin.strip()]

app = FastAPI(title="Match Intelligence API", version="0.1.0")

app.mount("/media", StaticFiles(directory=str(service.clips_root)), name="media")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_allow_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class CreateMatchRequest(BaseModel):
    scenario_id: str = Field(default="sustained_pressure")
    seed: int = Field(default=42001)


class NarrativeRequest(BaseModel):
    audience_mode: str = Field(default="fan")
    language: str = Field(default="en-GB")
    insight_id: str | None = None
    selected_player_id: str | None = None


class RecapRequest(BaseModel):
    audience_mode: str = Field(default="fan")
    language: str = Field(default="en-GB")
    selected_player_id: str | None = None


class SpeechRequest(BaseModel):
    text: str
    language: str = Field(default="en-GB")
    voice: str | None = None
    rate: str = Field(default="+2%")
    pitch: str = Field(default="+1st")


class DualSpeechRequest(BaseModel):
    primary_text: str
    secondary_text: str
    language: str = Field(default="en-GB")
    primary_voice: str | None = None
    secondary_voice: str | None = None


class DualCommentaryRequest(BaseModel):
    language: str = Field(default="en-GB")
    insight_id: str | None = None


class ViewerProfileRequest(BaseModel):
    favorite_team_id: str | None = None
    favorite_player_id: str | None = None
    focus_metric: str | None = None


class VideoLinkRequest(BaseModel):
    url: str


async def _read_upload_bounded(file: UploadFile, max_bytes: int) -> bytes:
    collected = bytearray()
    chunk_size = 1024 * 1024

    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break

        collected.extend(chunk)
        if len(collected) > max_bytes:
            raise HTTPException(status_code=413, detail=f"Uploaded video exceeds {max_bytes} bytes")

    return bytes(collected)


def _download_video_link_bounded(url: str, max_bytes: int) -> bytes:
    def _ensure_public_target(target_url: str) -> None:
        parsed = urlparse(target_url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("Video URL must be http(s)")

        hostname = (parsed.hostname or "").strip().lower()
        if not hostname:
            raise ValueError("Video URL host is missing")
        if hostname in {"localhost"} or hostname.endswith(".localhost"):
            raise ValueError("Private/loopback targets are not allowed")

        try:
            candidate_ips = {
                ipaddress.ip_address(hostname)
            } if _looks_like_ip(hostname) else {
                ipaddress.ip_address(entry[4][0])
                for entry in socket.getaddrinfo(hostname, None)
            }
        except (ValueError, socket.gaierror):
            raise ValueError("Unable to resolve video URL host")

        for ip in candidate_ips:
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
                raise ValueError("Private/loopback targets are not allowed")

    def _looks_like_ip(value: str) -> bool:
        try:
            ipaddress.ip_address(value)
            return True
        except ValueError:
            return False

    current_url = url
    data = bytearray()
    for _ in range(MAX_VIDEO_REDIRECTS + 1):
        _ensure_public_target(current_url)
        response = requests.get(
            current_url,
            headers={
                "User-Agent": "Mozilla/5.0 tactiq-video-fetch",
                "Accept": "video/*,*/*;q=0.8",
            },
            stream=True,
            allow_redirects=False,
            timeout=20,
        )
        try:
            if 300 <= response.status_code < 400 and response.headers.get("Location"):
                current_url = urljoin(current_url, response.headers["Location"])
                continue

            if response.status_code >= 400:
                raise ValueError(f"Video URL returned HTTP {response.status_code}")

            content_type = (response.headers.get("Content-Type") or "").lower()
            if content_type and not content_type.startswith("video/") and "octet-stream" not in content_type:
                raise ValueError(f"URL did not return a video content type ({content_type})")

            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                data.extend(chunk)
                if len(data) > max_bytes:
                    raise ValueError(f"Video exceeds upload limit of {max_bytes} bytes")
            break
        finally:
            response.close()
    else:
        raise ValueError("Too many redirects while downloading video URL")

    if not data:
        raise ValueError("Downloaded video is empty")

    return bytes(data)


@app.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
def ready() -> dict[str, str]:
    return {"status": "ready"}


@app.post("/api/v1/matches")
def create_match(payload: CreateMatchRequest) -> dict:
    try:
        state = service.create_match(scenario_id=payload.scenario_id, seed=payload.seed)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return state.model_dump()


@app.get("/api/v1/scenarios")
def get_scenarios() -> list[dict]:
    return [scenario.__dict__ for scenario in list_scenarios()]


@app.post("/api/v1/matches/{match_id}/start")
async def start_match(match_id: str) -> dict[str, str]:
    if match_id not in service.matches:
        raise HTTPException(status_code=404, detail="Match not found")
    await service.start_match(match_id)
    return {"status": "started"}


@app.get("/api/v1/matches/{match_id}/state")
def get_state(match_id: str) -> dict:
    if match_id not in service.matches:
        raise HTTPException(status_code=404, detail="Match not found")
    return service.get_match(match_id).model_dump()


@app.get("/api/v1/matches/{match_id}/events")
def get_events(match_id: str) -> list[dict]:
    if match_id not in service.matches:
        raise HTTPException(status_code=404, detail="Match not found")
    return [e.model_dump() for e in service.get_events(match_id)]


@app.get("/api/v1/matches/{match_id}/insights")
def get_insights(match_id: str) -> list[dict]:
    if match_id not in service.matches:
        raise HTTPException(status_code=404, detail="Match not found")
    return service.get_serialized_insights(match_id)


@app.post("/api/v1/matches/{match_id}/viewer-profile")
def set_viewer_profile(match_id: str, payload: ViewerProfileRequest) -> dict:
    if match_id not in service.matches:
        raise HTTPException(status_code=404, detail="Match not found")
    return service.set_viewer_profile(
        match_id=match_id,
        favorite_team_id=payload.favorite_team_id,
        favorite_player_id=payload.favorite_player_id,
        focus_metric=payload.focus_metric,
    )


@app.get("/api/v1/matches/{match_id}/overlay")
def get_overlay(match_id: str) -> dict:
    if match_id not in service.matches:
        raise HTTPException(status_code=404, detail="Match not found")
    return service.get_overlay_payload(match_id)


@app.post("/api/v1/video/upload")
async def upload_video_for_analysis(
    file: UploadFile = File(...),
    source_name: str | None = Form(default=None),
) -> dict:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Video filename is required")

    content = await _read_upload_bounded(file, MAX_VIDEO_UPLOAD_BYTES)
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded video is empty")

    effective_source = source_name or file.filename
    try:
        return await run_in_threadpool(
            service.create_match_from_video,
            video_bytes=content,
            source_name=effective_source,
            use_auto_eventing=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v1/video/from-link")
def create_from_video_link(payload: VideoLinkRequest) -> dict:
    if not payload.url.strip():
        raise HTTPException(status_code=400, detail="Video URL is required")

    source = payload.url.strip()
    parsed = urlparse(source)
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(status_code=400, detail="Video URL must be http(s)")

    try:
        content = _download_video_link_bounded(source, MAX_VIDEO_UPLOAD_BYTES)
        source_name = os.path.basename(parsed.path) or "linked_video.mp4"
        return service.create_match_from_video(
            video_bytes=content,
            source_name=source_name,
            use_auto_eventing=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except requests.RequestException as exc:
        raise HTTPException(status_code=400, detail=f"Unable to fetch video URL: {exc}") from exc


@app.post("/api/v1/matches/{match_id}/narratives")
def generate_narrative(match_id: str, payload: NarrativeRequest) -> dict:
    if match_id not in service.matches:
        raise HTTPException(status_code=404, detail="Match not found")
    try:
        result = service.generate_narrative(
            match_id=match_id,
            audience_mode=payload.audience_mode,
            language=payload.language,
            insight_id=payload.insight_id,
            selected_player_id=payload.selected_player_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "title": result.title,
        "body": result.body,
        "audience_mode": result.audience_mode,
        "language": result.language,
        "fallback_used": result.fallback_used,
        "provider": result.provider,
    }


@app.post("/api/v1/matches/{match_id}/recap")
def generate_recap(match_id: str, payload: RecapRequest) -> dict:
    if match_id not in service.matches:
        raise HTTPException(status_code=404, detail="Match not found")
    try:
        recap, top_insights, chapters = service.generate_recap(
            match_id=match_id,
            audience_mode=payload.audience_mode,
            language=payload.language,
            selected_player_id=payload.selected_player_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "title": recap.title,
        "body": recap.body,
        "audience_mode": recap.audience_mode,
        "language": recap.language,
        "fallback_used": recap.fallback_used,
        "provider": recap.provider,
        "top_insights": [
            {
                "insight_id": insight.insight_id,
                "title": insight.title,
                "confidence": insight.confidence,
                "team_id": insight.team_id,
            }
            for insight in top_insights
        ],
        "chapters": chapters,
    }


@app.post("/api/v1/speech/synthesize")
def synthesize_speech(payload: SpeechRequest) -> dict:
    try:
        result = service.synthesize_speech(
            text=payload.text,
            language=payload.language,
            voice=payload.voice,
            rate=payload.rate,
            pitch=payload.pitch,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {
        "provider": result.provider,
        "content_type": result.content_type,
        "audio_base64": result.audio_base64,
    }


@app.post("/api/v1/speech/synthesize-dual")
def synthesize_dual_speech(payload: DualSpeechRequest) -> dict:
    try:
        result = service.synthesize_dual_commentary(
            primary_text=payload.primary_text,
            secondary_text=payload.secondary_text,
            language=payload.language,
            primary_voice=payload.primary_voice,
            secondary_voice=payload.secondary_voice,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {
        "provider": result.provider,
        "content_type": result.content_type,
        "audio_base64": result.audio_base64,
    }


@app.post("/api/v1/matches/{match_id}/commentary/dual")
def generate_dual_commentary(match_id: str, payload: DualCommentaryRequest) -> dict:
    if match_id not in service.matches:
        raise HTTPException(status_code=404, detail="Match not found")

    try:
        result = service.generate_dual_commentary(
            match_id=match_id,
            language=payload.language,
            insight_id=payload.insight_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "primary_text": result.primary_text,
        "secondary_text": result.secondary_text,
        "language": result.language,
        "fallback_used": result.fallback_used,
        "provider": result.provider,
    }


@app.get("/api/v1/matches/{match_id}/commentary/debug")
def get_commentary_debug(match_id: str, limit: int = Query(default=20, ge=1, le=100)) -> dict:
    if match_id not in service.matches:
        raise HTTPException(status_code=404, detail="Match not found")

    return {
        "match_id": match_id,
        "count": len(service.commentary_audit_log.get(match_id, [])),
        "entries": service.get_commentary_audit(match_id=match_id, limit=limit),
    }


@app.websocket("/api/v1/ws/matches/{match_id}")
async def stream_match(websocket: WebSocket, match_id: str) -> None:
    if match_id not in service.matches:
        await websocket.close(code=4404)
        return

    await websocket.accept()
    queue = await service.subscribe(match_id)

    try:
        await websocket.send_json({"type": "match.snapshot", "payload": service.get_match(match_id).model_dump()})
        while True:
            message = await queue.get()
            await websocket.send_json(message)
    except WebSocketDisconnect:
        service.unsubscribe(match_id, queue)
