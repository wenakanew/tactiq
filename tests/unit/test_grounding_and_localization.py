import json

import pytest

from tactiq.agents.grounding import Claim, GroundingError, validate_text
from tactiq.agents.localization import LANGUAGES
from tactiq.agents.narrative import DeterministicNarrativeGenerator, FoundryNarrativeGenerator
from tactiq.application.service import MatchService
from tactiq.domain.models import Event, EvidenceItem, Insight, MatchState


@pytest.fixture
def insight():
    return Insight(
        match_id="grounded", type="sustained_pressure", priority="high", confidence=0.9, team_id="team_a",
        window_start_ms=60000, window_end_ms=90000, title="Pressure Building",
        summary="Team A are sustaining attacking pressure.",
        evidence=[
            EvidenceItem(metric="progressive_actions", value=6, unit="count", supporting_event_ids=["e1"]),
            EvidenceItem(metric="shots", value=1, unit="count", supporting_event_ids=["e2"]),
        ],
    )


def test_numeric_claims_require_resolving_evidence_and_entities(insight):
    claim = Claim(metric="progressive_actions", value=6, supporting_event_ids=["e1"])
    validate_text("Team A produced 6 progressive actions.", insight, [claim], ["team_a"])
    for text in ("Team A produced 99 progressive actions.", "a_99 dominated the match.", "They controlled the entire match.",
                 "Team A produced twelve shots.", "Arsenal are building pressure.", "Team A produced 6 shots."):
        with pytest.raises(GroundingError):
            validate_text(text, insight, [claim])
    with pytest.raises(GroundingError, match="declare"):
        validate_text("6 progressive actions.", insight)
    with pytest.raises(GroundingError, match="resolve"):
        validate_text("6 progressive actions.", insight, [
            Claim(metric="shots", value=6, supporting_event_ids=["e1"])
        ])


def test_foundry_rejects_hallucinated_number_and_accepts_grounded_json(insight, monkeypatch):
    monkeypatch.setenv("FOUNDRY_ENABLED", "true")
    monkeypatch.setenv("AZURE_FOUNDRY_PROJECT_ENDPOINT", "https://example.invalid")
    monkeypatch.setenv("AZURE_FOUNDRY_PROJECT_DEPLOYMENT_NAME", "test")
    generator = FoundryNarrativeGenerator(DeterministicNarrativeGenerator())
    response = {
        "title": "Pressure Building", "body": "Team A produced 6 progressive actions.",
        "claims": [{"metric": "progressive_actions", "value": 6, "supporting_event_ids": ["e1"]}],
        "entities": ["team_a"],
    }
    monkeypatch.setattr(generator, "_call_foundry", lambda _: json.dumps(response))
    generated = generator.generate(insight, "analyst", "en-GB")
    assert not generated.fallback_used
    response["body"] = "Team A produced 99 progressive actions."
    rejected = generator.generate(insight, "analyst", "en-GB")
    assert rejected.fallback_used
    assert "99" not in rejected.body


@pytest.mark.parametrize("language", LANGUAGES)
def test_localized_fallback_preserves_evidence_values_and_never_reuses_english_summary(insight, language):
    result = DeterministicNarrativeGenerator().generate(insight, "analyst", language)
    assert "progressive_actions=6" in result.body
    assert "shots=1" in result.body
    assert result.language == language
    if language != "en-GB":
        assert insight.summary not in result.body
    assert result.grounding["status"] == "deterministic"


def test_player_mode_does_not_turn_team_insight_into_player_attribution(insight):
    result = DeterministicNarrativeGenerator().generate(insight, "player", "en-GB", "a_99")
    assert "No supported contribution" in result.body
    assert "central" not in result.body


def test_service_guardrail_checks_event_resolution_and_recap_quality(insight, tmp_path, monkeypatch):
    monkeypatch.setenv("TACTIQ_MEDIA_DIR", str(tmp_path))
    service = MatchService()
    service.matches[insight.match_id] = MatchState(match_id=insight.match_id, scenario_id="test", seed=1)
    service.insights[insight.match_id] = [insight]
    guarded = service.generate_narrative(insight.match_id, "fan", "en-GB")
    assert guarded.provider == "guardrail-unresolved_supporting_events"
    recap, selected, chapters = service.generate_recap(insight.match_id, "fan", "en-GB")
    assert recap.provider == "guardrail-recap-evidence"
    assert not selected and not chapters
    service.events[insight.match_id] = [
        Event(event_id=f"e{index}", match_id=insight.match_id, sequence=index, match_clock_ms=60000 + index,
              type="pass_completed", team_id="team_a", player_id="a_10")
        for index in (1, 2)
    ]
    selected_player = service.generate_narrative(insight.match_id, "player", "en-GB", selected_player_id="a_99")
    assert selected_player.provider == "guardrail-player-scope"
