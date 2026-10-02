from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
from typing import Any, Protocol

from tactiq.domain.models import Insight

logger = logging.getLogger(__name__)


@dataclass
class NarrativeResult:
    title: str
    body: str
    audience_mode: str
    language: str
    fallback_used: bool
    provider: str = "deterministic"


class NarrativeGenerator(Protocol):
    def generate(
        self,
        insight: Insight,
        audience_mode: str,
        language: str,
        selected_player_id: str | None = None,
    ) -> NarrativeResult: ...


class DeterministicNarrativeGenerator:
    def generate(
        self,
        insight: Insight,
        audience_mode: str,
        language: str,
        selected_player_id: str | None = None,
    ) -> NarrativeResult:
        base = insight.summary
        evidence = {item.metric: item.value for item in insight.evidence}

        if audience_mode == "analyst":
            body = (
                f"{base} "
                f"Window {insight.window_start_ms//1000}s-{insight.window_end_ms//1000}s: "
                f"progressive_actions={evidence.get('progressive_actions', evidence.get('team_b_progressive_actions', 'n/a'))}, "
                f"final_third_entries={evidence.get('final_third_entries', 'n/a')}, "
                f"shots={evidence.get('shots', 'n/a')}."
            )
        elif audience_mode == "player":
            player_text = selected_player_id or "the selected player"
            body = f"{base} Focus: {player_text} is central to this phase based on the evidence window."
        else:
            body = base

        if language == "sw-KE":
            body = (
                "Muktadha wa mechi: "
                + body
                .replace("Pressure Building", "Shinikizo Linaongezeka")
                .replace("Momentum Shift", "Mabadiliko ya Kasi ya Mechi")
            )
        elif language == "fr-FR":
            body = "Contexte du match : " + body

        return NarrativeResult(
            title=insight.title,
            body=body,
            audience_mode=audience_mode,
            language=language,
            fallback_used=True,
        )


class FoundryNarrativeGenerator:
    """
    Placeholder integration point for Phase 3.
    Real Foundry SDK integration is added behind this interface.
    """

    def __init__(self, fallback: NarrativeGenerator) -> None:
        self._fallback = fallback
        self._foundry_enabled = os.getenv("FOUNDRY_ENABLED", "false").lower() == "true"
        self._endpoint = os.getenv("AZURE_FOUNDRY_PROJECT_ENDPOINT")
        self._deployment = os.getenv("AZURE_FOUNDRY_PROJECT_DEPLOYMENT_NAME")
        self._api_key = os.getenv("AZURE_FOUNDRY_API_KEY")
        self._project_client: Any | None = None

    def generate(
        self,
        insight: Insight,
        audience_mode: str,
        language: str,
        selected_player_id: str | None = None,
    ) -> NarrativeResult:
        if not self._is_configured():
            return self._fallback.generate(
                insight=insight,
                audience_mode=audience_mode,
                language=language,
                selected_player_id=selected_player_id,
            )

        try:
            prompt = self._build_prompt(
                insight=insight,
                audience_mode=audience_mode,
                language=language,
                selected_player_id=selected_player_id,
            )
            response_text = self._call_foundry(prompt)
            parsed = self._parse_response(response_text)
            return NarrativeResult(
                title=parsed.get("title", insight.title),
                body=parsed.get("body", insight.summary),
                audience_mode=audience_mode,
                language=language,
                fallback_used=False,
                provider="foundry",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Foundry narrative generation failed; using fallback: %s", exc)
            fallback = self._fallback.generate(
                insight=insight,
                audience_mode=audience_mode,
                language=language,
                selected_player_id=selected_player_id,
            )
            fallback.provider = "deterministic"
            return fallback

    def _is_configured(self) -> bool:
        return bool(self._foundry_enabled and self._endpoint and self._deployment)

    def _build_prompt(
        self,
        insight: Insight,
        audience_mode: str,
        language: str,
        selected_player_id: str | None,
    ) -> str:
        payload = {
            "insight_type": insight.type,
            "title": insight.title,
            "summary": insight.summary,
            "confidence": insight.confidence,
            "window": {
                "start_ms": insight.window_start_ms,
                "end_ms": insight.window_end_ms,
            },
            "team_id": insight.team_id,
            "evidence": [
                {
                    "metric": item.metric,
                    "value": item.value,
                    "unit": item.unit,
                }
                for item in insight.evidence
            ],
            "audience_mode": audience_mode,
            "language": language,
            "selected_player_id": selected_player_id,
        }

        return (
            "You are a grounded football narrative generator. "
            "Use only provided evidence. Do not invent statistics. "
            "Return strict JSON with keys title and body.\n\n"
            f"INPUT_JSON:\n{json.dumps(payload, ensure_ascii=False)}"
        )

    def _parse_response(self, response_text: str) -> dict[str, str]:
        parsed = json.loads(response_text)
        if not isinstance(parsed, dict):
            raise ValueError("Foundry response is not a JSON object")
        title = str(parsed.get("title", "")).strip()
        body = str(parsed.get("body", "")).strip()
        if not body:
            raise ValueError("Foundry response body is empty")
        if not title:
            title = "Match Insight"
        return {"title": title, "body": body}

    def _ensure_client(self) -> Any:
        if self._project_client is not None:
            return self._project_client

        from azure.ai.projects import AIProjectClient  # type: ignore[import-not-found]
        from azure.core.credentials import AzureKeyCredential  # type: ignore[import-not-found]
        from azure.identity import DefaultAzureCredential  # type: ignore[import-not-found]

        if self._api_key:
            credential = AzureKeyCredential(self._api_key)
        else:
            credential = DefaultAzureCredential()

        self._project_client = AIProjectClient(endpoint=self._endpoint, credential=credential)
        return self._project_client

    def _call_foundry(self, prompt: str) -> str:
        client = self._ensure_client()
        messages = [
            {"role": "system", "content": "Return strict JSON only."},
            {"role": "user", "content": prompt},
        ]

        # Preferred path for current azure-ai-projects SDK versions.
        if hasattr(client, "get_openai_client"):
            openai_kwargs: dict[str, Any] = {}
            if self._api_key:
                # Override token-based default with explicit key auth when provided.
                openai_kwargs["api_key"] = self._api_key

            openai_client = client.get_openai_client(**openai_kwargs)
            response = openai_client.chat.completions.create(
                model=self._deployment,
                messages=messages,
                max_completion_tokens=220,
            )
        else:
            # Backward-compatibility path for older SDK surfaces.
            inference = getattr(client, "inference", None)
            if inference is None:
                raise RuntimeError("AIProjectClient inference API is unavailable")

            if hasattr(inference, "get_chat_completions_client"):
                chat_client = inference.get_chat_completions_client()
            else:
                raise RuntimeError("Chat completions client is unavailable in current Foundry SDK")

            response = chat_client.complete(
                model=self._deployment,
                messages=messages,
                max_tokens=220,
            )

        choices = getattr(response, "choices", None)
        if not choices:
            raise RuntimeError("Foundry response has no choices")

        first = choices[0]
        message = getattr(first, "message", None)
        content = getattr(message, "content", None) if message is not None else None

        if isinstance(content, str) and content.strip():
            return content

        if isinstance(content, list) and content:
            text_parts = []
            for part in content:
                part_text = getattr(part, "text", None) or (part.get("text") if isinstance(part, dict) else None)
                if part_text:
                    text_parts.append(str(part_text))
            merged = "\n".join(text_parts).strip()
            if merged:
                return merged

        raise RuntimeError("Could not extract text from Foundry response")
