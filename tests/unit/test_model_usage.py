from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from tactiq.agents.narrative import DeterministicNarrativeGenerator, FoundryNarrativeGenerator
from tactiq.agents.usage import capture_model_usage, record_model_usage


def test_single_sdk_response_is_captured_without_changing_text_contract(monkeypatch):
    generator = FoundryNarrativeGenerator(DeterministicNarrativeGenerator())
    sent = {}
    response = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=4, total_tokens=15),
        choices=[SimpleNamespace(message=SimpleNamespace(content="provider text"))],
    )
    def complete(**kwargs):
        sent.update(kwargs)
        return response

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=complete)))
    monkeypatch.setattr(generator, "_ensure_client", lambda: SimpleNamespace(get_openai_client=lambda **kwargs: client))
    with capture_model_usage() as capture:
        assert generator._call_foundry_with_system("prompt", "system", 100) == "provider text"
    assert capture.complete(1).total_tokens == 15
    assert sent["response_format"] == {"type": "json_object"}


def test_concurrent_and_nested_captures_do_not_mix_requests():
    def request(tokens):
        with capture_model_usage() as capture:
            record_model_usage({"prompt_tokens": tokens, "completion_tokens": 1, "total_tokens": tokens + 1})
            with capture_model_usage() as nested:
                record_model_usage(None)
            assert nested.complete(1) is None
        return capture.complete(1).total_tokens

    with ThreadPoolExecutor(max_workers=8) as executor:
        assert list(executor.map(request, range(1, 25))) == list(range(2, 26))


@pytest.mark.parametrize("usage", [
    None, {}, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    {"prompt_tokens": 10.0, "completion_tokens": 2, "total_tokens": 12},
    {"prompt_tokens": "10", "completion_tokens": 2, "total_tokens": 12},
])
def test_missing_or_invalid_usage_is_not_a_refund(usage):
    with capture_model_usage() as capture:
        record_model_usage(usage)
    assert capture.complete(1) is None
