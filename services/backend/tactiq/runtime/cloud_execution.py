"""Cheap cancellation hints between authoritative fenced database writes."""
from __future__ import annotations

import time

from .cloud_worker import CloudWorker


class CheckpointExecutionStore:
    """A sequential worker's hint cache, NOT execution or commit authority.

    CV/scenario loops check cancellation frequently. Opening a DB connection for
    every video frame is unnecessary: commit, finish, renew and claim all still
    check the authoritative lease/fence. Cancellation hint latency is <=0.5s.
    No aggregate, media, identity, quota or budget data is cached here.
    """
    def __init__(self, store, *, clock=time.monotonic):
        self.store = store
        self.clock = clock
        self._checked_at = float("-inf")
        self._cancelled = False
        self._key = None

    def claim(self, reference, owner, lease_seconds):
        self._key = None
        return self.store.claim(reference, owner, lease_seconds)

    def cancelled(self, claim, owner):
        now = self.clock()
        key = (claim.tenant_id, claim.job_id, claim.fence, owner)
        if key != self._key or now - self._checked_at >= 0.5:
            self._cancelled = self.store.cancelled(claim, owner)
            self._checked_at, self._key = self.clock(), key
        return self._cancelled

    def terminal(self, reference):
        return self.store.terminal(reference)

    def renew(self, claim, owner, lease_seconds):
        return self.store.renew(claim, owner, lease_seconds)

    def finish(self, claim, owner, result):
        return self.store.finish(claim, owner, result)

    def fail(self, claim, owner, reason):
        return self.store.fail(claim, owner, reason)


def create_analysis_worker(store, handlers, owner: str) -> CloudWorker:
    return CloudWorker(CheckpointExecutionStore(store), handlers, owner)
