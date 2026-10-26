from __future__ import annotations

from dataclasses import dataclass
from collections import OrderedDict
import base64
import hashlib
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from threading import Lock
from urllib.parse import urlparse
from xml.sax.saxutils import escape, quoteattr

import requests
from tactiq.agents.commentary import CommentaryTurn
from tactiq.agents.localization import LANGUAGES

logger = logging.getLogger(__name__)


@dataclass
class SpeechResult:
    audio_base64: str
    content_type: str
    provider: str


class AzureSpeechSynthesizer:
    def __init__(self, *, metered: bool = False) -> None:
        self._endpoint = os.getenv("AZURE_SPEECH_ENDPOINT")
        self._key = os.getenv("AZURE_SPEECH_API_KEY")
        self._region = os.getenv("AZURE_SPEECH_REGION")
        self._force_rest = os.getenv("AZURE_SPEECH_FORCE_REST", "true").lower() in {"1", "true", "yes", "on"}
        self._maximum_attempts = 1 if metered else 3
        self._single_endpoint = metered
        if metered:
            self._force_rest = True
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
        self._audio_cache: OrderedDict[tuple[str, str], tuple[float, SpeechResult]] = OrderedDict()
        self._cache_lock = Lock()

    def capabilities(self) -> dict:
        languages = {}
        for language in LANGUAGES:
            single = dual = False
            try:
                self._validate_voice(language, self._default_voice_for(language))
                single = True
            except (ValueError, RuntimeError):
                pass
            try:
                a, b = self._commentator_pair_for(language)
                self._validate_voice(language, a)
                self._validate_voice(language, b)
                dual = a != b
            except (ValueError, RuntimeError):
                pass
            languages[language] = {"single": single, "dual": dual}
        return {"configured": self.is_configured(), "languages": languages, "health_verified": False}

    def _cached_audio(self, tenant: str, ssml: str) -> SpeechResult | None:
        key = tenant, hashlib.sha256(ssml.encode()).hexdigest()
        with self._cache_lock:
            entry = self._audio_cache.get(key)
            if entry and time.monotonic() - entry[0] <= 300:
                self._audio_cache.move_to_end(key)
                return entry[1]
            self._audio_cache.pop(key, None)
            return None

    def _cache_audio(self, tenant: str, ssml: str, result: SpeechResult) -> SpeechResult:
        key = tenant, hashlib.sha256(ssml.encode()).hexdigest()
        with self._cache_lock:
            self._audio_cache[key] = time.monotonic(), result
            while len(self._audio_cache) > 24 or sum(len(item[1].audio_base64) for item in self._audio_cache.values()) > 8 * 1024 * 1024:
                self._audio_cache.popitem(last=False)
        return result

    def is_configured(self) -> bool:
        return bool(self._key and (self._endpoint or self._region))

    def cache_identity(self, language: str, *, dual: bool) -> str:
        voices = self._commentator_pair_for(language) if dual else (self._default_voice_for(language),)
        return hashlib.sha256(repr((self._endpoint, self._region, language, voices, "owned-speech-v1")).encode()).hexdigest()

    def synthesize(
        self,
        text: str,
        language: str,
        voice: str | None = None,
        rate: str = "+2%",
        pitch: str = "+1st",
        *,
        tenant_id: str = "local",
    ) -> SpeechResult:
        if not text.strip():
            raise ValueError("Text is required for speech synthesis")
        if len(text) > 8000:
            raise ValueError("Speech text exceeds the supported length")
        if not self.is_configured():
            raise RuntimeError("Azure Speech is not configured")

        ssml = self._single_commentary_ssml(
            text=text,
            language=language,
            voice=voice or self._default_voice_for(language),
            rate=rate,
            pitch=pitch,
        )
        cached = self._cached_audio(tenant_id, ssml)
        if cached:
            return cached
        if self._force_rest:
            return self._cache_audio(tenant_id, ssml, self._synthesize_with_rest(ssml=ssml, provider="azure-speech-rest"))

        try:
            return self._cache_audio(tenant_id, ssml, self._synthesize_with_sdk(ssml=ssml, language=language, provider="azure-speech"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Azure Speech SDK path failed, falling back to REST: %s", exc)
            return self._cache_audio(tenant_id, ssml, self._synthesize_with_rest(ssml=ssml, provider="azure-speech-rest"))

    def synthesize_turns(
        self, turns: list[CommentaryTurn], language: str, primary_voice: str | None = None,
        secondary_voice: str | None = None, *, tenant_id: str = "local",
    ) -> SpeechResult:
        if not 2 <= len(turns) <= 6 or any(not turn.text.strip() for turn in turns):
            raise ValueError("Ordered commentary requires two to six non-empty turns")
        if any(turn.role != ("lead" if index % 2 == 0 else "analyst") for index, turn in enumerate(turns)):
            raise ValueError("Commentary must alternate lead and analyst voices")
        if sum(len(turn.text) for turn in turns) > 8000:
            raise ValueError("Commentary exceeds the supported speech length")
        if not self.is_configured():
            raise RuntimeError("Azure Speech is not configured")
        a, b = self._commentator_pair_for(language, primary_voice, secondary_voice)
        self._validate_voice(language, a)
        self._validate_voice(language, b)
        if a == b:
            raise ValueError("Dual commentary requires two distinct configured voices")
        parts = []
        for index, turn in enumerate(turns):
            voice, rate, pitch = (a, "+6%", "+1st") if turn.role == "lead" else (b, "-2%", "-2st")
            pause = "<break time='180ms'/>" if index < len(turns) - 1 else ""
            parts.append(
                f"<voice name={quoteattr(voice)}><prosody rate={quoteattr(rate)} pitch={quoteattr(pitch)}>"
                f"{escape(self._humanize_text(turn.text))}</prosody>{pause}</voice>"
            )
        ssml = (f"<speak version='1.0' xml:lang={quoteattr(language)} xmlns='http://www.w3.org/2001/10/synthesis'>"
                + "".join(parts) + "</speak>")
        cached = self._cached_audio(tenant_id, ssml)
        if cached:
            return cached
        result = self._synthesize_with_rest(ssml=ssml, provider="azure-speech-ordered-voices-rest")
        return self._cache_audio(tenant_id, ssml, result)

    def synthesize_dual_commentary(
        self,
        primary_text: str,
        secondary_text: str,
        language: str,
        primary_voice: str | None = None,
        secondary_voice: str | None = None,
        *,
        tenant_id: str = "local",
    ) -> SpeechResult:
        if not primary_text.strip() or not secondary_text.strip():
            raise ValueError("Primary and secondary commentary text are required")
        if not self.is_configured():
            raise RuntimeError("Azure Speech is not configured")

        voice_a, voice_b = self._commentator_pair_for(language, primary_voice, secondary_voice)
        self._validate_voice(language, voice_a)
        self._validate_voice(language, voice_b)
        if voice_a == voice_b:
            raise ValueError("Dual commentary requires two distinct configured voices")

        primary_result = self.synthesize(
            text=primary_text,
            language=language,
            voice=primary_voice or voice_a,
            rate="+8%",
            pitch="+1st",
            tenant_id=tenant_id,
        )
        secondary_result = self.synthesize(
            text=secondary_text,
            language=language,
            voice=secondary_voice or voice_b,
            rate="-4%",
            pitch="-3st",
            tenant_id=tenant_id,
        )

        primary_audio = base64.b64decode(primary_result.audio_base64)
        secondary_audio = base64.b64decode(secondary_result.audio_base64)
        stitched_audio = self._join_audio(primary_audio, secondary_audio)

        return SpeechResult(
            audio_base64=base64.b64encode(stitched_audio).decode("utf-8"),
            content_type="audio/mpeg",
            provider=f"azure-speech-duo-stitch({primary_result.provider},{secondary_result.provider})",
        )

    @staticmethod
    def _join_audio(primary: bytes, secondary: bytes) -> bytes:
        binary = shutil.which("ffmpeg")
        if not binary:
            raise RuntimeError("FFmpeg is required to compose dual commentary audio")
        with tempfile.TemporaryDirectory(prefix="tactiq-speech-") as directory:
            root = Path(directory)
            first, second, output = root / "lead.mp3", root / "analyst.mp3", root / "commentary.mp3"
            first.write_bytes(primary)
            second.write_bytes(secondary)
            try:
                result = subprocess.run(
                    [binary, "-hide_banner", "-loglevel", "error", "-y", "-i", str(first), "-i", str(second),
                     "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1[a]", "-map", "[a]",
                     "-c:a", "libmp3lame", "-b:a", "48k", str(output)],
                    capture_output=True, timeout=20, check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise RuntimeError("Unable to compose commentary audio") from exc
            if result.returncode or not output.is_file() or output.stat().st_size == 0:
                raise RuntimeError("Unable to decode and compose commentary audio")
            return output.read_bytes()

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
        if not self.is_configured():
            raise RuntimeError("Azure Speech is not configured")

        errors: list[str] = []
        deadline = time.monotonic() + 25
        endpoints = self._speech_rest_urls(self._endpoint or "", self._region)
        for endpoint in endpoints[:1] if self._single_endpoint else endpoints:
            headers = {
                "Ocp-Apim-Subscription-Key": self._key,
                "Content-Type": "application/ssml+xml",
                "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
                "User-Agent": "tactiq",
            }
            if self._region:
                headers["Ocp-Apim-Subscription-Region"] = self._region

            for attempt in range(1, self._maximum_attempts + 1):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                timeout_seconds = min(12.0, remaining)
                response = None
                try:
                    response = requests.post(
                        endpoint,
                        data=ssml.encode("utf-8"),
                        headers=headers,
                        timeout=timeout_seconds,
                    )
                    if response.status_code >= 400:
                        status = response.status_code
                        errors.append(f"attempt {attempt}: HTTP {status}")
                        if status in {401, 403}:
                            raise RuntimeError(f"Azure Speech authentication was rejected (HTTP {status})")

                        if not self._is_retryable_http_status(status):
                            break

                        if attempt < self._maximum_attempts:
                            delay = self._retry_delay_seconds(response.headers.get("Retry-After"), attempt)
                            time.sleep(min(delay, max(0, deadline - time.monotonic())))
                        continue

                    audio_bytes = response.content
                    if not audio_bytes:
                        errors.append(f"attempt {attempt}: empty audio body")
                        if attempt < self._maximum_attempts:
                            time.sleep(min(self._retry_delay_seconds(response.headers.get("Retry-After"), attempt),
                                           max(0, deadline - time.monotonic())))
                        continue

                    audio_base64 = base64.b64encode(audio_bytes).decode("utf-8")
                    return SpeechResult(audio_base64=audio_base64, content_type="audio/mpeg", provider=provider)
                except requests.RequestException as exc:
                    errors.append(f"attempt {attempt}: {type(exc).__name__}")
                    if attempt < self._maximum_attempts:
                        time.sleep(min(self._retry_delay_seconds(None, attempt), max(0, deadline - time.monotonic())))
                finally:
                    if response is not None:
                        response.close()

        logger.warning("Azure Speech REST synthesis failed on all endpoints: %s", "; ".join(errors))
        raise RuntimeError("Azure Speech synthesis failed")

    @staticmethod
    def _speech_rest_urls(endpoint: str, region: str | None) -> list[str]:
        urls: list[str] = []
        if region:
            if not re.fullmatch(r"[a-z0-9-]{1,64}", region):
                raise RuntimeError("Azure Speech region is invalid")
            urls.append(f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1")

        normalized = endpoint.strip()
        if not normalized:
            if urls:
                return list(dict.fromkeys(urls))
            raise RuntimeError("Azure Speech endpoint or region is not configured")

        parsed = urlparse(normalized)
        if not parsed.scheme:
            parsed = urlparse(f"https://{normalized}")

        if not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.scheme not in {"http", "https"}:
            raise RuntimeError("Azure Speech endpoint is invalid")
        if os.getenv("APP_ENV", "local").lower() == "production" and parsed.scheme != "https":
            raise RuntimeError("Azure Speech endpoint must use HTTPS in production")
        if parsed.path.rstrip("/").endswith("cognitiveservices/v1"):
            urls.append(normalized)
            return list(dict.fromkeys(urls))

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
        pair = self._default_commentator_pair_by_language.get(language)
        voice = (pair[0] if pair else None) or self._default_voice or self._commentator_a_voice or self._default_commentator_a_voice
        if voice:
            return voice
        raise RuntimeError(
            "No default voice configured. Set AZURE_SPEECH_DEFAULT_VOICE or "
            "a matching-language voice map/lead commentator, or pass voice explicitly.",
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
        AzureSpeechSynthesizer._validate_voice(language, voice)
        rate_match = re.fullmatch(r"([+-]?\d+)%", rate)
        pitch_match = re.fullmatch(r"([+-]?\d+)st", pitch)
        if not rate_match or not -50 <= int(rate_match[1]) <= 100:
            raise ValueError("Speech rate must be a percentage in [-50, 100]")
        if not pitch_match or not -10 <= int(pitch_match[1]) <= 10:
            raise ValueError("Speech pitch must be a semitone offset in [-10, 10]")
        clean = AzureSpeechSynthesizer._humanize_text(text)
        return (
            f"<speak version='1.0' xml:lang={quoteattr(language)} xmlns='http://www.w3.org/2001/10/synthesis'>"
            f"<voice name={quoteattr(voice)}>"
            f"<prosody rate={quoteattr(rate)} pitch={quoteattr(pitch)} volume='+0%'>"
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
        AzureSpeechSynthesizer._validate_voice(language, primary_voice)
        AzureSpeechSynthesizer._validate_voice(language, secondary_voice)
        primary = escape(AzureSpeechSynthesizer._humanize_text(primary_text))
        secondary = escape(AzureSpeechSynthesizer._humanize_text(secondary_text))
        return (
            f"<speak version='1.0' xml:lang='{language}' xmlns='http://www.w3.org/2001/10/synthesis'>"
            f"<voice name='{primary_voice}'><prosody rate='+9%' pitch='+1st' volume='+2dB'>{primary}</prosody></voice>"
            "<break time='220ms'/>"
            f"<voice name='{secondary_voice}'><prosody rate='-1%' pitch='-2st' volume='+0dB'>{secondary}</prosody></voice>"
            "</speak>"
        )

    def _commentator_pair_for(
        self, language: str, primary_voice: str | None = None, secondary_voice: str | None = None
    ) -> tuple[str, str]:
        mapped = self._default_commentator_pair_by_language.get(language)
        voice_a = primary_voice or (mapped[0] if mapped else None) or self._commentator_a_voice or self._default_commentator_a_voice
        voice_b = secondary_voice or (mapped[1] if mapped else None) or self._commentator_b_voice or self._default_commentator_b_voice
        if not voice_a or not voice_b:
            raise RuntimeError(
                "Configure both commentator voices, supply a complete language-specific pair, "
                "or pass the missing voice explicitly."
            )
        return voice_a, voice_b

    @staticmethod
    def _validate_voice(language: str, voice: str) -> None:
        if not re.fullmatch(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})+", language):
            raise ValueError("Speech language must be a supported locale identifier")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", voice):
            raise ValueError("Speech voice identifier is invalid")
        voice_language = re.match(r"^([a-z]{2,3})-[A-Z]{2,3}-", voice)
        if (voice_language and voice_language[1] != language.split("-")[0]
                and "multilingual" not in voice.lower()):
            raise ValueError("Configured voice does not support the requested language; configure a matching-language voice")

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
