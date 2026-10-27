"""Rebuild only the deliberate public synthetic example; never read workspace data."""
from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

from tactiq.agents.localization import LANGUAGES
from tactiq.agents.narrative import DeterministicNarrativeGenerator
from tactiq.application.service import MatchService
from tactiq.domain.models import MatchStatus
from tactiq.simulator.scenarios import scenario_timeline

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "apps" / "web" / "public" / "examples" / "pressure-v1.json"


def build_example() -> dict:
    with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
        "APP_ENV": "local", "TACTIQ_VALUE_MODEL_PATH": "",
        "NARRATIVE_MIN_CONFIDENCE": "0.6", "NARRATIVE_MIN_EVIDENCE_ITEMS": "2",
    }):
        service = MatchService(media_root=Path(directory), paid_providers=False)
        match = service.create_match(
            "sustained_pressure", 42001, tenant_id="public-example", operation_id="0" * 32,
            team_display_names={"team_a": "Team A", "team_b": "Team B"},
        )
        for event in scenario_timeline(match.scenario_id, match.match_id, match.seed, match.scenario_recipe_version):
            service.record_event(event, persist=False)
        match.status = MatchStatus.COMPLETED
        replay = service.get_replay(match.match_id)
        # A public catalogue key, not an identifier from a user's workspace.
        replay["match_id"] = "public-pressure-v1"
        replay.pop("attack_directions", None)
        # Two samples/second preserve the renderer's 500 ms interpolation bound.
        replay["frames"] = [frame for index, frame in enumerate(replay["frames"])
                            if index % 2 == 0 or index == len(replay["frames"]) - 1]
        replay["metadata"]["tracking_sample_fps"] = 2
        replay["metadata"]["notes"] += " Public replay sampled at 2 fps; positions rounded to 0.001 normalized units. Metrics use original events."
        for frame in replay["frames"]:
            frame["tactics"] = {key: value for key, value in frame["tactics"].items()
                                if key in {"phase", "chain_count", "lanes", "team_shapes", "value_model"}}
            tracking = frame["tracking"]
            tracking.pop("calibration", None)
            for entity in [*tracking["players"], tracking.get("ball")]:
                if entity:
                    entity.pop("image_position", None)
                    entity.pop("team_confidence", None)
                    if entity.get("position"):
                        entity["position"] = {key: round(value, 3) for key, value in entity["position"].items()}
        insights = []
        generator = DeterministicNarrativeGenerator()
        public_events = {event.event_id: f"event-{event.sequence}" for event in service.get_events(match.match_id)}
        for index, original in enumerate(service.get_insights(match.match_id)):
            if service._fails_quality_guardrail(original)[0]:
                continue
            scoped = service._contextualize_insight(match.match_id, original)
            public_id = f"moment-{index + 1}"
            narratives = {}
            for language in LANGUAGES:
                narratives[language] = {}
                for audience in ("fan", "analyst"):
                    result = asdict(generator.generate(scoped, audience, language))
                    if audience == "analyst":
                        paragraphs = result["body"].split("\n\n")
                        if len(paragraphs) > 1:
                            paragraphs.insert(0, paragraphs.pop(1))
                        result["body"] = "\n\n".join(paragraphs)
                    narratives[language][audience] = result
            insights.append({
                "insight_id": public_id, "type": scoped.type, "title": scoped.title,
                "summary": scoped.summary, "confidence": scoped.confidence,
                "evidence": [{"metric": item.metric, "value": item.value} for item in scoped.evidence],
                "provenance": {
                    "window": {"start_ms": scoped.window_start_ms, "end_ms": scoped.window_end_ms},
                    "metrics": [{"metric": item.metric, "value": item.value, "unit": item.unit} for item in scoped.evidence],
                },
                "support": [{"metric": item.metric,
                             "event_ids": [public_events[key] for key in item.supporting_event_ids]}
                            for item in scoped.evidence],
                "events": [{"event_id": public_events[event.event_id], "type": event.type, "match_clock_ms": event.match_clock_ms}
                           for event in service.get_events(match.match_id)
                           if scoped.window_start_ms <= event.match_clock_ms <= scoped.window_end_ms
                           and (event.type not in {"tracking_frame", "motion_sample"}
                                or any(event.event_id in item.supporting_event_ids for item in scoped.evidence))],
                "narratives": narratives,
            })
        replay["insights"] = [
            {"insight_id": item["insight_id"], "title": item["title"],
             "window_start_ms": item["provenance"]["window"]["start_ms"],
             "window_end_ms": item["provenance"]["window"]["end_ms"]}
            for item in insights
        ]
        return {
            "version": 1, "id": "public-pressure-v1", "scenario": "sustained_pressure",
            "seed": 42001, "recipe_version": match.scenario_recipe_version,
            "provenance": {
                "source": "project-generated synthetic events and positions",
                "narratives": "prepared deterministic domain templates; not model-generated",
                "agent_workflow": "not used for these example narratives",
                "limitations": "Illustrates a scripted sequence, not validated real-match football accuracy.",
            },
            "replay": replay, "insights": insights,
            "audio": {
                "insight_id": next(item["insight_id"] for item in insights if item["type"] == "sustained_pressure"),
                "language": "en-GB", "audience": "fan", "url": "/examples/pressure-en.wav",
                "provider": "offline Windows speech",
                "note": "Prepared offline English narration of this exact Fan explanation; not Azure Speech or model output.",
            },
            "recaps": {language: {audience: asdict(service.generate_recap(match.match_id, audience, language)[0])
                                  for audience in ("fan", "analyst")} for language in LANGUAGES},
        }


if __name__ == "__main__":
    payload = build_example()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"Built {len(payload['replay']['frames'])} frames and {len(payload['insights'])} supported moments")
