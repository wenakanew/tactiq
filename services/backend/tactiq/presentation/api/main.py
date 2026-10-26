from __future__ import annotations

import os
import asyncio
from contextlib import asynccontextmanager
import logging
from pathlib import Path
import re
import shutil
import time
from typing import Literal
from urllib.parse import urlparse
from uuid import uuid4

import anyio
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from starlette.concurrency import run_in_threadpool
from starlette.websockets import WebSocketState

from tactiq.application.service import MatchCapacityError, service
from tactiq.agents.commentary import CommentaryTurn, GeneratedDialogue
from tactiq.cv.detection import CvUnavailableError
from tactiq.cv.ingest import extract_cv_tracks_from_video_bytes
from tactiq.cv.types import CvOptions
from tactiq.domain.models import MatchStatus
from tactiq.presentation.api.policy import BodyLimitMiddleware, RequestLimiter, ResponseLifetimeMiddleware
from tactiq.runtime import AuthSettings, BackgroundJobs, RecordValidationError, SQLiteStore, require_role, validate_identifier
from tactiq.runtime.observability import RequestMetrics, ensure_request_id
from tactiq.simulator.scenarios import list_scenarios
from tactiq.video.download import download_video
from tactiq.video.providers import download_youtube, parse_video_source, youtube_available
from tactiq.operations.monitor import prometheus_metrics, slo_status

logger = logging.getLogger(__name__)
SUPPORTED_LANGUAGES = Literal["en-GB", "sw-KE", "fr-FR", "es-ES", "pt-BR", "ar-SA", "hi-IN"]


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


MAX_VIDEO_UPLOAD_BYTES = _env_int("VIDEO_UPLOAD_MAX_BYTES", 200 * 1024 * 1024)
MAX_VIDEO_REDIRECTS = _env_int("TACTIQ_VIDEO_MAX_REDIRECTS", 4)


def _cors_allow_origins() -> list[str]:
    raw = (os.getenv("TACTIQ_CORS_ALLOW_ORIGINS") or "").strip()
    defaults = [] if os.getenv("APP_ENV", "local").lower() == "production" else ["http://localhost:3000", "http://127.0.0.1:3000"]
    configured = [origin.strip() for origin in raw.split(",") if origin.strip()] if raw else []
    combined: list[str] = []
    for origin in [*configured, *defaults]:
        if origin not in combined:
            combined.append(origin)
    for origin in combined:
        parsed = urlparse(origin)
        if not parsed.netloc or parsed.path or parsed.scheme not in {"http", "https"}:
            raise ValueError("TACTIQ_CORS_ALLOW_ORIGINS must contain HTTP(S) origins without paths")
        if os.getenv("APP_ENV", "local").lower() == "production" and parsed.scheme != "https":
            raise ValueError("Production browser origins must use HTTPS")
    return combined


def _pending_video_path(payload: dict) -> Path | None:
    if not payload.get("input_path"):
        return None
    operation = payload.get("operation_id", "")
    if not isinstance(operation, str) or not re.fullmatch(r"[a-f0-9]{32}", operation):
        raise ValueError("Invalid analysis input identifier")
    root = (service.media_root / "pending").resolve()
    path = Path(payload["input_path"])
    if path.is_symlink() or path.resolve() != root / f"{operation}.bin":
        raise ValueError("Invalid pending video location")
    return path


def _cleanup_terminal_video_input(job) -> None:
    if job.kind == "video" and job.status in {"succeeded", "failed", "cancelled", "dead_letter"}:
        path = _pending_video_path(job.payload)
        if path is not None:
            path.unlink(missing_ok=True)


def _cleanup_inactive_video_inputs(store: SQLiteStore) -> None:
    active = {Path(path).resolve() for path in store.active_video_inputs()}
    root = (service.media_root / "pending").resolve()
    for path in root.iterdir():
        if not re.fullmatch(r"[a-f0-9]{32}\.(bin|part)", path.name):
            continue
        if path.is_symlink() or not path.is_file():
            logger.warning("Unexpected pending input entry requires operator inspection")
            continue
        if path.with_suffix(".bin").resolve() not in active:
            path.unlink(missing_ok=True)


def _run_video_job(payload: dict) -> dict:
    started = time.monotonic()
    status_code = 200
    tenant = payload["tenant_id"]
    operation = payload["operation_id"]
    target_id = f"match_{operation}"
    input_path = _pending_video_path(payload)
    try:
        with service.lock:
            if service.restore_match(tenant, target_id) and service.matches[target_id].status == MatchStatus.COMPLETED:
                state = service.matches[target_id]
                metadata = state.analysis_metadata
                return service._video_summary(state, payload["source_name"], metadata.get("extractor", "video"),
                                              float(metadata.get("extractor_confidence", 0)), metadata.get("notes", "Recovered completed analysis."))
        if payload.get("youtube_video_id"):
            if input_path is None:
                raise ValueError("Provider jobs require a managed input location")
            if input_path.exists():
                content = input_path.read_bytes()
            else:
                content = download_youtube(payload["youtube_video_id"], MAX_VIDEO_UPLOAD_BYTES)
                temporary = input_path.with_suffix(".part")
                try:
                    if temporary.is_symlink():
                        raise ValueError("Invalid managed provider input")
                    temporary.write_bytes(content)
                    temporary.replace(input_path)
                finally:
                    temporary.unlink(missing_ok=True)
        else:
            content = input_path.read_bytes() if input_path else _download_video_link_bounded(payload["url"], MAX_VIDEO_UPLOAD_BYTES)
        if len(content) > MAX_VIDEO_UPLOAD_BYTES:
            raise ValueError("Video exceeds the configured limit")
        arguments = {
            "video_bytes": content, "source_name": payload["source_name"], "tenant_id": tenant,
            "operation_id": operation, "team_display_names": payload.get("team_display_names"),
            "source_provider": payload.get("source_provider", "upload"),
            "source_video_id": payload.get("youtube_video_id"),
        }
        if payload["analysis_mode"] == "cv":
            return service.create_match_from_cv(**arguments, options=CvOptions.model_validate(payload.get("options", {})))
        return service.create_match_from_video(**arguments)
    except ValueError:
        status_code = 400
        raise
    except Exception:
        status_code = 500
        raise
    finally:
        try:
            if input_path is not None:
                input_path.unlink(missing_ok=True)
                input_path.with_suffix(".part").unlink(missing_ok=True)
        finally:
            if hasattr(app.state, "job_metrics"):
                app.state.job_metrics.record("POST", f"/jobs/{payload['analysis_mode']}", status_code,
                                             time.monotonic() - started, error=status_code >= 500)


@asynccontextmanager
async def lifespan(application: FastAPI):
    if os.getenv("TACTIQ_RUNTIME_MODE", "local").strip().lower() != "local":
        raise RuntimeError("Cloud runtime requires tactiq.presentation.api.entrypoint:app; refusing local storage")
    auth = AuthSettings.from_env()
    store = SQLiteStore(os.getenv("TACTIQ_DATABASE_PATH") or service.media_root / "metadata.sqlite3")
    jobs = None
    try:
        service.configure_storage(store)
        (service.media_root / "pending").mkdir(parents=True, exist_ok=True)
        jobs = BackgroundJobs(
            store, {"video": _run_video_job},
            max_workers=_env_int("TACTIQ_JOB_WORKERS", 2), max_pending=_env_int("TACTIQ_JOB_CAPACITY", 8),
        )
        application.state.auth = auth
        application.state.store = store
        application.state.jobs = jobs
        application.state.metrics = RequestMetrics()
        application.state.job_metrics = RequestMetrics()
        application.state.limiter = RequestLimiter(
            _env_int("TACTIQ_REQUESTS_PER_MINUTE", 240), _env_int("TACTIQ_EXPENSIVE_REQUESTS_PER_MINUTE", 12)
        )
        application.state.heavy_admission = asyncio.Semaphore(4)
        jobs.recover()
        _cleanup_inactive_video_inputs(store)
        yield
    finally:
        for match_id in tuple(service.tasks):
            await service.pause_match(match_id)
        if jobs is not None:
            await run_in_threadpool(jobs.close)
        store.close()
        service.store = None


app = FastAPI(title="Match Intelligence API", version="0.2.0", lifespan=lifespan)

@app.middleware("http")
async def request_policy(request: Request, call_next):
    started = time.monotonic()
    request_id = ensure_request_id(request.headers.get("X-Request-ID"))
    response = None
    admitted = False
    try:
        if request.method == "OPTIONS" or request.url.path in {"/health/live", "/health/ready"}:
            response = await call_next(request)
        elif not hasattr(request.app.state, "auth"):
            response = JSONResponse({"detail": "API runtime is not initialized"}, status_code=503)
        elif request.url.path == "/api/v1/capabilities":
            response = await call_next(request)
        else:
            auth = request.app.state.auth
            principal = auth.authenticate(request.headers.get("Authorization"), request.cookies.get(auth.cookie_name))
            request.state.principal = principal
            modifying = request.method not in {"GET", "HEAD"}
            origin = request.headers.get("Origin")
            allowed = set(_cors_allow_origins()) | {f"{request.url.scheme}://{request.url.netloc}"}
            if origin and origin not in allowed:
                raise HTTPException(403, "Browser origin is not allowed")
            if modifying and auth.enabled and not request.headers.get("Authorization") and not origin:
                raise HTTPException(403, "Cookie-authenticated writes require a verified Origin")
            expensive = modifying and any(
                part in request.url.path for part in ("/video", "/cv/", "/narratives", "/recap", "/commentary/dual", "/speech/")
            )
            identity = f"{principal.tenant_id}:{principal.subject}"
            request.app.state.limiter.check(identity, expensive)
            if expensive:
                try:
                    await asyncio.wait_for(request.app.state.heavy_admission.acquire(), timeout=0.01)
                    admitted = True
                except TimeoutError as exc:
                    raise HTTPException(503, "Analysis request capacity reached", headers={"Retry-After": "5"}) from exc
            minimum = "viewer"
            if modifying and not any(part in request.url.path for part in ("/narratives", "/recap", "/commentary/dual", "/speech/", "/session")):
                minimum = "analyst"
            if request.method == "DELETE" or request.url.path.startswith("/api/v1/operations/"):
                minimum = "admin"
            if request.url.path.endswith("/commentary/debug"):
                minimum = "analyst"
            require_role(principal, minimum)
            resource = re.match(r"^/(?:api/v1/matches|api/v1/jobs|media)/([^/]+)/", request.url.path + "/")
            if resource:
                try:
                    validate_identifier(resource.group(1), "resource_id")
                except RecordValidationError as exc:
                    raise HTTPException(400, "Invalid resource identifier") from exc
            match = re.match(r"^/(?:api/v1/matches|media)/([^/]+)/", request.url.path + "/")
            if match:
                if not await run_in_threadpool(service.pin_match, principal.tenant_id, match.group(1)):
                    raise HTTPException(404, "Match not found")
                request.scope["tactiq_match_release"] = lambda match_id=match.group(1): service.unpin_match(match_id)
            response = await call_next(request)
    except HTTPException as exc:
        response = JSONResponse({"detail": exc.detail, "request_id": request_id}, status_code=exc.status_code, headers=exc.headers)
    except MatchCapacityError as exc:
        response = JSONResponse({"detail": str(exc), "request_id": request_id}, status_code=503)
    except Exception:
        logger.exception("API request failed", extra={"request_id": request_id})
        response = JSONResponse({"detail": "Internal server error", "request_id": request_id}, status_code=500)
    finally:
        if request.scope.get("body_limit_exceeded"):
            response = JSONResponse({"detail": "Request body exceeds the configured limit", "request_id": request_id},
                                    status_code=413)
        if admitted:
            request.app.state.heavy_admission.release()
        if response is not None and hasattr(request.app.state, "metrics"):
            route = getattr(request.scope.get("route"), "path", "unmatched")
            request.app.state.metrics.record(request.method, route, response.status_code, time.monotonic() - started,
                                             error=response.status_code >= 500)
    assert response is not None
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    if request.url.path.startswith(("/api/", "/media/")):
        response.headers["Cache-Control"] = "private, no-store"
    return response


app.add_middleware(BodyLimitMiddleware, upload_limit=MAX_VIDEO_UPLOAD_BYTES)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_allow_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID", "Range"],
    expose_headers=["X-Request-ID", "Content-Range", "Accept-Ranges"],
)
app.add_middleware(ResponseLifetimeMiddleware)


class TeamNames(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    team_a: str = Field(min_length=1, max_length=80)
    team_b: str = Field(min_length=1, max_length=80)


class CreateMatchRequest(BaseModel):
    scenario_id: Literal["sustained_pressure", "rhythm_shift", "player_influence"] = "sustained_pressure"
    seed: int = Field(default=42001)
    team_display_names: TeamNames | None = None


class NarrativeRequest(BaseModel):
    audience_mode: Literal["fan", "analyst", "player"] = "fan"
    language: SUPPORTED_LANGUAGES = "en-GB"
    insight_id: str | None = Field(default=None, min_length=1, max_length=256, pattern=r"^\S+$")
    selected_player_id: str | None = Field(default=None, min_length=1, max_length=256, pattern=r"^\S+$")
    depth: Literal["broadcast", "deep"] = "deep"
    at_ms: int | None = Field(default=None, ge=0, le=600000, strict=True)
    allow_cloud: bool = True
    overview: bool = False


class RecapRequest(BaseModel):
    audience_mode: Literal["fan", "analyst", "player"] = "fan"
    language: SUPPORTED_LANGUAGES = "en-GB"
    selected_player_id: str | None = Field(default=None, min_length=1, max_length=256, pattern=r"^\S+$")


class SpeechRequest(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    language: SUPPORTED_LANGUAGES = "en-GB"
    voice: str | None = Field(default=None, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    rate: str = Field(default="+2%", max_length=12)
    pitch: str = Field(default="+1st", max_length=12)


class DualSpeechRequest(BaseModel):
    primary_text: str | None = Field(default=None, min_length=1, max_length=4000)
    secondary_text: str | None = Field(default=None, min_length=1, max_length=4000)
    turns: list[CommentaryTurn] | None = Field(default=None, min_length=2, max_length=6)
    language: SUPPORTED_LANGUAGES = "en-GB"
    primary_voice: str | None = Field(default=None, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    secondary_voice: str | None = Field(default=None, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")

    @model_validator(mode="after")
    def complete_commentary(self):
        if self.turns is not None:
            if self.primary_text is not None or self.secondary_text is not None:
                raise ValueError("Supply ordered turns or the legacy text pair, not both")
            GeneratedDialogue(turns=self.turns)
        elif not self.primary_text or not self.secondary_text:
            raise ValueError("Both legacy commentary texts are required")
        return self


class DualCommentaryRequest(BaseModel):
    language: SUPPORTED_LANGUAGES = "en-GB"
    insight_id: str | None = Field(default=None, min_length=1, max_length=256, pattern=r"^\S+$")
    depth: Literal["broadcast", "deep"] = "deep"
    at_ms: int | None = Field(default=None, ge=0, le=600000, strict=True)
    overview: bool = False


class ViewerProfileRequest(BaseModel):
    favorite_team_id: str | None = Field(default=None, min_length=1, max_length=256, pattern=r"^\S+$")
    favorite_player_id: str | None = Field(default=None, min_length=1, max_length=256, pattern=r"^\S+$")
    focus_metric: str | None = Field(default=None, min_length=1, max_length=100, pattern=r"^\S+$")


class VideoLinkRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    analysis_mode: Literal["events", "cv"] = "cv"
    rights_confirmed: bool = False
    options: CvOptions = Field(default_factory=CvOptions)
    team_display_names: TeamNames | None = None


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
    return download_video(url, max_bytes, MAX_VIDEO_REDIRECTS)


@app.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
def ready(request: Request) -> dict[str, str]:
    if not hasattr(request.app.state, "store"):
        raise HTTPException(503, "Runtime is not initialized")
    request.app.state.store.healthcheck()
    return {"status": "ready"}


@app.get("/api/v1/capabilities")
def capabilities(request: Request) -> dict:
    return {
        "auth_enabled": request.app.state.auth.enabled,
        "cv_detector": os.getenv("TACTIQ_CV_DETECTOR", "onnx"),
        "cv_model_configured": bool(os.getenv("TACTIQ_CV_MODEL_PATH", "").strip())
            and Path(os.getenv("TACTIQ_CV_MODEL_PATH", "")).is_file(),
        "value_model_status": "validated" if service.value_model and service.value_model.validated
            else "reference_unvalidated" if service.value_model else "unavailable",
        "languages": ["en-GB", "sw-KE", "fr-FR", "es-ES", "pt-BR", "ar-SA", "hi-IN"],
        "youtube_import_configured": youtube_available(),
        "speech_configured": service.speech_synthesizer.is_configured(),
        "cloud_model_enabled": os.getenv("FOUNDRY_ENABLED", "false").lower() == "true",
        "speech": service.speech_synthesizer.capabilities(),
        "narrative": service.narrative_generator.status(),
    }


@app.get("/api/v1/session")
def current_session(request: Request) -> dict:
    return {"auth_enabled": request.app.state.auth.enabled, **request.state.principal.model_dump()}


@app.post("/api/v1/session")
def establish_session(request: Request, response: Response) -> dict:
    header = request.headers.get("Authorization", "")
    parts = header.split()
    if len(parts) != 2 or parts[0].casefold() != "bearer" or not parts[1].isascii() or len(parts[1]) > 8192:
        raise HTTPException(401, "Provide a bearer token to establish a browser session")
    settings = request.app.state.auth
    principal = settings.authenticate(header)
    response.set_cookie(value=parts[1], **settings.cookie_options())
    return principal.model_dump()


@app.post("/api/v1/session/logout")
def logout(request: Request, response: Response) -> dict:
    response.delete_cookie(request.app.state.auth.cookie_name, path="/")
    return {"status": "signed_out"}


@app.get("/api/v1/operations/metrics")
def operational_metrics(request: Request) -> dict:
    return {"http": request.app.state.metrics.snapshot(), "pipeline": service.processing_metrics.snapshot(),
            "jobs": request.app.state.job_metrics.snapshot()}


@app.get("/api/v1/operations/status")
def operational_status(request: Request) -> dict:
    return slo_status(request.app.state.metrics.snapshot(), service.processing_metrics.snapshot(),
                      job_metrics=request.app.state.job_metrics.snapshot())


@app.get("/api/v1/operations/prometheus", response_class=PlainTextResponse)
def prometheus(request: Request) -> str:
    return prometheus_metrics(request.app.state.metrics.snapshot(), service.processing_metrics.snapshot())


@app.get("/api/v1/matches")
def list_matches(request: Request, limit: int = Query(default=50, ge=1, le=100)) -> list[dict]:
    return request.app.state.store.list_matches(request.state.principal.tenant_id, limit)


@app.post("/api/v1/matches")
def create_match(payload: CreateMatchRequest, request: Request) -> dict:
    try:
        state = service.create_match(scenario_id=payload.scenario_id, seed=payload.seed,
                                     tenant_id=request.state.principal.tenant_id,
                                     team_display_names=payload.team_display_names.model_dump() if payload.team_display_names else None)
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
    try:
        await service.start_match(match_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"status": "started"}


@app.post("/api/v1/matches/{match_id}/pause")
async def pause_match(match_id: str) -> dict:
    await service.pause_match(match_id)
    return {"status": service.matches[match_id].status.value}


@app.get("/api/v1/matches/{match_id}/replay")
def get_replay(match_id: str, after_sequence: int = Query(default=0, ge=0)) -> JSONResponse:
    try:
        return JSONResponse(service.get_replay(match_id, after_sequence))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.delete("/api/v1/matches/{match_id}")
def remove_match(match_id: str) -> dict:
    service.delete_match(match_id)
    return {"status": "deleted"}


@app.get("/api/v1/matches/{match_id}/media/source")
def get_source_video(match_id: str) -> FileResponse:
    source = service.match_video_source_path.get(match_id)
    if not source:
        raise HTTPException(404, "Source video is unavailable")
    path = Path(source).resolve()
    match_root = service.source_root / match_id
    if (match_root.is_symlink() or match_root.resolve().parent != service.source_root.resolve()
            or Path(source).is_symlink() or not path.is_relative_to(match_root.resolve()) or not path.is_file()):
        raise HTTPException(404, "Source video is unavailable")
    return FileResponse(path, media_type="video/mp4", content_disposition_type="inline")


@app.get("/media/{match_id}/{filename}")
def get_clip(match_id: str, filename: str) -> FileResponse:
    if not re.fullmatch(r"[a-f0-9-]+\.mp4", filename):
        raise HTTPException(404, "Clip not found")
    insight_id = filename[:-4]
    clip = service.insight_clip_map.get(match_id, {}).get(insight_id)
    match_root = service.clips_root / match_id
    raw_path = match_root / filename
    path = raw_path.resolve()
    if (not clip or clip.get("status") != "ready" or match_root.is_symlink() or raw_path.is_symlink()
            or match_root.resolve().parent != service.clips_root.resolve()
            or not path.is_relative_to(match_root.resolve()) or not path.is_file()):
        raise HTTPException(404, "Clip not found")
    return FileResponse(path, media_type="video/mp4", content_disposition_type="inline")


@app.get("/api/v1/matches/{match_id}/state")
def get_state(match_id: str) -> dict:
    if match_id not in service.matches:
        raise HTTPException(status_code=404, detail="Match not found")
    return service.get_state_payload(match_id)


@app.get("/api/v1/matches/{match_id}/events")
def get_events(match_id: str) -> JSONResponse:
    if match_id not in service.matches:
        raise HTTPException(status_code=404, detail="Match not found")
    return JSONResponse([e.model_dump() for e in service.get_events(match_id)])


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
    request: Request,
    file: UploadFile = File(...),
    source_name: str | None = Form(default=None),
    analysis_mode: Literal["events", "cv"] = Form(default="cv"),
) -> dict:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Video filename is required")

    content = await _read_upload_bounded(file, MAX_VIDEO_UPLOAD_BYTES)
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded video is empty")

    effective_source = source_name or file.filename
    _check_analysis_configuration(analysis_mode)
    try:
        if analysis_mode == "cv":
            return await run_in_threadpool(
                service.create_match_from_cv, video_bytes=content, source_name=effective_source,
                tenant_id=request.state.principal.tenant_id,
            )
        return await run_in_threadpool(
            service.create_match_from_video,
            video_bytes=content,
            source_name=effective_source,
            use_auto_eventing=True,
            tenant_id=request.state.principal.tenant_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/v1/video/from-link")
def create_from_video_link(payload: VideoLinkRequest, request: Request) -> dict:
    if not payload.url.strip():
        raise HTTPException(status_code=400, detail="Video URL is required")

    source = payload.url.strip()
    try:
        selected = parse_video_source(source)
        if selected.provider == "youtube" and not payload.rights_confirmed:
            raise ValueError("Confirm that you own or have permission to analyze this YouTube video.")
        _check_analysis_configuration(payload.analysis_mode)
        if selected.provider == "youtube":
            if selected.video_id is None:
                raise ValueError("YouTube video identifier is missing")
            content = download_youtube(selected.video_id, MAX_VIDEO_UPLOAD_BYTES)
        else:
            content = _download_video_link_bounded(source, MAX_VIDEO_UPLOAD_BYTES)
        source_name = f"youtube_{selected.video_id}.mp4" if selected.provider == "youtube" else os.path.basename(urlparse(source).path) or "linked_video.mp4"
        arguments = {
            "video_bytes": content, "source_name": source_name,
            "tenant_id": request.state.principal.tenant_id,
            "team_display_names": payload.team_display_names.model_dump() if payload.team_display_names else None,
            "source_provider": selected.provider, "source_video_id": selected.video_id,
        }
        if payload.analysis_mode == "cv":
            return service.create_match_from_cv(**arguments, options=payload.options)
        return service.create_match_from_video(**arguments, use_auto_eventing=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/v1/cv/ingest/upload")
async def upload_video_for_cv_ingest(
    file: UploadFile = File(...),
    source_name: str | None = Form(default=None),
    max_tracks: int = Query(default=1200, ge=100, le=10000),
    options_json: str | None = Form(default=None),
) -> dict:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Video filename is required")

    content = await _read_upload_bounded(file, MAX_VIDEO_UPLOAD_BYTES)
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded video is empty")

    effective_source = source_name or file.filename
    try:
        options = CvOptions.model_validate_json(options_json) if options_json else CvOptions()
        result = await run_in_threadpool(
            extract_cv_tracks_from_video_bytes,
            content,
            effective_source,
            options,
        )
    except CvUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    payload = result.model_dump()
    total_tracks = len(payload["tracks"])
    payload["tracks"] = payload["tracks"][:max_tracks]
    payload["tracks_truncated"] = total_tracks > max_tracks
    payload["track_count_total"] = total_tracks
    payload["track_count_returned"] = len(payload["tracks"])
    return payload


def _public_job(job) -> dict:
    return job.model_dump(mode="json", exclude={"payload", "tenant_id"})


def _check_analysis_configuration(mode: str) -> None:
    if mode != "cv":
        return
    backend = os.getenv("TACTIQ_CV_DETECTOR", "onnx").strip().lower()
    if backend == "motion" and os.getenv("APP_ENV", "local").lower() != "production":
        return
    if backend != "onnx":
        raise HTTPException(503, "Configure an ONNX detector for model-based tracking")
    path = os.getenv("TACTIQ_CV_MODEL_PATH", "").strip()
    if not path or not Path(path).is_file():
        raise HTTPException(503, "Configure a readable TACTIQ_CV_MODEL_PATH before submitting tracking jobs")


@app.post("/api/v1/video/jobs", status_code=202)
async def submit_video_job(
    request: Request, file: UploadFile = File(...),
    analysis_mode: Literal["events", "cv"] = Form(default="cv"),
    options_json: str | None = Form(default=None),
    team_a_name: str | None = Form(default=None, max_length=80),
    team_b_name: str | None = Form(default=None, max_length=80),
) -> dict:
    if not file.filename:
        raise HTTPException(400, "Video filename is required")
    try:
        options = CvOptions.model_validate_json(options_json) if options_json else CvOptions()
    except ValidationError as exc:
        raise HTTPException(400, "Invalid CV options") from exc
    content = await _read_upload_bounded(file, MAX_VIDEO_UPLOAD_BYTES)
    if not content:
        raise HTTPException(400, "Uploaded video is empty")
    _check_analysis_configuration(analysis_mode)
    operation = uuid4().hex
    if shutil.disk_usage(service.media_root).free < MAX_VIDEO_UPLOAD_BYTES * 4:
        raise HTTPException(507, "Insufficient free space for bounded video processing")
    path = service.media_root / "pending" / f"{operation}.bin"
    await run_in_threadpool(path.write_bytes, content)
    names = {
        "team_a": (team_a_name or "").strip() or service.team_display_names["team_a"],
        "team_b": (team_b_name or "").strip() or service.team_display_names["team_b"],
    }
    try:
        job = request.app.state.jobs.submit(request.state.principal.tenant_id, "video", {
            "tenant_id": request.state.principal.tenant_id, "operation_id": operation,
            "input_path": str(path), "analysis_mode": analysis_mode,
            "source_name": service._safe_filename(file.filename), "options": options.model_dump(),
            "team_display_names": names,
        })
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return _public_job(job)


@app.post("/api/v1/video/jobs/from-link", status_code=202)
def submit_video_link_job(payload: VideoLinkRequest, request: Request) -> dict:
    try:
        source = parse_video_source(payload.url)
        if source.provider == "youtube" and not payload.rights_confirmed:
            raise ValueError("Confirm that you own or have permission to analyze this YouTube video.")
        _check_analysis_configuration(payload.analysis_mode)
        if source.provider == "youtube" and not youtube_available():
            raise RuntimeError("YouTube import requires the locked resolver/EJS packages and Node 22 or newer.")
        content = None if source.provider == "youtube" else _download_video_link_bounded(payload.url, MAX_VIDEO_UPLOAD_BYTES)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    if shutil.disk_usage(service.media_root).free < MAX_VIDEO_UPLOAD_BYTES * 4:
        raise HTTPException(507, "Insufficient free space for bounded video processing")
    operation = uuid4().hex
    path = service.media_root / "pending" / f"{operation}.bin"
    try:
        if content is not None:
            path.write_bytes(content)
        return _public_job(request.app.state.jobs.submit(request.state.principal.tenant_id, "video", {
            "tenant_id": request.state.principal.tenant_id, "operation_id": operation,
            "input_path": str(path), "analysis_mode": payload.analysis_mode,
            "source_name": f"youtube_{source.video_id}.mp4" if source.provider == "youtube"
                else service._safe_filename(os.path.basename(urlparse(payload.url).path)),
            "source_provider": source.provider,
            **({"youtube_video_id": source.video_id} if source.provider == "youtube" else {}),
            "options": payload.options.model_dump(),
            "team_display_names": payload.team_display_names.model_dump() if payload.team_display_names else service.team_display_names,
        }))
    except Exception:
        path.unlink(missing_ok=True)
        raise


@app.get("/api/v1/jobs/{job_id}")
def get_job(job_id: str, request: Request) -> dict:
    job = request.app.state.jobs.get(request.state.principal.tenant_id, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    _cleanup_terminal_video_input(job)
    return _public_job(job)


@app.post("/api/v1/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: Request) -> dict:
    job = request.app.state.jobs.cancel(request.state.principal.tenant_id, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    _cleanup_terminal_video_input(job)
    return _public_job(job)


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
            depth=payload.depth, at_ms=payload.at_ms, allow_cloud=payload.allow_cloud, overview=payload.overview,
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
        "grounding": result.grounding,
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
        "grounding": recap.grounding,
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
def synthesize_speech(payload: SpeechRequest, request: Request) -> dict:
    try:
        result = service.synthesize_speech(
            text=payload.text,
            language=payload.language,
            voice=payload.voice,
            rate=payload.rate,
            pitch=payload.pitch,
            tenant_id=request.state.principal.tenant_id,
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
def synthesize_dual_speech(payload: DualSpeechRequest, request: Request) -> dict:
    try:
        if payload.turns is not None:
            result = service.synthesize_commentary_turns(
                payload.turns, payload.language, payload.primary_voice, payload.secondary_voice,
                tenant_id=request.state.principal.tenant_id,
            )
        else:
            assert payload.primary_text is not None and payload.secondary_text is not None
            result = service.synthesize_dual_commentary(
                primary_text=payload.primary_text, secondary_text=payload.secondary_text,
                language=payload.language, primary_voice=payload.primary_voice,
                secondary_voice=payload.secondary_voice, tenant_id=request.state.principal.tenant_id,
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
            depth=payload.depth, at_ms=payload.at_ms, overview=payload.overview,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "primary_text": result.primary_text,
        "secondary_text": result.secondary_text,
        "language": result.language,
        "fallback_used": result.fallback_used,
        "provider": result.provider,
        "turns": [turn.model_dump() for turn in result.turns],
        "depth": result.depth,
        "provider_failure": (result.audit or {}).get("provider_failure"),
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
    queue = None
    pinned = False
    tasks: list[asyncio.Task] = []
    try:
        settings = websocket.app.state.auth
        principal = settings.authenticate(websocket.headers.get("Authorization"), websocket.cookies.get(settings.cookie_name))
        origin = websocket.headers.get("Origin")
        allowed = set(_cors_allow_origins()) | {
            f"http://{websocket.url.netloc}", f"https://{websocket.url.netloc}"
        }
        if origin and origin not in allowed:
            raise HTTPException(403, "WebSocket origin is not allowed")
        if settings.enabled and not websocket.headers.get("Authorization") and not origin:
            raise HTTPException(403, "Browser sessions require a verified WebSocket origin")
        websocket.app.state.limiter.check(f"{principal.tenant_id}:{principal.subject}")
        if not await run_in_threadpool(service.pin_match, principal.tenant_id, match_id):
            raise HTTPException(404, "Match not found")
        pinned = True
        queue = await service.subscribe(match_id)
        await websocket.accept()
        await websocket.send_json({"type": "match.snapshot", "payload": await asyncio.to_thread(service.get_state_payload, match_id)})
        await websocket.send_json({"type": "insights.snapshot", "payload": service.get_serialized_insights(match_id)})

        async def send_updates() -> None:
            assert queue is not None
            while True:
                await websocket.send_json(await queue.get())

        async def observe_disconnect() -> None:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    return

        tasks = [asyncio.create_task(send_updates()), asyncio.create_task(observe_disconnect())]
        completed, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in completed:
            task.result()
    except HTTPException as exc:
        await websocket.close(code=4404 if exc.status_code == 404 else 4401 if exc.status_code == 401 else 4403)
    except RuntimeError:
        logger.warning("Match stream could not be established", extra={"match_id": match_id})
        if websocket.application_state != WebSocketState.DISCONNECTED and websocket.client_state != WebSocketState.DISCONNECTED:
            await websocket.close(code=4429)
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        if queue is not None:
            service.unsubscribe(match_id, queue)
        if pinned:
            service.unpin_match(match_id)
        for task in tasks:
            task.cancel()
        if tasks:
            with anyio.CancelScope(shield=True):
                await asyncio.gather(*tasks, return_exceptions=True)
