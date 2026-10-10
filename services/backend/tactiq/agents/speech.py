from __future__ import annotations

from dataclasses import dataclass
import base64
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import logging
import os
import time
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
        self._default_voice = os.getenv("AZURE_SPEECH_DEFAULT_VOICE")
        self._default_commentator_a_voice = os.getenv("AZURE_SPEECH_DEFAULT_COMMENTATOR_A_VOICE")
        self._default_commentator_b_voice = os.getenv("AZURE_SPEECH_DEFAULT_COMMENTATOR_B_VOICE")
        self._commentator_a_voice = os.getenv("AZURE_SPEECH_COMMENTATOR_A_VOICE")
        self._commentator_b_voice = os.getenv("AZURE_SPEECH_COMMENTATOR_B_VOICE")
        self._default_voice_by_language = self._load_voice_map(
            "AZURE_SPEECH_DEFAULT_VOICE_BY_LANGUAGE_JSON",
        )
        self._default_commentator_pair_by_language = self._load_pair_map(
            "AZURE_SPEECH_DEFAULT_COMMENTATOR_PAIR_BY_LANGUAGE_JSON",
        )

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
                        status = response.status_code
                        errors.append(f"{endpoint} attempt {attempt} -> HTTP {status} {detail}")

                        if not self._is_retryable_http_status(status):
                            break

                        if attempt < 3:
                            delay = self._retry_delay_seconds(response.headers.get("Retry-After"), attempt)
                            time.sleep(delay)
                        continue

                    audio_bytes = response.content
                    if not audio_bytes:
                        errors.append(f"{endpoint} attempt {attempt} -> HTTP 200 empty audio body")
                        if attempt < 3:
                            time.sleep(self._retry_delay_seconds(response.headers.get("Retry-After"), attempt))
                        continue

                    audio_base64 = base64.b64encode(audio_bytes).decode("utf-8")
                    return SpeechResult(audio_base64=audio_base64, content_type="audio/mpeg", provider=provider)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{endpoint} attempt {attempt} -> {type(exc).__name__} {str(exc)[:120]}")
                    if attempt < 3:
                        time.sleep(self._retry_delay_seconds(None, attempt))

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
    def _is_retryable_http_status(status_code: int) -> bool:
        return status_code in {408, 425, 429, 500, 502, 503, 504}

    @staticmethod
    def _retry_delay_seconds(retry_after_header: str | None, attempt: int) -> float:
        if retry_after_header:
            retry_after = retry_after_header.strip()
            if retry_after.isdigit():
                return min(8.0, max(0.0, float(retry_after)))
            try:
                dt = parsedate_to_datetime(retry_after)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                seconds = (dt - datetime.now(timezone.utc)).total_seconds()
                return min(8.0, max(0.0, seconds))
            except Exception:  # noqa: BLE001
                pass

        return min(8.0, 0.6 * (2 ** max(0, attempt - 1)))

    @staticmethod
    def _load_voice_map(env_var: str) -> dict[str, str]:
        raw = (os.getenv(env_var) or "").strip()
        if not raw:
            return {}

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{env_var} must be valid JSON") from exc

        if not isinstance(parsed, dict):
            raise RuntimeError(f"{env_var} must be a JSON object")

        mapping: dict[str, str] = {}
        for key, value in parsed.items():
            lang = str(key).strip()
            voice = str(value).strip()
            if lang and voice:
                mapping[lang] = voice
        return mapping

    @staticmethod
    def _load_pair_map(env_var: str) -> dict[str, tuple[str, str]]:
        raw = (os.getenv(env_var) or "").strip()
        if not raw:
            return {}

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{env_var} must be valid JSON") from exc

        if not isinstance(parsed, dict):
            raise RuntimeError(f"{env_var} must be a JSON object")

        mapping: dict[str, tuple[str, str]] = {}
        for key, value in parsed.items():
            lang = str(key).strip()
            if not lang:
                continue

            if isinstance(value, list | tuple) and len(value) == 2:
                voice_a = str(value[0]).strip()
                voice_b = str(value[1]).strip()
            elif isinstance(value, dict):
                voice_a = str(value.get("primary") or value.get("a") or "").strip()
                voice_b = str(value.get("secondary") or value.get("b") or "").strip()
            else:
                continue

            if voice_a and voice_b:
                mapping[lang] = (voice_a, voice_b)

        return mapping

    def _default_voice_for(self, language: str) -> str:
        mapped = self._default_voice_by_language.get(language)
        if mapped:
            return mapped
        if self._default_voice:
            return self._default_voice
        raise RuntimeError(
            "No default voice configured. Set AZURE_SPEECH_DEFAULT_VOICE or "
            "AZURE_SPEECH_DEFAULT_VOICE_BY_LANGUAGE_JSON, or pass voice explicitly.",
        )

    def _default_commentator_pair_for(self, language: str) -> tuple[str, str]:
        mapped = self._default_commentator_pair_by_language.get(language)
        if mapped:
            return mapped

        if self._default_commentator_a_voice and self._default_commentator_b_voice:
            return self._default_commentator_a_voice, self._default_commentator_b_voice

        raise RuntimeError(
            "No default commentator pair configured. Set AZURE_SPEECH_DEFAULT_COMMENTATOR_A_VOICE and "
            "AZURE_SPEECH_DEFAULT_COMMENTATOR_B_VOICE, or configure "
            "AZURE_SPEECH_DEFAULT_COMMENTATOR_PAIR_BY_LANGUAGE_JSON.",
        )

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
