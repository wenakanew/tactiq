from __future__ import annotations

import asyncio
from collections import OrderedDict, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import os
import hashlib
import logging
import math
from pathlib import Path
import re
import shutil
from statistics import mean
import subprocess
import time
from threading import Lock, RLock
from typing import Any, Callable
from uuid import uuid4

from tactiq.agents.narrative import (
    DeterministicNarrativeGenerator,
    DualCommentaryResult,
    FoundryNarrativeGenerator,
    NarrativeResult,
)
from tactiq.agents.speech import AzureSpeechSynthesizer, SpeechResult
from tactiq.agents.localization import language_copy, localized_insight
from tactiq.agents.commentary import CommentaryTurn
from tactiq.domain.models import Event, EvidenceItem, Insight, MatchState, MatchStatus, PlayerIdentity
from tactiq.cv.events import tracks_to_events
from tactiq.cv.ingest import extract_cv_tracks_from_video_bytes
from tactiq.cv.types import CvOptions
from tactiq.intelligence.engine import detect_insights, insight_priority_weight, reduce_match_state
from tactiq.intelligence.value import configured_value_model
from tactiq.intelligence.geometry import has_pitch_geometry
from tactiq.runtime import SQLiteStore
from tactiq.runtime.observability import RequestMetrics
from tactiq.simulator.scenarios import RECIPE_VERSION, load_scenario, scenario_timeline
from tactiq.video.extractor import extract_events_from_video_auto, extract_events_from_video_stub
from tactiq.video.validation import validate_video_container
from tactiq.video.media import normalize_video

logger = logging.getLogger(__name__)


class MatchCapacityError(RuntimeError):
    pass


class MatchService:
    @staticmethod
    def _env_int(name: str, default: int) -> int:
        raw = (os.getenv(name) or "").strip()
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError as exc:
            raise ValueError(f"{name} must be an integer") from exc

    @staticmethod
    def _env_float(name: str, default: float) -> float:
        raw = (os.getenv(name) or "").strip()
        if not raw:
            return default
        try:
            value = float(raw)
            if not math.isfinite(value):
                raise ValueError()
            return value
        except ValueError as exc:
            raise ValueError(f"{name} must be a finite number") from exc

    @staticmethod
    def _env_text(name: str, default: str) -> str:
        raw = (os.getenv(name) or "").strip()
        return raw or default

    def __init__(self, *, media_root: Path | None = None, paid_providers: bool = True) -> None:
        self.lock = RLock()
        self.reference_lock = Lock()
        self.store: SQLiteStore | None = None
        self.processing_metrics = RequestMetrics()
        self.last_access: dict[str, float] = {}
        self.in_use: dict[str, int] = defaultdict(int)
        self.max_cached_matches = self._env_int("TACTIQ_MAX_CACHED_MATCHES", 32)
        if not 2 <= self.max_cached_matches <= 256:
            raise ValueError("TACTIQ_MAX_CACHED_MATCHES must be in [2, 256]")
        self.matches: dict[str, MatchState] = {}
        self.events: dict[str, list[Event]] = defaultdict(list)
        self.insights: dict[str, list[Insight]] = defaultdict(list)
        self.subscribers: dict[str, set[asyncio.Queue[dict]]] = defaultdict(set)
        self.tasks: dict[str, asyncio.Task] = {}
        self.viewer_profiles: dict[str, dict[str, str]] = defaultdict(dict)
        self.commentary_audit_log: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.commentary_audit_max_entries = 100
        self.commentary_memory: dict[str, list[str]] = defaultdict(list)
        self.commentary_memory_max = self._env_int("COMMENTARY_MEMORY_MAX_LINES", 24)
        self.overlay_queue_limit = self._env_int("OVERLAY_QUEUE_LIMIT", 3)
        self.narrative_min_confidence = self._env_float("NARRATIVE_MIN_CONFIDENCE", 0.6)
        self.narrative_min_evidence_items = self._env_int("NARRATIVE_MIN_EVIDENCE_ITEMS", 2)
        self.team_display_names = {
            "team_a": self._env_text("TACTIQ_TEAM_A_NAME", "Team A"),
            "team_b": self._env_text("TACTIQ_TEAM_B_NAME", "Team B"),
        }
        self.clip_pre_roll_ms = self._env_int("TACTIQ_CLIP_PRE_ROLL_MS", 6000)
        self.clip_post_roll_ms = self._env_int("TACTIQ_CLIP_POST_ROLL_MS", 4000)
        self.clip_max_per_match = self._env_int("TACTIQ_CLIP_MAX_PER_MATCH", 5)
        self.clip_min_confidence = self._env_float("TACTIQ_CLIP_MIN_CONFIDENCE", 0.7)
        if self.commentary_memory_max < 1 or not 1 <= self.overlay_queue_limit <= 20:
            raise ValueError("Commentary memory and overlay queue limits must be positive and bounded")
        if not 0 <= self.narrative_min_confidence <= 1 or not 0 <= self.clip_min_confidence <= 1:
            raise ValueError("Confidence thresholds must lie in [0, 1]")
        if self.narrative_min_evidence_items < 1 or not 0 <= self.clip_max_per_match <= 20:
            raise ValueError("Evidence and clip limits are invalid")
        if min(self.clip_pre_roll_ms, self.clip_post_roll_ms) < 0 or max(self.clip_pre_roll_ms, self.clip_post_roll_ms) > 60_000:
            raise ValueError("Clip pre/post-roll must be in [0, 60000] milliseconds")
        media_root_env = os.getenv("TACTIQ_MEDIA_DIR", "").strip()
        default_media_root = Path(__file__).resolve().parents[3] / ".tactiq_media"
        self.media_root = media_root if media_root is not None else Path(media_root_env) if media_root_env else default_media_root
        self.media_root.mkdir(parents=True, exist_ok=True)
        self.source_root = self.media_root / "sources"
        self.source_root.mkdir(parents=True, exist_ok=True)
        self.clips_root = self.media_root / "clips"
        self.clips_root.mkdir(parents=True, exist_ok=True)
        self.ffmpeg_bin = shutil.which("ffmpeg")
        self.ffprobe_bin = shutil.which("ffprobe")
        self.match_video_source_path: dict[str, str] = {}
        self.match_video_duration_ms: dict[str, int] = {}
        self.insight_clip_map: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        self.replay_frames: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.value_model = configured_value_model()
        self.narrative_generator = (
            FoundryNarrativeGenerator(DeterministicNarrativeGenerator())
            if paid_providers else DeterministicNarrativeGenerator()
        )
        self.speech_synthesizer = AzureSpeechSynthesizer() if paid_providers else None
        self.generated_cache: OrderedDict[tuple[str, ...], tuple[float, NarrativeResult | DualCommentaryResult]] = OrderedDict()

    def _generation_key(self, target: Insight, kind: str, language: str, audience: str = "", player: str = "") -> tuple[str, ...]:
        state = self.matches[target.match_id]
        digest = hashlib.sha256(target.model_dump_json().encode()).hexdigest()
        profile = self.narrative_generator.cache_identity() if isinstance(self.narrative_generator, FoundryNarrativeGenerator) else str(id(self.narrative_generator))
        return kind, state.tenant_id, target.match_id, language, audience, player, digest, profile

    def _cached_generation(self, key: tuple[str, ...]) -> NarrativeResult | DualCommentaryResult | None:
        with self.lock:
            value = self.generated_cache.get(key)
            if value is None:
                return None
            if time.monotonic() - value[0] > 300:
                del self.generated_cache[key]
                return None
            self.generated_cache.move_to_end(key)
            return deepcopy(value[1])

    def _cache_generation(self, key: tuple[str, ...], result: NarrativeResult | DualCommentaryResult) -> None:
        if result.fallback_used:
            return
        with self.lock:
            self.generated_cache[key] = time.monotonic(), deepcopy(result)
            self.generated_cache.move_to_end(key)
            while len(self.generated_cache) > 64:
                self.generated_cache.popitem(last=False)

    @staticmethod
    def _safe_filename(name: str, fallback: str = "source") -> str:
        base = (name or "").strip() or fallback
        base = base.replace("\\", "/").split("/")[-1]
        stem, ext = os.path.splitext(base)
        stem = re.sub(r"[^a-zA-Z0-9._-]", "_", stem).strip("._") or fallback
        ext = re.sub(r"[^a-zA-Z0-9.]", "", ext)[:12]
        if not ext:
            ext = ".mp4"
        return f"{stem}{ext}"

    @staticmethod
    def _to_media_url(relative_path: str) -> str:
        return "/media/" + relative_path.replace(os.sep, "/")

    def _store_source_video(self, match_id: str, video_bytes: bytes, source_name: str) -> str:
        target_dir = self.source_root / match_id
        target_dir.mkdir(parents=True, exist_ok=True)
        safe_name = self._safe_filename(source_name)
        target_path = target_dir / safe_name
        target_path.write_bytes(video_bytes)
        return str(target_path)

    def _resolve_video_duration_ms(self, video_path: str) -> int:
        if self.ffprobe_bin:
            try:
                proc = subprocess.run(
                    [
                        self.ffprobe_bin,
                        "-v",
                        "error",
                        "-protocol_whitelist",
                        "file,pipe",
                        "-show_entries",
                        "format=duration",
                        "-of",
                        "default=noprint_wrappers=1:nokey=1",
                        video_path,
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=8,
                )
                raw = (proc.stdout or "").strip()
                if raw:
                    seconds = float(raw)
                    if seconds > 0:
                        return int(seconds * 1000)
            except Exception:  # noqa: BLE001
                pass

        try:
            import cv2  # type: ignore[import-not-found]

            cap = cv2.VideoCapture(video_path)
            if cap.isOpened():
                fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
                frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
                cap.release()
                if fps > 0 and frame_count > 0:
                    return int((frame_count / fps) * 1000)
        except Exception:  # noqa: BLE001
            pass

        return 0

    def _clip_candidate_insights(self, insights: list[Insight]) -> list[Insight]:
        major_types = {"turning_point", "player_speed_burst", "sustained_pressure", "player_influence"}
        ranked = sorted(
            insights,
            key=lambda insight: (
                insight.type == "turning_point",
                insight.priority == "high",
                insight.confidence,
            ),
            reverse=True,
        )
        selected: list[Insight] = []
        selected_windows: list[int] = []
        for insight in ranked:
            if insight.confidence < self.clip_min_confidence and insight.type not in major_types:
                continue
            end_ms = insight.window_end_ms
            if any(abs(end_ms - prior_end) < 5000 for prior_end in selected_windows):
                continue
            selected.append(insight)
            selected_windows.append(end_ms)
            if len(selected) >= self.clip_max_per_match:
                break
        return selected

    def _build_clip(self, match_id: str, insight: Insight, source_video_path: str, duration_ms: int) -> dict[str, Any]:
        if not self.ffmpeg_bin:
            return {"status": "unavailable", "reason": "ffmpeg_missing"}

        start_ms = max(0, insight.window_start_ms - self.clip_pre_roll_ms)
        end_ms = insight.window_end_ms + self.clip_post_roll_ms
        if duration_ms > 0:
            if start_ms >= duration_ms:
                return {"status": "unavailable", "reason": "window_outside_source"}
            end_ms = min(end_ms, duration_ms)
        if end_ms <= start_ms:
            return {"status": "unavailable", "reason": "empty_clip_window"}

        clip_dir = self.clips_root / match_id
        clip_dir.mkdir(parents=True, exist_ok=True)
        clip_name = f"{insight.insight_id}.mp4"
        clip_path = clip_dir / clip_name

        cmd = [
            self.ffmpeg_bin,
            "-y",
            "-ss",
            f"{start_ms / 1000:.3f}",
            "-to",
            f"{end_ms / 1000:.3f}",
            "-protocol_whitelist",
            "file,pipe",
            "-i",
            source_video_path,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "24",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(clip_path),
        ]

        try:
            proc = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=60)
            if proc.returncode != 0 or not clip_path.exists() or clip_path.stat().st_size == 0:
                return {
                    "status": "failed",
                    "reason": "clip_generation_failed",
                    "error": (proc.stderr or proc.stdout or "ffmpeg failed")[:240],
                }
        except Exception as exc:  # noqa: BLE001
            return {
                "status": "failed",
                "reason": "clip_generation_failed",
                "error": str(exc)[:240],
            }

        relative = os.path.join(match_id, clip_name)
        return {
            "status": "ready",
            "url": self._to_media_url(relative),
            "start_ms": start_ms,
            "end_ms": end_ms,
            "duration_ms": max(0, end_ms - start_ms),
        }

    def _build_insight_clips(self, match_id: str, insights: list[Insight]) -> None:
        if self.matches[match_id].analysis_metadata.get("extractor") == "video-stub-v1":
            self.insight_clip_map[match_id] = {
                insight.insight_id: {"status": "unavailable", "reason": "synthetic_events_are_not_video_evidence"}
                for insight in insights
            }
            return
        source_video_path = self.match_video_source_path.get(match_id)
        if not source_video_path or not os.path.exists(source_video_path):
            self.insight_clip_map[match_id] = {
                insight.insight_id: {"status": "unavailable", "reason": "source_video_missing"}
                for insight in insights
            }
            return

        duration_ms = self.match_video_duration_ms.get(match_id) or self._resolve_video_duration_ms(source_video_path)
        if duration_ms > 0:
            self.match_video_duration_ms[match_id] = duration_ms

        clips: dict[str, dict[str, Any]] = {
            insight.insight_id: {"status": "unavailable", "reason": "not_prioritized"}
            for insight in insights
        }
        for insight in self._clip_candidate_insights(insights):
            clips[insight.insight_id] = self._build_clip(
                match_id=match_id,
                insight=insight,
                source_video_path=source_video_path,
                duration_ms=duration_ms,
            )

        self.insight_clip_map[match_id] = clips

    def _serialize_insight(self, match_id: str, insight: Insight) -> dict[str, Any]:
        payload = insight.model_dump()
        payload["provenance"] = self._insight_provenance(insight)
        payload["clip"] = self.insight_clip_map.get(match_id, {}).get(
            insight.insight_id,
            {"status": "unavailable", "reason": "not_available"},
        )
        if payload["clip"].get("status") == "ready" and self._fails_quality_guardrail(insight)[0]:
            payload["clip"] = {"status": "unavailable", "reason": "insufficient_supported_evidence"}
        return payload

    def get_serialized_insights(self, match_id: str) -> list[dict[str, Any]]:
        return [self._serialize_insight(match_id, insight) for insight in self.insights[match_id]]

    @staticmethod
    def _insight_reason_code(insight_type: str) -> str:
        mapping = {
            "sustained_pressure": "pressure_sustained",
            "rhythm_shift": "rhythm_shift_detected",
            "player_influence": "player_influence_rising",
            "player_speed_burst": "speed_burst_detected",
            "turning_point": "state_turning_point",
            "post_match_recap": "chaptered_recap_summary",
        }
        return mapping.get(insight_type, "pattern_detected")

    def _insight_provenance(self, insight: Insight) -> dict[str, Any]:
        supporting_ids = sorted(
            {
                event_id
                for item in insight.evidence
                for event_id in item.supporting_event_ids
                if event_id
            }
        )
        return {
            "insight_id": insight.insight_id,
            "type": insight.type,
            "player_id": insight.player_id,
            "metric_version": insight.metric_version,
            "model_version": insight.model_version,
            "data_source": self.matches[insight.match_id].analysis_metadata.get("detection_source", "synthetic"),
            "reason_code": self._insight_reason_code(insight.type),
            "window": {
                "start_ms": insight.window_start_ms,
                "end_ms": insight.window_end_ms,
            },
            "confidence": insight.confidence,
            "quality": {"eligible": not self._fails_quality_guardrail(insight)[0],
                        "reason": self._fails_quality_guardrail(insight)[1]},
            "metrics": [
                {
                    "metric": item.metric,
                    "value": item.value,
                    "unit": item.unit,
                }
                for item in insight.evidence
            ],
            "supporting_event_ids": supporting_ids,
        }

    def _fails_quality_guardrail(self, insight: Insight) -> tuple[bool, str]:
        state = self.matches.get(insight.match_id)
        if state is not None and state.analysis_metadata.get("source_timed_synthetic_preview"):
            return True, "synthetic_video_preview"
        if state is not None and state.analysis_metadata.get("detection_source") == "heuristic":
            return True, "unvalidated_motion_preview"
        supporting_events_count = len(
            {
                event_id
                for item in insight.evidence
                for event_id in item.supporting_event_ids
                if event_id
            }
        )
        if insight.confidence < self.narrative_min_confidence:
            return True, "low_confidence"
        if len(insight.evidence) < self.narrative_min_evidence_items:
            return True, "insufficient_evidence_items"
        if supporting_events_count < self.narrative_min_evidence_items:
            return True, "insufficient_supporting_events"
        known = {event.event_id for event in self.events.get(insight.match_id, [])}
        if any(key not in known for item in insight.evidence for key in item.supporting_event_ids):
            return True, "unresolved_supporting_events"
        return False, "ok"

    @staticmethod
    def _normalize_text(text: str) -> str:
        lowered = text.casefold().strip()
        lowered = re.sub(r"[^\w\s]", " ", lowered)
        lowered = re.sub(r"\s+", " ", lowered)
        return lowered

    def _is_repetitive(self, a: str, b: str) -> bool:
        left = set(self._normalize_text(a).split())
        right = set(self._normalize_text(b).split())
        if not left or not right:
            return False
        overlap = len(left & right) / max(len(left), len(right))
        return overlap >= 0.8

    def _anti_repeat_line(self, match_id: str, line: str, role: str) -> str:
        memory = self.commentary_memory[match_id]
        if any(self._is_repetitive(line, previous) for previous in memory[-6:]):
            logger.info("Repeated commentary line detected", extra={"match_id": match_id, "role": role})
        memory.append(line)
        self.commentary_memory[match_id] = memory[-self.commentary_memory_max :]
        return line

    def _overlay_priority_queue(self, insights: list[Insight], match_clock_ms: int, limit: int | None = None) -> list[dict[str, Any]]:
        if not insights:
            return []

        insights = [insight for insight in insights if not self._fails_quality_guardrail(insight)[0]]
        effective_limit = limit or self.overlay_queue_limit
        type_counts: dict[str, int] = defaultdict(int)
        for insight in insights:
            type_counts[insight.type] += 1

        ranked: list[tuple[float, Insight]] = []
        for insight in insights:
            age_seconds = max(0, (match_clock_ms - insight.window_end_ms) / 1000)
            freshness = max(0.1, 1.0 - min(age_seconds, 180.0) / 180.0)
            novelty = 1.0 / (1.0 + max(0, type_counts[insight.type] - 1))
            score = (0.45 * insight.confidence) + (0.35 * insight_priority_weight(insight.priority)) + (0.2 * novelty)
            score *= freshness
            ranked.append((score, insight))

        ranked.sort(key=lambda item: item[0], reverse=True)
        return [
            {
                "score": round(score, 4),
                "insight": {
                    **insight.model_dump(),
                    "clip": self.insight_clip_map.get(insight.match_id, {}).get(
                        insight.insight_id,
                        {"status": "unavailable", "reason": "not_available"},
                    ),
                },
                "provenance": self._insight_provenance(insight),
            }
            for score, insight in ranked[:effective_limit]
        ]

    def configure_storage(self, store: SQLiteStore) -> None:
        self.store = store

    def _forget_match(self, match_id: str) -> None:
        for collection in (
            self.matches, self.events, self.insights, self.viewer_profiles, self.commentary_audit_log,
            self.commentary_memory, self.match_video_source_path, self.match_video_duration_ms,
            self.insight_clip_map, self.replay_frames, self.last_access,
        ):
            collection.pop(match_id, None)
        for key in tuple(self.generated_cache):
            if key[2] == match_id:
                del self.generated_cache[key]

    def _ensure_cache_capacity(self) -> None:
        if len(self.matches) < self.max_cached_matches:
            return
        available = [key for key, state in self.matches.items() if state.status != MatchStatus.RUNNING
                     and not self.in_use.get(key) and not self.subscribers.get(key) and key not in self.tasks]
        if self.store is None or not available:
            raise MatchCapacityError("Active match capacity reached; pause or close an existing match")
        victim = min(available, key=lambda key: self.last_access.get(key, 0))
        self.persist_match(victim)
        self._forget_match(victim)

    def pin_match(self, tenant_id: str, match_id: str) -> bool:
        with self.lock:
            if not self.restore_match(tenant_id, match_id):
                return False
            with self.reference_lock:
                self.in_use[match_id] += 1
            self.last_access[match_id] = time.monotonic()
            return True

    def unpin_match(self, match_id: str) -> None:
        with self.reference_lock:
            remaining = self.in_use.get(match_id, 0) - 1
            if remaining > 0:
                self.in_use[match_id] = remaining
            else:
                self.in_use.pop(match_id, None)

    def delete_match(self, match_id: str) -> None:
        with self.lock:
            state = self.matches[match_id]
            if state.status == MatchStatus.RUNNING or self.tasks.get(match_id) or self.subscribers.get(match_id) or self.in_use.get(match_id, 0) > 1:
                raise MatchCapacityError("Match is active; pause processing and close viewers before deleting")
            folders = []
            files = []
            for root in (self.source_root, self.clips_root):
                folder = root / match_id
                if not folder.exists():
                    continue
                if folder.resolve().parent != root.resolve() or folder.is_symlink():
                    raise ValueError("Refusing to delete media outside the match directory")
                for item in folder.iterdir():
                    if not item.is_file() or item.is_symlink():
                        raise ValueError("Unexpected media entry; manual inspection is required")
                    files.append(item)
                folders.append(folder)
            for path in files:
                path.unlink()
            for folder in folders:
                folder.rmdir()
            if self.store:
                self.store.delete_match(state.tenant_id, match_id)
            self._forget_match(match_id)

    def persist_match(self, match_id: str) -> None:
        if self.store is None:
            return
        with self.lock:
            state = self.matches[match_id]
            self.store.save_match(state.tenant_id, match_id, {
                "snapshot_version": 1,
                "state": state.model_dump(mode="json"),
                "events": [event.model_dump(mode="json") for event in self.events[match_id]],
                "insights": [insight.model_dump(mode="json") for insight in self.insights[match_id]],
                "viewer_profile": dict(self.viewer_profiles[match_id]),
                "commentary_audit": deepcopy(self.commentary_audit_log[match_id]),
                "commentary_memory": list(self.commentary_memory[match_id]),
                "video_source": self.match_video_source_path.get(match_id),
                "video_duration_ms": self.match_video_duration_ms.get(match_id),
                "insight_clips": deepcopy(self.insight_clip_map[match_id]),
                "replay_frames": self.replay_frames[match_id],
            })

    def restore_match(self, tenant_id: str, match_id: str) -> bool:
        with self.lock:
            if match_id in self.matches:
                return self.matches[match_id].tenant_id == tenant_id
            snapshot = self.store.load_match(tenant_id, match_id) if self.store else None
            if snapshot is None:
                return False
            self._ensure_cache_capacity()
            if snapshot.get("snapshot_version") != 1:
                raise ValueError("Stored match snapshot version is unsupported")
            state = MatchState.model_validate(snapshot["state"])
            if state.tenant_id != tenant_id or state.match_id != match_id:
                raise ValueError("Stored match ownership does not match its record")
            if state.status == MatchStatus.RUNNING:
                state.status = MatchStatus.PAUSED
            if state.scenario_id in {"cv_upload", "video_upload"} and not state.analysis_metadata.get("score_source"):
                state.score = {}
                state.analysis_metadata["score_source"] = "unknown"
            self.matches[match_id] = state
            self.last_access[match_id] = time.monotonic()
            self.events[match_id] = [Event.model_validate(value) for value in snapshot["events"]]
            state.action_count = sum(event.type not in {"tracking_frame", "motion_sample"} for event in self.events[match_id])
            state.tracking_sample_count = len(self.events[match_id]) - state.action_count
            if state.analysis_metadata.get("detection_source") == "heuristic":
                state.tracking_sample_count, state.action_count = len(self.events[match_id]), 0
            self.insights[match_id] = [Insight.model_validate(value) for value in snapshot["insights"]]
            self.viewer_profiles[match_id] = snapshot["viewer_profile"]
            self.commentary_audit_log[match_id] = snapshot["commentary_audit"]
            self.commentary_memory[match_id] = snapshot["commentary_memory"]
            self.insight_clip_map[match_id] = snapshot["insight_clips"]
            self.replay_frames[match_id] = snapshot["replay_frames"]
            if snapshot.get("video_source"):
                self.match_video_source_path[match_id] = snapshot["video_source"]
            if snapshot.get("video_duration_ms") is not None:
                self.match_video_duration_ms[match_id] = snapshot["video_duration_ms"]
            return True

    def create_match(
        self, scenario_id: str, seed: int, tenant_id: str = "local", operation_id: str | None = None,
        team_display_names: dict[str, str] | None = None,
    ) -> MatchState:
        planned_events = []
        if scenario_id not in {"video_upload", "cv_upload"}:
            _, planned_events = load_scenario(scenario_id, "validation", seed)
        if operation_id is not None and not re.fullmatch(r"[a-f0-9]{32}", operation_id):
            raise ValueError("Invalid analysis operation ID")
        match_id = f"match_{operation_id or uuid4().hex}"
        with self.lock:
            if match_id in self.matches and self.matches[match_id].tenant_id != tenant_id:
                raise ValueError("Analysis identifier belongs to a different tenant")
            existing = self.store.load_match(tenant_id, match_id) if self.store else None
            if not existing and self.store and len(self.store.list_matches(tenant_id, limit=100)) >= 100:
                raise MatchCapacityError("Tenant match storage limit reached; an administrator must remove older matches")
            if match_id not in self.matches:
                self._ensure_cache_capacity()
            state = MatchState(
                match_id=match_id, tenant_id=tenant_id, scenario_id=scenario_id, seed=seed,
                scenario_recipe_version=RECIPE_VERSION if planned_events else 1,
                team_display_names=dict(team_display_names or self.team_display_names),
                status=MatchStatus.RUNNING if scenario_id in {"cv_upload", "video_upload"} else MatchStatus.CREATED,
            )
            if scenario_id in {"cv_upload", "video_upload"}:
                state.score = {}
            elif planned_events:
                state.analysis_metadata = {
                    "detection_source": "synthetic", "geometry_source": "simulation",
                    "scenario_recipe_version": RECIPE_VERSION, "tracking_sample_fps": 4,
                    "notes": "Deterministic simulated match and player trajectories; not observations from real footage.",
                }
            self.matches[match_id] = state
            self.last_access[match_id] = time.monotonic()
            for event in planned_events:
                for player_id in (event.player_id, event.target_player_id):
                    if player_id:
                        state.player_roster[player_id] = PlayerIdentity(player_id=player_id, team_id=event.team_id)
            self.events[match_id] = []
            self.insights[match_id] = []
            self.replay_frames[match_id] = []
            self.insight_clip_map[match_id] = {}
            self.persist_match(match_id)
            return state

    def record_event(self, event: Event, *, persist: bool = True) -> list[Insight]:
        started = time.monotonic()
        with self.lock:
            state = self.matches[event.match_id]
            if event.sequence <= state.last_sequence:
                if any(item.event_id == event.event_id and item == event for item in self.events[event.match_id]):
                    return []
                raise ValueError("Event sequence is duplicated or out of order")
            if event.match_clock_ms < state.match_clock_ms:
                raise ValueError("Event time must not precede the committed match clock")
            history = self.events[event.match_id]
            history.append(event)
            for player_id in (event.player_id, event.target_player_id):
                if player_id:
                    state.player_roster[player_id] = PlayerIdentity(player_id=player_id, team_id=event.team_id)
            for player in event.qualifiers.get("cv_frame", {}).get("players", []):
                state.player_roster[player["track_id"]] = PlayerIdentity(
                    player_id=player["track_id"], team_id=player.get("team_id")
                )
            reduce_match_state(state, event, history, self.value_model)
            emitted = [] if state.analysis_metadata.get("detection_source") == "heuristic" else detect_insights(
                state, history, self.insights[event.match_id]
            )
            by_id = {item.event_id: item for item in history} if emitted else {}
            for insight in emitted:
                quality = []
                for supporting_id in {key for evidence in insight.evidence for key in evidence.supporting_event_ids}:
                    supporting = by_id[supporting_id]
                    if "source_confidence" in supporting.qualifiers:
                        quality.append(float(supporting.qualifiers["source_confidence"]))
                    frame_source = supporting.qualifiers.get("cv_frame")
                    if frame_source:
                        ball = frame_source.get("ball")
                        requires_ball = insight.type != "player_speed_burst"
                        if ball and requires_ball:
                            quality.append(float(ball["confidence"]))
                        elif requires_ball and frame_source.get("detection_source") == "model":
                            quality.append(0.0)
                        if ball and ball.get("inferred") and requires_ball:
                            quality.append(0.0)
                        quality.extend(float(player["confidence"]) for player in frame_source.get("players", [])
                                       if not player.get("inferred") and player.get("team_id") == insight.team_id
                                       and (requires_ball or (player.get("speed_kmh") or 0) >= 24))
                if quality:
                    insight.confidence = min(insight.confidence, min(quality))
            self.insights[event.match_id].extend(emitted)
            frame = {
                "timestamp_ms": state.match_clock_ms, "sequence": state.last_sequence,
                "score": dict(state.score), "possession_team_id": state.possession_team_id,
                "tracking": deepcopy(state.cv_live), "tactics": deepcopy(state.tactical_state),
            }
            frames = self.replay_frames[event.match_id]
            if frames and frames[-1]["timestamp_ms"] == frame["timestamp_ms"]:
                frames[-1] = frame
            else:
                frames.append(frame)
            if persist:
                self.persist_match(event.match_id)
            self.processing_metrics.record("POST", "/pipeline/event-to-insight", 200, time.monotonic() - started)
            return emitted

    def create_match_from_cv(
        self, video_bytes: bytes, source_name: str, options: CvOptions | None = None, tenant_id: str = "local",
        operation_id: str | None = None, team_display_names: dict[str, str] | None = None,
        source_provider: str = "upload", source_video_id: str | None = None,
    ) -> dict:
        options = options or CvOptions()
        prepared = normalize_video(video_bytes, options.max_duration_seconds)
        video_bytes = prepared.content
        result = extract_cv_tracks_from_video_bytes(video_bytes, source_name, options)
        match = self.create_match("cv_upload", 0, tenant_id, operation_id, team_display_names)
        match.attack_directions = dict(options.attack_directions or {})
        match.status = MatchStatus.RUNNING
        match.analysis_metadata = result.model_dump(exclude={"tracks", "calibration_history", "sampled_timestamps_ms"})
        match.analysis_metadata.update({
            "normalized_source": True, "original_duration_ms": prepared.original_duration_ms,
            "truncated": result.truncated or prepared.truncated,
            "attack_directions_source": "operator" if options.attack_directions else "unknown",
            "score_source": "unknown",
            "input_provider": source_provider,
            "provider_video_id": source_video_id,
            "confidence_scope": "observed_ball_candidate_scores",
            "calibration_confidence": result.calibration.confidence,
        })
        if prepared.truncated:
            match.analysis_metadata["warnings"].append(
                f"Analysis and replay are limited to the first {options.max_duration_seconds:g} seconds."
            )
        try:
            source = self._store_source_video(match.match_id, video_bytes, Path(source_name).stem + ".mp4")
            self.match_video_source_path[match.match_id] = source
            self.match_video_duration_ms[match.match_id] = self._resolve_video_duration_ms(source)
            for event in tracks_to_events(result, match.match_id, match.attack_directions):
                self.record_event(event, persist=False)
            self._build_insight_clips(match.match_id, self.insights[match.match_id])
            ball_scores = [point.confidence for point in result.tracks if point.object_class == "ball" and not point.inferred]
            confidence = mean(ball_scores) if ball_scores else 0.0
            match.analysis_metadata["extractor_confidence"] = confidence
            return self._finish_video_match(match, source_name, result.extractor, confidence, result.notes)
        except (OSError, ValueError, RuntimeError):
            with self.lock:
                match.status = MatchStatus.FAILED
                match.error = "cv_analysis_failed"
                self.persist_match(match.match_id)
            logger.exception("CV match processing failed", extra={"match_id": match.match_id})
            raise

    def get_replay(self, match_id: str, after_sequence: int = 0) -> dict:
        with self.lock:
            state = self.matches[match_id]
            if after_sequence < 0 or after_sequence > state.last_sequence:
                raise ValueError("Replay cursor is outside the committed sequence")
            frames = self.replay_frames[match_id]
            if after_sequence:
                frames = [frame for frame in frames if frame["sequence"] > after_sequence]
            return {
                "schema_version": "1.0.0",
                "match_id": match_id,
                "duration_ms": self.match_video_duration_ms.get(match_id, state.match_clock_ms),
                "processed_until_ms": state.match_clock_ms,
                "source_url": f"/api/v1/matches/{match_id}/media/source" if match_id in self.match_video_source_path else None,
                "team_display_names": dict(state.team_display_names),
                "attack_directions": dict(state.attack_directions),
                "metadata": deepcopy(state.analysis_metadata),
                "frames": deepcopy(frames),
                "after_sequence": after_sequence,
                "insights": self.get_serialized_insights(match_id),
            }

    def _video_summary(self, match: MatchState, source_name: str, extractor: str, confidence: float, notes: str) -> dict:
        return {
            "match_id": match.match_id, "source": source_name, "extractor": extractor,
            "extractor_confidence": confidence, "notes": notes,
            "event_count": len(self.events[match.match_id]), "insight_count": len(self.insights[match.match_id]),
            "football_action_count": match.action_count, "tracking_sample_count": match.tracking_sample_count,
            "clips_generated": sum(clip.get("status") == "ready" for clip in self.insight_clip_map[match.match_id].values()),
            "state": match.model_dump(), "insights": self.get_serialized_insights(match.match_id),
        }

    def _finish_video_match(self, match: MatchState, source_name: str, extractor: str, confidence: float, notes: str) -> dict:
        with self.lock:
            match.status = MatchStatus.COMPLETED
            self.persist_match(match.match_id)
            return self._video_summary(match, source_name, extractor, confidence, notes)

    def create_match_from_video(
        self, video_bytes: bytes, source_name: str, use_auto_eventing: bool = True, tenant_id: str = "local",
        operation_id: str | None = None, team_display_names: dict[str, str] | None = None,
        source_provider: str = "upload", source_video_id: str | None = None,
    ) -> dict:
        operation_id = operation_id or uuid4().hex
        try:
            return self._create_video_match(video_bytes, source_name, use_auto_eventing, tenant_id,
                                            operation_id, team_display_names, source_provider, source_video_id)
        except (OSError, ValueError, RuntimeError):
            match_id = f"match_{operation_id}"
            with self.lock:
                state = self.matches.get(match_id)
                if state and state.tenant_id == tenant_id:
                    state.status = MatchStatus.FAILED
                    state.error = "video_analysis_failed"
                    self.persist_match(match_id)
            logger.exception("Video analysis failed", extra={"operation_id": operation_id})
            raise

    def _create_video_match(
        self, video_bytes: bytes, source_name: str, use_auto_eventing: bool, tenant_id: str,
        operation_id: str, team_display_names: dict[str, str] | None,
        source_provider: str, source_video_id: str | None,
    ) -> dict:
        if use_auto_eventing:
            validate_video_container(video_bytes)
        match = self.create_match(scenario_id="video_upload", seed=0, tenant_id=tenant_id, operation_id=operation_id,
                                  team_display_names=team_display_names)
        source_is_video = True
        try:
            validate_video_container(video_bytes)
        except ValueError:
            source_is_video = False
        if source_is_video and use_auto_eventing:
            prepared = normalize_video(video_bytes, 180)
            video_bytes = prepared.content
        if use_auto_eventing and source_is_video:
            try:
                extracted = extract_events_from_video_auto(content=video_bytes, match_id=match.match_id)
            except (RuntimeError, ValueError) as exc:
                logger.warning("Motion auto-eventing unavailable; using synthetic fallback: %s", exc)
                extracted = extract_events_from_video_stub(content=video_bytes, match_id=match.match_id)
        else:
            extracted = extract_events_from_video_stub(content=video_bytes, match_id=match.match_id)

        self.events[match.match_id] = []
        self.insights[match.match_id] = []
        self.matches[match.match_id].status = MatchStatus.RUNNING
        match.analysis_metadata = {
            "input_provider": source_provider, "provider_video_id": source_video_id,
            "extractor": extracted.extractor, "detection_source": "synthetic" if extracted.extractor == "video-stub-v1" else "heuristic",
            "calibrated": False, "notes": extracted.notes,
            "score_source": "unknown",
            "warnings": ["These events are inferred candidates, not validated football detections."],
        }
        if source_is_video and use_auto_eventing:
            match.analysis_metadata["normalized_source"] = True
            match.analysis_metadata["original_duration_ms"] = prepared.original_duration_ms
            match.analysis_metadata["truncated"] = prepared.truncated
            if prepared.truncated:
                match.analysis_metadata["warnings"].append("Analysis and replay are limited to the first 180 seconds.")

        duration_ms = 0
        if use_auto_eventing and source_is_video:
            stored_source = self._store_source_video(match_id=match.match_id, video_bytes=video_bytes,
                                                     source_name=Path(source_name).stem + ".mp4")
            self.match_video_source_path[match.match_id] = stored_source
            duration_ms = self._resolve_video_duration_ms(stored_source)
            if duration_ms > 0:
                self.match_video_duration_ms[match.match_id] = duration_ms

        events = extracted.events
        synthetic_preview = use_auto_eventing and extracted.extractor == "video-stub-v1"
        confidence = 0.0 if synthetic_preview else extracted.confidence
        match.analysis_metadata["extractor_confidence"] = confidence
        if synthetic_preview:
            match.analysis_metadata["source_timed_synthetic_preview"] = True
            match.analysis_metadata["warnings"].append(
                "Synthetic preview events are generated, not observations from this video; they cannot support tactical claims or evidence clips."
            )
            if duration_ms > 0 and events:
                first_ms, last_ms = events[0].match_clock_ms, events[-1].match_clock_ms
                span = max(1, last_ms - first_ms)
                events = [
                    event.model_copy(update={
                        "match_clock_ms": round((event.match_clock_ms - first_ms) / span * max(0, duration_ms - 1))
                    })
                    for event in events
                ]
            else:
                events = []
                match.analysis_metadata["warnings"].append("Source timing unavailable; no synthetic replay timeline was attached.")

        for event in events:
            event.qualifiers["source"] = extracted.extractor
            event.qualifiers["source_confidence"] = confidence
            if extracted.extractor != "video-stub-v1":
                event.qualifiers["candidate_event_type"] = event.type
                event.type = "motion_sample"
                event.player_id = event.target_player_id = None
                event.qualifiers.pop("shot_speed_kmh", None)
                event.qualifiers.pop("ball_speed_kmh", None)
                event.qualifiers.pop("progressive", None)
                event.qualifiers.pop("final_third_entry", None)
            self.record_event(event, persist=False)

        self._build_insight_clips(match_id=match.match_id, insights=self.insights[match.match_id])

        notes = extracted.notes
        if synthetic_preview:
            notes = "Auto-eventing unavailable; source-timed synthetic preview only, not observations or evidence from this video."
        if not use_auto_eventing:
            notes = f"{notes} Clip generation requires a downloadable video source."
        match.analysis_metadata["notes"] = notes

        return self._finish_video_match(match, source_name, extracted.extractor, confidence, notes)

    def get_match(self, match_id: str) -> MatchState:
        return self.matches[match_id]

    def get_state_payload(self, match_id: str) -> dict:
        with self.lock:
            return self.matches[match_id].model_dump()

    def get_events(self, match_id: str) -> list[Event]:
        with self.lock:
            return list(self.events[match_id])

    def get_insights(self, match_id: str) -> list[Insight]:
        return self.insights[match_id]

    def _contextualize_insight(self, match_id: str, insight: Insight) -> Insight:
        supporting = {key for item in insight.evidence for key in item.supporting_event_ids}
        players = set()
        event_types = set()
        for event in self.events[match_id]:
            if event.event_id not in supporting:
                continue
            event_types.add(event.type)
            for player in (event.player_id, event.target_player_id, event.qualifiers.get("created_by")):
                if isinstance(player, str):
                    players.add(player)
            players.update(player["track_id"] for player in event.qualifiers.get("cv_frame", {}).get("players", []))
        scoped = [event for event in self.events[match_id] if insight.window_start_ms <= event.match_clock_ms <= insight.window_end_ms
                  and event.type not in {"tracking_frame", "motion_sample"}]
        own = [event for event in scoped if event.team_id == insight.team_id]
        for event in own:
            players.update(player for player in (event.player_id, event.target_player_id, event.qualifiers.get("created_by"))
                           if isinstance(player, str))
            event_types.add(event.type)
        evidence = list(insight.evidence)
        base_eligible = not self._fails_quality_guardrail(insight)[0]
        for kind, metric in (("pass_completed", "window_passes_completed"), ("carry", "window_carries"), ("recovery", "window_recoveries")):
            samples = [event for event in own if event.type == kind]
            if base_eligible and samples and not any(item.metric == metric for item in evidence):
                evidence.append(EvidenceItem(metric=metric, value=len(samples), unit="count",
                                             supporting_event_ids=[event.event_id for event in samples]))
        return insight.model_copy(update={"evidence": evidence, "context": {
            **insight.context,
            "team_display_names": self.matches[match_id].team_display_names,
            "known_player_ids": sorted(players),
            "event_types": sorted(event_types),
            "phase_events": [{"type": event.type, "player_id": event.player_id, "event_id": event.event_id}
                             for event in own[-6:]],
            "has_pitch_geometry": any(event.type == "tracking_frame" and event.match_clock_ms <= insight.window_end_ms
                                      and event.match_clock_ms >= insight.window_start_ms
                                      and has_pitch_geometry(event.qualifiers.get("cv_frame", {}))
                                      for event in self.events[match_id]),
            "data_source": self.matches[match_id].analysis_metadata.get("detection_source", "synthetic"),
            "analysis_scope": "segment" if self.matches[match_id].scenario_id in {"cv_upload", "video_upload"} else "scenario",
        }})

    def _observation_overview(self, match_id: str, at_ms: int | None) -> Insight:
        state = self.matches[match_id]
        end = state.match_clock_ms if at_ms is None else min(at_ms, state.match_clock_ms)
        samples = [event for event in self.events[match_id] if event.type == "tracking_frame"
                   and max(0, end - 90000) <= event.match_clock_ms <= end]
        if not samples:
            raise ValueError("No tracking samples are available at this playback time")
        frames = [event.qualifiers["cv_frame"] for event in samples]
        observed = sum(bool(frame.get("ball") and not frame["ball"].get("inferred")) for frame in frames)
        predicted = sum(bool(frame.get("ball") and frame["ball"].get("inferred")) for frame in frames)
        players = sum(sum(not player.get("inferred") for player in frame.get("players", [])) for frame in frames)
        references = [event.event_id for event in samples]
        values = {"sampled_frames": len(samples), "observed_ball_frames": observed,
                  "observed_player_samples": players, "predicted_ball_frames": predicted,
                  "ball_observation_rate_pct": round(observed / len(samples) * 100, 1),
                  "calibrated_samples": sum(bool(frame.get("calibrated")) for frame in frames)}
        return Insight(
            insight_id=f"overview_{end}", match_id=match_id, type="tracking_overview", priority="low",
            confidence=0, team_id="unknown", window_start_ms=samples[0].match_clock_ms, window_end_ms=end,
            title="Observation overview", summary="Tracking observations and limitations, not a tactical conclusion.",
            evidence=[EvidenceItem(metric=key, value=value, unit="%" if key.endswith("_pct") else "count",
                                   supporting_event_ids=references) for key, value in values.items()],
            context={"observation_overview": True, "data_source": state.analysis_metadata.get("detection_source"),
                     "analysis_scope": "segment" if state.scenario_id in {"cv_upload", "video_upload"} else "scenario",
                     "has_pitch_geometry": has_pitch_geometry(frames[-1]),
                     "known_player_ids": sorted({player["track_id"] for frame in frames for player in frame.get("players", [])}),
                     "team_display_names": state.team_display_names, "event_types": ["tracking_frame"]},
        )

    def _narration_target(self, match_id: str, insight_id: str | None, at_ms: int | None, depth: str, overview: bool = False) -> Insight:
        if depth not in {"broadcast", "deep"}:
            raise ValueError("Commentary depth must be broadcast or deep")
        state = self.matches[match_id]
        limit = self.match_video_duration_ms.get(match_id, state.match_clock_ms)
        if at_ms is not None and (at_ms < 0 or at_ms > limit + 1):
            raise ValueError("Commentary time lies outside the available replay")
        if overview:
            if insight_id:
                raise ValueError("Select a supported moment or an observation overview, not both")
            target = self._observation_overview(match_id, at_ms)
            target.context["depth"] = depth
            return target
        eligible = [item for item in self.insights[match_id] if at_ms is None or item.window_end_ms <= at_ms]
        if insight_id:
            target = next((item for item in self.insights[match_id] if item.insight_id == insight_id), None)
            if target is None:
                raise ValueError("Insight not found")
            if at_ms is not None and target.window_end_ms > at_ms:
                raise ValueError("The selected insight has not occurred at this playback time")
        else:
            supported = [item for item in eligible if not self._fails_quality_guardrail(item)[0]]
            target = supported[-1] if supported else None
            if target is None and any(event.type == "tracking_frame" for event in self.events[match_id]):
                target = self._observation_overview(match_id, at_ms)
            elif target is None:
                if not eligible:
                    raise ValueError("No insights available for commentary generation")
                target = eligible[-1]
        if target.type != "tracking_overview":
            target = self._contextualize_insight(match_id, target)
        target.context["depth"] = depth
        return target

    def generate_narrative(
        self,
        match_id: str,
        audience_mode: str,
        language: str,
        insight_id: str | None = None,
        selected_player_id: str | None = None,
        depth: str = "deep",
        at_ms: int | None = None,
        allow_cloud: bool = True,
        overview: bool = False,
    ) -> NarrativeResult:
        if audience_mode == "player" and not selected_player_id:
            selected_player_id = self.viewer_profiles.get(match_id, {}).get("favorite_player_id")

        target = self._narration_target(match_id, insight_id, at_ms, depth, overview)
        copy = language_copy(language)

        if audience_mode == "player":
            if not selected_player_id:
                raise ValueError("Select a player for player-focused narration")
            participating = [
                event for event in self.events[match_id]
                if any(event.event_id in item.supporting_event_ids for item in target.evidence)
                and selected_player_id in {event.player_id, event.target_player_id, event.qualifiers.get("created_by")}
            ]
            if not participating:
                return NarrativeResult(
                    title=copy["focus"], body=copy["no_player"].format(player=selected_player_id),
                    audience_mode=audience_mode, language=language, fallback_used=True,
                    provider="guardrail-player-scope", grounding={"status": "no-supported-player-contribution"},
                )
            target.context["supported_player_id"] = selected_player_id

        fails_guardrail, reason = (False, "observation_facts_only") if target.type == "tracking_overview" else self._fails_quality_guardrail(target)
        if fails_guardrail:
            return NarrativeResult(
                title=copy["evidence_check"],
                body=copy["insufficient"],
                audience_mode=audience_mode,
                language=language,
                fallback_used=True,
                provider=f"guardrail-{reason}",
                grounding={"status": reason},
            )

        key = self._generation_key(target, "single", language, audience_mode, selected_player_id or "")
        cached = self._cached_generation(key) if allow_cloud else None
        if isinstance(cached, NarrativeResult):
            return cached
        generator = self.narrative_generator if allow_cloud else DeterministicNarrativeGenerator()
        result = generator.generate(
            insight=target,
            audience_mode=audience_mode,
            language=language,
            selected_player_id=selected_player_id,
        )
        self._cache_generation(key, result)
        return result

    def generate_dual_commentary(
        self,
        match_id: str,
        language: str,
        insight_id: str | None = None,
        depth: str = "deep",
        at_ms: int | None = None,
        overview: bool = False,
    ) -> DualCommentaryResult:
        target = self._narration_target(match_id, insight_id, at_ms, depth, overview)
        copy = language_copy(language)

        fails_guardrail, reason = (False, "observation_facts_only") if target.type == "tracking_overview" else self._fails_quality_guardrail(target)
        if fails_guardrail:
            result = DualCommentaryResult(
                primary_text=copy["insufficient"],
                secondary_text=copy["secondary"],
                language=language,
                fallback_used=True,
                provider=f"guardrail-{reason}",
                audit={
                    "orchestration_path": "guardrail",
                    "reason": reason,
                    "provenance": self._insight_provenance(target),
                    "agents": [],
                },
            )
            entry = {
                "call_id": f"commentary_{uuid4().hex[:12]}",
                "timestamp_utc": datetime.now(tz=timezone.utc).isoformat(),
                "match_id": match_id,
                "insight_id": target.insight_id,
                "language": language,
                "provider": result.provider,
                "fallback_used": result.fallback_used,
                "primary_text": result.primary_text,
                "secondary_text": result.secondary_text,
                "audit": result.audit or {},
            }
            self.commentary_audit_log[match_id].append(entry)
            self.commentary_audit_log[match_id] = self.commentary_audit_log[match_id][-self.commentary_audit_max_entries :]
            self.persist_match(match_id)
            return result

        key = self._generation_key(target, "dual", language)
        cached = self._cached_generation(key)
        if isinstance(cached, DualCommentaryResult):
            return cached
        result = self.narrative_generator.generate_dual_commentary(
            insight=target,
            language=language,
        )
        audit_payload = dict(result.audit or {})
        audit_payload["provenance"] = self._insight_provenance(target)
        result.audit = audit_payload
        self._cache_generation(key, result)

        entry = {
            "call_id": f"commentary_{uuid4().hex[:12]}",
            "timestamp_utc": datetime.now(tz=timezone.utc).isoformat(),
            "match_id": match_id,
            "insight_id": target.insight_id,
            "language": language,
            "provider": result.provider,
            "fallback_used": result.fallback_used,
            "primary_text": result.primary_text,
            "secondary_text": result.secondary_text,
            "turns": [turn.model_dump() for turn in result.turns],
            "depth": result.depth,
            "audit": result.audit or {},
        }
        self.commentary_audit_log[match_id].append(entry)
        self.commentary_audit_log[match_id] = self.commentary_audit_log[match_id][-self.commentary_audit_max_entries :]

        self.persist_match(match_id)
        return result

    def get_commentary_audit(self, match_id: str, limit: int = 20) -> list[dict[str, Any]]:
        safe_limit = max(1, min(limit, self.commentary_audit_max_entries))
        entries = self.commentary_audit_log.get(match_id, [])
        return list(reversed(entries[-safe_limit:]))

    def set_viewer_profile(
        self,
        match_id: str,
        favorite_team_id: str | None = None,
        favorite_player_id: str | None = None,
        focus_metric: str | None = None,
    ) -> dict[str, str]:
        profile = self.viewer_profiles[match_id]
        if favorite_team_id:
            profile["favorite_team_id"] = favorite_team_id
        if favorite_player_id:
            profile["favorite_player_id"] = favorite_player_id
        if focus_metric:
            profile["focus_metric"] = focus_metric
        self.persist_match(match_id)
        return profile

    def get_overlay_payload(self, match_id: str) -> dict:
        state = self.matches[match_id]
        queue = self._overlay_priority_queue(self.insights[match_id], state.match_clock_ms)
        top = queue[0] if queue else None
        latest_insight = self.insights[match_id][-1] if self.insights[match_id] else None

        return {
            "overlay_version": "1.0.0",
            "match_id": match_id,
            "clock_ms": state.match_clock_ms,
            "status": state.status.value,
            "score": state.score,
            "team_display_names": state.team_display_names,
            "possession_team_id": state.possession_team_id,
            "metrics_by_team": state.metrics_by_team,
            "cv_live": state.cv_live,
            "tactical_state": state.tactical_state,
            "analysis_metadata": state.analysis_metadata,
            "current_insight": (
                {
                    **top["insight"],
                    "provenance": top["provenance"],
                    "overlay_score": top["score"],
                }
                if top
                else None
            ),
            "overlay_queue": queue,
            "viewer_profile": self.viewer_profiles.get(match_id, {}),
            "render_hints": {
                "placement": "lower-third",
                "priority": (top["insight"].get("priority") if top else (latest_insight.priority if latest_insight else "low")),
                "machine_readable": True,
                "queue_limit": self.overlay_queue_limit,
            },
        }

    def generate_recap(
        self,
        match_id: str,
        audience_mode: str,
        language: str,
        selected_player_id: str | None = None,
    ) -> tuple[NarrativeResult, list[Insight], list[dict[str, Any]]]:
        insights = self.insights[match_id]
        if not insights:
            raise ValueError("No insights available for recap generation")

        eligible = [item for item in insights if not self._fails_quality_guardrail(item)[0]]
        copy = language_copy(language)
        if audience_mode == "player":
            selected_player_id = selected_player_id or self.viewer_profiles[match_id].get("favorite_player_id")
            if not selected_player_id:
                raise ValueError("Select a player for player-focused recaps")
            player_events = {event.event_id for event in self.events[match_id]
                             if selected_player_id in {event.player_id, event.target_player_id, event.qualifiers.get("created_by")}}
            eligible = [item for item in eligible
                        if any(player_events.intersection(evidence.supporting_event_ids) for evidence in item.evidence)]
        if not eligible:
            return NarrativeResult(
                title=copy["post_match_recap"][0],
                body=copy["no_player"].format(player=selected_player_id) if audience_mode == "player" else copy["insufficient"],
                audience_mode=audience_mode, language=language, fallback_used=True, provider="guardrail-recap-evidence",
                grounding={"status": "insufficient_recap_evidence"},
            ), [], []
        ranked = sorted(eligible, key=lambda item: (item.priority == "high", item.confidence), reverse=True)
        top_insights = ranked[:3]

        recap_insight = Insight(
            match_id=match_id,
            type="post_match_recap",
            priority="high",
            confidence=mean([item.confidence for item in top_insights]),
            team_id=top_insights[0].team_id,
            window_start_ms=min(item.window_start_ms for item in insights),
            window_end_ms=self.matches[match_id].match_clock_ms,
            title="Post-Match Recap",
            summary=(
                f"Analysis produced {len(eligible)} supported tactical insight(s). "
                f"Top patterns: {', '.join(item.title for item in top_insights)}."
            ),
            evidence=[evidence for item in top_insights for evidence in item.evidence],
        )
        recap_insight = self._contextualize_insight(match_id, recap_insight)
        if audience_mode == "player":
            recap_insight.context["supported_player_id"] = selected_player_id

        generator = self.narrative_generator if not self._fails_quality_guardrail(recap_insight)[0] else DeterministicNarrativeGenerator()
        recap = generator.generate(
            insight=recap_insight,
            audience_mode=audience_mode,
            language=language,
            selected_player_id=selected_player_id,
        )
        chapters = [
            {
                "chapter_id": f"chapter_{index + 1}",
                "title": localized_insight(self._contextualize_insight(match_id, item), language)[0],
                "summary": localized_insight(self._contextualize_insight(match_id, item), language)[1],
                "window_start_ms": item.window_start_ms,
                "window_end_ms": item.window_end_ms,
                "confidence": item.confidence,
                "team_id": item.team_id,
                "type": item.type,
                "provenance": self._insight_provenance(item),
            }
            for index, item in enumerate(top_insights)
        ]
        return recap, top_insights, chapters

    def synthesize_speech(
        self,
        text: str,
        language: str,
        voice: str | None = None,
        rate: str = "+2%",
        pitch: str = "+1st",
        tenant_id: str = "local",
    ) -> SpeechResult:
        return self.speech_synthesizer.synthesize(
            text=text,
            language=language,
            voice=voice,
            rate=rate,
            pitch=pitch,
            tenant_id=tenant_id,
        )

    def synthesize_commentary_turns(
        self, turns: list[CommentaryTurn], language: str, primary_voice: str | None = None, secondary_voice: str | None = None,
        tenant_id: str = "local",
    ) -> SpeechResult:
        return self.speech_synthesizer.synthesize_turns(
            turns, language, primary_voice, secondary_voice, tenant_id=tenant_id,
        )

    def synthesize_dual_commentary(
        self,
        primary_text: str,
        secondary_text: str,
        language: str,
        primary_voice: str | None = None,
        secondary_voice: str | None = None,
        tenant_id: str = "local",
    ) -> SpeechResult:
        return self.speech_synthesizer.synthesize_dual_commentary(
            primary_text=primary_text,
            secondary_text=secondary_text,
            language=language,
            primary_voice=primary_voice,
            secondary_voice=secondary_voice,
            tenant_id=tenant_id,
        )

    async def subscribe(self, match_id: str) -> asyncio.Queue[dict]:
        if len(self.subscribers[match_id]) >= 16:
            raise RuntimeError("Match subscriber limit reached")
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=128)
        self.subscribers[match_id].add(queue)
        await queue.put({"type": "connection.ready", "match_id": match_id})
        return queue

    def unsubscribe(self, match_id: str, queue: asyncio.Queue[dict]) -> None:
        self.subscribers[match_id].discard(queue)

    async def _publish(self, match_id: str, message: dict) -> None:
        for queue in tuple(self.subscribers[match_id]):
            if queue.full():
                logger.warning("Slow match subscriber requires resynchronization", extra={"match_id": match_id})
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait({"type": "resync.required", "payload": {"last_sequence": self.matches[match_id].last_sequence}})
            else:
                queue.put_nowait(message)

    @staticmethod
    async def _scenario_work(operation: Callable[..., Any], *args, **kwargs) -> Any:
        worker = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            while not worker.done():
                try:
                    await asyncio.shield(worker)
                except asyncio.CancelledError:
                    continue
            worker.result()
            raise

    async def start_match(self, match_id: str) -> None:
        state = self.get_match(match_id)
        if state.status == MatchStatus.RUNNING:
            return
        if state.status in {MatchStatus.COMPLETED, MatchStatus.FAILED}:
            raise ValueError("Create a new match instead of restarting a finished match")
        if len(self.tasks) >= 8:
            raise MatchCapacityError("Live scenario capacity reached")
        state.status = MatchStatus.RUNNING

        async def runner() -> None:
            try:
                await self._scenario_work(self.persist_match, match_id)
                scenario_events = await self._scenario_work(
                    scenario_timeline, state.scenario_id, match_id, state.seed, state.scenario_recipe_version,
                )
                pending = 0
                for event in scenario_events:
                    if event.sequence <= state.last_sequence:
                        continue
                    if state.status != MatchStatus.RUNNING:
                        break
                    new_insights = await self._scenario_work(
                        self.record_event, event, persist=state.scenario_recipe_version == 1,
                    )
                    pending += 1
                    if event.type != "tracking_frame":
                        await self._publish(match_id, {"type": "event.created", "payload": event.model_dump()})
                    if state.scenario_recipe_version == 1 or pending >= 16:
                        await self._publish(match_id, {"type": "state.updated", "payload": self.get_state_payload(match_id)})
                        pending = 0
                    for insight in new_insights:
                        await self._publish(match_id, {"type": "insight.created", "payload": self._serialize_insight(match_id, insight)})
                    if state.scenario_recipe_version == 1 or pending == 0:
                        await asyncio.sleep(.35 if state.scenario_recipe_version == 1 else .005)
                if state.status == MatchStatus.RUNNING:
                    state.status = MatchStatus.COMPLETED
            except asyncio.CancelledError:
                state.status = MatchStatus.PAUSED
                raise
            except Exception:
                logger.exception("Scenario execution failed", extra={"match_id": match_id})
                state.status = MatchStatus.FAILED
                state.error = "scenario_execution_failed"
            finally:
                try:
                    await self._scenario_work(self.persist_match, match_id)
                except Exception:
                    logger.exception("Scenario persistence failed", extra={"match_id": match_id})
                    state.status = MatchStatus.FAILED
                    state.error = "scenario_persistence_failed"
                    raise
                finally:
                    self.tasks.pop(match_id, None)
                await self._publish(match_id, {"type": "state.updated", "payload": self.get_state_payload(match_id)})
                await self._publish(match_id, {"type": "match.status", "payload": {"status": state.status.value}})

        self.tasks[match_id] = asyncio.create_task(runner())

    async def pause_match(self, match_id: str) -> None:
        task = self.tasks.get(match_id)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


_local_service: MatchService | None = None


def __getattr__(name: str):
    # Importing the computation class in a cloud worker must not open local data.
    if name != "service":
        raise AttributeError(name)
    global _local_service
    if _local_service is None:
        _local_service = MatchService()
    return _local_service
