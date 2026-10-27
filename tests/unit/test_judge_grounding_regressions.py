"""Offline evidence-boundary tests; translations are fixtures, not fluent review."""
import json

import pytest

from tactiq.agents.commentary import LABELS, METRICS
from tactiq.agents.grounding import Claim, GroundingError, validate_text
from tactiq.agents.localization import LANGUAGES
from tactiq.agents.localization import localized_insight
from tactiq.agents.narrative import DeterministicNarrativeGenerator, FoundryNarrativeGenerator
from tactiq.domain.models import EvidenceItem, Insight


def fixture(language="en-GB"):
    return Insight(
        match_id="boundary", type="sustained_pressure", priority="high", confidence=.9,
        team_id="team_a", window_start_ms=60000, window_end_ms=90000,
        title="Pressure Building", summary="Team A are sustaining attacking pressure.",
        evidence=[EvidenceItem(metric=metric, value=value, unit="count", supporting_event_ids=[event])
                  for metric, value, event in (("progressive_actions", 6, "e1"), ("shots", 1, "e2"))],
        context={"language": language, "depth": "broadcast", "data_source": "synthetic",
                 "known_player_ids": ["a_10"], "phase_events": [
                     {"type": "pass_completed", "event_id": "e1", "match_clock_ms": 60000}]},
    )


CASES = {
    "en-GB": ("six progressive actions", "twelve shots", "They controlled the entire match.", "They scored.", "Arsenal controlled the passage."),
    "sw-KE": ("hatua sita za kusonga mbele", "mashuti kumi na mawili", "Walitawala mechi nzima.", "Walifunga bao.", "Arsenal walitawala kipindi hiki."),
    "fr-FR": ("six actions progressives", "douze tirs", "Ils ont dominé tout le match.", "Ils ont marqué un but.", "Arsenal a dominé ce passage."),
    "es-ES": ("seis acciones progresivas", "doce tiros", "Dominaron todo el partido.", "Marcaron un gol.", "Arsenal dominó este pasaje."),
    "pt-BR": ("seis ações progressivas", "doze finalizações", "Dominaram a partida inteira.", "Marcaram um gol.", "Arsenal dominou este trecho."),
    "ar-SA": ("ستة تحركات تقدمية", "اثنتا عشرة تسديدة", "سيطروا على المباراة بأكملها.", "سجلوا هدفاً.", "سيطر أرسنال على هذا المقطع."),
    "hi-IN": ("छह प्रगतिशील कार्रवाइयाँ", "बारह शॉट", "उन्होंने पूरे मैच पर नियंत्रण रखा।", "उन्होंने गोल किया।", "आर्सेनल ने इस अंश पर नियंत्रण रखा।"),
}
CLAIMS = [Claim(metric="progressive_actions", value=6, supporting_event_ids=["e1"])]


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("category", ["incorrect_words", "whole_match", "unsupported_event", "unknown_team", "wrong_metric", "unknown_player", "mixed_language"])
def test_equivalent_unsupported_prose_never_passes_with_correct_structured_claim(language, category):
    _, incorrect, whole_match, event, team = CASES[language]
    shots = dict(zip(METRICS, LABELS[language].split("|")))["shots"]
    text = {
        "incorrect_words": incorrect, "whole_match": whole_match, "unsupported_event": event,
        "unknown_team": team, "wrong_metric": f"6 {shots}", "unknown_player": "a_99",
        "mixed_language": f"team_a: {incorrect}",
    }[category]
    with pytest.raises(GroundingError):
        validate_text(text, fixture(language), CLAIMS)


@pytest.mark.parametrize("language", LANGUAGES)
def test_valid_digit_claims_and_invalid_structured_claims(language):
    label = dict(zip(METRICS, LABELS[language].split("|")))["progressive_actions"]
    validate_text(f"6 {label}", fixture(language), CLAIMS, ["team_a", "a_10"])
    with pytest.raises(GroundingError, match="declare"):
        validate_text(f"6 {label}", fixture(language))
    with pytest.raises(GroundingError, match="resolve"):
        validate_text(f"6 {label}", fixture(language), [
            Claim(metric="progressive_actions", value=6, supporting_event_ids=["future"]),
        ])
    with pytest.raises(GroundingError, match="entity"):
        validate_text(f"6 {label}", fixture(language), CLAIMS, ["team_z"])


@pytest.mark.parametrize("language", LANGUAGES)
def test_valid_number_words_are_either_checked_or_explicitly_unverified(language):
    valid = CASES[language][0]
    if language == "en-GB":
        validate_text(valid, fixture(language), CLAIMS)
    else:
        # A valid translation is not declared false; its free-form semantics are
        # unverified and the provider boundary must use the localized fallback.
        with pytest.raises(GroundingError, match="unverified"):
            validate_text(valid, fixture(language), CLAIMS)


@pytest.mark.parametrize("language", LANGUAGES)
def test_misleading_prose_does_not_reach_final_narrative(language, monkeypatch):
    monkeypatch.setenv("FOUNDRY_ENABLED", "true")
    monkeypatch.setenv("AZURE_FOUNDRY_PROJECT_ENDPOINT", "https://example.invalid")
    monkeypatch.setenv("AZURE_FOUNDRY_PROJECT_DEPLOYMENT_NAME", "offline")
    generator = FoundryNarrativeGenerator(DeterministicNarrativeGenerator())
    false_body = CASES[language][1]
    monkeypatch.setattr(generator, "_call_foundry", lambda _: json.dumps({
        "title": "Pressure Building", "body": false_body,
        "claims": [claim.model_dump() for claim in CLAIMS], "entities": ["team_a"],
    }))
    result = generator.generate(fixture(language), "fan", language)
    assert result.fallback_used and result.provider == "deterministic"
    assert result.grounding["provider_failure"]["reason"] == "invalid_output"
    assert false_body not in result.body


@pytest.mark.parametrize("language", LANGUAGES)
def test_approved_localized_model_output_still_reaches_result(language, monkeypatch):
    monkeypatch.setenv("FOUNDRY_ENABLED", "true")
    monkeypatch.setenv("AZURE_FOUNDRY_PROJECT_ENDPOINT", "https://example.invalid")
    monkeypatch.setenv("AZURE_FOUNDRY_PROJECT_DEPLOYMENT_NAME", "offline")
    insight = fixture(language)
    title, _ = localized_insight(insight, language)
    label = dict(zip(METRICS, LABELS[language].split("|")))["progressive_actions"]
    body = f"6 {label}"
    generator = FoundryNarrativeGenerator(DeterministicNarrativeGenerator())
    monkeypatch.setattr(generator, "_call_foundry", lambda _: json.dumps({
        "title": title, "body": body, "claims": [claim.model_dump() for claim in CLAIMS], "entities": ["team_a"],
    }))
    result = generator.generate(insight, "analyst", language)
    assert not result.fallback_used and result.provider == "foundry"
    assert result.body == body
