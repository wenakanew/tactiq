from __future__ import annotations

from dataclasses import dataclass
import base64
import logging
import os
from xml.sax.saxutils import escape

logger = logging.getLogger(__name__)


@dataclass
class SpeechResult:
    audio_base64: str
    content_type: str
    provider: str


class AzureSpeechSynthesizer:
    def __init__(self) -> None:
        self._endpoint = os.getenv("AZURE_SPEECH_ENDPOINT")
        self._key = os.getenv("AZURE_SPEECH_API_KEY")
        self._commentator_a_voice = os.getenv("AZURE_SPEECH_COMMENTATOR_A_VOICE")
        self._commentator_b_voice = os.getenv("AZURE_SPEECH_COMMENTATOR_B_VOICE")

    def is_configured(self) -> bool:
        return bool(self._endpoint and self._key)

    def synthesize(
        self,
        text: str,
        language: str,
        voice: str | None = None,
        rate: str = "+2%",
        pitch: str = "+1st",
    ) -> SpeechResult:
        if not text.strip():
            raise ValueError("Text is required for speech synthesis")
        if not self.is_configured():
            raise RuntimeError("Azure Speech is not configured")

        try:
            import azure.cognitiveservices.speech as speechsdk  # type: ignore[import-not-found]
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("Azure Speech SDK is not installed") from exc

        speech_config = speechsdk.SpeechConfig(
            endpoint=self._endpoint,
            subscription=self._key,
        )
        speech_config.speech_synthesis_language = language
        speech_config.set_speech_synthesis_output_format(
            speechsdk.SpeechSynthesisOutputFormat.Audio24Khz48KBitRateMonoMp3,
        )

        ssml = self._single_commentary_ssml(
            text=text,
            language=language,
            voice=voice or self._default_voice_for(language),
            rate=rate,
            pitch=pitch,
        )

        synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=None)
        result = synthesizer.speak_ssml_async(ssml).get()

        if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
            details = speechsdk.CancellationDetails(result)
            logger.warning("Azure Speech synthesis failed: %s", details.reason)
            raise RuntimeError("Azure Speech synthesis failed")

        audio_base64 = base64.b64encode(result.audio_data).decode("utf-8")
        return SpeechResult(
            audio_base64=audio_base64,
            content_type="audio/mpeg",
            provider="azure-speech",
        )

    def synthesize_dual_commentary(
        self,
        primary_text: str,
        secondary_text: str,
        language: str,
        primary_voice: str | None = None,
        secondary_voice: str | None = None,
    ) -> SpeechResult:
        if not primary_text.strip() or not secondary_text.strip():
            raise ValueError("Primary and secondary commentary text are required")
        if not self.is_configured():
            raise RuntimeError("Azure Speech is not configured")

        try:
            import azure.cognitiveservices.speech as speechsdk  # type: ignore[import-not-found]
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("Azure Speech SDK is not installed") from exc

        speech_config = speechsdk.SpeechConfig(
            endpoint=self._endpoint,
            subscription=self._key,
        )
        speech_config.speech_synthesis_language = language
        speech_config.set_speech_synthesis_output_format(
            speechsdk.SpeechSynthesisOutputFormat.Audio24Khz48KBitRateMonoMp3,
        )

        voice_a, voice_b = self._commentator_pair_for(language)
        ssml = self._dual_commentary_ssml(
            primary_text=primary_text,
            secondary_text=secondary_text,
            language=language,
            primary_voice=primary_voice or voice_a,
            secondary_voice=secondary_voice or voice_b,
        )

        synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=None)
        result = synthesizer.speak_ssml_async(ssml).get()

        if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
            details = speechsdk.CancellationDetails(result)
            logger.warning("Azure Speech dual synthesis failed: %s", details.reason)
            raise RuntimeError("Azure Speech dual synthesis failed")

        audio_base64 = base64.b64encode(result.audio_data).decode("utf-8")
        return SpeechResult(
            audio_base64=audio_base64,
            content_type="audio/mpeg",
            provider="azure-speech-duo",
        )

    @staticmethod
    def _default_voice_for(language: str) -> str:
        mapping = {
            "en-GB": "en-GB-RyanNeural",
            "sw-KE": "sw-KE-RafikiNeural",
            "fr-FR": "fr-FR-DeniseNeural",
        }
        return mapping.get(language, "en-GB-RyanNeural")

    @staticmethod
    def _default_commentator_pair_for(language: str) -> tuple[str, str]:
        mapping = {
            "en-GB": ("en-GB-RyanNeural", "en-GB-ThomasNeural"),
            "sw-KE": ("sw-KE-RafikiNeural", "en-GB-ThomasNeural"),
            "fr-FR": ("fr-FR-HenriNeural", "en-GB-ThomasNeural"),
        }
        return mapping.get(language, ("en-GB-RyanNeural", "en-GB-ThomasNeural"))

    @staticmethod
    def _single_commentary_ssml(text: str, language: str, voice: str, rate: str, pitch: str) -> str:
        clean = AzureSpeechSynthesizer._humanize_text(text)
        return (
            f"<speak version='1.0' xml:lang='{language}' xmlns='http://www.w3.org/2001/10/synthesis'>"
            f"<voice name='{voice}'>"
            f"<prosody rate='{rate}' pitch='{pitch}' volume='+0%'>"
            f"{escape(clean)}"
            "</prosody>"
            "</voice>"
            "</speak>"
        )

    @staticmethod
    def _dual_commentary_ssml(
        primary_text: str,
        secondary_text: str,
        language: str,
        primary_voice: str,
        secondary_voice: str,
    ) -> str:
        primary = escape(AzureSpeechSynthesizer._humanize_text(primary_text))
        secondary = escape(AzureSpeechSynthesizer._humanize_text(secondary_text))
        return (
            f"<speak version='1.0' xml:lang='{language}' xmlns='http://www.w3.org/2001/10/synthesis'>"
            f"<voice name='{primary_voice}'><prosody rate='+9%' pitch='+1st' volume='+2dB'>{primary}</prosody></voice>"
            "<break time='220ms'/>"
            f"<voice name='{secondary_voice}'><prosody rate='-1%' pitch='-2st' volume='+0dB'>{secondary}</prosody></voice>"
            "</speak>"
        )

    def _commentator_pair_for(self, language: str) -> tuple[str, str]:
        base_a, base_b = self._default_commentator_pair_for(language)
        return self._commentator_a_voice or base_a, self._commentator_b_voice or base_b

    @staticmethod
    def _humanize_text(text: str) -> str:
        normalized = " ".join(text.strip().split())
        normalized = normalized.replace(" xThreat ", " expected threat ")
        normalized = normalized.replace("xThreat", "expected threat")
        normalized = normalized.replace("team_a", "Team A")
        normalized = normalized.replace("team_b", "Team B")
        if "," not in normalized and len(normalized.split()) > 12:
            words = normalized.split()
            midpoint = len(words) // 2
            normalized = " ".join(words[:midpoint]) + ", " + " ".join(words[midpoint:])
        return normalized
