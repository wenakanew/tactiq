import pytest

from tactiq.agents.speech import AzureSpeechSynthesizer


@pytest.fixture
def clean_speech(monkeypatch):
    for name in (
        "AZURE_SPEECH_DEFAULT_VOICE", "AZURE_SPEECH_DEFAULT_VOICE_BY_LANGUAGE_JSON",
        "AZURE_SPEECH_DEFAULT_COMMENTATOR_A_VOICE", "AZURE_SPEECH_DEFAULT_COMMENTATOR_B_VOICE",
        "AZURE_SPEECH_DEFAULT_COMMENTATOR_PAIR_BY_LANGUAGE_JSON",
        "AZURE_SPEECH_COMMENTATOR_A_VOICE", "AZURE_SPEECH_COMMENTATOR_B_VOICE",
    ):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_role_overrides_do_not_require_unrelated_pair_defaults(clean_speech):
    clean_speech.setenv("AZURE_SPEECH_COMMENTATOR_A_VOICE", "en-GB-RyanNeural")
    clean_speech.setenv("AZURE_SPEECH_COMMENTATOR_B_VOICE", "en-GB-ThomasNeural")
    assert AzureSpeechSynthesizer()._commentator_pair_for("en-GB") == (
        "en-GB-RyanNeural", "en-GB-ThomasNeural"
    )


def test_single_narration_uses_the_configured_lead_when_no_dedicated_voice_is_set(clean_speech):
    clean_speech.setenv("AZURE_SPEECH_COMMENTATOR_A_VOICE", "en-GB-RyanNeural")
    assert AzureSpeechSynthesizer()._default_voice_for("en-GB") == "en-GB-RyanNeural"


def test_single_narration_prefers_a_language_pair_over_another_languages_global_lead(clean_speech):
    clean_speech.setenv("AZURE_SPEECH_COMMENTATOR_A_VOICE", "en-GB-RyanNeural")
    clean_speech.setenv("AZURE_SPEECH_DEFAULT_COMMENTATOR_PAIR_BY_LANGUAGE_JSON",
                       '{"sw-KE":["sw-KE-FixtureA","sw-KE-FixtureB"]}')
    assert AzureSpeechSynthesizer()._default_voice_for("sw-KE") == "sw-KE-FixtureA"


def test_one_request_voice_and_one_configured_role_form_a_complete_pair(clean_speech):
    clean_speech.setenv("AZURE_SPEECH_COMMENTATOR_B_VOICE", "en-GB-ThomasNeural")
    assert AzureSpeechSynthesizer()._commentator_pair_for("en-GB", primary_voice="en-GB-RyanNeural") == (
        "en-GB-RyanNeural", "en-GB-ThomasNeural"
    )


def test_language_specific_pair_is_not_overwritten_by_another_languages_global_role(clean_speech):
    clean_speech.setenv("AZURE_SPEECH_COMMENTATOR_A_VOICE", "en-GB-RyanNeural")
    clean_speech.setenv("AZURE_SPEECH_COMMENTATOR_B_VOICE", "en-GB-ThomasNeural")
    clean_speech.setenv("AZURE_SPEECH_DEFAULT_COMMENTATOR_PAIR_BY_LANGUAGE_JSON",
                       '{"sw-KE":{"primary":"sw-KE-FixtureA","secondary":"sw-KE-FixtureB"}}')
    assert AzureSpeechSynthesizer()._commentator_pair_for("sw-KE") == ("sw-KE-FixtureA", "sw-KE-FixtureB")


def test_explicit_request_voices_keep_precedence_over_maps(clean_speech):
    clean_speech.setenv("AZURE_SPEECH_DEFAULT_COMMENTATOR_PAIR_BY_LANGUAGE_JSON",
                       '{"en-GB":["en-GB-DefaultA","en-GB-DefaultB"]}')
    assert AzureSpeechSynthesizer()._commentator_pair_for(
        "en-GB", primary_voice="en-GB-RequestA", secondary_voice="en-GB-RequestB"
    ) == ("en-GB-RequestA", "en-GB-RequestB")


def test_a_different_language_voice_is_not_silently_used_for_localized_text():
    with pytest.raises(ValueError, match="language"):
        AzureSpeechSynthesizer._single_commentary_ssml(
            "Maelezo ya mechi.", "sw-KE", "en-GB-RyanNeural", "+2%", "+1st"
        )


def test_authentication_rejection_is_not_retried_against_multiple_endpoints(clean_speech):
    clean_speech.setenv("AZURE_SPEECH_API_KEY", "fake-test-key")
    clean_speech.setenv("AZURE_SPEECH_ENDPOINT", "https://fixture.cognitiveservices.azure.com")
    clean_speech.setenv("AZURE_SPEECH_REGION", "test-region")
    requested = []

    class Unauthorized:
        status_code = 401
        text = "private diagnostic body"

        def close(self):
            pass

    def post(*args, **kwargs):
        requested.append(args[0])
        return Unauthorized()

    clean_speech.setattr("tactiq.agents.speech.requests.post", post)
    with pytest.raises(RuntimeError, match="authentication.*401"):
        AzureSpeechSynthesizer()._synthesize_with_rest("<speak/>", "fixture")
    assert len(requested) == 1
