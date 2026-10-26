import base64
from xml.etree import ElementTree

from tactiq.agents.commentary import CommentaryTurn
from tactiq.agents.narrative import DeterministicNarrativeGenerator, FoundryNarrativeGenerator
from tactiq.agents.speech import AzureSpeechSynthesizer, SpeechResult
from tactiq.application.service import MatchService
from tactiq.domain.models import Event, EvidenceItem, Insight, Position


def rich_insight(match_id="rich"):
    return Insight(
        match_id=match_id, type="sustained_pressure", priority="high", confidence=.85, team_id="team_a",
        window_start_ms=0, window_end_ms=60000, title="Pressure Building",
        summary="Team A are sustaining attacking pressure through progression, entries and shots.",
        evidence=[EvidenceItem(metric=name, value=value, unit="count", supporting_event_ids=["e1", "e2", "e3", "e4"])
                  for name, value in (("progressive_actions", 6), ("final_third_entries", 3), ("shots", 1))],
        context={"data_source": "synthetic", "depth": "deep",
                 "phase_events": [{"type": kind} for kind in ("recovery", "pass_completed", "shot")]},
    )


def test_supported_deep_fallback_is_substantive_and_keeps_speaker_order():
    generator = DeterministicNarrativeGenerator()
    insight = rich_insight()
    result = generator.generate_dual_commentary(insight, "en-GB")
    assert [turn.role for turn in result.turns] == ["lead", "analyst", "lead", "analyst"]
    words = " ".join(turn.text for turn in result.turns).split()
    assert 120 <= len(words) <= 180
    assert "simulated" in result.turns[-1].text
    assert result.primary_text == " ".join(turn.text for turn in result.turns if turn.role == "lead")
    insight.context["depth"] = "broadcast"
    broadcast = generator.generate_dual_commentary(insight, "en-GB")
    assert len(broadcast.turns) == 2
    assert len(" ".join(turn.text for turn in broadcast.turns).split()) < len(words)


def test_quota_failure_stops_further_model_calls_and_reports_the_real_reason(monkeypatch):
    monkeypatch.setenv("FOUNDRY_ENABLED", "true")
    monkeypatch.setenv("AZURE_FOUNDRY_PROJECT_ENDPOINT", "https://example.invalid")
    monkeypatch.setenv("AZURE_FOUNDRY_PROJECT_DEPLOYMENT_NAME", "test-deployment")
    monkeypatch.setenv("FOUNDRY_COMMENTARY_USE_DIRECT_AGENTS", "false")
    generator = FoundryNarrativeGenerator(DeterministicNarrativeGenerator())
    calls = []

    class QuotaError(RuntimeError):
        status_code = 429

    def unavailable(_):
        calls.append(True)
        raise QuotaError("test quota restriction")

    monkeypatch.setattr(generator, "_call_foundry", unavailable)
    first = generator.generate_dual_commentary(rich_insight(), "en-GB")
    second = generator.generate_dual_commentary(rich_insight(), "en-GB")
    assert len(calls) == 1 and first.fallback_used and second.fallback_used
    assert second.audit["provider_failure"]["reason"] == "quota"
    assert generator.status()["status"] == "cooldown"


def test_historical_explanations_never_receive_future_goals(tmp_path, monkeypatch):
    monkeypatch.setenv("TACTIQ_MEDIA_DIR", str(tmp_path))
    service = MatchService()
    state = service.create_match("sustained_pressure", 1)
    for index, (clock, kind) in enumerate(((0, "recovery"), (1000, "pass_completed"), (20000, "shot"), (20001, "goal")), 1):
        service.record_event(Event(event_id=f"e{index}", match_id=state.match_id, sequence=index, match_clock_ms=clock,
                                   type=kind, team_id="team_a", player_id="a_06", position=Position(x=40, y=50)))
    target = rich_insight(state.match_id)
    target.window_end_ms = 1000
    target.evidence = [EvidenceItem(metric="progressive_actions", value=1, unit="count", supporting_event_ids=["e1", "e2"])]
    service.insights[state.match_id] = [target]
    grounded = service._narration_target(state.match_id, target.insight_id, 1000, "deep")
    assert {item["type"] for item in grounded.context["phase_events"]} == {"recovery", "pass_completed"}
    assert "goal" not in grounded.context["event_types"]
    assert {key for item in grounded.evidence for key in item.supporting_event_ids} <= {"e1", "e2"}


def test_extra_context_does_not_promote_an_unsupported_tactical_claim(tmp_path, monkeypatch):
    monkeypatch.setenv("TACTIQ_MEDIA_DIR", str(tmp_path))
    service = MatchService()
    state = service.create_match("sustained_pressure", 1)
    for index, kind in enumerate(("recovery", "pass_completed", "carry", "shot"), 1):
        service.record_event(Event(event_id=f"e{index}", match_id=state.match_id, sequence=index,
                                   match_clock_ms=index * 1000, type=kind, team_id="team_a"))
    weak = rich_insight(state.match_id)
    weak.evidence = [EvidenceItem(metric="shots", value=1, unit="count", supporting_event_ids=["e4"])]
    service.insights[state.match_id] = [weak]
    result = service.generate_narrative(state.match_id, "fan", "en-GB", weak.insight_id)
    assert result.provider == "guardrail-insufficient_evidence_items"
    assert result.fallback_used


def test_tracking_without_tactical_insights_has_a_labelled_observation_script(tmp_path, monkeypatch):
    monkeypatch.setenv("TACTIQ_MEDIA_DIR", str(tmp_path))
    monkeypatch.setenv("FOUNDRY_ENABLED", "false")
    service = MatchService()
    state = service.create_match("cv_upload", 0)
    state.analysis_metadata = {"detection_source": "model"}
    for index in range(4):
        service.record_event(Event(
            match_id=state.match_id, sequence=index + 1, match_clock_ms=index * 250, type="tracking_frame",
            team_id="unknown", synthetic=False, qualifiers={"cv_frame": {
                "tracking_source": "model-fixture", "detection_source": "model", "geometry_source": "image",
                "calibrated": False, "players": [{"track_id": "player_1", "team_id": None, "position": None,
                    "image_position": {"x": 40, "y": 50}, "confidence": .8, "inferred": False}], "ball": None,
            }},
        ))
    assert not service.insights[state.match_id]
    result = service.generate_dual_commentary(state.match_id, "en-GB", at_ms=500)
    assert result.primary_text.startswith("This is an observation overview")
    assert "not a tactical verdict" in result.primary_text
    assert result.audit["provenance"]["type"] == "tracking_overview"
    assert result.audit["provenance"]["window"]["end_ms"] == 500
    assert not state.cv_live.get("ball_position")


def test_ordered_speech_uses_the_existing_pair_in_one_request_and_is_tenant_scoped(monkeypatch):
    monkeypatch.setenv("AZURE_SPEECH_API_KEY", "offline-test-key")
    monkeypatch.setenv("AZURE_SPEECH_REGION", "westeurope")
    monkeypatch.setenv("AZURE_SPEECH_COMMENTATOR_A_VOICE", "en-GB-RyanNeural")
    monkeypatch.setenv("AZURE_SPEECH_COMMENTATOR_B_VOICE", "en-GB-ThomasNeural")
    monkeypatch.delenv("AZURE_SPEECH_DEFAULT_COMMENTATOR_PAIR_BY_LANGUAGE_JSON", raising=False)
    synthesizer = AzureSpeechSynthesizer()
    requests = []

    def render(*, ssml, provider):
        requests.append(ssml)
        return SpeechResult(base64.b64encode(b"ID3fixture").decode(), "audio/mpeg", provider)

    monkeypatch.setattr(synthesizer, "_synthesize_with_rest", render)
    turns = [CommentaryTurn(role=role, text=text) for role, text in (
        ("lead", "A & B."), ("analyst", "Evidence, not invention."), ("lead", "The next passage."),
        ("analyst", "Keep the source limitations visible."))]
    synthesizer.synthesize_turns(turns, "en-GB", tenant_id="one")
    synthesizer.synthesize_turns(turns, "en-GB", tenant_id="one")
    assert len(requests) == 1
    root = ElementTree.fromstring(requests[0])
    voices = root.findall("{http://www.w3.org/2001/10/synthesis}voice")
    assert not root.findall("{http://www.w3.org/2001/10/synthesis}break")
    assert len(root.findall(".//{http://www.w3.org/2001/10/synthesis}break")) == 3
    assert [voice.attrib["name"] for voice in voices] == [
        "en-GB-RyanNeural", "en-GB-ThomasNeural", "en-GB-RyanNeural", "en-GB-ThomasNeural"]
    assert "A &amp; B." in requests[0]
    synthesizer.synthesize_turns(turns, "en-GB", tenant_id="two")
    assert len(requests) == 2
