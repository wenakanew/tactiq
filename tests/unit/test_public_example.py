import hashlib
import json
from pathlib import Path
import wave

from tools.build_public_example import OUTPUT, build_example
from tactiq.agents.localization import LANGUAGES
from tactiq.agents.narrative import FoundryNarrativeGenerator
from tactiq.domain.models import Event
from pydantic import ValidationError
import pytest


def test_public_package_is_reproducible_without_paid_provider(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Public artifact must never call a paid provider")

    monkeypatch.setattr(FoundryNarrativeGenerator, "_call_foundry", forbidden)
    expected = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert build_example() == expected
    monkeypatch.setenv("TACTIQ_VALUE_MODEL_PATH", "must-not-read-private-model.json")
    monkeypatch.setenv("TACTIQ_TEAM_A_NAME", "PRIVATE TEAM")
    monkeypatch.setenv("NARRATIVE_MIN_CONFIDENCE", "1")
    assert build_example() == expected
    assert expected["replay"]["match_id"] == expected["id"]
    assert expected["replay"]["source_url"] is None
    frames = expected["replay"]["frames"]
    assert max(b["timestamp_ms"] - a["timestamp_ms"] for a, b in zip(frames, frames[1:])) <= 500
    assert OUTPUT.stat().st_size < 2_000_000
    for item in expected["insights"]:
        window = item["provenance"]["window"]
        assert all(window["start_ms"] <= event["match_clock_ms"] <= window["end_ms"] for event in item["events"])
        event_ids = {event["event_id"] for event in item["events"]}
        assert all(set(metric["event_ids"]) <= event_ids for metric in item["support"])
        assert all(metric["unit"] for metric in item["provenance"]["metrics"])
        assert set(item["narratives"]) == set(LANGUAGES)
        assert item["narratives"]["en-GB"]["fan"]["body"] != item["narratives"]["en-GB"]["analyst"]["body"]
        assert all(result["provider"] == "deterministic" and result["fallback_used"]
                   for audience in item["narratives"].values() for result in audience.values())
    serialized = OUTPUT.read_text(encoding="utf-8")
    assert all(value not in serialized for value in ("match_000000", "/api/", "token", "tenant_id", "private-match", "https://"))
    assert expected["audio"]["insight_id"] in {item["insight_id"] for item in expected["insights"]}
    with wave.open(str(OUTPUT.parent / "pressure-en.wav")) as audio:
        assert audio.getnchannels() == 1 and audio.getframerate() == 16000
        assert 1 < audio.getnframes() / audio.getframerate() < 120
    original = Path("docs/assets/production/commentary-live.mp3").read_bytes()
    assert hashlib.sha256(original).digest() == hashlib.sha256((OUTPUT.parent / "azure-commentary.mp3").read_bytes()).digest()


@pytest.mark.parametrize("value", [None, "tomorrow", float("nan"), float("inf"), -1])
def test_canonical_event_invalid_timestamp_contract(value):
    with pytest.raises(ValidationError):
        Event(match_id="canonical", sequence=1, type="pass_completed", team_id="team_a", match_clock_ms=value)


def test_canonical_event_requires_timestamp():
    with pytest.raises(ValidationError):
        Event(match_id="canonical", sequence=1, type="pass_completed", team_id="team_a")


@pytest.mark.parametrize("value", [True, "100", 100.0])
def test_canonical_coercion_does_not_apply_to_arbitrary_context(value):
    pytest.importorskip("agent_framework")
    from tactiq.agents.workflow import MomentEvidence
    event = Event(match_id="canonical", sequence=1, type="pass_completed", team_id="team_a", match_clock_ms=value)
    assert type(event.match_clock_ms) is int
    from test_agent_framework_workflow import moment
    insight = moment()
    insight.context["phase_events"] = [{"type": "pass_completed", "match_clock_ms": value}]
    assert not MomentEvidence.from_insight(insight, "en-GB").insight().context["phase_events"]
