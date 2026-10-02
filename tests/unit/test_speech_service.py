from tactiq.application.service import MatchService
from tactiq.agents.speech import SpeechResult


class _StubSpeech:
    def synthesize(
        self,
        text: str,
        language: str,
        voice: str | None = None,
        rate: str = "+2%",
        pitch: str = "+1st",
    ) -> SpeechResult:
        return SpeechResult(audio_base64="ZmFrZQ==", content_type="audio/mpeg", provider="azure-speech")

    def synthesize_dual_commentary(
        self,
        primary_text: str,
        secondary_text: str,
        language: str,
        primary_voice: str | None = None,
        secondary_voice: str | None = None,
    ) -> SpeechResult:
        return SpeechResult(audio_base64="ZHVv", content_type="audio/mpeg", provider="azure-speech-duo")


class _FailSpeech:
    def synthesize(
        self,
        text: str,
        language: str,
        voice: str | None = None,
        rate: str = "+2%",
        pitch: str = "+1st",
    ) -> SpeechResult:
        raise RuntimeError("Azure Speech is not configured")

    def synthesize_dual_commentary(
        self,
        primary_text: str,
        secondary_text: str,
        language: str,
        primary_voice: str | None = None,
        secondary_voice: str | None = None,
    ) -> SpeechResult:
        raise RuntimeError("Azure Speech is not configured")


def test_synthesize_speech_success() -> None:
    service = MatchService()
    service.speech_synthesizer = _StubSpeech()  # type: ignore[assignment]

    result = service.synthesize_speech(text="hello", language="en-GB")

    assert result.provider == "azure-speech"
    assert result.content_type == "audio/mpeg"
    assert result.audio_base64 == "ZmFrZQ=="


def test_synthesize_dual_commentary_success() -> None:
    service = MatchService()
    service.speech_synthesizer = _StubSpeech()  # type: ignore[assignment]

    result = service.synthesize_dual_commentary(
        primary_text="Team A are building pressure.",
        secondary_text="Yes, and Team B are pinned back.",
        language="en-GB",
    )

    assert result.provider == "azure-speech-duo"
    assert result.content_type == "audio/mpeg"
    assert result.audio_base64 == "ZHVv"


def test_synthesize_speech_error_propagates() -> None:
    service = MatchService()
    service.speech_synthesizer = _FailSpeech()  # type: ignore[assignment]

    try:
        service.synthesize_speech(text="hello", language="en-GB")
        raise AssertionError("Expected RuntimeError")
    except RuntimeError as exc:
        assert "not configured" in str(exc)
