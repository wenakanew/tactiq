import asyncio
import json

import pytest

from tactiq.agents.narrative import DeterministicNarrativeGenerator, FoundryNarrativeGenerator
from tactiq.agents.usage import capture_model_usage
from tactiq.domain.models import EvidenceItem, Insight

# The integration is optional for local deterministic installs. CI exercising
# the foundry extra runs these against real SDK scheduling with no network.
pytest.importorskip("agent_framework")
from agent_framework import ChatResponse, Message
from tactiq.agents.workflow import MomentEvidence, RoleProfile, run_commentary, run_sync


def moment(depth="broadcast"):
    return Insight(
        match_id="private-match", type="sustained_pressure", priority="high", confidence=.85,
        team_id="team_a", window_start_ms=0, window_end_ms=60000,
        title="Pressure Building", summary="Team A are sustaining attacking pressure.",
        evidence=[EvidenceItem(metric=name, value=value, unit="count",
                               supporting_event_ids=["e1", "e2", "e3", "e4"])
                  for name, value in (("progressive_actions", 6), ("final_third_entries", 3), ("shots", 1))],
        context={"depth": depth, "data_source": "synthetic", "raw_footage": "secret.mp4",
                 "media_url": "https://private.invalid/video", "phase_events": [
                     {"type": "pass_completed", "event_id": "e1", "match_clock_ms": 1000, "raw_frame": "PRIVATE"}]},
    )


PROFILES = (
    RoleProfile("lead", "lead-profile", "Describe the passage.", "revision-1", "test-profile"),
    RoleProfile("analyst", "analyst-profile", "Explain the evidence.", "revision-2", "test-profile"),
)


class OfflineClient:
    additional_properties = {}

    def __init__(self, responses, usage=None):
        self.responses = list(responses)
        self.calls = []
        self.usage = usage

    async def get_response(self, messages, *, stream=False, options=None, **kwargs):
        assert not stream
        self.calls.append((messages, options))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return ChatResponse(messages=[Message("assistant", [json.dumps(item)])], usage_details=self.usage)


def role(text="the supported passage shows attacking pressure.", count=1):
    return {"texts": [text] * count, "claims": [], "entities": []}


def run(client, insight=None, language="en-GB"):
    return asyncio.run(run_commentary(
        insight or moment(), language, client, PROFILES, "offline-deployment",
        FoundryNarrativeGenerator._validate_dialogue,
    ))


def configured(monkeypatch):
    monkeypatch.setenv("FOUNDRY_ENABLED", "true")
    monkeypatch.setenv("AZURE_FOUNDRY_PROJECT_ENDPOINT", "https://example.invalid")
    monkeypatch.setenv("AZURE_FOUNDRY_PROJECT_DEPLOYMENT_NAME", "offline-deployment")
    monkeypatch.setenv("FOUNDRY_COMMENTARY_USE_AGENT_FRAMEWORK", "true")
    monkeypatch.setenv("FOUNDRY_COMMENTARY_USE_DIRECT_AGENTS", "true")
    return FoundryNarrativeGenerator(DeterministicNarrativeGenerator())


def test_real_sdk_schedules_distinct_roles_with_validated_typed_handoff():
    client = OfflineClient([role(), role("the measurements describe this simulated passage, not a prediction.")])
    result = run(client)
    assert [turn.role for turn in result.turns] == ["lead", "analyst"]
    assert len(client.calls) == 2
    lead_messages, options = client.calls[0]
    assert options["response_format"].__name__ == "GeneratedRole"
    assert options["max_tokens"] == 500 and options["store"] is False
    assert "Describe the passage." in options["instructions"]
    assert "Explain the evidence." in client.calls[1][1]["instructions"]
    analyst_input = "\n".join(message.text for message in client.calls[1][0])
    assert "VALIDATED_LEAD_JSON" in analyst_input and "the supported passage" in analyst_input
    assert result.audit["orchestration_path"] == "microsoft-agent-framework"
    assert result.audit["sdk_version"] == "1.23.0"
    assert [item["version"] for item in result.audit["agents"]] == ["revision-1", "revision-2"]
    assert len(result.audit["trace"]) == 4
    audit = json.dumps(result.audit)
    assert "Describe the passage." not in audit and "secret.mp4" not in audit


def test_real_sdk_usage_is_aggregated_across_roles_and_sync_thread_bridge():
    async def request():
        client = OfflineClient([role(), role()], usage={
            "input_token_count": 100, "output_token_count": 20, "total_token_count": 120,
        })
        with capture_model_usage() as capture:
            run_sync(lambda: run_commentary(
                moment(), "en-GB", client, PROFILES, "offline",
                FoundryNarrativeGenerator._validate_dialogue,
            ))
        actual = capture.complete(2)
        assert actual.input_tokens == 200 and actual.output_tokens == 40
        assert actual.total_tokens == 240

    asyncio.run(request())


def test_partial_workflow_has_no_complete_usage():
    client = OfflineClient([role(), RuntimeError("Analyst failed")], usage={
        "input_token_count": 100, "output_token_count": 20, "total_token_count": 120,
    })
    with capture_model_usage() as capture:
        with pytest.raises(Exception, match="Analyst failed"):
            run(client)
    assert len(capture.calls) == 1 and capture.complete(2) is None


def test_immutable_shared_evidence_excludes_private_context_and_identifiers():
    insight = moment()
    evidence = MomentEvidence.from_insight(insight, "en-GB")
    insight.context["phase_events"][0]["type"] = "goal"
    payload = evidence.prompt()
    assert "pass_completed" in payload and '"goal"' not in payload
    assert all(secret not in payload for secret in ("secret.mp4", "private.invalid", "PRIVATE", "private-match"))
    with pytest.raises(AttributeError):
        evidence.language = "fr-FR"


@pytest.mark.parametrize("profile_index,expected_calls", [(0, 0), (1, 1)])
def test_framework_bounds_complete_role_request_before_each_paid_call(profile_index, expected_calls):
    profiles = list(PROFILES)
    original = profiles[profile_index]
    profiles[profile_index] = RoleProfile(
        original.role, original.name, "x" * 64000, original.version, original.source,
    )
    client = OfflineClient([role(), role()])
    with pytest.raises(Exception, match="input budget"):
        asyncio.run(run_commentary(
            moment(), "en-GB", client, tuple(profiles), "offline-deployment",
            FoundryNarrativeGenerator._validate_dialogue,
        ))
    assert len(client.calls) == expected_calls


def test_framework_request_bound_counts_schema_instructions_and_unicode_expansion():
    from tactiq.agents.workflow import _bounded_role_prompt

    assert _bounded_role_prompt("small evidence", "bounded profile") == "small evidence"
    with pytest.raises(ValueError, match="input budget"):
        _bounded_role_prompt("x" * 64000, "")
    with pytest.raises(ValueError, match="input budget"):
        _bounded_role_prompt("\u00e9" * 12000, "")


@pytest.mark.parametrize("language", ["en-GB", "sw-KE", "fr-FR", "es-ES", "pt-BR", "ar-SA", "hi-IN"])
def test_all_existing_locales_reach_both_agents_without_changing_contract(language):
    from tactiq.agents.commentary import deterministic_turns

    turns = deterministic_turns(moment(), language)
    lead, analyst = role(turns[0].text), role(turns[1].text)
    analyst["claims"] = [
        {"metric": item.metric, "value": item.value, "supporting_event_ids": item.supporting_event_ids}
        for item in moment().evidence
    ]
    client = OfflineClient([lead, analyst])
    result = run(client, language=language)
    for messages, _ in client.calls:
        assert f'"language": "{language}"' in "\n".join(message.text for message in messages)
    assert len(result.turns) == 2
    assert result.audit["human_locale_review_required"] == (language != "en-GB")


def test_deep_workflow_preserves_four_ordered_turns_and_budget():
    text = "the supported passage shows attacking pressure and the supplied sequence explains this pattern. " * 3
    client = OfflineClient([role(text, 2), role(text, 2)])
    result = run(client, moment("deep"))
    assert [turn.role for turn in result.turns] == ["lead", "analyst", "lead", "analyst"]
    assert 120 <= sum(len(turn.text.split()) for turn in result.turns) <= 200
    assert client.calls[0][1]["max_tokens"] == 1100


@pytest.mark.parametrize("invalid", [
    role("there were 999 shots."),
    {"texts": ["the passage."], "claims": [], "entities": ["player_999"]},
    {"texts": ["the passage."], "claims": [{"metric": "shots", "value": 1, "supporting_event_ids": ["future"]}]},
    {"texts": ["the passage."], "arbitrary_field": True},
    role(""),
    role("the passage.", 2),
    role("the supported passage. " * 30),
])
def test_invalid_lead_stops_before_any_analyst_call(invalid):
    client = OfflineClient([invalid, role()])
    with pytest.raises(Exception):
        run(client)
    assert len(client.calls) == 1


def test_invalid_analyst_never_returns_framework_success():
    client = OfflineClient([role(), role("the final score is 999.")])
    with pytest.raises(Exception):
        run(client)
    assert len(client.calls) == 2


@pytest.mark.parametrize("status,reason", [(401, "authentication"), (403, "authentication"),
                                          (404, "deployment"), (429, "quota")])
@pytest.mark.parametrize("failed_role", ["lead", "analyst"])
def test_provider_failure_stops_workflow_and_enters_existing_cooldown(monkeypatch, status, reason, failed_role):
    generator = configured(monkeypatch)

    class ProviderFailure(RuntimeError):
        status_code = status

    responses = [role()] if failed_role == "analyst" else []
    client = OfflineClient([*responses, ProviderFailure("PRIVATE provider details")])

    async def offline(insight, language, **kwargs):
        return await run_commentary(insight, language, client, kwargs["profiles"],
                                    kwargs["deployment"], kwargs["validate_dialogue"])

    monkeypatch.setattr("tactiq.agents.workflow.run_foundry_commentary", offline)
    monkeypatch.setattr(generator, "_call_foundry", lambda _: pytest.fail("Legacy prompt path must not run"))
    monkeypatch.setattr(generator, "_generate_dual_commentary_via_agents",
                        lambda **_: pytest.fail("Legacy direct path must not run"))
    first = generator.generate_dual_commentary(moment(), "en-GB")
    second = generator.generate_dual_commentary(moment(), "en-GB")
    assert len(client.calls) == (2 if failed_role == "analyst" else 1)
    assert first.provider == second.provider == "deterministic"
    assert first.fallback_used and second.fallback_used
    assert first.audit["provider_failure"]["reason"] == reason
    assert second.audit["provider_failure"]["reason"] == reason
    assert first.audit["attempted_orchestration_path"] == "microsoft-agent-framework"
    assert first.audit["trace"][0]["role"] == "lead"
    assert "PRIVATE" not in json.dumps(first.audit)
    assert generator.status()["status"] == "cooldown"


def test_explicit_switch_integration_and_cache_identity(monkeypatch):
    generator = configured(monkeypatch)
    client = OfflineClient([role(), role()])

    async def offline(insight, language, **kwargs):
        return await run_commentary(insight, language, client, kwargs["profiles"],
                                    kwargs["deployment"], kwargs["validate_dialogue"])

    monkeypatch.setattr("tactiq.agents.workflow.run_foundry_commentary", offline)
    result = generator.generate_dual_commentary(moment(), "en-GB")
    assert result.provider == "foundry-agent-framework" and not result.fallback_used
    assert result.primary_text == result.turns[0].text and result.secondary_text == result.turns[1].text
    identity = generator.cache_identity()
    generator._use_agent_framework = False
    assert generator.cache_identity() != identity


def test_missing_dependency_is_explicit_not_legacy_success(monkeypatch):
    generator = configured(monkeypatch)

    def missing(*_):
        raise ModuleNotFoundError("agent_framework")

    monkeypatch.setattr(generator, "_generate_dual_commentary_via_framework", missing)
    result = generator.generate_dual_commentary(moment(), "en-GB")
    assert result.fallback_used and result.provider == "deterministic"
    assert result.audit["provider_failure"]["reason"] == "dependency_unavailable"
    assert result.audit["orchestration_path"] == "deterministic"


def test_disabled_framework_retains_deterministic_local_behavior(monkeypatch):
    generator = configured(monkeypatch)
    generator._foundry_enabled = False
    monkeypatch.setattr(generator, "_generate_dual_commentary_via_framework",
                        lambda *_: pytest.fail("Disabled provider must not invoke the SDK"))
    for language in ("en-GB", "sw-KE", "fr-FR", "es-ES", "pt-BR", "ar-SA", "hi-IN"):
        expected = DeterministicNarrativeGenerator().generate_dual_commentary(moment(), language)
        actual = generator.generate_dual_commentary(moment(), language)
        assert actual.turns == expected.turns and actual.fallback_used


def test_future_phase_events_are_excluded_from_the_shared_moment():
    insight = moment()
    insight.context["phase_events"].append({"type": "goal", "match_clock_ms": 60001})
    assert '"goal"' not in MomentEvidence.from_insight(insight, "en-GB").prompt()


def test_oversized_moment_is_rejected_before_any_model_call():
    insight = moment()
    insight.summary = "evidence " * 10000
    client = OfflineClient([role()])
    with pytest.raises(ValueError, match="input evidence budget"):
        run(client, insight)
    assert not client.calls


def test_azure_credential_failure_without_http_status_also_cools_down(monkeypatch):
    from azure.core.exceptions import ClientAuthenticationError
    generator = configured(monkeypatch)
    failure = generator._record_failure(ClientAuthenticationError("offline credential failure"))
    assert failure["reason"] == "authentication"
    assert generator.status()["status"] == "cooldown"


def test_framework_deadline_stops_before_analyst(monkeypatch):
    original_timeout = asyncio.timeout
    monkeypatch.setattr("tactiq.agents.workflow.asyncio.timeout", lambda _: original_timeout(.001))

    class SlowClient(OfflineClient):
        async def get_response(self, *args, **kwargs):
            await asyncio.sleep(.1)
            return await super().get_response(*args, **kwargs)

    client = SlowClient([role(), role()])
    with pytest.raises(TimeoutError):
        run(client)
    assert not client.calls


def test_foundry_factory_disables_retries_tools_and_closes_ephemeral_clients(monkeypatch):
    from tactiq.agents.workflow import run_foundry_commentary
    captured = {}
    closed = []

    class Resource:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            closed.append(type(self).__name__)

    class Credential(Resource):
        def __init__(self, **kwargs):
            captured["credential"] = kwargs

    class Project(Resource):
        def __init__(self, **kwargs):
            captured["project"] = kwargs

        def get_openai_client(self, **kwargs):
            captured["openai"] = kwargs
            return Resource()

    client = OfflineClient([role(), role()])

    def adapter(**kwargs):
        captured["adapter"] = kwargs
        return client

    monkeypatch.setattr("azure.identity.aio.DefaultAzureCredential", Credential)
    monkeypatch.setattr("azure.ai.projects.aio.AIProjectClient", Project)
    monkeypatch.setattr("agent_framework.openai.OpenAIChatClient", adapter)
    result = asyncio.run(run_foundry_commentary(
        moment(), "en-GB", endpoint="https://example.invalid", deployment="offline-deployment",
        api_key="offline-key", profiles=PROFILES, validate_dialogue=FoundryNarrativeGenerator._validate_dialogue,
    ))
    assert len(result.turns) == 2
    assert captured["project"]["retry_total"] == 0
    assert captured["project"]["endpoint"] == "https://example.invalid"
    assert captured["openai"] == {"timeout": 60, "max_retries": 0, "api_key": "offline-key"}
    assert captured["adapter"]["model"] == "offline-deployment"
    assert captured["adapter"]["function_invocation_configuration"] == {"enabled": False}
    assert closed == ["Resource", "Project", "Credential"]


def test_synchronous_contract_also_works_inside_an_existing_event_loop():
    async def existing_loop():
        client = OfflineClient([role(), role()])
        return run_sync(lambda: run_commentary(
            moment(), "en-GB", client, PROFILES, "offline", FoundryNarrativeGenerator._validate_dialogue,
        ))

    assert len(asyncio.run(existing_loop()).turns) == 2


@pytest.mark.parametrize("http_status", [200, 401, 404, 429])
def test_real_openai_sdk_adapter_uses_structured_responses_without_http_retries(http_status):
    from agent_framework.openai import OpenAIChatClient
    from openai import AsyncOpenAI
    # Use the transport version bundled by the installed OpenAI SDK, including
    # releases which use httpx2 rather than the application's httpx dev client.
    from importlib.metadata import version
    if int(version("openai").split(".")[0]) >= 3:
        import httpx2 as httpx
    else:
        import httpx

    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        assert request.url.path == "/responses"
        if http_status != 200:
            return httpx.Response(http_status, json={"error": {"message": "offline failure", "type": "test"}})
        return httpx.Response(200, json={
            "id": f"resp_{len(requests)}", "object": "response", "created_at": 0, "status": "completed",
            "model": "offline", "parallel_tool_calls": False, "tool_choice": "auto", "tools": [],
            "output": [{"id": "msg_test", "type": "message", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": json.dumps(role()), "annotations": []}]}],
            "usage": {"input_tokens": 10, "output_tokens": 10, "total_tokens": 20},
        })

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as transport:
            async with AsyncOpenAI(api_key="offline-test", base_url="https://example.invalid",
                                   http_client=transport, max_retries=0) as openai:
                return await run_commentary(moment(), "en-GB", OpenAIChatClient(model="offline", async_client=openai),
                                            PROFILES, "offline", FoundryNarrativeGenerator._validate_dialogue)

    if http_status == 200:
        assert len(asyncio.run(exercise()).turns) == 2
        assert len(requests) == 2
        assert requests[0]["text"]["format"]["type"] == "json_schema"
        assert requests[0]["max_output_tokens"] == 500 and requests[0]["store"] is False
    else:
        with pytest.raises(Exception) as caught:
            asyncio.run(exercise())
        generator = FoundryNarrativeGenerator(DeterministicNarrativeGenerator())
        failure = generator._record_failure(caught.value)
        assert failure["status_code"] == http_status
        assert generator._unavailable_until > 0
        assert len(requests) == 1
