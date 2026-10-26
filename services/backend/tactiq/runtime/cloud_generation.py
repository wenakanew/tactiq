"""Owned, conservatively reserved generation. Ambiguous calls are never replayed."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from typing import Literal

from tactiq.agents.commentary import CommentaryTurn
from tactiq.agents.narrative import DUAL_COMMENTARY_TOKEN_BOUND, DeterministicNarrativeGenerator, FoundryNarrativeGenerator
from tactiq.agents.speech import AzureSpeechSynthesizer
from tactiq.agents.usage import capture_model_usage
from tactiq.domain.models import Insight

from .access import AccessDenied, AccessStore, UsageIntent, UsagePolicy
from .cloud_store import CloudApplicationStore
from .contracts import MediaStore

logger = logging.getLogger(__name__)


class GenerationUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class GenerationSettings:
    model_enabled: bool = False
    speech_enabled: bool = False
    policy: UsagePolicy | None = None
    model_price: int = 0
    speech_price: int = 0

    def __post_init__(self) -> None:
        if self.model_enabled or self.speech_enabled:
            if self.policy is None:
                raise ValueError("Paid generation requires an explicit application allocation")
        for enabled, price in ((self.model_enabled, self.model_price), (self.speech_enabled, self.speech_price)):
            if enabled and (type(price) is not int or not 1 <= price <= 1_000_000_000):
                raise ValueError("Enabled providers require approved positive microUSD prices per million units")

    @classmethod
    def from_environment(cls) -> GenerationSettings:
        def enabled(name: str) -> bool:
            value = os.getenv(name, "false").strip().lower()
            if value not in {"true", "false"}:
                raise ValueError(f"{name} must be true or false")
            return value == "true"

        model = enabled("TACTIQ_CLOUD_MODEL_ENABLED")
        speech = enabled("TACTIQ_CLOUD_SPEECH_ENABLED")
        if not (model or speech):
            return cls()
        policy = UsagePolicy(
            float(os.environ["TACTIQ_AI_BUDGET_PERIOD_START"]),
            float(os.environ["TACTIQ_AI_BUDGET_PERIOD_END"]),
            int(os.environ["TACTIQ_AI_GLOBAL_LIMIT_MICROUSD"]),
            int(os.environ["TACTIQ_AI_TENANT_LIMIT_MICROUSD"]),
        )
        return cls(
            model, speech, policy,
            int(os.environ["TACTIQ_MODEL_PRICE_MICROUSD_PER_MILLION_TOKENS"]) if model else 0,
            int(os.environ["TACTIQ_SPEECH_PRICE_MICROUSD_PER_MILLION_CHARACTERS"]) if speech else 0,
        )

    def active(self) -> bool:
        return self.policy is not None and self.policy.period_start <= time.time() < self.policy.period_end


def _identity(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


class CloudGeneration:
    def __init__(
        self, settings: GenerationSettings, store: CloudApplicationStore, access: AccessStore, media: MediaStore,
        *, narrative: FoundryNarrativeGenerator | None = None, speech: AzureSpeechSynthesizer | None = None,
    ) -> None:
        self.settings, self.store, self.access, self.media = settings, store, access, media
        self.fallback = DeterministicNarrativeGenerator()
        self.narrative = narrative or FoundryNarrativeGenerator(self.fallback, metered=True)
        self.speech = speech or AzureSpeechSynthesizer(metered=True)

    def capabilities(self) -> dict:
        model = self.narrative.status()
        speech = self.speech.capabilities()
        model_ready = (self.settings.model_enabled and self.settings.active() and model["configured"]
                       and model["status"] != "cooldown")
        speech_ready = (self.settings.speech_enabled and self.settings.active() and speech["configured"]
                        and any(item["single"] or item["dual"] for item in speech["languages"].values()))
        return {
            "paid_generation_enabled": bool(model_ready or speech_ready),
            "cloud_model_enabled": bool(model_ready), "speech_configured": bool(speech_ready),
            "narrative": {**model, "configured": bool(model_ready),
                          "provider": "foundry-with-deterministic-fallback" if model_ready else "deterministic"},
            "speech": {**speech, "configured": bool(speech_ready)},
            "provider_health_verified": False,
            "usage_accounting": "reserved-with-verified-model-settlement" if self.settings.active() else "disabled",
        }

    def _reserve(self, proof: str, operation: str, meter: Literal["model_tokens", "speech_characters"], units: int):
        price = self.settings.model_price if meter == "model_tokens" else self.settings.speech_price
        return self.access.reserve_usage(
            proof, UsageIntent(operation, meter, units, max(1, (units * price + 999_999) // 1_000_000)),
        )

    def script(
        self, proof: str, tenant: str, match: str, kind: Literal["single", "dual"],
        insight: Insight, language: str, audience: str, player: str | None, *, allow_cloud: bool,
    ) -> dict:
        self.access.require_analysis(proof, match)
        if len(insight.model_dump_json().encode("utf-8")) > 32_000:
            raise ValueError("Selected evidence exceeds the generation input budget")
        configured = self.capabilities()["cloud_model_enabled"]
        paid = allow_cloud and configured
        operation = _identity({
            "version": 1, "match": match, "kind": kind, "insight": insight.model_dump(mode="json"),
            "language": language, "audience": audience, "player": player, "paid": paid,
            "model": self.narrative.cache_identity() if paid else "deterministic",
            "budget_period": self.settings.policy.period_start if paid else None,
        })
        cached = self.store.generation_result(tenant, match, operation)
        if cached is not None:
            return cached
        if paid:
            # Each role has <=64,000 serialized UTF-8 input bytes (including
            # profiles/schema/handoff), <=1,100 completion tokens and a
            # 4,096-token tool-free envelope; one byte bounds one input token.
            # No tools, history, repair loop or paid transport retries are used.
            units = DUAL_COMMENTARY_TOKEN_BOUND if kind == "dual" else FoundryNarrativeGenerator.single_request_token_bound(
                insight, audience, language, player,
            )
            reservation = self._reserve(proof, operation, "model_tokens", units)
            if reservation.replayed:
                raise GenerationUnavailable("Generation is in progress or its outcome is uncertain; no paid retry was made")
        generator = self.narrative if paid else self.fallback
        with capture_model_usage() as usage:
            if kind == "dual":
                generated = generator.generate_dual_commentary(insight, language)
            else:
                generated = generator.generate(insight, audience, language, player)
        result = asdict(generated)
        if kind == "dual":
            result["turns"] = [turn.model_dump() for turn in generated.turns]
        result["script_id"] = _identity({"match": match, "kind": kind, "result": result})
        result = self.store.save_script(tenant, match, result["script_id"], {"kind": kind, **result})
        self.store.save_generation_result(tenant, match, operation, result)
        if paid:
            actual = usage.complete(2 if kind == "dual" else 1)
            if (not generated.fallback_used and generated.provider in {"foundry", "foundry-agent-framework"}
                    and actual is not None):
                cost = (actual.total_tokens * self.settings.model_price + 999_999) // 1_000_000
                if actual.total_tokens > reservation.units or cost > reservation.maximum_cost_microusd:
                    logger.error("cloud_model_usage_exceeded_reservation")
                    raise GenerationUnavailable("Provider usage exceeded the approved reservation; review billing")
                # Durability precedes settlement; a crash can strand a reservation
                # but can never refund an ambiguous call or repeat a paid call.
                self.access.settle_usage(proof, reservation.reservation_id, actual.total_tokens, cost)
                logger.info("cloud_model_usage_settled", extra={
                    "input_tokens": actual.input_tokens, "output_tokens": actual.output_tokens,
                    "cost_microusd": cost,
                })
            else:
                logger.warning("cloud_model_usage_uncertain_reservation_retained")
        return result

    def synthesize(self, proof: str, tenant: str, match: str, script_id: str, *, dual: bool) -> dict:
        self.access.require_analysis(proof, match)
        script = self.store.load_script(tenant, match, script_id)
        if script is None:
            raise AccessDenied("Script is unavailable to this analysis")
        if script["kind"] != ("dual" if dual else "single"):
            raise ValueError("Speech route does not match this owned script")
        language = script["language"]
        capability = self.capabilities()
        if not capability["speech_configured"] or not capability["speech"]["languages"][language]["dual" if dual else "single"]:
            raise GenerationUnavailable("Speech requires configured voices and an active approved application allocation")
        operation = _identity({
            "version": 1, "match": match, "script": script_id, "kind": "speech",
            "voices": self.speech.cache_identity(language, dual=dual),
        })
        cached = self.store.generation_result(tenant, match, operation)
        if cached is None:
            turns = [CommentaryTurn.model_validate(turn) for turn in script.get("turns", [])] if dual else []
            text = " ".join(turn.text for turn in turns) if dual else script["body"]
            if not text.strip() or len(text) > 8000:
                raise ValueError("Owned script exceeds the speech input budget")
            # Includes SSML escaping and humanized identifiers; no SDK/HTTP
            # retries or alternate endpoints in the metered synthesizer.
            reservation = self._reserve(proof, operation, "speech_characters", len(text) * 5 + 2048)
            if reservation.replayed:
                raise GenerationUnavailable("Speech is in progress or its outcome is uncertain; no paid retry was made")
            try:
                audio = self.speech.synthesize_turns(turns, language, tenant_id=tenant) if dual else self.speech.synthesize(
                    text, language, tenant_id=tenant,
                )
                content = base64.b64decode(audio.audio_base64, validate=True)
                if not 1 <= len(content) <= 8 * 1024 * 1024:
                    raise ValueError("Speech response exceeds its bounded audio budget")
                reference = self.media.upload(tenant, match, io.BytesIO(content), size=len(content), content_type=audio.content_type)
                self.store.register_media(reference)
                cached = {"media_id": reference.media_id, "provider": audio.provider, "content_type": audio.content_type}
                self.store.save_generation_result(tenant, match, operation, cached)
            except Exception as exc:
                logger.warning("cloud_speech_failed", extra={"exception_type": type(exc).__name__})
                raise GenerationUnavailable("Speech provider operation failed; no automatic paid retry was made") from exc
        reference = self.store.load_media(tenant, match, cached["media_id"])
        if reference is None or reference.size > 8 * 1024 * 1024:
            raise GenerationUnavailable("Stored speech is unavailable")
        content = b"".join(self.media.download(reference, tenant_id=tenant, match_id=match))
        return {"provider": cached["provider"], "content_type": cached["content_type"],
                "audio_base64": base64.b64encode(content).decode("ascii")}
