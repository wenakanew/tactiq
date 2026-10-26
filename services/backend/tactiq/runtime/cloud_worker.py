"""Queue delivery boundaries; the database, not a broker lock, owns execution."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import json
import logging
from threading import Event, Thread
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from ._validation import encode_object, identifier

logger = logging.getLogger(__name__)


class WorkReference(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    version: Literal[1] = 1
    tenant_id: str = Field(min_length=1, max_length=256, pattern=r"^[a-zA-Z0-9_-]+$")
    job_id: str = Field(min_length=1, max_length=256, pattern=r"^[a-zA-Z0-9_-]+$")


@dataclass(frozen=True)
class ExecutionClaim:
    tenant_id: str
    job_id: str
    fence: int
    kind: str
    payload: dict[str, Any] = field(repr=False)


class LeaseLostError(RuntimeError):
    pass


class ExecutionStore(Protocol):
    def claim(self, reference: WorkReference, owner: str, lease_seconds: int) -> ExecutionClaim | None: ...
    def terminal(self, reference: WorkReference) -> bool: ...
    def renew(self, claim: ExecutionClaim, owner: str, lease_seconds: int) -> bool: ...
    def cancelled(self, claim: ExecutionClaim, owner: str) -> bool: ...
    def finish(self, claim: ExecutionClaim, owner: str, result: dict[str, Any]) -> bool: ...
    def fail(self, claim: ExecutionClaim, owner: str, reason: str) -> bool: ...


@dataclass(frozen=True)
class OutboxClaim:
    operation_id: str
    reference: WorkReference
    fence: int


class OutboxStore(Protocol):
    def claim_outbox(self, owner: str, lease_seconds: int) -> OutboxClaim | None: ...
    def acknowledge_outbox(self, claim: OutboxClaim, owner: str) -> bool: ...
    def release_outbox(self, claim: OutboxClaim, owner: str) -> bool: ...


def dispatch_once(
    store: OutboxStore, owner: str, send: Callable[[WorkReference, str], None], *, lease_seconds: int = 60,
) -> bool:
    """Send at least once; duplicate delivery is absorbed by the fenced job claim."""
    identifier(owner, "outbox owner")
    if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or not 15 <= lease_seconds <= 300:
        raise ValueError("Outbox lease must be between 15 and 300 seconds")
    claim = store.claim_outbox(owner, lease_seconds)
    if claim is None:
        return False
    try:
        send(claim.reference, claim.operation_id)
    except Exception:
        logger.exception("cloud_outbox_send_failed", extra={"job_id": claim.reference.job_id})
        store.release_outbox(claim, owner)
        raise
    if not store.acknowledge_outbox(claim, owner):
        raise LeaseLostError("Outbox acknowledgement lost ownership; delivery may be duplicated")
    return True


class Delivery(Protocol):
    @property
    def body(self) -> bytes: ...
    def complete(self) -> None: ...
    def abandon(self) -> None: ...
    def dead_letter(self, reason: str) -> None: ...


class ExecutionContext:
    def __init__(self, lost: Event, cancelled: Callable[[], bool], claim: ExecutionClaim, owner: str) -> None:
        self._lost = lost
        self._cancelled = cancelled
        self._claim = claim
        self._owner = owner

    @property
    def claim(self) -> ExecutionClaim:
        """Pass with owner to the store's fenced commit, not just checkpoint()."""
        return self._claim

    @property
    def owner(self) -> str:
        return self._owner

    def checkpoint(self) -> None:
        """Handlers must check before committing each externally visible side effect."""
        if self._lost.is_set():
            raise LeaseLostError("Analysis ownership was lost")
        if self._cancelled():
            raise LeaseLostError("Analysis cancellation was requested")


class CloudWorker:
    def __init__(
        self, store: ExecutionStore, handlers: dict[str, Callable[[dict[str, Any], ExecutionContext], dict[str, Any]]],
        owner: str, *, lease_seconds: int = 60,
    ) -> None:
        identifier(owner, "worker owner")
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or not 15 <= lease_seconds <= 300:
            raise ValueError("Worker lease must be between 15 and 300 seconds")
        if not handlers or any(not callable(handler) for handler in handlers.values()):
            raise ValueError("Worker requires named handlers")
        self.store, self.handlers, self.owner = store, dict(handlers), owner
        self.lease_seconds = lease_seconds

    def process(self, delivery: Delivery) -> Literal["completed", "busy", "rejected", "failed", "lost"]:
        try:
            content = delivery.body
            if len(content) > 4096:
                raise ValueError("Work envelope exceeds its limit")
            reference = WorkReference.model_validate_json(content)
        except ValueError:
            logger.warning("cloud_work_envelope_rejected")
            delivery.dead_letter("Invalid work reference")
            return "rejected"
        claim = self.store.claim(reference, self.owner, self.lease_seconds)
        if claim is None:
            if self.store.terminal(reference):
                delivery.complete()
                return "completed"
            delivery.abandon()
            return "busy"
        if claim.tenant_id != reference.tenant_id or claim.job_id != reference.job_id:
            raise RuntimeError("Job claim does not match the requested owner")
        stop, lost = Event(), Event()

        def heartbeat() -> None:
            while not stop.wait(self.lease_seconds / 3):
                try:
                    if not self.store.renew(claim, self.owner, self.lease_seconds):
                        lost.set()
                        return
                except Exception:
                    logger.exception("cloud_work_lease_renewal_failed", extra={"job_id": claim.job_id})
                    lost.set()
                    return

        thread = Thread(target=heartbeat, name="tactiq-job-lease", daemon=True)
        thread.start()
        context = ExecutionContext(lost, lambda: self.store.cancelled(claim, self.owner), claim, self.owner)
        outcome: Literal["completed", "failed", "lost"]
        try:
            context.checkpoint()
            handler = self.handlers.get(claim.kind)
            if handler is None:
                raise ValueError("Job handler is unavailable")
            result = handler(claim.payload, context)
            encode_object(result)
            context.checkpoint()
            if not self.store.finish(claim, self.owner, result):
                raise LeaseLostError("Analysis completion lost ownership")
            outcome = "completed"
        except LeaseLostError:
            logger.warning("cloud_work_ownership_interrupted", extra={"job_id": claim.job_id})
            outcome = "lost"
        except Exception:
            logger.exception("cloud_work_execution_failed", extra={"job_id": claim.job_id})
            outcome = "failed" if self.store.fail(claim, self.owner, "Analysis execution failed.") else "lost"
        finally:
            stop.set()
            thread.join(timeout=10)
            if thread.is_alive():
                lost.set()
                logger.error("cloud_work_heartbeat_did_not_stop", extra={"job_id": claim.job_id})
                raise RuntimeError("Worker heartbeat did not stop within its budget")
        if outcome == "lost":
            delivery.abandon()
        else:
            delivery.complete()
        return outcome


class ServiceBusDelivery:
    def __init__(self, receiver: Any, message: Any) -> None:
        self.receiver, self.message = receiver, message

    @property
    def body(self) -> bytes:
        chunks = []
        size = 0
        for chunk in self.message.body:
            size += len(chunk)
            if size > 4096:
                raise ValueError("Work envelope exceeds its limit")
            chunks.append(bytes(chunk))
        return b"".join(chunks)

    def complete(self) -> None:
        self.receiver.complete_message(self.message)

    def abandon(self) -> None:
        self.receiver.abandon_message(self.message)

    def dead_letter(self, reason: str) -> None:
        self.receiver.dead_letter_message(self.message, reason=reason)


def send_reference(sender: Any, reference: WorkReference, operation_id: str) -> None:
    """Caller acknowledges its transactional outbox only after this call succeeds."""
    from azure.servicebus import ServiceBusMessage

    identifier(operation_id, "outbox operation")
    sender.send_messages(ServiceBusMessage(
        json.dumps(reference.model_dump(), separators=(",", ":")), message_id=operation_id,
        content_type="application/json",
    ))


def receive_once(receiver: Any, worker: CloudWorker) -> int:
    from azure.servicebus import AutoLockRenewer

    with AutoLockRenewer(max_lock_renewal_duration=900) as renewer:
        messages = receiver.receive_messages(max_message_count=1, max_wait_time=5)
        for message in messages:
            renewer.register(receiver, message, max_lock_renewal_duration=900)
            worker.process(ServiceBusDelivery(receiver, message))
    return len(messages)
