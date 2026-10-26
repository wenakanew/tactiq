"""Opt-in, cookie-authenticated PostgreSQL/Blob API. Local routes are untouched."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from tactiq.agents.localization import language_copy, localized_insight
from tactiq.application.cloud import (
    CloudAnalysisService, CloudCapacityError, LinkRequest, ProcessingUnavailable, ScenarioRequest, TeamNames, VideoRequest,
)
from tactiq.domain.models import EvidenceItem, Insight
from tactiq.intelligence.geometry import has_pitch_geometry
from tactiq.presentation.api.policy import BodyLimitMiddleware
from tactiq.runtime.access import AccessDenied, BudgetExceeded, ClientIPPolicy, ReservationConflict, SignInRequired
from tactiq.runtime.cloud_composition import CloudDependencies, compose_cloud
from tactiq.runtime.cloud_connections import DatabaseCapacityError
from tactiq.runtime.cloud_generation import CloudGeneration, GenerationUnavailable
from tactiq.runtime.contracts import CloudRecordError, RecordCursor, RevisionConflictError, cloud_identifier
from tactiq.runtime.identity import IdentityUnavailable
from tactiq.runtime.observability import ensure_request_id
from tactiq.simulator.scenarios import list_scenarios
from tactiq.video.providers import youtube_available

logger = logging.getLogger(__name__)
COOKIE = "__Host-tactiq_access"
LANGUAGES = Literal["en-GB", "sw-KE", "fr-FR", "es-ES", "pt-BR", "ar-SA", "hi-IN"]


class ScriptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: LANGUAGES = "en-GB"
    audience_mode: Literal["fan", "analyst", "player"] = "fan"
    insight_id: str | None = Field(default=None, max_length=128)
    selected_player_id: str | None = Field(default=None, max_length=128)
    depth: Literal["broadcast", "deep"] = "deep"
    at_ms: int | None = Field(default=None, strict=True, ge=0, le=600_000)
    allow_cloud: bool = True
    overview: bool = False


class OwnedSpeechRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    match_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    script_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


def _public_state(state: dict) -> dict:
    return {key: value for key, value in state.items() if key not in {"_cloud", "insights"}}


def _insights(match_id: str, state: dict) -> list[dict]:
    result = []
    for insight in state.get("insights", []):
        item = dict(insight)
        clip = dict(item.get("clip", {}))
        if clip.get("status") == "ready":
            clip["url"] = f"/api/v1/matches/{match_id}/media/{clip['media_id']}"
        item["clip"] = clip
        result.append(item)
    return result


def create_cloud_app(dependencies: CloudDependencies | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application):
        deps = dependencies or compose_cloud()
        try:
            deps.store.healthcheck()
            if deps.owned:
                deps.media.healthcheck()
            application.state.cloud = deps
            application.state.generation = deps.generation or CloudGeneration(
                deps.settings.generation, deps.store, deps.access, deps.media,
            )
            application.state.analysis = CloudAnalysisService(
                deps.store, deps.access, deps.media,
                max_pending=deps.settings.max_pending, max_upload_bytes=deps.settings.max_upload_bytes,
                processing_limit_per_day=deps.settings.processing_limit_per_day,
            )
            yield
        finally:
            deps.close()

    app = FastAPI(title="Tactiq cloud API", version="0.3.0", lifespan=lifespan)
    # Body limits apply before multipart parsing; no public unrestricted upload.
    limit = dependencies.settings.max_upload_bytes if dependencies else int(
        os.environ.get("VIDEO_UPLOAD_MAX_BYTES", str(200 * 1024 * 1024))
    )
    app.add_middleware(BodyLimitMiddleware, upload_limit=limit)
    origins = list(dict.fromkeys(
        (dependencies.settings.public_origin, *dependencies.settings.allowed_origins)
        if dependencies else (
            os.environ.get("TACTIQ_PUBLIC_ORIGIN", ""),
            *os.environ.get("TACTIQ_CORS_ALLOW_ORIGINS", "").split(","),
        )
    ))

    @app.middleware("http")
    async def policy(request: Request, call_next):
        request_id = ensure_request_id(request.headers.get("X-Request-ID"))
        try:
            deps = getattr(request.app.state, "cloud", None)
            if request.url.path != "/health/live" and deps is None:
                raise HTTPException(503, "Cloud runtime is unavailable")
            if deps and request.method not in {"GET", "HEAD", "OPTIONS"}:
                if request.url.path != "/api/v1/auth/microsoft/callback":
                    allowed = {deps.settings.public_origin, *deps.settings.allowed_origins}
                    if request.headers.get("Origin") not in allowed:
                        raise HTTPException(403, "A verified browser Origin is required")
            response = await call_next(request)
        except SignInRequired as exc:
            response = JSONResponse({"detail": {
                "code": "sign_in_required", "message": str(exc), "reset_at": exc.resets_at,
            }}, status_code=403)
        except BudgetExceeded:
            response = JSONResponse({"detail": {
                "code": "generation_allocation_exhausted",
                "message": "The approved AI allocation is unavailable or exhausted; deterministic explanations remain available",
            }}, status_code=429, headers={"Retry-After": "60"})
        except AccessDenied:
            response = JSONResponse({"detail": "Owned session or analysis is unavailable"}, status_code=401)
        except (ReservationConflict, RevisionConflictError):
            response = JSONResponse({"detail": "Conflicting operation or revision"}, status_code=409)
        except CloudCapacityError as exc:
            response = JSONResponse({"detail": {
                "code": exc.code, "message": "Analysis capacity is currently full", "trial_use_charged": False,
            }}, status_code=429, headers={"Retry-After": str(exc.retry_after)})
        except ProcessingUnavailable:
            response = JSONResponse({"detail": {
                "code": "processing_allocation_unavailable",
                "message": "An approved global native-processing allocation is required", "trial_use_charged": False,
            }}, status_code=503, headers={"Retry-After": "60"})
        except DatabaseCapacityError:
            response = JSONResponse({"detail": {
                "code": "database_capacity", "message": "Database request capacity is full; retry the same operation",
            }}, status_code=503, headers={"Retry-After": "1"})
        except (ValueError, CloudRecordError):
            response = JSONResponse({"detail": "Invalid request"}, status_code=400)
        except IdentityUnavailable:
            response = JSONResponse({"detail": "Microsoft sign-in is unavailable"}, status_code=503)
        except GenerationUnavailable as exc:
            response = JSONResponse({"detail": str(exc)}, status_code=503, headers={"Retry-After": "5"})
        except HTTPException as exc:
            response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
        except Exception:
            # Exceptions from storage/provider SDKs can contain private locations.
            logger.error("cloud_request_failed", extra={"request_id": request_id})
            response = JSONResponse({"detail": "Cloud operation is unavailable; no local fallback was used"}, status_code=503)
        response.headers.update({
            "X-Request-ID": request_id, "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer",
        })
        if request.method == "POST" and request.url.path == "/api/v1/auth/microsoft/callback":
            response.delete_cookie("__Host-tactiq_oidc", path="/", secure=True, httponly=True, samesite="none")
        return response

    app.add_middleware(
        CORSMiddleware, allow_origins=[origin for origin in origins if origin], allow_credentials=True,
        allow_methods=["GET", "HEAD", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Idempotency-Key", "Range", "If-Range", "X-Request-ID"],
        expose_headers=["X-Request-ID", "Content-Range", "Accept-Ranges", "ETag", "Retry-After"],
    )

    def deps(request: Request) -> CloudDependencies:
        return request.app.state.cloud

    def token(request: Request) -> str:
        value = request.cookies.get(COOKIE)
        if not value:
            raise HTTPException(401, "Establish a guest or Microsoft session")
        return value

    def verified_ip(request: Request) -> str:
        if request.client is None:
            raise HTTPException(503, "Verified ingress address is unavailable")
        return ClientIPPolicy(deps(request).settings.trusted_proxy_cidrs).resolve(
            request.client.host, request.headers.get("X-Forwarded-For"),
        )

    def session_payload(request: Request, proof: str) -> dict:
        principal = deps(request).access.authenticate(proof)
        trial = deps(request).access.trial_allowance(proof, verified_ip(request))
        return {
            "kind": "guest" if principal.guest else "member",
            "principal": {"tenant_id": principal.tenant_id, "subject": principal.subject, "role": principal.role},
            "trial": {"remaining": trial.remaining, "limit": 3, "reset_at": trial.resets_at},
            "expires_at": principal.expires_at,
        }

    def owned(request: Request, match_id: str):
        cloud_identifier(match_id, "match_id")
        # Promotion may grant the member access to a guest workspace, so the
        # owner returned here, not the member's default tenant, scopes every read.
        deps(request).access.require_analysis(token(request), match_id)
        tenants = deps(request).access.authorized_tenants(token(request))
        aggregate = deps(request).store.load_owned(tenants, match_id)
        if aggregate is None:
            raise HTTPException(404, "Analysis is unavailable")
        return aggregate

    def operation(request: Request) -> str:
        value = request.headers.get("Idempotency-Key", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
            raise HTTPException(400, "A stable Idempotency-Key is required")
        return value

    def admitted(reservation, request: Request) -> dict:
        job = deps(request).store.job(reservation.tenant_id, reservation.analysis_id)
        return {
            "match_id": reservation.analysis_id, "job_id": reservation.analysis_id,
            "status": job["status"], "remaining_trial_uses": reservation.remaining,
            "reset_at": reservation.resets_at, "replayed": reservation.replayed,
        }

    @app.get("/health/live")
    def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready(request: Request):
        deps(request).store.healthcheck()
        return {"status": "ready", "runtime_mode": "cloud"}

    @app.get("/api/v1/capabilities")
    def capabilities(request: Request):
        return {
            "runtime_mode": "cloud", "auth_enabled": True, "paginated_streams": True,
            "public_trial": {"enabled": True, "microsoft_login_enabled": deps(request).sign_in is not None,
                             "owned_speech_required": True},
            **request.app.state.generation.capabilities(),
            "analysis_admission_enabled": deps(request).settings.processing_limit_per_day is not None,
            "deterministic_narration_enabled": True, "deterministic_recap_enabled": True,
            "languages": list(LANGUAGES.__args__),
            "youtube_import_configured": youtube_available(),
            "cv_detector": os.getenv("TACTIQ_CV_DETECTOR", "onnx"),
            "cv_model_configured": bool(os.getenv("TACTIQ_CV_MODEL_PATH"))
                and Path(os.environ["TACTIQ_CV_MODEL_PATH"]).is_file(),
        }

    @app.get("/api/v1/session")
    def current_session(request: Request):
        return session_payload(request, token(request))

    @app.post("/api/v1/session")
    @app.post("/api/v1/session/guest")
    def guest_session(request: Request, response: Response):
        proof = request.cookies.get(COOKIE)
        if proof:
            try:
                return session_payload(request, proof)
            except AccessDenied:
                pass
        issued = deps(request).access.issue_guest()
        response.set_cookie(value=issued.token, **issued.cookie_options())
        return session_payload(request, issued.token)

    @app.post("/api/v1/session/logout")
    def logout(request: Request, response: Response):
        deps(request).access.revoke_session(token(request))
        response.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="lax")
        return {"signed_out": True}

    @app.get("/api/v1/auth/microsoft/login")
    def login(request: Request, return_to: str = "/workspace"):
        if return_to != "/workspace":
            raise HTTPException(400, "Only the fixed workspace return route is supported")
        identity = deps(request).sign_in
        if identity is None:
            raise HTTPException(503, "Microsoft sign-in is not configured")
        result = identity.begin(request.cookies.get(COOKIE))
        response = RedirectResponse(result.authorization_uri, status_code=303)
        response.set_cookie(value=result.browser_binding, **result.cookie_options())
        return response

    @app.post("/api/v1/auth/microsoft/callback")
    async def callback(request: Request):
        configured = deps(request).settings.microsoft_redirect_uri
        if deps(request).sign_in is None or configured is None:
            raise HTTPException(503, "Microsoft sign-in is not configured")
        # The registered HTTPS redirect is bound into the saved provider flow.
        # Internal proxy hops change scheme/host; never trust them to rebuild it.
        if request.url.path != "/api/v1/auth/microsoft/callback" or request.url.query:
            raise HTTPException(400, "Callback must use the fixed registered route without a query")
        if not request.headers.get("Content-Type", "").startswith("application/x-www-form-urlencoded"):
            raise HTTPException(400, "Microsoft callback must use form_post")
        form = await request.form(max_fields=12, max_files=0)
        if len(form.multi_items()) != len(form):
            raise HTTPException(400, "Duplicate identity response fields")
        issued = await run_in_threadpool(
            deps(request).sign_in.complete, request.cookies.get("__Host-tactiq_oidc", ""), dict(form),
        )
        response = RedirectResponse(deps(request).settings.public_origin + "/workspace", status_code=303)
        response.set_cookie(value=issued.token, **issued.cookie_options())
        response.delete_cookie("__Host-tactiq_oidc", path="/", secure=True, httponly=True, samesite="none")
        return response

    @app.get("/api/v1/scenarios")
    def scenarios():
        return [asdict(item) for item in list_scenarios()]

    @app.post("/api/v1/matches", status_code=202)
    def scenario(payload: ScenarioRequest, request: Request):
        reservation = request.app.state.analysis.admit(
            token(request), verified_ip(request), operation(request), "scenario", payload.model_dump(mode="json"),
        )
        return admitted(reservation, request)

    @app.get("/api/v1/matches")
    def history(request: Request, after: str = "", limit: int = Query(default=30, ge=1, le=100)):
        tenants = deps(request).access.authorized_tenants(token(request))
        rows = deps(request).store.list_matches(tenants, after=after, limit=limit)
        return {"matches": [
            {**row, "state": _public_state(row["state"])} for row in rows
        ], "next_after": rows[-1]["match_id"] if len(rows) == limit else None}

    @app.get("/api/v1/matches/{match_id}/state")
    def state(match_id: str, request: Request):
        aggregate = owned(request, match_id)
        return {**_public_state(aggregate.state), "revision": aggregate.revision}

    @app.get("/api/v1/matches/{match_id}/insights")
    def insights(match_id: str, request: Request):
        return {"insights": _insights(match_id, owned(request, match_id).state)}

    def stream_page(match_id: str, stream: str, request: Request, revision: int | None, after: int, limit: int):
        aggregate = owned(request, match_id)
        cursor = RecordCursor(aggregate.tenant_id, match_id, stream, revision or aggregate.revision, after)
        page = deps(request).store.page(aggregate.tenant_id, match_id, stream, cursor=cursor, limit=limit)
        return aggregate, {
            "records": [asdict(item) for item in page.records],
            "cursor": {"revision": page.cursor.revision, "after": page.cursor.after}, "has_more": page.has_more,
        }

    @app.get("/api/v1/matches/{match_id}/events")
    def events(
        match_id: str, request: Request, revision: int | None = Query(default=None, ge=1),
        after: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=100),
    ):
        return stream_page(match_id, "events", request, revision, after, limit)[1]

    @app.get("/api/v1/matches/{match_id}/replay")
    def replay(
        match_id: str, request: Request, revision: int | None = Query(default=None, ge=1),
        after: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=100),
    ):
        aggregate, page = stream_page(match_id, "replay", request, revision, after, limit)
        current = aggregate.state
        return {
            **page, "schema_version": "1.0.0", "match_id": match_id,
            "duration_ms": current["_cloud"].get("duration_ms", current["match_clock_ms"]),
            "processed_until_ms": current["match_clock_ms"],
            "source_url": f"/api/v1/matches/{match_id}/media/source" if current["_cloud"].get("source_media_id") else None,
            "team_display_names": current["team_display_names"], "attack_directions": current.get("attack_directions", {}),
            "metadata": current.get("analysis_metadata", {}), "insights": _insights(match_id, current),
        }

    @app.post("/api/v1/video/jobs", status_code=202)
    async def upload(
        request: Request, file: UploadFile = File(...), analysis_mode: str = Form("cv"),
        rights_confirmed: bool = Form(False), options: str = Form("{}"),
        team_display_names: str | None = Form(None),
    ):
        proof, ip, key = token(request), verified_ip(request), operation(request)
        try:
            payload = VideoRequest(
                analysis_mode=analysis_mode, rights_confirmed=rights_confirmed,
                options=json.loads(options),
                team_display_names=TeamNames.model_validate_json(team_display_names) if team_display_names else None,
            )
        except (ValueError, ValidationError):
            raise HTTPException(400, "Invalid video analysis options") from None
        collected = bytearray()
        while chunk := await file.read(1024 * 1024):
            collected.extend(chunk)
            if len(collected) > deps(request).settings.max_upload_bytes:
                raise HTTPException(413, "Video exceeds the configured limit")
        reservation = await run_in_threadpool(
            request.app.state.analysis.admit_upload, proof, ip, key, bytes(collected), payload,
        )
        return admitted(reservation, request)

    @app.post("/api/v1/video/jobs/from-link", status_code=202)
    def link(payload: LinkRequest, request: Request):
        reservation = request.app.state.analysis.admit_link(
            token(request), verified_ip(request), operation(request), payload,
        )
        return admitted(reservation, request)

    @app.get("/api/v1/jobs/{job_id}")
    def job(job_id: str, request: Request):
        aggregate = owned(request, job_id)
        result = deps(request).store.job(aggregate.tenant_id, job_id)
        if result is None:
            raise HTTPException(404, "Analysis job is unavailable")
        return result

    @app.post("/api/v1/jobs/{job_id}/cancel")
    def cancel(job_id: str, request: Request):
        aggregate = owned(request, job_id)
        return {"cancelled": deps(request).store.request_cancel(aggregate.tenant_id, job_id)}

    @app.post("/api/v1/matches/{match_id}/pause")
    def pause(match_id: str, request: Request):
        aggregate = owned(request, match_id)
        return {"paused": deps(request).store.pause(aggregate.tenant_id, match_id)}

    @app.post("/api/v1/matches/{match_id}/start")
    def resume(match_id: str, request: Request):
        aggregate = owned(request, match_id)
        deps(request).store.resume(aggregate.tenant_id, match_id)
        return deps(request).store.job(aggregate.tenant_id, match_id)

    @app.api_route("/api/v1/matches/{match_id}/media/{media_id}", methods=["GET", "HEAD"])
    def media(match_id: str, media_id: str, request: Request):
        aggregate = owned(request, match_id)
        if media_id == "source":
            media_id = aggregate.state["_cloud"].get("source_media_id", "")
        if not media_id:
            raise HTTPException(404, "Media is unavailable")
        reference = deps(request).store.load_media(aggregate.tenant_id, match_id, media_id)
        if reference is None:
            raise HTTPException(404, "Media is unavailable")
        start, end, status = 0, reference.size - 1, 200
        requested = request.headers.get("Range")
        if requested and request.headers.get("If-Range", reference.etag) == reference.etag:
            parsed = re.fullmatch(r"bytes=(\d*)-(\d*)", requested)
            if parsed is None or not any(parsed.groups()):
                raise HTTPException(416, "Only one byte range is supported", headers={"Content-Range": f"bytes */{reference.size}"})
            left, right = parsed.groups()
            if left:
                start, end = int(left), min(int(right), end) if right else end
            else:
                start = max(0, reference.size - int(right))
            if start > end or start >= reference.size or (not left and int(right) == 0):
                raise HTTPException(416, "Range is outside media", headers={"Content-Range": f"bytes */{reference.size}"})
            status = 206
        headers = {"Content-Length": str(end - start + 1), "Accept-Ranges": "bytes", "ETag": reference.etag}
        if status == 206:
            headers["Content-Range"] = f"bytes {start}-{end}/{reference.size}"
        if request.method == "HEAD":
            return Response(status_code=status, media_type=reference.content_type, headers=headers)
        chunks = iter(deps(request).media.download(
            reference, tenant_id=aggregate.tenant_id, match_id=match_id, offset=start, length=end - start + 1,
        ))
        # Fetch before headers so provider/authentication failures become 503, not
        # a successful empty media response. Remaining chunks stay streaming.
        first = next(chunks)

        def content():
            yield first
            yield from chunks

        return StreamingResponse(content(), status_code=status, media_type=reference.content_type, headers=headers)

    def target(request: Request, match_id: str, payload: ScriptRequest) -> Insight:
        aggregate = owned(request, match_id)
        duration = aggregate.state["_cloud"].get("duration_ms", aggregate.state["match_clock_ms"])
        if payload.at_ms is not None and payload.at_ms > duration + 1:
            raise HTTPException(400, "Commentary time lies outside the available replay")
        if payload.overview and payload.insight_id:
            raise HTTPException(400, "Select a supported insight or an observation overview, not both")
        candidates = aggregate.state.get("insights", [])
        if payload.insight_id:
            candidates = [item for item in candidates if item["insight_id"] == payload.insight_id]
        if payload.at_ms is not None:
            candidates = [item for item in candidates if item["window_end_ms"] <= payload.at_ms]
        if candidates and not payload.overview:
            selected = candidates[-1]
            if not selected.get("provenance", {}).get("quality", {}).get("eligible", False):
                raise HTTPException(409, "This insight does not meet the supported-evidence guardrail")
            insight = Insight.model_validate(selected)
        else:
            if payload.insight_id:
                raise HTTPException(404, "Owned insight is unavailable")
            current = aggregate.state
            selected = deps(request).store.replay_at(
                aggregate.tenant_id, match_id,
                current["match_clock_ms"] if payload.at_ms is None else min(payload.at_ms, current["match_clock_ms"]),
            )
            if selected is None or not selected[0].get("tracking"):
                raise HTTPException(409, "No committed tracking observations are available at this playback time")
            frame, event_id = selected
            tracking = frame["tracking"]
            ball = tracking.get("ball")
            observed = int(bool(ball and not ball.get("inferred")))
            predicted = int(bool(ball and ball.get("inferred")))
            players = [player for player in tracking.get("players", []) if not player.get("inferred")]
            facts = {
                "sampled_frames": 1, "observed_ball_frames": observed, "predicted_ball_frames": predicted,
                "observed_player_samples": len(players), "ball_observation_rate_pct": observed * 100,
                "calibrated_samples": int(bool(tracking.get("calibrated"))),
            }
            insight = Insight(
                insight_id="overview", match_id=match_id, type="tracking_overview", priority="normal",
                confidence=0, team_id="unknown",
                window_start_ms=frame["timestamp_ms"], window_end_ms=frame["timestamp_ms"],
                title="Observation overview", summary="This overview describes one committed replay sample, not a tactical conclusion.",
                evidence=[EvidenceItem(
                    metric=key, value=value, unit="%" if key.endswith("_pct") else "count",
                    supporting_event_ids=[event_id] if event_id else [],
                ) for key, value in facts.items()],
                context={
                    "observation_overview": True,
                    "data_source": current.get("analysis_metadata", {}).get(
                        "detection_source", "synthetic" if current["scenario_id"] not in {"cv_upload", "video_upload"} else "unknown",
                    ),
                    "analysis_scope": "scenario" if current["scenario_id"] not in {"cv_upload", "video_upload"} else "segment",
                    "has_pitch_geometry": has_pitch_geometry(tracking),
                    "known_player_ids": [player["track_id"] for player in players], "event_types": ["tracking_frame"],
                },
            )
        insight.context.update(
            depth=payload.depth, team_display_names=aggregate.state["team_display_names"],
            score_known=bool(aggregate.state.get("score")),
        )
        return insight

    @app.post("/api/v1/matches/{match_id}/narratives")
    def narrative(match_id: str, payload: ScriptRequest, request: Request):
        insight = target(request, match_id, payload)
        aggregate = owned(request, match_id)
        return request.app.state.generation.script(
            token(request), aggregate.tenant_id, match_id, "single", insight,
            payload.language, payload.audience_mode, payload.selected_player_id, allow_cloud=payload.allow_cloud,
        )

    @app.post("/api/v1/matches/{match_id}/commentary/dual")
    def dual(match_id: str, payload: ScriptRequest, request: Request):
        insight = target(request, match_id, payload)
        aggregate = owned(request, match_id)
        return request.app.state.generation.script(
            token(request), aggregate.tenant_id, match_id, "dual", insight,
            payload.language, payload.audience_mode, payload.selected_player_id, allow_cloud=payload.allow_cloud,
        )

    @app.post("/api/v1/matches/{match_id}/recap")
    def recap(match_id: str, payload: ScriptRequest, request: Request):
        aggregate = owned(request, match_id)
        selected = [
            item for item in aggregate.state.get("insights", [])
            if item.get("provenance", {}).get("quality", {}).get("eligible", False)
        ]
        if payload.audience_mode == "player":
            if not payload.selected_player_id:
                raise HTTPException(400, "Select a player for player-focused recaps")
            selected = [item for item in selected if payload.selected_player_id in {
                item.get("player_id"), *item.get("context", {}).get("known_player_ids", []),
            }]
        selected.sort(key=lambda item: (item["priority"] == "high", item["confidence"]), reverse=True)
        selected = selected[:3]
        copy = language_copy(payload.language)
        if not selected:
            return {
                "title": copy["post_match_recap"][0], "body": copy["insufficient"],
                "audience_mode": payload.audience_mode, "language": payload.language, "fallback_used": True,
                "provider": "guardrail-recap-evidence", "grounding": {"status": "insufficient_recap_evidence"},
                "top_insights": [], "chapters": [],
            }
        chapters = []
        passages = []
        providers = []
        fallback_used = False
        for index, item in enumerate(selected):
            insight = Insight.model_validate(item)
            insight.context["depth"] = payload.depth
            generated = request.app.state.generation.script(
                token(request), aggregate.tenant_id, match_id, "single", insight,
                payload.language, payload.audience_mode, payload.selected_player_id, allow_cloud=payload.allow_cloud,
            )
            passages.append(generated["body"])
            providers.append(generated["provider"])
            fallback_used = fallback_used or generated["fallback_used"]
            title, summary = localized_insight(insight, payload.language)
            chapters.append({
                "chapter_id": f"chapter_{index + 1}", "title": title, "summary": summary,
                "window_start_ms": insight.window_start_ms, "window_end_ms": insight.window_end_ms,
                "confidence": insight.confidence, "team_id": insight.team_id, "type": insight.type,
                "provenance": item["provenance"],
            })
        body = "\n\n".join(passages)
        result = {
            "title": copy["post_match_recap"][0], "body": body,
            "audience_mode": payload.audience_mode, "language": payload.language, "fallback_used": fallback_used,
            "provider": providers[0] if len(set(providers)) == 1 else "mixed",
            "grounding": {"status": "chapter-grounded", "source": "owned-supported-insights"},
            "top_insights": [{key: item[key] for key in ("insight_id", "title", "confidence", "team_id")} for item in selected],
            "chapters": chapters,
        }
        result["script_id"] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
        request.app.state.cloud.store.save_script(
            aggregate.tenant_id, match_id, result["script_id"], {**result, "kind": "single"},
        )
        return result

    @app.post("/api/v1/speech/synthesize")
    def synthesize(payload: OwnedSpeechRequest, request: Request):
        aggregate = owned(request, payload.match_id)
        return request.app.state.generation.synthesize(
            token(request), aggregate.tenant_id, payload.match_id, payload.script_id, dual=False,
        )

    @app.post("/api/v1/speech/synthesize-dual")
    def synthesize_dual(payload: OwnedSpeechRequest, request: Request):
        aggregate = owned(request, payload.match_id)
        return request.app.state.generation.synthesize(
            token(request), aggregate.tenant_id, payload.match_id, payload.script_id, dual=True,
        )

    return app
