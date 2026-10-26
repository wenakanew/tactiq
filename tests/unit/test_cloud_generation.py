import base64
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tactiq.agents.narrative import DeterministicNarrativeGenerator, FoundryNarrativeGenerator
from tactiq.agents.speech import SpeechResult
from tactiq.agents.usage import record_model_usage
from tactiq.domain.models import EvidenceItem, Insight
from tactiq.runtime.access import AccessDenied, AnalysisIntent, BudgetExceeded, SQLiteAccessStore, UsagePolicy
from tactiq.runtime.cloud_generation import CloudGeneration, GenerationSettings, GenerationUnavailable
from tactiq.runtime.contracts import CloudRecordError, MAX_RECORD_BYTES, MediaRef, bounded_json


class MemoryStore:
    def __init__(self):
        self.scripts, self.results, self.media = {}, {}, {}

    def save_script(self, tenant, match, identifier, payload):
        bounded_json(payload, MAX_RECORD_BYTES)
        return self.scripts.setdefault((tenant, match, identifier), payload)

    def load_script(self, tenant, match, identifier):
        return self.scripts.get((tenant, match, identifier))

    def generation_result(self, tenant, match, identifier):
        return self.results.get((tenant, match, identifier))

    def save_generation_result(self, tenant, match, identifier, payload):
        bounded_json(payload, MAX_RECORD_BYTES)
        self.results.setdefault((tenant, match, identifier), payload)

    def register_media(self, reference):
        self.media[reference.tenant_id, reference.match_id, reference.media_id] = reference

    def load_media(self, tenant, match, identifier):
        return self.media.get((tenant, match, identifier))


class MemoryMedia:
    def __init__(self):
        self.objects = {}

    def upload(self, tenant, match, source, *, size, content_type):
        reference = MediaRef(tenant, match, uuid4().hex, size, content_type, '"fixture"')
        self.objects[reference.media_id] = source.read()
        return reference

    def download(self, reference, *, tenant_id, match_id):
        assert (tenant_id, match_id) == (reference.tenant_id, reference.match_id)
        yield self.objects[reference.media_id]


class NarrativeBoundary(DeterministicNarrativeGenerator):
    """Offline boundary, not evidence of live Foundry model execution."""
    calls = 0
    fail = False

    def status(self):
        return {"configured": True, "status": "configured"}

    def cache_identity(self):
        return "test-model"

    def generate(self, *args):
        self.calls += 1
        if self.fail:
            raise RuntimeError("Ambiguous provider outcome")
        return super().generate(*args)

    def generate_dual_commentary(self, *args):
        self.calls += 1
        return super().generate_dual_commentary(*args)


class SpeechBoundary:
    calls = 0
    fail = False

    def capabilities(self):
        return {"configured": True, "health_verified": False, "languages": {"en-GB": {"single": True, "dual": True}}}

    def cache_identity(self, language, *, dual):
        return f"{language}-{dual}"

    def synthesize(self, *_args, **_kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("Ambiguous provider outcome")
        return SpeechResult(base64.b64encode(b"offline audio boundary").decode(), "audio/mpeg", "offline-fixture")

    def synthesize_turns(self, *args, **kwargs):
        return self.synthesize(*args, **kwargs)


@pytest.fixture
def runtime(tmp_path):
    now = time.time()
    policy = UsagePolicy(now - 10, now + 86400, 100_000_000, 10_000_000)
    access = SQLiteAccessStore(tmp_path / "access.sqlite3", ip_hmac_key=b"k" * 32, usage_policy=policy)
    guest = access.issue_guest()
    admitted = access.reserve_analysis(guest.token, "127.0.0.1", AnalysisIntent("scenario", "scenario", "a" * 64))
    insight = Insight(
        match_id=admitted.analysis_id, type="sustained_pressure", priority="high", confidence=.8,
        team_id="team_a", window_start_ms=0, window_end_ms=1000, title="Pressure", summary="Supported passage.",
        evidence=[EvidenceItem(metric="shots", value=1, unit="count", supporting_event_ids=["e1"])],
    )
    store, media, model, speech = MemoryStore(), MemoryMedia(), NarrativeBoundary(), SpeechBoundary()
    settings = GenerationSettings(True, True, policy, 1_000_000, 16_000_000)
    service = CloudGeneration(settings, store, access, media, narrative=model, speech=speech)
    return SimpleNamespace(service=service, settings=settings, access=access, token=guest.token,
                           tenant=admitted.tenant_id, match=admitted.analysis_id, insight=insight,
                           store=store, media=media, model=model, speech=speech)


def script(runtime, *, dual=False, allow=True):
    r = runtime
    return r.service.script(r.token, r.tenant, r.match, "dual" if dual else "single",
                            r.insight, "en-GB", "fan", None, allow_cloud=allow)


def test_paid_capabilities_require_configuration_and_active_allocation(runtime):
    r = runtime
    assert r.service.capabilities()["cloud_model_enabled"]
    assert r.service.capabilities()["speech_configured"]
    assert not r.service.capabilities()["provider_health_verified"]
    r.service.settings = GenerationSettings()
    assert not r.service.capabilities()["paid_generation_enabled"]
    script(r)
    assert r.model.calls == 0


def test_missing_prices_or_allocation_fail_closed():
    with pytest.raises(ValueError):
        GenerationSettings(model_enabled=True)
    with pytest.raises(ValueError):
        GenerationSettings(speech_enabled=True, policy=UsagePolicy(1, 2, 100, 100))


def test_owned_deterministic_dual_serializes_and_is_reusable_for_speech(runtime):
    result = script(runtime, dual=True, allow=False)
    assert result["script_id"] and len(result["turns"]) == 2
    assert runtime.model.calls == 0
    for _ in range(2):
        audio = runtime.service.synthesize(runtime.token, runtime.tenant, runtime.match, result["script_id"], dual=True)
        assert base64.b64decode(audio["audio_base64"]) == b"offline audio boundary"
    assert runtime.speech.calls == 1


def test_paid_results_survive_service_restart_without_another_provider_call(runtime):
    r = runtime
    first = script(r)
    r.service = CloudGeneration(r.settings, r.store, r.access, r.media, narrative=r.model, speech=r.speech)
    assert script(r) == first
    assert r.model.calls == 1
    assert r.access.trial_allowance(r.token, "127.0.0.1").remaining == 2


def test_paid_model_reservation_uses_enforced_two_request_bound(runtime, monkeypatch):
    calls = []
    reserve = runtime.service._reserve

    def capture(proof, operation, meter, units):
        calls.append((meter, units))
        return reserve(proof, operation, meter, units)

    monkeypatch.setattr(runtime.service, "_reserve", capture)
    script(runtime, dual=True)
    assert calls == [("model_tokens", 2 * (64_000 + 1100 + 4096))]


def test_single_model_reservation_uses_exact_utf8_prompt_and_completion_bound(runtime, monkeypatch):
    calls = []
    reserve = runtime.service._reserve
    monkeypatch.setattr(runtime.service, "_reserve", lambda proof, operation, meter, units: (
        calls.append((meter, units)) or reserve(proof, operation, meter, units)
    ))
    expected = FoundryNarrativeGenerator.single_request_token_bound(runtime.insight, "fan", "en-GB", None)
    script(runtime)
    assert calls == [("model_tokens", expected)]
    assert 5696 < expected < 196608


def test_single_model_bound_rejects_oversize_before_reserving(runtime, monkeypatch):
    runtime.insight.context["notes"] = "x" * 64_001
    with pytest.raises(ValueError, match="evidence exceeds"):
        script(runtime)
    assert runtime.model.calls == 0


def test_concurrent_retries_never_repeat_paid_calls(runtime):
    def request(_):
        try:
            return script(runtime)
        except GenerationUnavailable:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(request, range(20)))
    assert runtime.model.calls == 1
    assert any(results)
    assert script(runtime)["script_id"]


def test_ambiguous_model_outcome_stays_reserved_and_is_not_retried(runtime):
    runtime.model.fail = True
    with pytest.raises(RuntimeError):
        script(runtime)
    with pytest.raises(GenerationUnavailable):
        script(runtime)
    assert runtime.model.calls == 1


def test_ambiguous_speech_outcome_is_not_retried(runtime):
    r = runtime
    owned = script(r, allow=False)
    r.speech.fail = True
    for _ in range(2):
        with pytest.raises(GenerationUnavailable):
            r.service.synthesize(r.token, r.tenant, r.match, owned["script_id"], dual=False)
    assert r.speech.calls == 1


def test_ownership_and_script_shape_precede_paid_speech(runtime):
    r = runtime
    owned = script(r, allow=False)
    stranger = r.access.issue_guest()
    with pytest.raises(AccessDenied):
        r.service.synthesize(stranger.token, r.tenant, r.match, owned["script_id"], dual=False)
    with pytest.raises(AccessDenied):
        r.service.synthesize(r.token, r.tenant, r.match, "unknown_script", dual=False)
    with pytest.raises(ValueError):
        r.service.synthesize(r.token, r.tenant, r.match, owned["script_id"], dual=True)
    assert r.speech.calls == 0


def test_budget_denial_precedes_provider_call(runtime, tmp_path):
    r = runtime
    policy = replace(r.settings.policy, global_limit_microusd=1, tenant_limit_microusd=1)
    r.access = SQLiteAccessStore(tmp_path / "limited.sqlite3", ip_hmac_key=b"k" * 32, usage_policy=policy)
    r.token = r.access.issue_guest().token
    admitted = r.access.reserve_analysis(r.token, "127.0.0.1", AnalysisIntent("other", "scenario", "b" * 64))
    r.tenant, r.match = admitted.tenant_id, admitted.analysis_id
    r.insight.match_id = r.match
    r.service = CloudGeneration(replace(r.settings, policy=policy), r.store, r.access, r.media,
                               narrative=r.model, speech=r.speech)
    with pytest.raises(BudgetExceeded):
        script(r)
    assert r.model.calls == 0


def test_oversized_evidence_never_calls_provider(runtime):
    runtime.insight.context["unexpected"] = "x" * 33_000
    with pytest.raises(ValueError, match="input budget"):
        script(runtime)
    assert runtime.model.calls == 0


def test_semantic_workflow_path_does_not_allow_persisted_filesystem_locations():
    assert bounded_json({"audit": {"orchestration_path": "microsoft-agent-framework"}}, 1024)
    with pytest.raises(CloudRecordError):
        bounded_json({"audit": {"orchestration_path": "C:\\private\\file"}}, 1024)
    with pytest.raises(CloudRecordError):
        bounded_json({"source_path": "private-file"}, 1024)


def metered_model(runtime, monkeypatch, *, usages=None, fallback=False):
    def generate(*args):
        runtime.model.calls += 1
        for usage in usages or []:
            record_model_usage(usage)
        result = runtime.service.fallback.generate(*args)
        result.provider, result.fallback_used = "foundry", fallback
        return result

    monkeypatch.setattr(runtime.model, "generate", generate)


def test_verified_usage_settles_only_after_durable_save_and_cache_never_repeats(runtime, monkeypatch):
    r = runtime
    metered_model(r, monkeypatch, usages=[{"prompt_tokens": 100, "completion_tokens": 23, "total_tokens": 123}])
    r.service.settings = replace(r.settings, model_price=30_000_001)
    settled = []
    settle = r.access.settle_usage

    def capture(proof, identifier, units, cost):
        assert r.store.results and r.store.scripts
        result = settle(proof, identifier, units, cost)
        settled.append(result)
        return result

    monkeypatch.setattr(r.access, "settle_usage", capture)
    first = script(r)
    assert script(r) == first
    assert r.model.calls == len(settled) == 1
    assert settled[0].status == "settled"
    assert settled[0].actual_cost_microusd == 3691


@pytest.mark.parametrize("usages,fallback", [
    ([], False),
    ([None], False),
    ([{"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}], True),
    ([{"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}] * 2, False),
    ([{"prompt_tokens": True, "completion_tokens": 2, "total_tokens": 3}], False),
    ([{"prompt_tokens": 10, "completion_tokens": -1, "total_tokens": 9}], False),
    ([{"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 99}], False),
    ([{"prompt_tokens": 10, "completion_tokens": 2}], False),
])
def test_uncertain_or_fallback_usage_keeps_full_reservation(runtime, monkeypatch, usages, fallback):
    metered_model(runtime, monkeypatch, usages=usages, fallback=fallback)

    def forbidden(*args):
        pytest.fail("An uncertain call must not settle")

    monkeypatch.setattr(runtime.access, "settle_usage", forbidden)
    script(runtime)


def test_durable_failure_retains_reservation_and_prevents_paid_retry(runtime, monkeypatch):
    metered_model(runtime, monkeypatch, usages=[{"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}])
    settled = []
    monkeypatch.setattr(runtime.access, "settle_usage", lambda *args: settled.append(args))

    def fail(*args):
        raise OSError("Durable store unavailable")

    monkeypatch.setattr(runtime.store, "save_generation_result", fail)
    with pytest.raises(OSError):
        script(runtime)
    with pytest.raises(GenerationUnavailable):
        script(runtime)
    assert runtime.model.calls == 1 and not settled


def test_settlement_failure_is_explicit_and_cached_result_prevents_paid_retry(runtime, monkeypatch):
    metered_model(runtime, monkeypatch, usages=[{"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}])

    def fail(*args):
        raise OSError("Ledger unavailable")

    monkeypatch.setattr(runtime.access, "settle_usage", fail)
    with pytest.raises(OSError):
        script(runtime)
    assert script(runtime)["script_id"]
    assert runtime.model.calls == 1


def test_reported_overrun_is_not_hidden_by_settlement(runtime, monkeypatch):
    metered_model(runtime, monkeypatch, usages=[
        {"prompt_tokens": 196608, "completion_tokens": 1, "total_tokens": 196609},
    ])
    with pytest.raises(GenerationUnavailable, match="exceeded"):
        script(runtime)
    assert runtime.model.calls == 1


def test_dual_requires_both_trusted_role_responses(runtime, monkeypatch):
    r = runtime

    def generate(*args):
        r.model.calls += 1
        for _ in range(2):
            record_model_usage({"input_token_count": 100, "output_token_count": 20, "total_token_count": 120},
                               framework=True)
        result = r.service.fallback.generate_dual_commentary(*args)
        result.provider, result.fallback_used = "foundry-agent-framework", False
        return result

    monkeypatch.setattr(r.model, "generate_dual_commentary", generate)
    settled = []
    settle = r.access.settle_usage

    def capture(*args):
        settled.append(args[2:])
        return settle(*args)

    monkeypatch.setattr(r.access, "settle_usage", capture)
    script(r, dual=True)
    assert settled == [(240, 240)]
