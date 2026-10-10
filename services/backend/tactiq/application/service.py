from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import shutil
from statistics import mean
import subprocess
from typing import Any
from uuid import uuid4

from tactiq.agents.narrative import (
    DeterministicNarrativeGenerator,
    DualCommentaryResult,
    FoundryNarrativeGenerator,
    NarrativeResult,
)
from tactiq.agents.speech import AzureSpeechSynthesizer, SpeechResult
from tactiq.domain.models import Event, Insight, MatchState, MatchStatus
from tactiq.intelligence.engine import detect_insights, insight_priority_weight, reduce_match_state
from tactiq.simulator.scenarios import load_scenario
from tactiq.video.extractor import extract_events_from_video_auto, extract_events_from_video_stub


class MatchService:
    @staticmethod
    def _env_int(name: str, default: int) -> int:
        raw = (os.getenv(name) or "").strip()
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError:
            return default

    @staticmethod
    def _env_float(name: str, default: float) -> float:
        raw = (os.getenv(name) or "").strip()
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError:
            return default

    def __init__(self) -> None:
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
        self.clip_pre_roll_ms = self._env_int("TACTIQ_CLIP_PRE_ROLL_MS", 6000)
        self.clip_post_roll_ms = self._env_int("TACTIQ_CLIP_POST_ROLL_MS", 4000)
        self.clip_max_per_match = self._env_int("TACTIQ_CLIP_MAX_PER_MATCH", 5)
        self.clip_min_confidence = self._env_float("TACTIQ_CLIP_MIN_CONFIDENCE", 0.7)
        media_root_env = os.getenv("TACTIQ_MEDIA_DIR", "").strip()
        default_media_root = Path(__file__).resolve().parents[3] / ".tactiq_media"
        self.media_root = Path(media_root_env) if media_root_env else default_media_root
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
        self.narrative_generator = FoundryNarrativeGenerator(DeterministicNarrativeGenerator())
        self.speech_synthesizer = AzureSpeechSynthesizer()

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
            end_ms = min(end_ms, duration_ms)
        if end_ms <= start_ms:
            end_ms = start_ms + 2500

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
            "reason_code": self._insight_reason_code(insight.type),
            "window": {
                "start_ms": insight.window_start_ms,
                "end_ms": insight.window_end_ms,
            },
            "confidence": insight.confidence,
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
        return False, "ok"

    @staticmethod
    def _normalize_text(text: str) -> str:
        lowered = text.lower().strip()
        lowered = re.sub(r"[^a-z0-9\s]", " ", lowered)
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
            suffix = "fresh phase developing" if role == "lead" else "new tactical wrinkle emerging"
            line = f"{line.rstrip('.')} — {suffix}."
        memory.append(line)
        self.commentary_memory[match_id] = memory[-self.commentary_memory_max :]
        return line

    def _overlay_priority_queue(self, insights: list[Insight], match_clock_ms: int, limit: int | None = None) -> list[dict[str, Any]]:
        if not insights:
            return []

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

    def create_match(self, scenario_id: str, seed: int) -> MatchState:
        match_id = f"match_{uuid4().hex[:8]}"
        state = MatchState(match_id=match_id, scenario_id=scenario_id, seed=seed)
        self.matches[match_id] = state
        return state

    def create_match_from_video(self, video_bytes: bytes, source_name: str, use_auto_eventing: bool = True) -> dict:
        match = self.create_match(scenario_id="video_upload", seed=0)
        if use_auto_eventing:
            try:
                extracted = extract_events_from_video_auto(content=video_bytes, match_id=match.match_id)
            except Exception:  # noqa: BLE001
                extracted = extract_events_from_video_stub(content=video_bytes, match_id=match.match_id)
        else:
            extracted = extract_events_from_video_stub(content=video_bytes, match_id=match.match_id)

        self.events[match.match_id] = []
        self.insights[match.match_id] = []
        self.matches[match.match_id].status = MatchStatus.RUNNING

        for event in extracted.events:
            self.events[match.match_id].append(event)
            reduce_match_state(self.matches[match.match_id], event, self.events[match.match_id])
            new_insights = detect_insights(
                self.matches[match.match_id],
                self.events[match.match_id],
                self.insights[match.match_id],
            )
            if new_insights:
                self.insights[match.match_id].extend(new_insights)

        self.matches[match.match_id].status = MatchStatus.COMPLETED

        if use_auto_eventing:
            try:
                stored_source = self._store_source_video(match_id=match.match_id, video_bytes=video_bytes, source_name=source_name)
                self.match_video_source_path[match.match_id] = stored_source
                duration_ms = self._resolve_video_duration_ms(stored_source)
                if duration_ms > 0:
                    self.match_video_duration_ms[match.match_id] = duration_ms
            except Exception:  # noqa: BLE001
                self.match_video_source_path.pop(match.match_id, None)

        self._build_insight_clips(match_id=match.match_id, insights=self.insights[match.match_id])

        notes = extracted.notes
        if use_auto_eventing and extracted.extractor == "video-stub-v1":
            notes = "Auto-eventing unavailable for this clip/runtime; deterministic stub extraction was used."
        if not use_auto_eventing:
            notes = f"{notes} Clip generation requires a downloadable video source."

        clip_ready_count = sum(
            1
            for clip in self.insight_clip_map.get(match.match_id, {}).values()
            if clip.get("status") == "ready"
        )

        return {
            "match_id": match.match_id,
            "source": source_name,
            "extractor": extracted.extractor,
            "extractor_confidence": extracted.confidence,
            "notes": notes,
            "event_count": len(self.events[match.match_id]),
            "insight_count": len(self.insights[match.match_id]),
            "clips_generated": clip_ready_count,
            "state": self.matches[match.match_id].model_dump(),
            "insights": self.get_serialized_insights(match.match_id),
        }

    def get_match(self, match_id: str) -> MatchState:
        return self.matches[match_id]

    def get_events(self, match_id: str) -> list[Event]:
        return self.events[match_id]

    def get_insights(self, match_id: str) -> list[Insight]:
        return self.insights[match_id]

    def generate_narrative(
        self,
        match_id: str,
        audience_mode: str,
        language: str,
        insight_id: str | None = None,
        selected_player_id: str | None = None,
    ) -> NarrativeResult:
        insights = self.insights[match_id]
        if not insights:
            raise ValueError("No insights available for narrative generation")

        if audience_mode == "player" and not selected_player_id:
            selected_player_id = self.viewer_profiles.get(match_id, {}).get("favorite_player_id")

        if insight_id:
            target = next((item for item in insights if item.insight_id == insight_id), None)
            if target is None:
                raise ValueError("Insight not found")
        else:
            target = insights[-1]

        fails_guardrail, reason = self._fails_quality_guardrail(target)
        if fails_guardrail:
            safe_body = (
                "The current phase is still forming. We need stronger evidence before making a confident tactical claim."
            )
            return NarrativeResult(
                title="Evidence Check",
                body=safe_body,
                audience_mode=audience_mode,
                language=language,
                fallback_used=True,
                provider=f"guardrail-{reason}",
            )

        return self.narrative_generator.generate(
            insight=target,
            audience_mode=audience_mode,
            language=language,
            selected_player_id=selected_player_id,
        )

    def generate_dual_commentary(
        self,
        match_id: str,
        language: str,
        insight_id: str | None = None,
    ) -> DualCommentaryResult:
        insights = self.insights[match_id]
        if not insights:
            raise ValueError("No insights available for commentary generation")

        if insight_id:
            target = next((item for item in insights if item.insight_id == insight_id), None)
            if target is None:
                raise ValueError("Insight not found")
        else:
            target = insights[-1]

        fails_guardrail, reason = self._fails_quality_guardrail(target)
        if fails_guardrail:
            result = DualCommentaryResult(
                primary_text="This phase needs more evidence before we call it decisively.",
                secondary_text="Agreed. We'll stay grounded and update once the pattern is validated.",
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
            return result

        result = self.narrative_generator.generate_dual_commentary(
            insight=target,
            language=language,
        )
        result.primary_text = self._anti_repeat_line(match_id, result.primary_text, role="lead")
        result.secondary_text = self._anti_repeat_line(match_id, result.secondary_text, role="analyst")

        audit_payload = dict(result.audit or {})
        audit_payload["provenance"] = self._insight_provenance(target)
        result.audit = audit_payload

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
            "possession_team_id": state.possession_team_id,
            "metrics_by_team": state.metrics_by_team,
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

        ranked = sorted(insights, key=lambda item: item.confidence, reverse=True)
        top_insights = ranked[:3]

        recap_insight = Insight(
            match_id=match_id,
            type="post_match_recap",
            priority="high",
            confidence=mean([item.confidence for item in top_insights]),
            team_id=top_insights[0].team_id,
            window_start_ms=min(item.window_start_ms for item in insights),
            window_end_ms=max(item.window_end_ms for item in insights),
            title="Post-Match Recap",
            summary=(
                f"Match produced {len(insights)} tactical insight(s). "
                f"Top patterns: {', '.join(item.title for item in top_insights)}."
            ),
            evidence=[evidence for item in top_insights for evidence in item.evidence],
        )

        recap = self.narrative_generator.generate(
            insight=recap_insight,
            audience_mode=audience_mode,
            language=language,
            selected_player_id=selected_player_id,
        )
        chapters = [
            {
                "chapter_id": f"chapter_{index + 1}",
                "title": item.title,
                "summary": item.summary,
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
    ) -> SpeechResult:
        return self.speech_synthesizer.synthesize(
            text=text,
            language=language,
            voice=voice,
            rate=rate,
            pitch=pitch,
        )

    def synthesize_dual_commentary(
        self,
        primary_text: str,
        secondary_text: str,
        language: str,
        primary_voice: str | None = None,
        secondary_voice: str | None = None,
    ) -> SpeechResult:
        return self.speech_synthesizer.synthesize_dual_commentary(
            primary_text=primary_text,
            secondary_text=secondary_text,
            language=language,
            primary_voice=primary_voice,
            secondary_voice=secondary_voice,
        )

    async def subscribe(self, match_id: str) -> asyncio.Queue[dict]:
        queue: asyncio.Queue[dict] = asyncio.Queue()
        self.subscribers[match_id].add(queue)
        await queue.put({"type": "connection.ready", "match_id": match_id})
        return queue

    def unsubscribe(self, match_id: str, queue: asyncio.Queue[dict]) -> None:
        self.subscribers[match_id].discard(queue)

    async def _publish(self, match_id: str, message: dict) -> None:
        for queue in self.subscribers[match_id]:
            await queue.put(message)

    async def start_match(self, match_id: str) -> None:
        state = self.get_match(match_id)
        if state.status == MatchStatus.RUNNING:
            return

        state.status = MatchStatus.RUNNING

        async def runner() -> None:
            _, scenario_events = load_scenario(state.scenario_id, match_id)
            for event in scenario_events:
                if self.matches[match_id].status != MatchStatus.RUNNING:
                    break

                self.events[match_id].append(event)
                reduce_match_state(self.matches[match_id], event, self.events[match_id])

                new_insights = detect_insights(
                    self.matches[match_id],
                    self.events[match_id],
                    self.insights[match_id],
                )
                if new_insights:
                    self.insights[match_id].extend(new_insights)

                await self._publish(match_id, {"type": "event.created", "payload": event.model_dump()})
                await self._publish(match_id, {"type": "state.updated", "payload": self.matches[match_id].model_dump()})
                for insight in new_insights:
                    await self._publish(
                        match_id,
                        {
                            "type": "insight.created",
                            "payload": self._serialize_insight(match_id, insight),
                        },
                    )

                await asyncio.sleep(0.35)

            self.matches[match_id].status = MatchStatus.COMPLETED
            await self._publish(match_id, {"type": "match.status", "payload": {"status": MatchStatus.COMPLETED}})

        self.tasks[match_id] = asyncio.create_task(runner())


service = MatchService()
