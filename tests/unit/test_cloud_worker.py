from dataclasses import replace
import json

import pytest

from tactiq.runtime.cloud_worker import (
    CloudWorker, ExecutionClaim, LeaseLostError, OutboxClaim, ServiceBusDelivery, WorkReference, dispatch_once,
)


class Store:
    def __init__(self):
        self.claimed = False
        self.done = False
        self.cancel = False
        self.fence = 1
        self.results = []

    def claim(self, reference, owner, lease_seconds):
        if self.claimed or self.done:
            return None
        self.claimed = True
        return ExecutionClaim(reference.tenant_id, reference.job_id, self.fence, "analysis", {})

    def terminal(self, reference):
        return self.done

    def renew(self, claim, owner, lease_seconds):
        return claim.fence == self.fence

    def cancelled(self, claim, owner):
        return self.cancel

    def finish(self, claim, owner, result):
        if claim.fence != self.fence:
            return False
        self.results.append(result)
        self.done = True
        return True

    def fail(self, claim, owner, reason):
        self.results.append(reason)
        self.done = True
        return True


class Delivery:
    def __init__(self, body=None):
        self.body = body if body is not None else json.dumps({"version": 1, "tenant_id": "one", "job_id": "job"}).encode()
        self.settled = []

    def complete(self):
        self.settled.append("complete")

    def abandon(self):
        self.settled.append("abandon")

    def dead_letter(self, reason):
        self.settled.append(("dead", reason))


def test_duplicates_complete_without_repeating_committed_work():
    store = Store()
    calls = []
    worker = CloudWorker(store, {"analysis": lambda payload, context: calls.append(True) or {"ok": True}}, "worker")
    for _ in range(2):
        delivery = Delivery()
        assert worker.process(delivery) == "completed"
        assert delivery.settled == ["complete"]
    assert calls == [True]
    assert store.results == [{"ok": True}]


def test_handlers_receive_the_exact_fence_and_owner_for_atomic_state_commits():
    store = Store()
    observed = []
    def handler(payload, context):
        observed.append((context.claim.tenant_id, context.claim.job_id, context.claim.fence, context.owner))
        with pytest.raises(AttributeError):
            context.owner = "different"
        return {}
    assert CloudWorker(store, {"analysis": handler}, "worker").process(Delivery()) == "completed"
    assert observed == [("one", "job", 1, "worker")]


def test_a_busy_claim_is_not_acknowledged_or_stolen():
    store = Store()
    store.claimed = True
    delivery = Delivery()
    assert CloudWorker(store, {"analysis": lambda *_: {}}, "worker").process(delivery) == "busy"
    assert delivery.settled == ["abandon"]


def test_completion_with_a_stale_fence_cannot_acknowledge_delivery():
    store = Store()
    def handler(payload, context):
        store.fence += 1
        return {"stale": True}
    delivery = Delivery()
    assert CloudWorker(store, {"analysis": handler}, "worker").process(delivery) == "lost"
    assert not store.results and delivery.settled == ["abandon"]


def test_cancellation_checkpoint_prevents_result_commit():
    store = Store()
    def handler(payload, context):
        store.cancel = True
        context.checkpoint()
        pytest.fail("Cancellation must stop execution")
    delivery = Delivery()
    assert CloudWorker(store, {"analysis": handler}, "worker").process(delivery) == "lost"
    assert not store.results


@pytest.mark.parametrize("body", [b"{broken", b"x" * 4097, b'{"version":1,"tenant_id":"one","job_id":"job","url":"private"}'])
def test_queue_messages_are_references_only_not_external_payloads(body):
    delivery = Delivery(body)
    assert CloudWorker(Store(), {"analysis": lambda *_: {}}, "worker").process(delivery) == "rejected"
    assert delivery.settled == [("dead", "Invalid work reference")]


def test_handler_errors_are_recorded_as_failure_not_success_shaped_fallback():
    store = Store()
    def handler(*_):
        raise RuntimeError("private details")
    delivery = Delivery()
    assert CloudWorker(store, {"analysis": handler}, "worker").process(delivery) == "failed"
    assert store.results == ["Analysis execution failed."]


def test_cross_tenant_claim_is_an_explicit_invariant_failure():
    store = Store()
    original = store.claim
    store.claim = lambda *args: replace(original(*args), tenant_id="another")
    with pytest.raises(RuntimeError, match="owner"):
        CloudWorker(store, {"analysis": lambda *_: {}}, "worker").process(Delivery())


class Outbox:
    def __init__(self):
        self.pending = OutboxClaim("operation", WorkReference(tenant_id="one", job_id="job"), 1)
        self.acknowledged = False
        self.released = False
        self.fenced = False

    def claim_outbox(self, owner, lease_seconds):
        return self.pending

    def acknowledge_outbox(self, claim, owner):
        if self.fenced:
            return False
        self.acknowledged = True
        self.pending = None
        return True

    def release_outbox(self, claim, owner):
        self.released = True
        return True


def test_outbox_only_acknowledges_after_successful_send():
    outbox = Outbox()
    sent = []
    assert dispatch_once(outbox, "worker", lambda ref, operation: sent.append((ref, operation)))
    assert outbox.acknowledged and sent[0][1] == "operation"
    assert not dispatch_once(outbox, "worker", lambda *_: pytest.fail("No work is pending"))


def test_failed_outbox_send_preserves_durable_retry_intent():
    outbox = Outbox()
    def send(*_):
        raise ConnectionError("Broker is unavailable")
    with pytest.raises(ConnectionError):
        dispatch_once(outbox, "worker", send)
    assert outbox.released and not outbox.acknowledged and outbox.pending


def test_stale_outbox_sender_does_not_claim_a_successful_acknowledgement():
    outbox = Outbox()
    outbox.fenced = True
    with pytest.raises(LeaseLostError):
        dispatch_once(outbox, "worker", lambda *_: None)
    assert not outbox.acknowledged


def test_database_failure_is_not_settled_as_success():
    store = Store()
    def unavailable(*args):
        raise ConnectionError("Database unavailable")
    store.finish = unavailable
    store.fail = unavailable
    delivery = Delivery()
    with pytest.raises(ConnectionError):
        CloudWorker(store, {"analysis": lambda *_: {}}, "worker").process(delivery)
    assert delivery.settled == []


def test_broker_completion_failure_allows_redelivery_without_reexecution():
    store = Store()
    calls = []
    worker = CloudWorker(store, {"analysis": lambda *_: calls.append(True) or {}}, "worker")
    delivery = Delivery()
    def disconnected():
        raise ConnectionError("Broker unavailable after database commit")
    delivery.complete = disconnected
    with pytest.raises(ConnectionError):
        worker.process(delivery)
    assert store.done
    assert worker.process(Delivery()) == "completed"
    assert calls == [True]


def test_service_bus_body_assembly_is_bounded_before_json_parsing():
    from types import SimpleNamespace
    message = SimpleNamespace(body=iter([b"x" * 2048, b"x" * 2049]))
    with pytest.raises(ValueError, match="limit"):
        _ = ServiceBusDelivery(None, message).body
