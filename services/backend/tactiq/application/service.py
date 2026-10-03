from __future__ import annotations

import asyncio
from collections import defaultdict
from statistics import mean
from uuid import uuid4

from tactiq.agents.narrative import (
    DeterministicNarrativeGenerator,
    DualCommentaryResult,
    FoundryNarrativeGenerator,
    NarrativeResult,
)
from tactiq.agents.speech import AzureSpeechSynthesizer, SpeechResult
from tactiq.domain.models import Event, Insight, MatchState, MatchStatus
from tactiq.intelligence.engine import detect_insights, reduce_match_state
from tactiq.simulator.scenarios import load_scenario
from tactiq.video.extractor import extract_events_from_video_auto, extract_events_from_video_stub


class MatchService:
    def __init__(self) -> None:
        self.matches: dict[str, MatchState] = {}
        self.events: dict[str, list[Event]] = defaultdict(list)
        self.insights: dict[str, list[Insight]] = defaultdict(list)
        self.subscribers: dict[str, set[asyncio.Queue[dict]]] = defaultdict(set)
        self.tasks: dict[str, asyncio.Task] = {}
        self.viewer_profiles: dict[str, dict[str, str]] = defaultdict(dict)
        self.narrative_generator = FoundryNarrativeGenerator(DeterministicNarrativeGenerator())
        self.speech_synthesizer = AzureSpeechSynthesizer()

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

        notes = extracted.notes
        if use_auto_eventing and extracted.extractor == "video-stub-v1":
            notes = "Auto-eventing unavailable for this clip/runtime; deterministic stub extraction was used."

        return {
            "match_id": match.match_id,
            "source": source_name,
            "extractor": extracted.extractor,
            "extractor_confidence": extracted.confidence,
            "notes": notes,
            "event_count": len(self.events[match.match_id]),
            "insight_count": len(self.insights[match.match_id]),
            "state": self.matches[match.match_id].model_dump(),
            "insights": [item.model_dump() for item in self.insights[match.match_id]],
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

        return self.narrative_generator.generate_dual_commentary(
            insight=target,
            language=language,
        )

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
                    "insight_id": latest_insight.insight_id,
                    "type": latest_insight.type,
                    "priority": latest_insight.priority,
                    "confidence": latest_insight.confidence,
                    "team_id": latest_insight.team_id,
                    "title": latest_insight.title,
                    "summary": latest_insight.summary,
                    "window_start_ms": latest_insight.window_start_ms,
                    "window_end_ms": latest_insight.window_end_ms,
                    "evidence": [
                        {
                            "metric": item.metric,
                            "value": item.value,
                            "unit": item.unit,
                        }
                        for item in latest_insight.evidence
                    ],
                }
                if latest_insight
                else None
            ),
            "viewer_profile": self.viewer_profiles.get(match_id, {}),
            "render_hints": {
                "placement": "lower-third",
                "priority": latest_insight.priority if latest_insight else "low",
                "machine_readable": True,
            },
        }

    def generate_recap(
        self,
        match_id: str,
        audience_mode: str,
        language: str,
        selected_player_id: str | None = None,
    ) -> tuple[NarrativeResult, list[Insight]]:
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
        return recap, top_insights

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
                    await self._publish(match_id, {"type": "insight.created", "payload": insight.model_dump()})

                await asyncio.sleep(0.35)

            self.matches[match_id].status = MatchStatus.COMPLETED
            await self._publish(match_id, {"type": "match.status", "payload": {"status": MatchStatus.COMPLETED}})

        self.tasks[match_id] = asyncio.create_task(runner())


service = MatchService()
