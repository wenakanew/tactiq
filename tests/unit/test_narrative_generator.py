from unittest.mock import patch

from tactiq.agents.narrative import DeterministicNarrativeGenerator, FoundryNarrativeGenerator
from tactiq.domain.models import EvidenceItem, Insight


def _sample_insight() -> Insight:
    return Insight(
        match_id="match_1",
        type="sustained_pressure",
        priority="high",
        confidence=0.9,
        team_id="team_a",
        window_start_ms=60_000,
        window_end_ms=68_000,
        title="Pressure Building",
        summary="Team A are beginning to pin Team B back.",
        evidence=[
            EvidenceItem(metric="progressive_actions", value=6, unit="count", supporting_event_ids=["e1"]),
            EvidenceItem(metric="final_third_entries", value=4, unit="count", supporting_event_ids=["e2"]),
            EvidenceItem(metric="shots", value=1, unit="count", supporting_event_ids=["e3"]),
        ],
    )


def test_analyst_narrative_contains_metrics() -> None:
    generator = DeterministicNarrativeGenerator()
    result = generator.generate(_sample_insight(), audience_mode="analyst", language="en-GB")

    assert "progressive_actions=6" in result.body
    assert "final_third_entries=4" in result.body
    assert result.fallback_used is True


def test_swahili_narrative_prefix() -> None:
    generator = DeterministicNarrativeGenerator()
    result = generator.generate(_sample_insight(), audience_mode="fan", language="sw-KE")

    assert result.body.startswith("Muktadha wa mechi:")


def test_foundry_generator_falls_back_when_disabled() -> None:
    fallback = DeterministicNarrativeGenerator()
    with patch.dict(
        "os.environ",
        {
            "FOUNDRY_ENABLED": "false",
            "AZURE_FOUNDRY_PROJECT_ENDPOINT": "",
            "AZURE_FOUNDRY_PROJECT_DEPLOYMENT_NAME": "",
            "AZURE_FOUNDRY_API_KEY": "",
        },
        clear=False,
    ):
        generator = FoundryNarrativeGenerator(fallback)

        result = generator.generate(_sample_insight(), audience_mode="fan", language="en-GB")

    assert result.fallback_used is True
    assert result.provider == "deterministic"


def test_foundry_generator_falls_back_on_foundry_error() -> None:
    fallback = DeterministicNarrativeGenerator()
    generator = FoundryNarrativeGenerator(fallback)

    with patch.dict(
        "os.environ",
        {
            "FOUNDRY_ENABLED": "true",
            "AZURE_FOUNDRY_PROJECT_ENDPOINT": "https://example.invalid",
            "AZURE_FOUNDRY_PROJECT_DEPLOYMENT_NAME": "demo",
        },
        clear=False,
    ):
        generator = FoundryNarrativeGenerator(fallback)
        with patch.object(generator, "_call_foundry", side_effect=RuntimeError("boom")):
            result = generator.generate(_sample_insight(), audience_mode="analyst", language="en-GB")

    assert result.fallback_used is True
    assert result.provider == "deterministic"


def test_deterministic_dual_commentary_has_two_distinct_lines() -> None:
    generator = DeterministicNarrativeGenerator()
    result = generator.generate_dual_commentary(_sample_insight(), language="en-GB")

    assert result.primary_text
    assert result.secondary_text
    assert result.primary_text != result.secondary_text
    assert result.provider == "deterministic"


def test_foundry_dual_commentary_falls_back_on_error() -> None:
    fallback = DeterministicNarrativeGenerator()

    with patch.dict(
        "os.environ",
        {
            "FOUNDRY_ENABLED": "true",
            "AZURE_FOUNDRY_PROJECT_ENDPOINT": "https://example.invalid",
            "AZURE_FOUNDRY_PROJECT_DEPLOYMENT_NAME": "demo",
        },
        clear=False,
    ):
        generator = FoundryNarrativeGenerator(fallback)
        with patch.object(generator, "_call_foundry", side_effect=RuntimeError("boom")):
            result = generator.generate_dual_commentary(_sample_insight(), language="en-GB")

    assert result.fallback_used is True
    assert result.provider == "deterministic"
