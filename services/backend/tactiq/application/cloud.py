"""Durable admission and per-job computation; APIs never restore full histories."""
from __future__ import annotations

import hashlib
import io
import json
import math
import shutil
from pathlib import Path
from typing import Callable
from uuid import NAMESPACE_URL, uuid4, uuid5

from pydantic import BaseModel, ConfigDict, Field

from tactiq.cv.types import CvOptions
from tactiq.domain.models import MatchStatus
from tactiq.runtime.access import AccessStore, AnalysisIntent, AnalysisReservation
from tactiq.runtime.cloud_store import CloudApplicationStore
from tactiq.runtime.cloud_worker import ExecutionContext, LeaseLostError
from tactiq.runtime.contracts import MediaRef, MediaStore, RevisionConflictError, StreamRecord
from tactiq.simulator.scenarios import RECIPE_VERSION, scenario_timeline
from tactiq.video.download import download_video
from tactiq.video.providers import download_youtube, parse_video_source


class TeamNames(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    team_a: str = Field(min_length=1, max_length=80)
    team_b: str = Field(min_length=1, max_length=80)


class ScenarioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario_id: str = Field(default="sustained_pressure", pattern=r"^(sustained_pressure|rhythm_shift|player_influence)$")
    seed: int = Field(default=42001, strict=True, ge=-(2**63), le=2**63 - 1)
    recipe_version: int = Field(default=RECIPE_VERSION, strict=True, ge=1, le=RECIPE_VERSION)
    team_display_names: TeamNames | None = None


class VideoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    analysis_mode: str = Field(default="cv", pattern=r"^(cv|events)$")
    rights_confirmed: bool = False
    options: CvOptions = Field(default_factory=CvOptions)
    team_display_names: TeamNames | None = None


class LinkRequest(VideoRequest):
    url: str = Field(min_length=1, max_length=2048)


def fingerprint(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class CloudAnalysisService:
    def __init__(
        self, store: CloudApplicationStore, access: AccessStore, media: MediaStore,
        *, max_pending: int = 8, max_upload_bytes: int = 200 * 1024 * 1024,
        processing_limit_per_day: int | None = None,
    ) -> None:
        if not 1 <= max_pending <= 100 or not 1 <= max_upload_bytes <= 2 * 1024**3:
            raise ValueError("Invalid cloud admission limits")
        if not callable(getattr(access, "reserve_analysis_and_admit", None)):
            raise ValueError("Cloud access adapter must support atomic job admission")
        self.store, self.access, self.media = store, access, media
        self.max_pending, self.max_upload_bytes = max_pending, max_upload_bytes
        if processing_limit_per_day is not None and (
            type(processing_limit_per_day) is not int or not 1 <= processing_limit_per_day <= 10000
        ):
            raise ValueError("Invalid native-processing allocation")
        self.processing_limit_per_day = processing_limit_per_day

    def admit(
        self, token: str, ip: str, operation: str, kind: str, payload: dict,
        *, digest_payload: dict | None = None, locator: str | None = None, waiting_input: bool = False,
    ) -> AnalysisReservation:
        payload = dict(payload)
        payload["team_display_names"] = payload.get("team_display_names") or {"team_a": "Team A", "team_b": "Team B"}
        effective_digest = dict(digest_payload or payload)
        effective_digest["team_display_names"] = payload["team_display_names"]
        intent = AnalysisIntent(operation, kind, fingerprint(effective_digest))

        def commit(connection, reservation):
            # Serialize only admissions, never CV or Blob network traffic.
            connection.execute("SELECT pg_advisory_xact_lock(854917020604)")
            if not reservation.replayed:
                if self.processing_limit_per_day is None:
                    raise ProcessingUnavailable("No native-processing allocation has been approved")
                policy = connection.execute(
                    """SELECT approved_limit,admitted,
                       starts+interval '24 hours'<=clock_timestamp() AS expired,
                       EXTRACT(EPOCH FROM starts+interval '24 hours'-clock_timestamp()) AS remaining
                       FROM tactiq_cloud_processing_window WHERE singleton FOR UPDATE"""
                ).fetchone()
                if policy is None or policy["expired"]:
                    connection.execute(
                        """INSERT INTO tactiq_cloud_processing_window(singleton,starts,admitted,approved_limit)
                           VALUES (true,clock_timestamp(),0,%s) ON CONFLICT(singleton) DO UPDATE
                           SET starts=clock_timestamp(),admitted=0,approved_limit=EXCLUDED.approved_limit""",
                        (self.processing_limit_per_day,),
                    )
                elif policy["approved_limit"] != self.processing_limit_per_day:
                    raise ProcessingUnavailable("Native-processing policies disagree across replicas")
                elif policy["admitted"] >= self.processing_limit_per_day:
                    raise CloudCapacityError("Global native-processing allocation is exhausted",
                                             retry_after=max(1, math.ceil(policy["remaining"])),
                                             code="processing_limit_exhausted")
                pending = connection.execute(
                    "SELECT count(*) AS count FROM tactiq_cloud_jobs WHERE status IN ('queued','running','paused')"
                ).fetchone()["count"]
                if pending >= self.max_pending:
                    raise CloudCapacityError("Analysis processing capacity is currently full")
                count = connection.execute(
                    "SELECT count(*) AS count FROM tactiq_cloud_matches WHERE tenant_id=%s",
                    (reservation.tenant_id,),
                ).fetchone()["count"]
                if count >= 100:
                    raise CloudCapacityError("Workspace analysis retention capacity is full")
                connection.execute("UPDATE tactiq_cloud_processing_window SET admitted=admitted+1 WHERE singleton")
            self.store.admit(
                connection, reservation, "scenario" if kind == "scenario" else "video",
                payload, locator=locator, waiting_input=waiting_input,
            )

        return self.access.reserve_analysis_and_admit(token, ip, intent, commit)

    def admit_upload(
        self, token: str, ip: str, operation: str, content: bytes, request: VideoRequest,
    ) -> AnalysisReservation:
        from tactiq.video.validation import validate_video_container

        if not request.rights_confirmed:
            raise ValueError("Confirm permission to analyze this video")
        if not content or len(content) > self.max_upload_bytes:
            raise ValueError("Video size is outside the configured limit")
        validate_video_container(content)
        payload = request.model_dump(mode="json", exclude={"rights_confirmed"})
        payload["input_digest"] = hashlib.sha256(content).hexdigest()
        reservation = self.admit(token, ip, operation, "upload", payload, waiting_input=True)
        aggregate = self.store.load(reservation.tenant_id, reservation.analysis_id)
        if aggregate.state["_cloud"]["input_ready"]:
            return reservation
        reference = self.media.upload(
            reservation.tenant_id, reservation.analysis_id, io.BytesIO(content),
            size=len(content), content_type="application/octet-stream",
        )
        try:
            if not self.store.attach_input(reference):
                self.media.delete(reference, tenant_id=reference.tenant_id, match_id=reference.match_id)
        except BaseException:
            self.media.delete(reference, tenant_id=reference.tenant_id, match_id=reference.match_id)
            raise
        return reservation

    def admit_link(self, token: str, ip: str, operation: str, request: LinkRequest) -> AnalysisReservation:
        if not request.rights_confirmed:
            raise ValueError("Confirm permission to analyze this video")
        source = parse_video_source(request.url)
        payload = request.model_dump(mode="json", exclude={"url", "rights_confirmed"})
        payload["input_provider"] = source.provider
        if source.video_id:
            payload["provider_video_id"] = source.video_id
        # The job only contains an opaque input ID. A private bounded table holds
        # the validated locator; it is never emitted in history, logs or messages.
        return self.admit(
            token, ip, operation, "link", payload,
            digest_payload={**payload, "locator_digest": hashlib.sha256(source.url.encode()).hexdigest()},
            locator=source.url,
        )


class CloudCapacityError(RuntimeError):
    def __init__(self, message: str, *, retry_after: int = 60, code: str = "analysis_capacity"):
        super().__init__(message)
        self.retry_after = retry_after
        self.code = code


class ProcessingUnavailable(RuntimeError):
    pass


class AnalysisHandlers:
    """One bounded match in worker scratch, never an API replica's global cache.

    Recompute after a crash, then append only the missing deterministic prefix.
    Every externally visible database write is fenced by claim AND owner.
    """

    def __init__(
        self, store: CloudApplicationStore, media: MediaStore, scratch_root: Path,
        *, max_upload_bytes: int = 200 * 1024 * 1024, service_factory: Callable | None = None,
        link_loader: Callable | None = None,
    ) -> None:
        self.store, self.media = store, media
        self.scratch_root = scratch_root.resolve()
        self.scratch_root.mkdir(parents=True, exist_ok=True)
        self.max_upload_bytes = max_upload_bytes
        self.service_factory = service_factory
        self.link_loader = link_loader or self._download

    def handlers(self) -> dict:
        return {"scenario": self.scenario, "video": self.video}

    def _service(self, root: Path):
        if self.service_factory is not None:
            return self.service_factory(root)
        from tactiq.application.service import MatchService

        return MatchService(media_root=root, paid_providers=False)

    def _download(self, locator: str, payload: dict) -> bytes:
        if payload.get("input_provider") == "youtube":
            return download_youtube(payload["provider_video_id"], self.max_upload_bytes)
        return download_video(locator, self.max_upload_bytes, 4)

    def _completed(self, context: ExecutionContext) -> dict | None:
        context.checkpoint()
        aggregate = self.store.load(context.claim.tenant_id, context.claim.job_id)
        if aggregate is None:
            raise ValueError("Admitted analysis metadata is unavailable")
        if aggregate.state.get("status") == "completed":
            return {"match_id": context.claim.job_id}
        return None

    def scenario(self, payload: dict, context: ExecutionContext) -> dict:
        if completed := self._completed(context):
            return completed
        root = self.scratch_root / uuid4().hex
        try:
            service = self._service(root)
            match = service.create_match(
                payload["scenario_id"], payload["seed"], context.claim.tenant_id,
                team_display_names=payload.get("team_display_names"),
            )
            self._rename(service, match, context.claim.job_id)
            match.player_roster = {}
            match.scenario_recipe_version = payload["recipe_version"]
            match.analysis_metadata["scenario_recipe_version"] = payload["recipe_version"]
            for event in scenario_timeline(
                payload["scenario_id"], match.match_id, payload["seed"], payload["recipe_version"],
            ):
                context.checkpoint()
                service.record_event(event, persist=False)
            match.status = MatchStatus.COMPLETED
            context.checkpoint()
            return self._persist(service, match.match_id, context)
        finally:
            if root.exists():
                shutil.rmtree(root)

    @staticmethod
    def _rename(service, match, match_id: str) -> None:
        old = match.match_id
        match.match_id = match_id
        for name in ("matches", "events", "insights", "replay_frames", "insight_clip_map", "last_access"):
            values = getattr(service, name)
            if old in values:
                values[match_id] = values.pop(old)

    def video(self, payload: dict, context: ExecutionContext) -> dict:
        try:
            return self._video(payload, context)
        except (LeaseLostError, RevisionConflictError):
            raise
        except Exception:
            # Worker exception logging must not print signed/private locators
            # that a downloader/native decoder may include in its exception.
            raise RuntimeError("Cloud video analysis failed") from None

    def _video(self, payload: dict, context: ExecutionContext) -> dict:
        if completed := self._completed(context):
            return completed
        context.checkpoint()
        aggregate = self.store.load(context.claim.tenant_id, context.claim.job_id)
        media_id = aggregate.state["_cloud"].get("input_media_id")
        if media_id:
            reference = self.store.load_media(context.claim.tenant_id, context.claim.job_id, media_id)
            if reference is None or reference.size > self.max_upload_bytes:
                raise ValueError("Source media is unavailable")
            content = b"".join(self.media.download(
                reference, tenant_id=reference.tenant_id, match_id=reference.match_id,
            ))
            if hashlib.sha256(content).hexdigest() != payload["input_digest"]:
                raise ValueError("Source media differs from admitted input")
        else:
            locator = self.store.input_locator(context.claim, context.owner)
            content = self.link_loader(locator, payload)
        if not content or len(content) > self.max_upload_bytes:
            raise ValueError("Source exceeds configured limits")
        context.checkpoint()
        root = self.scratch_root / uuid4().hex
        try:
            service = self._service(root)
            operation = uuid4().hex
            arguments = dict(
                video_bytes=content, source_name="source.mp4", tenant_id=context.claim.tenant_id,
                operation_id=operation, team_display_names=payload.get("team_display_names"),
                source_provider=payload.get("input_provider", "upload"),
                source_video_id=payload.get("provider_video_id"),
            )
            if payload["analysis_mode"] == "cv":
                result = service.create_match_from_cv(**arguments, options=CvOptions.model_validate(payload["options"]))
            else:
                result = service.create_match_from_video(**arguments)
            temporary_id = result["match_id"]
            match = service.matches[temporary_id]
            self._rename(service, match, context.claim.job_id)
            for name in ("match_video_source_path", "match_video_duration_ms"):
                values = getattr(service, name)
                if temporary_id in values:
                    values[match.match_id] = values.pop(temporary_id)
            replacements = {}
            for event in service.events[match.match_id]:
                event.match_id = match.match_id
                previous = event.event_id
                event.event_id = str(uuid5(NAMESPACE_URL, f"cloud:{match.match_id}:event:{event.sequence}"))
                replacements[previous] = event.event_id
            for insight in service.insights[match.match_id]:
                for evidence in insight.evidence:
                    evidence.supporting_event_ids = [replacements[item] for item in evidence.supporting_event_ids]
            service.cloud_clip_match_id = temporary_id
            # CV cannot infer a score. Keep {} rather than introducing a fake 0-0.
            match.score = {}
            context.checkpoint()
            return self._persist(service, match.match_id, context)
        finally:
            if root.exists():
                shutil.rmtree(root)

    def _upload_output(self, path: Path, context: ExecutionContext) -> MediaRef:
        context.checkpoint()
        with path.open("rb") as content:
            reference = self.media.upload(
                context.claim.tenant_id, context.claim.job_id, content,
                size=path.stat().st_size, content_type="video/mp4",
            )
        try:
            context.checkpoint()
            self.store.register_output(reference, context.claim, context.owner)
        except BaseException:
            self.media.delete(reference, tenant_id=reference.tenant_id, match_id=reference.match_id)
            raise
        return reference

    def _persist(self, service, match_id: str, context: ExecutionContext) -> dict:
        match = service.matches[match_id]
        state = match.model_dump(mode="json")
        aggregate = self.store.load(context.claim.tenant_id, match_id)
        progress = dict(aggregate.state["_cloud"])
        source = service.match_video_source_path.get(match_id)
        if source:
            reference = self._upload_output(Path(source), context)
            progress["source_media_id"] = reference.media_id
            progress["duration_ms"] = service.match_video_duration_ms.get(match_id, match.match_clock_ms)
        insights = []
        for index, insight in enumerate(service.insights[match_id][:100]):
            old_id = insight.insight_id
            clip = service.insight_clip_map[match_id].get(old_id, {})
            insight.match_id = match_id
            insight = service._contextualize_insight(match_id, insight)
            insight.insight_id = str(uuid5(NAMESPACE_URL, f"cloud:{match_id}:insight:{index}"))
            item = insight.model_dump(mode="json")
            item["provenance"] = service._insight_provenance(insight)
            item["clip"] = {"status": "unavailable", "reason": "not_available"}
            if clip.get("status") == "ready" and not service._fails_quality_guardrail(insight)[0]:
                clip_path = service.clips_root / getattr(service, "cloud_clip_match_id", match_id) / f"{old_id}.mp4"
                reference = self._upload_output(clip_path, context)
                item["clip"] = {
                    "status": "ready", "media_id": reference.media_id,
                    **{key: clip[key] for key in ("start_ms", "end_ms", "duration_ms")},
                }
            insights.append(item)
        state["insights"] = insights
        state["analysis_metadata"].update(
            total_insight_count=len(service.insights[match_id]),
            stored_insight_limit=100,
            insights_truncated=len(service.insights[match_id]) > 100,
        )
        events = [
            StreamRecord(event.event_id, event.model_dump(mode="json")) for event in service.events[match_id]
        ]
        replay = [
            StreamRecord(f"frame_{frame['sequence']}", frame) for frame in service.replay_frames[match_id]
        ]
        content_digest = fingerprint({
            "events": [record.payload for record in events],
            "replay": [record.payload for record in replay],
        })
        if progress.get("content_digest", content_digest) != content_digest:
            raise ValueError("Recovered computation differs from the committed stream prefix")
        progress["content_digest"] = content_digest
        while progress["event_count"] < len(events) or progress["replay_count"] < len(replay):
            context.checkpoint()
            event_batch = tuple(events[progress["event_count"]:progress["event_count"] + 100])
            replay_batch = tuple(replay[progress["replay_count"]:progress["replay_count"] + 100])
            progress["event_count"] += len(event_batch)
            progress["replay_count"] += len(replay_batch)
            final = progress["event_count"] == len(events) and progress["replay_count"] == len(replay)
            current = dict(state) if final else dict(aggregate.state)
            if not final:
                current["status"] = "running"
                if event_batch:
                    current["last_sequence"] = event_batch[-1].payload["sequence"]
                    current["match_clock_ms"] = event_batch[-1].payload["match_clock_ms"]
            current["_cloud"] = progress
            aggregate = self.store.commit(
                context.claim.tenant_id, match_id, current, expected_revision=aggregate.revision,
                events=event_batch, replay=replay_batch, claim=context.claim, owner=context.owner,
            )
        if not events and not replay:
            state["_cloud"] = progress
            self.store.commit(
                context.claim.tenant_id, match_id, state, expected_revision=aggregate.revision,
                claim=context.claim, owner=context.owner,
            )
        return {"match_id": match_id, "event_count": len(events), "insight_count": len(insights)}


def expire_guest_data(store: CloudApplicationStore, access: AccessStore, media: MediaStore, limit: int = 100) -> int:
    """Idempotent system retention; AccessStore serializes expiry vs promotion."""
    expired = access.expire_guests(limit)
    for tenant in expired:
        for match_id in tenant.analysis_ids:
            store.request_cancel(tenant.tenant_id, match_id)
            after = ""
            while references := store.media_page(tenant.tenant_id, match_id, after=after):
                for reference in references:
                    try:
                        media.delete(reference, tenant_id=tenant.tenant_id, match_id=match_id)
                    except FileNotFoundError:
                        pass
                    except Exception as exc:
                        from azure.core.exceptions import ResourceNotFoundError
                        if not isinstance(exc, ResourceNotFoundError):
                            raise
                after = references[-1].media_id
            store.delete_expired(tenant.tenant_id, match_id)
        access.acknowledge_cleanup(tenant.tenant_id)
    return len(expired)
