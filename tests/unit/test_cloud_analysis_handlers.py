"""Offline computation checks, separate from real PostgreSQL integration proof."""
from copy import deepcopy
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest

from tactiq.application.cloud import AnalysisHandlers, ScenarioRequest
from tactiq.runtime.cloud_worker import ExecutionClaim, ExecutionContext, LeaseLostError
from tactiq.runtime.contracts import Aggregate, MAX_STATE_BYTES, bounded_json


class BoundedStore:
    def __init__(self, match_id):
        self.aggregate = Aggregate("tenant", match_id, 1, {
            "match_id": match_id, "tenant_id": "tenant", "status": "created",
            "match_clock_ms": 0, "last_sequence": 0,
            "_cloud": {"input_ready": True, "event_count": 0, "replay_count": 0},
        })
        self.events, self.replay, self.writes = [], [], []
        self.interrupt_after = None
        self.interrupted = False

    def load(self, tenant_id, match_id):
        assert (tenant_id, match_id) == ("tenant", self.aggregate.match_id)
        return deepcopy(self.aggregate)

    def commit(self, tenant_id, match_id, state, *, expected_revision, events=(), replay=(), claim=None, owner=None):
        assert claim is not None and owner == "worker"
        assert claim.tenant_id == tenant_id and claim.job_id == match_id
        assert expected_revision == self.aggregate.revision
        assert len(events) <= 100 and len(replay) <= 100
        bounded_json(state, MAX_STATE_BYTES)
        self.events.extend(events)
        self.replay.extend(replay)
        self.aggregate = Aggregate(tenant_id, match_id, expected_revision + 1, deepcopy(state))
        self.writes.append((claim.fence, len(events), len(replay)))
        if self.interrupt_after == len(self.writes):
            self.interrupted = True
        return deepcopy(self.aggregate)


@pytest.fixture
def scratch():
    root = Path("build") / ("cloud-handler-test-" + uuid4().hex)
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        import shutil
        shutil.rmtree(root)


@pytest.fixture(autouse=True)
def bounded_timeline(monkeypatch):
    from tactiq.application import cloud

    original = cloud.scenario_timeline
    # Recovery needs two batches, not several repeated complete match analyses.
    # The PostgreSQL request-flow test exercises a full, unmodified scenario.
    monkeypatch.setattr(cloud, "scenario_timeline", lambda *args: original(*args)[:160])


def context(store, payload, fence=1):
    claim = ExecutionClaim("tenant", store.aggregate.match_id, fence, "scenario", payload)
    return ExecutionContext(Event(), lambda: store.interrupted, claim, "worker")


def test_worker_computation_isolated_bounded_and_restartable_without_duplicate_records(scratch):
    payload = ScenarioRequest().model_dump()
    store = BoundedStore(uuid4().hex)
    handlers = AnalysisHandlers(store, object(), scratch)
    store.interrupt_after = 1
    with pytest.raises(LeaseLostError):
        handlers.scenario(payload, context(store, payload))
    first_ids = [item.record_id for item in store.events]
    assert len(first_ids) == 100 and store.aggregate.state["status"] == "running"
    assert list(scratch.iterdir()) == []
    store.interrupted = False
    result = handlers.scenario(payload, context(store, payload, fence=2))
    assert result["event_count"] == len(store.events) > 100
    assert len({item.record_id for item in store.events}) == len(store.events)
    assert [item.record_id for item in store.events[:100]] == first_ids
    assert store.aggregate.state["status"] == "completed"
    assert store.aggregate.state["last_sequence"] == len(store.events)
    assert store.aggregate.state["scenario_recipe_version"] == 2
    assert [item.payload["timestamp_ms"] for item in store.replay] == sorted(
        item.payload["timestamp_ms"] for item in store.replay
    )
    assert all(insight["provenance"]["data_source"] == "synthetic" for insight in store.aggregate.state["insights"])
    writes = len(store.writes)
    assert handlers.scenario(payload, context(store, payload, fence=3)) == {"match_id": store.aggregate.match_id}
    assert len(store.writes) == writes
    assert list(scratch.iterdir()) == []


def test_recovered_worker_rejects_changed_recipe_before_mixing_streams(scratch):
    payload = ScenarioRequest().model_dump()
    store = BoundedStore(uuid4().hex)
    handlers = AnalysisHandlers(store, object(), scratch)
    store.interrupt_after = 1
    with pytest.raises(LeaseLostError):
        handlers.scenario(payload, context(store, payload))
    store.interrupted = False
    changed = {**payload, "seed": 10}
    with pytest.raises(ValueError, match="differs from the committed stream"):
        handlers.scenario(changed, context(store, changed, fence=2))
    assert len(store.events) == 100
    assert list(scratch.iterdir()) == []


def test_version_one_recipe_keeps_its_version_and_synthetic_evidence(scratch):
    payload = ScenarioRequest(recipe_version=1).model_dump()
    store = BoundedStore(uuid4().hex)
    result = AnalysisHandlers(store, object(), scratch).scenario(payload, context(store, payload))
    assert result["event_count"] == 8
    assert store.aggregate.state["scenario_recipe_version"] == 1
    assert store.aggregate.state["analysis_metadata"]["scenario_recipe_version"] == 1
    assert all(item.payload["synthetic"] for item in store.events)
