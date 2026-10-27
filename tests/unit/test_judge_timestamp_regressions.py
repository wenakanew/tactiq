import json

import pytest

pytest.importorskip("agent_framework")
from tactiq.agents.workflow import MomentEvidence
from test_agent_framework_workflow import OfflineClient, moment, role, run


def test_actual_agent_prompt_contains_only_timestamped_in_window_events():
    insight = moment()
    insight.window_start_ms = 100
    insight.window_end_ms = 60000
    cases = [
        ("start", 100), ("inside", 300), ("end", 60000), ("before", 99), ("future", 60001),
        ("null", None), ("string", "300"), ("nonnumeric", "tomorrow"), ("boolean", True),
        ("float", 300.0), ("nan", float("nan")), ("infinite", float("inf")), ("negative", -1),
    ]
    insight.context["phase_events"] = [
        {"event_id": key, "type": "pass_completed", "match_clock_ms": value, "raw_frame": "PRIVATE"}
        for key, value in cases
    ] + [{"event_id": "missing", "type": "goal"}, "not-an-event"]
    client = OfflineClient([role(), role()])
    result = run(client, insight)
    assert len(result.turns) == 2 and len(client.calls) == 2
    for messages, _ in client.calls:
        prompt = "\n".join(message.text for message in messages)
        input_json = prompt.split("INPUT_JSON: ", 1)[1]
        evidence, _ = json.JSONDecoder().raw_decode(input_json)
        events = evidence["context"]["phase_events"]
        assert [event["event_id"] for event in events] == ["start", "inside", "end"]
        assert [event["match_clock_ms"] for event in events] == [100, 300, 60000]
        assert "PRIVATE" not in input_json


@pytest.mark.parametrize("invalid", [None, "events"])
def test_malformed_event_collection_rejected_before_provider(invalid):
    insight = moment()
    insight.context["phase_events"] = invalid
    client = OfflineClient([role(), role()])
    with pytest.raises(ValueError, match="phase_events"):
        run(client, insight)
    assert not client.calls


def test_event_bound_applied_after_temporal_filtering():
    insight = moment()
    insight.context["phase_events"] = [
        {"event_id": "valid", "type": "pass_completed", "match_clock_ms": 1},
        *[{"event_id": f"future-{index}", "match_clock_ms": 90000} for index in range(10)],
    ]
    assert [item["event_id"] for item in MomentEvidence.from_insight(insight, "en-GB").insight().context["phase_events"]] == ["valid"]


def test_missing_event_timestamp_is_not_substituted_and_omission_is_logged(caplog):
    insight = moment()
    insight.context["phase_events"] = [{"event_id": "unplaced", "type": "goal"}]
    client = OfflineClient([role(), role()])
    run(client, insight)
    for messages, _ in client.calls:
        prompt = "\n".join(message.text for message in messages)
        assert '"phase_events": []' in prompt
        assert "unplaced" not in prompt
    assert "Omitted 1 malformed" in caplog.text
