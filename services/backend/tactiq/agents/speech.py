from __future__ import annotations

from dataclasses import dataclass
import base64
import logging
import os
from urllib.parse import urlparse
from xml.sax.saxutils import escape

import requests

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
        self._region = os.getenv("AZURE_SPEECH_REGION")
        self._force_rest = os.getenv("AZURE_SPEECH_FORCE_REST", "true").lower() in {"1", "true", "yes", "on"}
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

        ssml = self._single_commentary_ssml(
            text=text,
            language=language,
            voice=voice or self._default_voice_for(language),
            rate=rate,
            pitch=pitch,
        )
        if self._force_rest:
            return self._synthesize_with_rest(ssml=ssml, provider="azure-speech-rest")

        try:
            return self._synthesize_with_sdk(ssml=ssml, language=language, provider="azure-speech")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Azure Speech SDK path failed, falling back to REST: %s", exc)
            return self._synthesize_with_rest(ssml=ssml, provider="azure-speech-rest")

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

        voice_a, voice_b = self._commentator_pair_for(language)

        primary_result = self.synthesize(
            text=primary_text,
            language=language,
            voice=primary_voice or voice_a,
            rate="+8%",
            pitch="+1st",
        )
        secondary_result = self.synthesize(
            text=secondary_text,
            language=language,
            voice=secondary_voice or voice_b,
            rate="-4%",
            pitch="-3st",
        )

        primary_audio = base64.b64decode(primary_result.audio_base64)
        secondary_audio = base64.b64decode(secondary_result.audio_base64)
        stitched_audio = primary_audio + secondary_audio

        return SpeechResult(
            audio_base64=base64.b64encode(stitched_audio).decode("utf-8"),
            content_type="audio/mpeg",
            provider=f"azure-speech-duo-stitch({primary_result.provider},{secondary_result.provider})",
        )

    def _synthesize_with_sdk(self, ssml: str, language: str, provider: str) -> SpeechResult:
        try:
            import azure.cognitiveservices.speech as speechsdk  # type: ignore[import-not-found]
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("Azure Speech SDK is not installed") from exc

        if self._region:
            speech_config = speechsdk.SpeechConfig(
                subscription=self._key,
                region=self._region,
            )
        else:
            speech_config = speechsdk.SpeechConfig(
                endpoint=self._endpoint,
                subscription=self._key,
            )
        speech_config.speech_synthesis_language = language
        speech_config.set_speech_synthesis_output_format(
            speechsdk.SpeechSynthesisOutputFormat.Audio24Khz48KBitRateMonoMp3,
        )
        synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=None)
        result = synthesizer.speak_ssml_async(ssml).get()
        if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
            details = speechsdk.CancellationDetails(result)
            logger.warning("Azure Speech SDK synthesis failed: %s", details.reason)
            raise RuntimeError("Azure Speech SDK synthesis failed")

        audio_base64 = base64.b64encode(result.audio_data).decode("utf-8")
        return SpeechResult(audio_base64=audio_base64, content_type="audio/mpeg", provider=provider)

    def _synthesize_with_rest(self, ssml: str, provider: str) -> SpeechResult:
        if not self._endpoint or not self._key:
            raise RuntimeError("Azure Speech is not configured")

        errors: list[str] = []
        for endpoint in self._speech_rest_urls(self._endpoint, self._region):
            headers = {
                "Ocp-Apim-Subscription-Key": self._key,
                "Content-Type": "application/ssml+xml",
                "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
                "User-Agent": "tactiq",
            }
            if self._region:
                headers["Ocp-Apim-Subscription-Region"] = self._region

            for attempt in range(1, 4):
                try:
                    response = requests.post(
                        endpoint,
                        data=ssml.encode("utf-8"),
                        headers=headers,
                        timeout=12,
                    )
                    if response.status_code >= 400:
                        detail = response.text[:180] if response.text else ""
                        errors.append(f"{endpoint} attempt {attempt} -> HTTP {response.status_code} {detail}")
                        continue

                    audio_bytes = response.content
                    audio_base64 = base64.b64encode(audio_bytes).decode("utf-8")
                    return SpeechResult(audio_base64=audio_base64, content_type="audio/mpeg", provider=provider)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{endpoint} attempt {attempt} -> {type(exc).__name__} {str(exc)[:120]}")

        logger.warning("Azure Speech REST synthesis failed on all endpoints: %s", "; ".join(errors))
        raise RuntimeError("Azure Speech synthesis failed")

    @staticmethod
    def _speech_rest_urls(endpoint: str, region: str | None) -> list[str]:
        urls: list[str] = []
        if region:
            urls.append(f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1")

        normalized = endpoint.strip()
        if not normalized:
            raise RuntimeError("Azure Speech endpoint is not configured")

        if "cognitiveservices/v1" in normalized:
            urls.append(normalized)
            return list(dict.fromkeys(urls))

        parsed = urlparse(normalized)
        if not parsed.scheme:
            parsed = urlparse(f"https://{normalized}")

        if not parsed.netloc:
            raise RuntimeError("Azure Speech endpoint is invalid")

        urls.append(f"{parsed.scheme}://{parsed.netloc}/cognitiveservices/v1")
        urls.append(f"{parsed.scheme}://{parsed.netloc}/tts/cognitiveservices/v1")
        return list(dict.fromkeys(urls))

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
