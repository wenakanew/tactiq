"""Grounded offline fallbacks and speech adapter contracts, not cloud quality tests."""

from __future__ import annotations

import base64
from xml.etree import ElementTree

import pytest


CONTENT_ROUTES = [
    "/api/v1/matches/{match_id}/narratives",
    "/api/v1/matches/{match_id}/recap",
    "/api/v1/matches/{match_id}/commentary/dual",
]
LANGUAGES = ["en-GB", "sw-KE", "fr-FR", "es-ES", "pt-BR", "ar-SA", "hi-IN"]
SPEECH_ROUTES = ["/api/v1/speech/synthesize", "/api/v1/speech/synthesize-dual"]


@pytest.mark.parametrize("route", CONTENT_ROUTES)
def test_content_generation_without_insights_is_an_explicit_client_error(api, match_id, route):
    response = api.request(
        "POST", route, case="no-insights", status=400, actor="viewer",
        path_params={"match_id": match_id}, json={},
    )
    assert "No insights" in response.json()["detail"]


@pytest.mark.parametrize("route", CONTENT_ROUTES)
@pytest.mark.parametrize("language", LANGUAGES)
def test_all_supported_locales_return_labelled_offline_content(api, populated_match, route, language):
    result = api.request(
        "POST", route, case=f"offline-content-{language}", actor="viewer",
        path_params={"match_id": populated_match}, json={"language": language},
        provenance=("real-scenario-events", "disabled-Foundry-deterministic-fallback"),
    ).json()
    assert result["language"] == language and result["fallback_used"] is True
    assert result["provider"] == "deterministic" or result["provider"].startswith("guardrail-")
    if route.endswith("/commentary/dual"):
        assert result["primary_text"].strip() and result["secondary_text"].strip()
    else:
        assert result["title"].strip() and result["body"].strip()
        assert isinstance(result["grounding"], dict)
        if route.endswith("/recap"):
            assert isinstance(result["top_insights"], list) and isinstance(result["chapters"], list)
            assert all(item["window_start_ms"] <= item["window_end_ms"] for item in result["chapters"])
    assert api.service.narrative_generator._foundry_enabled is False
    assert api.service.narrative_generator._project_client is None
    assert api.service.narrative_generator._agents_client is None


@pytest.mark.parametrize("route", CONTENT_ROUTES)
def test_content_routes_validate_locale_before_generation(api, populated_match, route):
    api.request(
        "POST", route, case="unsupported-content-locale", status=422,
        path_params={"match_id": populated_match}, json={"language": "xx-XX"},
    )


@pytest.mark.parametrize("route", CONTENT_ROUTES[:2])
@pytest.mark.parametrize("audience", ["fan", "analyst", "player"])
def test_content_audience_modes_use_only_supported_player_scope(api, populated_match, route, audience):
    result = api.request(
        "POST", route, case=f"audience-{audience}", actor="viewer",
        path_params={"match_id": populated_match},
        json={"audience_mode": audience, "selected_player_id": "a_10"},
    ).json()
    assert result["audience_mode"] == audience
    assert result["fallback_used"] is True
    assert result["provider"] != "foundry"


@pytest.mark.parametrize("route", CONTENT_ROUTES[:2])
def test_player_audience_requires_a_player_or_profile(api, populated_match, route):
    api.request(
        "POST", route, case="player-selection-required", status=400, actor="viewer",
        path_params={"match_id": populated_match}, json={"audience_mode": "player"},
    )
    api.request(
        "POST", "/api/v1/matches/{match_id}/viewer-profile", case="player-profile-selection",
        path_params={"match_id": populated_match}, json={"favorite_player_id": "a_10"},
    )
    selected = api.request(
        "POST", route, case="player-profile-fallback", actor="viewer",
        path_params={"match_id": populated_match}, json={"audience_mode": "player"},
    ).json()
    assert selected["audience_mode"] == "player" and selected["fallback_used"] is True


@pytest.mark.parametrize("route", CONTENT_ROUTES[:2])
def test_unknown_audience_and_wrong_field_types_are_validation_errors(api, populated_match, route):
    api.request(
        "POST", route, case="invalid-audience", status=422,
        path_params={"match_id": populated_match}, json={"audience_mode": "coach"},
    )
    api.request(
        "POST", route, case="invalid-player-field", status=422,
        path_params={"match_id": populated_match}, json={"selected_player_id": ["a_10"]},
    )


@pytest.mark.parametrize("route", [CONTENT_ROUTES[0], CONTENT_ROUTES[2]])
def test_explicit_insight_selection_and_unknown_insights(api, populated_match, route):
    insight = api.service.insights[populated_match][-1]
    selected = api.request(
        "POST", route, case="known-insight-selection", actor="viewer",
        path_params={"match_id": populated_match}, json={"insight_id": insight.insight_id},
    ).json()
    assert selected["fallback_used"] is True
    api.request(
        "POST", route, case="unknown-insight-selection", actor="viewer", status=400,
        path_params={"match_id": populated_match}, json={"insight_id": "missing_insight"},
    )


@pytest.mark.parametrize("route", [CONTENT_ROUTES[0], CONTENT_ROUTES[2]])
def test_an_insight_from_another_match_cannot_be_selected(api, populated_match, route):
    foreign = api.populate(api.create(actor="foreign"))
    foreign_insight = api.service.insights[foreign][-1].insight_id
    api.request(
        "POST", route, case="foreign-insight-selection", actor="viewer", status=400,
        path_params={"match_id": populated_match}, json={"insight_id": foreign_insight},
    )


@pytest.mark.parametrize("route", [CONTENT_ROUTES[0], CONTENT_ROUTES[2]])
def test_explicit_empty_insight_id_does_not_silently_select_a_different_insight(api, populated_match, route):
    api.request(
        "POST", route, case="empty-insight-selection", status=(400, 422), actor="viewer",
        path_params={"match_id": populated_match}, json={"insight_id": ""},
    )


@pytest.mark.parametrize("route", CONTENT_ROUTES)
def test_insufficient_evidence_is_a_labelled_guardrail_not_model_success(api, populated_match, route):
    for insight in api.service.insights[populated_match]:
        insight.confidence = 0.1
    result = api.request(
        "POST", route, case="low-confidence-guardrail", actor="viewer",
        path_params={"match_id": populated_match}, json={},
        provenance=("fault-injected-low-confidence-evidence",),
    ).json()
    assert result["fallback_used"] is True
    assert result["provider"].startswith("guardrail-")
    if route.endswith("/recap"):
        assert result["top_insights"] == [] and result["chapters"] == []


@pytest.mark.parametrize("route", CONTENT_ROUTES)
def test_synthetic_video_preview_cannot_support_claims_even_with_relaxed_thresholds(
    api, populated_match, monkeypatch, route,
):
    api.service.matches[populated_match].analysis_metadata.update({
        "extractor": "video-stub-v1", "source_timed_synthetic_preview": True,
    })
    insights = api.service.insights[populated_match]
    assert insights
    for insight in insights:
        insight.confidence = 0.0
    api.service.narrative_min_confidence = 0.0
    api.service.clip_min_confidence = 0.0
    api.service._build_insight_clips(populated_match, insights)
    assert all(
        clip == {"status": "unavailable", "reason": "synthetic_events_are_not_video_evidence"}
        for clip in api.service.insight_clip_map[populated_match].values()
    )

    def unsupported_generation(**kwargs):
        pytest.fail("Synthetic video previews must not invoke a narrative model")

    monkeypatch.setattr(api.service.narrative_generator, "generate", unsupported_generation)
    monkeypatch.setattr(api.service.narrative_generator, "generate_dual_commentary", unsupported_generation)
    result = api.request(
        "POST", route, case="synthetic-preview-evidence-guard", actor="viewer",
        path_params={"match_id": populated_match}, json={},
        provenance=("fault-injected-preview-provenance", "forbidden-narrative-model"),
    ).json()
    assert result["fallback_used"] is True
    if route.endswith("/recap"):
        assert result["provider"] == "guardrail-recap-evidence"
        assert result["chapters"] == result["top_insights"] == []
    else:
        assert result["provider"] == "guardrail-synthetic_video_preview"


def test_commentary_debug_reports_newest_audits_and_bounded_entries(api, populated_match):
    for _ in range(2):
        api.request(
            "POST", CONTENT_ROUTES[2], case="audited-offline-commentary", actor="viewer",
            path_params={"match_id": populated_match}, json={},
        )
    payload = api.request(
        "GET", "/api/v1/matches/{match_id}/commentary/debug", case="commentary-audit-limit",
        actor="analyst", path_params={"match_id": populated_match}, params={"limit": 1},
    ).json()
    assert payload["count"] == 2 and len(payload["entries"]) == 1
    latest = payload["entries"][0]
    assert latest["match_id"] == populated_match and latest["fallback_used"] is True
    assert latest["audit"]["provenance"]["supporting_event_ids"]
    assert latest["provider"] == "deterministic" or latest["provider"].startswith("guardrail-")


@pytest.mark.parametrize("route", SPEECH_ROUTES)
def test_speech_without_cloud_configuration_returns_service_unavailable(api, route):
    payload = {"text": "Offline speech."} if not route.endswith("-dual") else {
        "primary_text": "Offline lead.", "secondary_text": "Offline analyst.",
    }
    response = api.request("POST", route, case="speech-not-configured", status=503, actor="viewer", json=payload)
    assert "not configured" in response.json()["detail"]
    assert "audio_base64" not in response.json()


@pytest.mark.parametrize("route", SPEECH_ROUTES)
@pytest.mark.parametrize("kind", ["empty-object", "blank-text", "empty-text", "oversized-text", "bad-locale"])
def test_speech_rejects_invalid_payloads_before_external_io(api, route, kind):
    dual = route.endswith("-dual")
    payload = {"primary_text": "Lead.", "secondary_text": "Analyst."} if dual else {"text": "Lead."}
    status = 422
    if kind == "empty-object":
        payload = {}
    elif kind in {"blank-text", "empty-text"}:
        payload["primary_text" if dual else "text"] = " " if kind == "blank-text" else ""
        status = 400 if kind == "blank-text" else 422
    elif kind == "oversized-text":
        payload["primary_text" if dual else "text"] = "x" * (4001 if dual else 8001)
    else:
        payload["language"] = "xx-XX"
    api.request("POST", route, case=f"speech-invalid-{kind}", status=status, actor="viewer", json=payload)


@pytest.mark.parametrize("language", LANGUAGES)
def test_single_speech_valid_input_and_xml_escaping_use_only_fixture_audio(api, fake_speech, language):
    response = api.request(
        "POST", SPEECH_ROUTES[0], case=f"speech-fixture-{language}", actor="viewer",
        json={"text": "Fixture <press> & control.", "language": language},
        provenance=("local-generated-mp3", "fake-Speech-transport"),
    ).json()
    assert response["provider"] == "offline-fixture-speech"
    assert response["content_type"] == "audio/mpeg"
    assert base64.b64decode(response["audio_base64"], validate=True)
    xml = ElementTree.fromstring(fake_speech[0])
    assert "Fixture <press> & control." in "".join(xml.itertext())


def test_dual_speech_composes_two_distinct_fixture_voices_without_external_io(api, fake_speech):
    result = api.request(
        "POST", SPEECH_ROUTES[1], case="dual-speech-fixture", actor="viewer",
        json={"primary_text": "Offline lead.", "secondary_text": "Offline analyst."},
        provenance=("local-generated-mp3", "fake-Speech-transport", "real-FFmpeg-audio-concat"),
    ).json()
    assert result["content_type"] == "audio/mpeg"
    assert "offline-fixture-speech" in result["provider"]
    assert base64.b64decode(result["audio_base64"], validate=True)
    assert len(fake_speech) == 2
    voices = [
        next(node.attrib["name"] for node in ElementTree.fromstring(xml).iter() if node.tag.endswith("voice"))
        for xml in fake_speech
    ]
    assert len(set(voices)) == 2


@pytest.mark.parametrize(
    "field,value,status",
    [
        ("voice", "bad<voice", 422), ("voice", "v" * 101, 422),
        ("rate", "fast", 400), ("rate", "+101%", 400), ("rate", "-51%", 400),
        ("pitch", "+11st", 400), ("pitch", "-11st", 400), ("pitch", "high", 400),
    ],
    ids=["bad-voice", "long-voice", "bad-rate", "fast-rate", "slow-rate", "high-pitch", "low-pitch", "bad-pitch"],
)
def test_single_speech_validates_voice_and_prosody_before_transport(api, fake_speech, field, value, status):
    api.request(
        "POST", SPEECH_ROUTES[0], case=f"invalid-speech-{field}", status=status,
        json={"text": "Offline fixture.", field: value},
    )
    assert fake_speech == []


@pytest.mark.parametrize("voices,status", [
    ({"primary_voice": "same", "secondary_voice": "same"}, 400),
    ({"primary_voice": "bad<voice", "secondary_voice": "en-GB-FixtureAnalystNeural"}, 422),
])
def test_dual_speech_rejects_invalid_or_identical_voices(api, fake_speech, voices, status):
    api.request(
        "POST", SPEECH_ROUTES[1], case="invalid-dual-voices", status=status,
        json={"primary_text": "Lead.", "secondary_text": "Analyst.", **voices},
    )
    assert fake_speech == []


def test_speech_rejects_a_voice_from_another_language_before_transport(api, fake_speech):
    api.request(
        "POST", SPEECH_ROUTES[0], case="mismatched-voice-locale", status=400,
        json={"text": "Offline fixture.", "language": "sw-KE", "voice": "en-GB-FixtureLeadNeural"},
    )
    assert fake_speech == []


@pytest.mark.parametrize("route", SPEECH_ROUTES)
def test_speech_adapter_runtime_failure_is_a_503_not_success(api, fake_speech, monkeypatch, route):
    def unavailable(**kwargs):
        raise RuntimeError("Offline fixture speech adapter unavailable")

    monkeypatch.setattr(api.service.speech_synthesizer, "_synthesize_with_rest", unavailable)
    payload = {"text": "Offline lead."} if not route.endswith("-dual") else {
        "primary_text": "Offline lead.", "secondary_text": "Offline analyst.",
    }
    api.request(
        "POST", route, case="speech-adapter-unavailable", status=503, actor="viewer", json=payload,
        provenance=("fault-injected-Speech-transport",),
    )
