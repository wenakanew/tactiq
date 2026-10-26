from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import os
import time
import hashlib
from typing import Any, Protocol

from tactiq.domain.models import Insight
from tactiq.agents.grounding import Claim, GeneratedLine, GeneratedNarrative, validate_text
from tactiq.agents.localization import language_copy, localized_insight
from tactiq.agents.usage import record_model_usage
from tactiq.agents.commentary import CommentaryTurn, GeneratedDialogue, GeneratedRole, deep_supported, deterministic_turns

logger = logging.getLogger(__name__)
NARRATIVE_SYSTEM_PROMPT = "Return strict JSON only."
NARRATIVE_MAX_COMPLETION_TOKENS = 1600
DUAL_COMMENTARY_TOKEN_BOUND = 2 * (64_000 + 1100 + 4096)


@dataclass
class NarrativeResult:
    title: str
    body: str
    audience_mode: str
    language: str
    fallback_used: bool
    provider: str = "deterministic"
    grounding: dict[str, Any] | None = None


@dataclass
class DualCommentaryResult:
    primary_text: str
    secondary_text: str
    language: str
    fallback_used: bool
    provider: str = "deterministic"
    audit: dict[str, Any] | None = None
    turns: list[CommentaryTurn] = field(default_factory=list)
    depth: str = "deep"


@dataclass
class CommentaryAgentResolution:
    name: str
    instructions: str
    version: str | None
    source: str


class NarrativeGenerator(Protocol):
    def generate(
        self,
        insight: Insight,
        audience_mode: str,
        language: str,
        selected_player_id: str | None = None,
    ) -> NarrativeResult: ...

    def generate_dual_commentary(
        self,
        insight: Insight,
        language: str,
    ) -> DualCommentaryResult: ...


class DeterministicNarrativeGenerator:
    def generate(
        self,
        insight: Insight,
        audience_mode: str,
        language: str,
        selected_player_id: str | None = None,
    ) -> NarrativeResult:
        title, _ = localized_insight(insight, language)
        copy = language_copy(language)
        turns = deterministic_turns(insight, language)
        detailed = "\n\n".join(turn.text for turn in turns)

        if audience_mode == "analyst":
            body = (
                f"{detailed} "
                f"{copy['window']} {insight.window_start_ms//1000}s-{insight.window_end_ms//1000}s: "
                + "; ".join(f"{item.metric}={item.value} {item.unit}" for item in insight.evidence)
            )
        elif audience_mode == "player":
            player_text = selected_player_id or "the selected player"
            supported = selected_player_id and selected_player_id in {
                insight.player_id, insight.context.get("supported_player_id")
            }
            body = f"{detailed} {copy['focus']}: {player_text}." if supported else copy["no_player"].format(player=player_text)
        else:
            body = detailed

        return NarrativeResult(
            title=title,
            body=body,
            audience_mode=audience_mode,
            language=language,
            fallback_used=True,
            grounding={"status": "deterministic", "localization": "domain-template" if language != "en-GB" else "source",
                       "human_locale_review_required": language != "en-GB"},
        )

    def generate_dual_commentary(
        self,
        insight: Insight,
        language: str,
    ) -> DualCommentaryResult:
        turns = deterministic_turns(insight, language)

        return DualCommentaryResult(
            primary_text=" ".join(turn.text for turn in turns if turn.role == "lead"),
            secondary_text=" ".join(turn.text for turn in turns if turn.role == "analyst"),
            language=language,
            fallback_used=True,
            provider="deterministic",
            audit={
                "orchestration_path": "deterministic",
                "agents": [],
                "tuning_version": None,
                "grounding_status": "deterministic",
                "human_locale_review_required": language != "en-GB",
            },
            turns=turns,
            depth=insight.context.get("depth", "deep"),
        )


class FoundryNarrativeGenerator:

    def __init__(self, fallback: NarrativeGenerator, *, metered: bool = False) -> None:
        self._fallback = fallback
        self._foundry_enabled = self._env_bool("FOUNDRY_ENABLED")
        self._endpoint = os.getenv("AZURE_FOUNDRY_PROJECT_ENDPOINT")
        self._deployment = os.getenv("AZURE_FOUNDRY_PROJECT_DEPLOYMENT_NAME")
        self._api_key = os.getenv("AZURE_FOUNDRY_API_KEY")
        self._commentary_playbyplay_agent_name = os.getenv("FOUNDRY_COMMENTARY_PLAYBYPLAY_AGENT_NAME")
        self._commentary_color_agent_name = os.getenv("FOUNDRY_COMMENTARY_COLOR_AGENT_NAME")
        self._commentary_tuning_version = os.getenv("FOUNDRY_COMMENTARY_PROMPT_TUNING_VERSION")
        self._commentary_playbyplay_profile = os.getenv("FOUNDRY_COMMENTARY_PLAYBYPLAY_PROFILE")
        self._commentary_color_profile = os.getenv("FOUNDRY_COMMENTARY_COLOR_PROFILE")
        self._use_direct_commentary_agents = self._env_bool("FOUNDRY_COMMENTARY_USE_DIRECT_AGENTS")
        self._use_agent_framework = self._env_bool("FOUNDRY_COMMENTARY_USE_AGENT_FRAMEWORK")
        self._metered = metered
        if metered:
            self._use_agent_framework = True
            self._use_direct_commentary_agents = False
            if any(len((profile or "").encode("utf-8")) > 4000 for profile in (
                self._commentary_playbyplay_profile, self._commentary_color_profile,
            )):
                raise ValueError("Metered commentary profiles exceed their input budget")
        self._project_client: Any | None = None
        self._agents_client: Any | None = None
        self._unavailable_until = 0.0
        self._last_failure: dict[str, Any] | None = None

    def status(self) -> dict[str, Any]:
        configured = bool(self._foundry_enabled and self._endpoint and self._deployment)
        cooling = configured and time.monotonic() < self._unavailable_until
        return {"configured": configured, "status": "cooldown" if cooling else "configured" if configured else "disabled",
                "last_failure": self._last_failure if cooling else None}

    def cache_identity(self) -> str:
        profile = (self._endpoint, self._deployment, self._commentary_playbyplay_agent_name, self._commentary_color_agent_name,
                   self._commentary_tuning_version, self._commentary_playbyplay_profile, self._commentary_color_profile,
                   self._use_direct_commentary_agents, self._use_agent_framework, self.status()["status"])
        return hashlib.sha256(repr(profile).encode()).hexdigest()

    def _record_failure(self, exc: Exception) -> dict[str, Any]:
        status = getattr(exc, "status_code", None)
        cause = exc
        for _ in range(10):
            if status is not None:
                break
            next_cause = cause.__cause__ or cause.__context__
            if next_cause is None:
                break
            cause = next_cause
            status = getattr(cause, "status_code", None)
        authentication_error = type(cause or exc).__name__ in {
            "ClientAuthenticationError", "CredentialUnavailableError", "AuthenticationError", "PermissionDeniedError",
        }
        reason = "quota" if status == 429 else "authentication" if status in {401, 403} or authentication_error else (
            "deployment" if status == 404 else "dependency_unavailable" if isinstance(exc, ImportError) else
            "invalid_output" if isinstance(exc, ValueError) else "service_error")
        failure = {"reason": reason, "status_code": status if isinstance(status, int) else None,
                   "exception_type": type(exc).__name__}
        self._last_failure = failure
        if status in {401, 403, 404, 429} or authentication_error:
            self._unavailable_until = time.monotonic() + 300
        return failure

    @staticmethod
    def _env_bool(name: str) -> bool:
        value = os.getenv(name)
        if value is None:
            return False
        return value.strip().lower() in {"1", "true", "yes", "on"}

    def generate(
        self,
        insight: Insight,
        audience_mode: str,
        language: str,
        selected_player_id: str | None = None,
    ) -> NarrativeResult:
        insight = insight.model_copy(update={"context": {**insight.context, "language": language}})
        if not self._is_configured():
            fallback = self._fallback.generate(
                insight=insight,
                audience_mode=audience_mode,
                language=language,
                selected_player_id=selected_player_id,
            )
            if self.status()["status"] == "cooldown":
                fallback.grounding = {**(fallback.grounding or {}), "provider_failure": self._last_failure}
            return fallback

        try:
            prompt = self._build_prompt(
                insight=insight,
                audience_mode=audience_mode,
                language=language,
                selected_player_id=selected_player_id,
            )
            response_text = self._call_foundry(prompt)
            parsed = GeneratedNarrative.model_validate_json(response_text)
            validate_text(f"{parsed.title} {parsed.body}", insight, parsed.claims, parsed.entities)
            rich = insight.context.get("depth", "deep") == "deep" and deep_supported(insight)
            words = len(parsed.body.split())
            if words > (220 if rich else 100) or rich and language == "en-GB" and words < 120:
                raise ValueError("Narrative does not meet its evidence and depth budget")
            return NarrativeResult(
                title=parsed.title,
                body=parsed.body,
                audience_mode=audience_mode,
                language=language,
                fallback_used=False,
                provider="foundry",
                grounding={"status": "structure-numbers-and-entity-ids-checked",
                           "claims": [claim.model_dump() for claim in parsed.claims]},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Foundry narrative generation failed; using fallback (%s)", type(exc).__name__)
            fallback = self._fallback.generate(
                insight=insight,
                audience_mode=audience_mode,
                language=language,
                selected_player_id=selected_player_id,
            )
            fallback.provider = "deterministic"
            fallback.grounding = {**(fallback.grounding or {}), "provider_failure": self._record_failure(exc)}
            return fallback

    def _is_configured(self) -> bool:
        return bool(self._foundry_enabled and self._endpoint and self._deployment and time.monotonic() >= self._unavailable_until)

    @staticmethod
    def _build_prompt(
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
                    "supporting_event_ids": item.supporting_event_ids[:8],
                }
                for item in insight.evidence
            ],
            "audience_mode": audience_mode,
            "language": language,
            "selected_player_id": selected_player_id,
            "supported_entities": [insight.team_id, *insight.context.get("known_player_ids", [])],
            "context": insight.context,
            "depth": insight.context.get("depth", "deep"),
        }

        return (
            "You are a grounded football narrative generator. "
            "Use only provided evidence. Do not invent statistics. "
            "Do not attribute contribution to an unsupported selected player. "
            "Localize the complete explanation to the requested locale. Preserve identifiers, values and units. "
            "Explicitly label synthetic input as simulated. An observation overview is not proof of passes, shots or tactics. "
            "Return strict JSON: title, body, claims, entities. "
            "Each numerical claim must include metric, value, and supporting_event_ids from the input. "
            "Claims have exactly those three keys; unit belongs to the input evidence, not a claim. "
            "List only referenced supported team/player IDs in entities, never supporting event IDs. "
            "Describe and qualify only this passage; do not discuss wider match periods, even in disclaimers. "
            "Use supplied names only, with sentence-case titles and no invented proper names.\n\n"
            "For a supported in-depth explanation, use 120-180 words covering the passage, evidence, sequence and limits. "
            "For sparse evidence or broadcast depth, be shorter; never pad or invent details. "
            f"OUTPUT_JSON_SCHEMA:\n{json.dumps(GeneratedNarrative.model_json_schema())}\n"
            f"INPUT_JSON:\n{json.dumps(payload, ensure_ascii=False)}"
        )

    @staticmethod
    def single_request_token_bound(
        insight: Insight, audience_mode: str, language: str, selected_player_id: str | None,
    ) -> int:
        scoped = insight.model_copy(update={"context": {**insight.context, "language": language}})
        prompt = FoundryNarrativeGenerator._build_prompt(scoped, audience_mode, language, selected_player_id)
        size = len((prompt + NARRATIVE_SYSTEM_PROMPT).encode("utf-8"))
        if size > 64_000:
            raise ValueError("Metered narrative exceeds its input budget")
        # At most one input token per UTF-8 byte, plus completion and a bounded
        # tool-free chat envelope; no second role request exists on this path.
        return size + NARRATIVE_MAX_COMPLETION_TOKENS + 4096

    def generate_dual_commentary(
        self,
        insight: Insight,
        language: str,
    ) -> DualCommentaryResult:
        insight = insight.model_copy(update={"context": {**insight.context, "language": language}})
        if not self._is_configured():
            fallback = self._fallback.generate_dual_commentary(insight=insight, language=language)
            if self.status()["status"] == "cooldown":
                fallback.audit = {**(fallback.audit or {}), "provider_failure": self._last_failure}
            return fallback

        try:
            if self._use_agent_framework:
                return self._generate_dual_commentary_via_framework(insight, language)
            if self._use_direct_commentary_agents:
                direct_result = self._generate_dual_commentary_via_agents(insight=insight, language=language)
                if direct_result is not None:
                    return direct_result

            prompt = self._build_dual_commentary_prompt(insight=insight, language=language)
            response_text = self._call_foundry(prompt)
            parsed = self._parse_dual_commentary_response(response_text)
            claims = [Claim.model_validate(claim) for claim in parsed.get("claims", [])]
            entities = parsed.get("entities", [])
            turns = [CommentaryTurn.model_validate(turn) for turn in parsed["turns"]]
            self._validate_dialogue(turns, insight, language)
            validate_text(" ".join(turn.text for turn in turns), insight, claims, entities)
            return DualCommentaryResult(
                primary_text=parsed["primary_text"],
                secondary_text=parsed["secondary_text"],
                language=language,
                fallback_used=False,
                provider="foundry",
                audit={
                    "orchestration_path": "foundry-prompt",
                    "agents": [
                        {
                            "name": self._commentary_playbyplay_agent_name,
                            "version": self._commentary_tuning_version,
                            "source": "prompt-profile",
                            "instructions": self._commentary_playbyplay_profile or "",
                        },
                        {
                            "name": self._commentary_color_agent_name,
                            "version": self._commentary_tuning_version,
                            "source": "prompt-profile",
                            "instructions": self._commentary_color_profile or "",
                        },
                    ],
                    "tuning_version": self._commentary_tuning_version,
                },
                turns=turns,
                depth=insight.context.get("depth", "deep"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Foundry dual commentary generation failed; using fallback (%s)", type(exc).__name__)
            fallback = self._fallback.generate_dual_commentary(insight=insight, language=language)
            fallback.provider = "deterministic"
            fallback.audit = {**(fallback.audit or {}), "provider_failure": self._record_failure(exc)}
            if self._use_agent_framework:
                fallback.audit.update({
                    "attempted_orchestration_path": "microsoft-agent-framework",
                    "trace": getattr(exc, "commentary_workflow_trace", []),
                    "request_id": getattr(exc, "commentary_request_id", None),
                })
            return fallback

    def _generate_dual_commentary_via_framework(self, insight: Insight, language: str) -> DualCommentaryResult:
        from tactiq.agents.workflow import RoleProfile, run_foundry_commentary, run_sync

        profiles = tuple(
            RoleProfile(
                role=role, name=name or f"tactiq-{role}",
                instructions=profile or default,
                version=(self._commentary_tuning_version or "unversioned") if profile else "builtin-v1",
                source="env-profile" if profile else "builtin-profile",
            )
            for role, name, profile, default in (
                ("lead", self._commentary_playbyplay_agent_name, self._commentary_playbyplay_profile,
                 "Describe the supported football passage and sequence as the lead commentator."),
                ("analyst", self._commentary_color_agent_name, self._commentary_color_profile,
                 "Explain the supplied measurements and source limitations as the analyst, without repeating the lead."),
            )
        )
        result = run_sync(lambda: run_foundry_commentary(
            insight, language, endpoint=self._endpoint, deployment=self._deployment, api_key=self._api_key,
            profiles=profiles, validate_dialogue=self._validate_dialogue,
        ))
        return DualCommentaryResult(
            primary_text=" ".join(turn.text for turn in result.turns if turn.role == "lead"),
            secondary_text=" ".join(turn.text for turn in result.turns if turn.role == "analyst"),
            language=language, fallback_used=False, provider="foundry-agent-framework",
            audit={**result.audit, "tuning_version": self._commentary_tuning_version},
            turns=result.turns, depth=insight.context.get("depth", "deep"),
        )

    @staticmethod
    def _validate_dialogue(turns: list[CommentaryTurn], insight: Insight, language: str) -> None:
        GeneratedDialogue(turns=turns)
        words = sum(len(turn.text.split()) for turn in turns)
        rich = insight.context.get("depth", "deep") == "deep" and deep_supported(insight)
        if words > (200 if rich else 80):
            raise ValueError("Commentary exceeds its playback depth budget")
        if rich and (len(turns) < 4 or language == "en-GB" and words < 120):
            raise ValueError("In-depth commentary does not substantively explain the supplied evidence")

    def _generate_dual_commentary_via_agents(self, insight: Insight, language: str) -> DualCommentaryResult | None:
        lead_resolution = self._resolve_commentary_agent_instructions(
            agent_name=self._commentary_playbyplay_agent_name,
            fallback_profile=self._commentary_playbyplay_profile,
        )
        analyst_resolution = self._resolve_commentary_agent_instructions(
            agent_name=self._commentary_color_agent_name,
            fallback_profile=self._commentary_color_profile,
        )

        if not lead_resolution or not analyst_resolution:
            return None

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
                    "supporting_event_ids": item.supporting_event_ids[:8],
                }
                for item in insight.evidence
            ],
            "language": language,
            "context": insight.context,
            "tuning_version": self._commentary_tuning_version,
        }

        rich = insight.context.get("depth", "deep") == "deep" and deep_supported(insight)
        role_count = 2 if rich else 1
        role_budget = "60-90 words across the two paragraphs" if rich else "15-30 words"
        lead_user_prompt = (
            f"Generate {role_count} ordered lead paragraphs for this passage, {role_budget}. "
            "First establish the moment; then describe the supported sequence. No labels or markdown. "
            "Return JSON with texts (an array), claims, entities; numerical claims require metric, value, supporting_event_ids. "
            f"INPUT_JSON: {json.dumps(payload, ensure_ascii=False)}"
        )
        contract = (
            "\nApplication output contract overrides any earlier formatting instructions: return JSON only with "
            "texts (an array), claims, entities. Use only supplied evidence, keep the requested language, "
            "and never invent entities, statistics, events or whole-match conclusions."
        )
        lead_instructions = lead_resolution.instructions + contract + "\nAssigned role: lead. Describe WHAT is happening."
        analyst_instructions = analyst_resolution.instructions + contract + "\nAssigned role: analyst. Explain WHY the supplied pattern matters without repeating the lead."
        lead = self._parse_role_response(
            self._call_foundry_with_system(
                user_prompt=lead_user_prompt,
                system_prompt=lead_instructions,
                max_completion_tokens=1100 if rich else 500,
            )
        )
        lead_text = " ".join(lead.texts)
        validate_text(lead_text, insight, lead.claims, lead.entities)

        analyst_user_prompt = (
            f"Generate {role_count} ordered analyst paragraphs, {role_budget}, complementing the lead. "
            "First explain the measurements; then explain implications and source limitations. Do not repeat the lead. "
            "Return JSON with texts (an array), claims, entities; numerical claims require metric, value, supporting_event_ids. "
            f"LEAD_LINE: {lead_text}\n"
            f"INPUT_JSON: {json.dumps(payload, ensure_ascii=False)}"
        )
        analyst = self._parse_role_response(
            self._call_foundry_with_system(
                user_prompt=analyst_user_prompt,
                system_prompt=analyst_instructions,
                max_completion_tokens=1100 if rich else 500,
            )
        )
        analyst_text = " ".join(analyst.texts)
        validate_text(analyst_text, insight, analyst.claims, analyst.entities)

        if not lead_text or not analyst_text:
            return None
        if len(lead.texts) != len(analyst.texts):
            raise ValueError("Lead and analyst must supply matching ordered turns")
        turns = [CommentaryTurn(role=role, text=text) for pair in zip(lead.texts, analyst.texts)
                 for role, text in zip(("lead", "analyst"), pair)]
        self._validate_dialogue(turns, insight, language)

        return DualCommentaryResult(
            primary_text=lead_text,
            secondary_text=analyst_text,
            language=language,
            fallback_used=False,
            provider="foundry-role-profiles",
            audit={
                "orchestration_path": "foundry-role-profiles",
                "grounding_status": "structure-numbers-and-entity-ids-checked",
                "agents": [
                    {
                        "name": lead_resolution.name,
                        "version": lead_resolution.version,
                        "source": lead_resolution.source,
                        "instructions": lead_instructions,
                    },
                    {
                        "name": analyst_resolution.name,
                        "version": analyst_resolution.version,
                        "source": analyst_resolution.source,
                        "instructions": analyst_instructions,
                    },
                ],
                "tuning_version": self._commentary_tuning_version,
            },
            turns=turns,
            depth=insight.context.get("depth", "deep"),
        )

    @staticmethod
    def _parse_role_response(response: str) -> GeneratedRole:
        parsed = json.loads(response)
        if isinstance(parsed, dict) and "text" in parsed:
            legacy = GeneratedLine.model_validate(parsed)
            return GeneratedRole(texts=[legacy.text], claims=legacy.claims, entities=legacy.entities)
        return GeneratedRole.model_validate(parsed)

    def _resolve_commentary_agent_instructions(
        self,
        agent_name: str | None,
        fallback_profile: str | None,
    ) -> CommentaryAgentResolution | None:
        normalized_name = (agent_name or "").strip()
        if not normalized_name:
            return None

        try:
            client = self._ensure_agents_client()
            versions = list(client.agents.list_versions(
                agent_name=normalized_name, limit=1, connection_timeout=5, read_timeout=10, retry_total=0
            ))
            if versions:
                latest = versions[0]
                instructions = ""

                definition = getattr(latest, "definition", None)
                if isinstance(definition, dict):
                    instructions = str(definition.get("instructions", "")).strip()
                elif definition is not None:
                    dict_like = getattr(definition, "as_dict", None)
                    if callable(dict_like):
                        mapped = dict_like()
                        if isinstance(mapped, dict):
                            instructions = str(mapped.get("instructions", "")).strip()
                    if not instructions:
                        instructions = str(getattr(definition, "instructions", "")).strip()

                if not instructions:
                    instructions = str(getattr(latest, "instructions", "")).strip()

                if instructions:
                    revision = (
                        getattr(latest, "version", None)
                        or getattr(latest, "revision", None)
                        or getattr(latest, "id", None)
                        or getattr(latest, "name", None)
                    )
                    return CommentaryAgentResolution(
                        name=normalized_name,
                        instructions=instructions,
                        version=str(revision) if revision is not None else None,
                        source="foundry-agent-version",
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Unable to fetch Foundry agent instructions for %s (%s)", normalized_name, type(exc).__name__)

        fallback = (fallback_profile or "").strip()
        if not fallback:
            return None

        return CommentaryAgentResolution(
            name=normalized_name,
            instructions=fallback,
            version=self._commentary_tuning_version,
            source="env-fallback-profile",
        )

    @staticmethod
    def _sanitize_commentary_line(text: str) -> str:
        normalized = " ".join(text.replace("\n", " ").split()).strip()
        if not normalized:
            return ""

        if normalized.startswith("{"):
            try:
                parsed = json.loads(normalized)
                if isinstance(parsed, dict):
                    for key in ("line", "text", "primary_text", "secondary_text"):
                        value = parsed.get(key)
                        if isinstance(value, str) and value.strip():
                            return " ".join(value.strip().split())
            except Exception:  # noqa: BLE001
                pass

        return normalized.strip('"').strip()

    def _build_dual_commentary_prompt(self, insight: Insight, language: str) -> str:
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
                    "supporting_event_ids": item.supporting_event_ids[:8],
                }
                for item in insight.evidence
            ],
            "language": language,
            "context": insight.context,
            "agent_profiles": {
                "playbyplay_agent_name": self._commentary_playbyplay_agent_name,
                "color_agent_name": self._commentary_color_agent_name,
                "tuning_version": self._commentary_tuning_version,
                "playbyplay_profile": self._commentary_playbyplay_profile or "",
                "color_profile": self._commentary_color_profile or "",
            },
        }

        return (
            "You are producing live football TV commentary for TWO MALE commentators with distinct roles. "
            "The lead establishes what happens; the analyst explains the evidence and its limits. "
            "Use the provided profiles for role and tone, but the application grounding and depth contract takes precedence. "
            "Use only provided evidence. No invented stats. "
            "Identify synthetic data as simulated; observation overviews must not become tactical verdicts. "
            "For deep depth with substantial evidence, return 4-6 alternating turns and 120-180 words total. "
            "Cover the passage, supporting measurements, sequence and source limitations. "
            "For broadcast or sparse evidence, return two complementary turns, 30-60 words total or fewer if needed. "
            "Never pad, imply unobserved actions or disclose future events. No markdown or speaker labels inside text. "
            "Return strict JSON with turns [{role: lead|analyst, text}], claims, entities, beginning with the lead. "
            "Each numerical claim must include metric, value, supporting_event_ids from the input. "
            "List every referenced entity ID in entities. Use the requested language for both lines.\n\n"
            f"INPUT_JSON:\n{json.dumps(payload, ensure_ascii=False)}"
        )

    @staticmethod
    def _parse_dual_commentary_response(response_text: str) -> dict[str, Any]:
        parsed = json.loads(response_text)
        if not isinstance(parsed, dict):
            raise ValueError("Foundry dual commentary response is not a JSON object")

        if "turns" not in parsed:
            if set(parsed) - {"primary_text", "secondary_text", "claims", "entities"}:
                raise ValueError("Foundry commentary contains unsupported fields")
            primary, secondary = parsed.get("primary_text"), parsed.get("secondary_text")
            if not isinstance(primary, str) or not isinstance(secondary, str) or not primary.strip() or not secondary.strip():
                raise ValueError("Foundry dual commentary response is missing text")
            parsed = {"turns": [{"role": "lead", "text": primary.strip()}, {"role": "analyst", "text": secondary.strip()}],
                      "claims": parsed.get("claims", []), "entities": parsed.get("entities", [])}
        dialogue = GeneratedDialogue.model_validate(parsed)
        return {
            "primary_text": " ".join(turn.text for turn in dialogue.turns if turn.role == "lead"),
            "secondary_text": " ".join(turn.text for turn in dialogue.turns if turn.role == "analyst"),
            "turns": [turn.model_dump() for turn in dialogue.turns],
            "claims": [claim.model_dump() for claim in dialogue.claims],
            "entities": dialogue.entities,
        }

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

    def _ensure_agents_client(self) -> Any:
        if self._agents_client is not None:
            return self._agents_client

        from azure.ai.projects import AIProjectClient  # type: ignore[import-not-found]
        from azure.identity import DefaultAzureCredential  # type: ignore[import-not-found]

        self._agents_client = AIProjectClient(
            endpoint=self._endpoint,
            credential=DefaultAzureCredential(
                process_timeout=5,
                exclude_managed_identity_credential=os.getenv("APP_ENV", "local").lower() != "production",
            ),
        )
        return self._agents_client

    def _call_foundry(self, prompt: str) -> str:
        return self._call_foundry_with_system(
            user_prompt=prompt,
            system_prompt=NARRATIVE_SYSTEM_PROMPT,
            max_completion_tokens=NARRATIVE_MAX_COMPLETION_TOKENS,
        )

    def _call_foundry_with_system(self, user_prompt: str, system_prompt: str, max_completion_tokens: int) -> str:
        if self._metered and len((user_prompt + system_prompt).encode("utf-8")) > 64_000:
            raise ValueError("Metered narrative exceeds its input budget")
        client = self._ensure_client()
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        # Preferred path for current azure-ai-projects SDK versions.
        if hasattr(client, "get_openai_client"):
            openai_kwargs: dict[str, Any] = {"timeout": 60, "max_retries": 0}
            if self._api_key:
                # Override token-based default with explicit key auth when provided.
                openai_kwargs["api_key"] = self._api_key

            openai_client = client.get_openai_client(**openai_kwargs)
            response = openai_client.chat.completions.create(
                model=self._deployment,
                messages=messages,
                max_completion_tokens=max_completion_tokens,
                response_format={"type": "json_object"},
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
                max_tokens=max_completion_tokens,
            )

        record_model_usage(getattr(response, "usage", None))
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
