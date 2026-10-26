"""Durable, bounded background execution on one host, not a distributed queue.

max_pending bounds all admitted unfinished futures, including running jobs.
An exclusive OS file lock allows one runner per database, including across
local processes; a second runner fails explicitly. Use local disks and a
single application worker hosting this runner, not network filesystems.

Call recover() during startup, after handlers are registered. Recovery drains
persisted queues in bounded batches and consumes attempts for interrupted
work. Execution is at-least-once across crashes: handlers must be idempotent.
Only RetryableJobError retries. Running Python threads cannot be forcefully
cancelled; cancellation discards their result after the handler returns but
cannot roll back external side effects.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, BinaryIO
from uuid import uuid4

from fastapi import HTTPException

from ._validation import encode_object, identifier, positive_integer
from .models import Job
from .storage import SQLiteStore


_logger = logging.getLogger(__name__)
_EXECUTION_ERROR = "Job execution failed."


class RetryableJobError(Exception):
    """A handler explicitly permits another attempt within its persisted budget."""


class PublicJobError(Exception):
    """Only curated adapter messages belong here, never external exception text."""

    def __init__(self, message: str) -> None:
        if not message.strip() or len(message) > 500:
            raise ValueError("Public job messages must be non-empty and bounded")
        self.public_message = message
        super().__init__(message)


class _RunnerLease:
    """An OS-owned lock is released automatically if the owning process crashes."""

    def __init__(self, database_path: Path) -> None:
        self._handle: BinaryIO | None = None
        lock_path = database_path.with_name(database_path.name + ".jobs.lock")
        handle = lock_path.open("a+b")
        try:
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise RuntimeError("Another background runner already owns this database") from exc
        except BaseException:
            handle.close()
            raise
        self._handle = handle

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


class BackgroundJobs:
    def __init__(
        self,
        store: SQLiteStore,
        handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]],
        max_workers: int = 2,
        max_pending: int = 8,
        max_attempts: int = 2,
    ) -> None:
        positive_integer(max_workers, "max_workers")
        positive_integer(max_pending, "max_pending")
        positive_integer(max_attempts, "max_attempts")
        if not isinstance(handlers, dict):
            raise TypeError("handlers must be a dictionary of named callables")
        for kind, handler in handlers.items():
            identifier(kind, "handler kind")
            if not callable(handler):
                raise TypeError("Every job handler must be callable")
        store.healthcheck()
        self._store = store
        self._handlers = dict(handlers)
        self._capacity = max_pending
        self._max_attempts = max_attempts
        self._owner_id = uuid4().hex
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop_retries = threading.Event()
        self._worker_context = threading.local()
        self._futures: dict[str, Future[None]] = {}
        self._closed = False
        self._recovery_enabled = False
        self._lease = _RunnerLease(store.path)
        try:
            self._executor = ThreadPoolExecutor(
                max_workers=max_workers, thread_name_prefix="tactiq-job"
            )
            self._dispatcher = threading.Thread(
                target=self._dispatch, name="tactiq-job-recovery", daemon=True
            )
            self._dispatcher.start()
        except BaseException:
            executor = getattr(self, "_executor", None)
            if executor is not None:
                executor.shutdown(wait=False, cancel_futures=True)
            self._lease.release()
            raise

    @staticmethod
    def _unavailable(detail: str) -> HTTPException:
        return HTTPException(status_code=503, detail=detail, headers={"Retry-After": "1"})

    def submit(self, tenant_id: str, kind: str, payload: dict[str, Any]) -> Job:
        identifier(tenant_id, "tenant_id")
        identifier(kind, "kind")
        encode_object(payload)
        with self._lock:
            if self._closed:
                raise self._unavailable("Background jobs are unavailable")
            if kind not in self._handlers:
                raise HTTPException(status_code=400, detail="Unsupported job kind")
            if len(self._futures) >= self._capacity:
                raise self._unavailable("Background job capacity is exhausted")
            job = self._store._create_job(tenant_id, kind, payload, self._max_attempts)
            try:
                self._schedule_locked(job)
            except Exception as exc:
                _logger.exception("background_job_scheduling_failed", extra={"job_id": job.job_id})
                self._store._fail_queued_job(job.job_id)
                raise self._unavailable("Background jobs are unavailable") from exc
            return job

    def get(self, tenant_id: str, job_id: str) -> Job | None:
        return self._store._get_job(tenant_id, job_id)

    def cancel(self, tenant_id: str, job_id: str) -> Job | None:
        """Queued work cancels immediately; running work records a cancellation request."""
        with self._lock:
            job = self._store._cancel_job(tenant_id, job_id)
            if job is not None and job.status == "cancelled":
                future = self._futures.get(job_id)
                if future is not None:
                    future.cancel()
            return job

    def recover(self) -> int:
        """Resume durable work; return the number of queued records found.

        Repeated calls do not reset this runner's active claims. Records beyond
        max_pending stay on disk and are admitted as slots become available.
        """
        with self._lock:
            if self._closed:
                raise self._unavailable("Background jobs are unavailable")
            self._store._recover_jobs(self._owner_id)
            queued = self._store._queued_count()
            self._recovery_enabled = True
            self._wake.set()
            return queued

    def _schedule_locked(self, job: Job) -> None:
        future = self._executor.submit(self._execute, job.job_id)
        self._futures[job.job_id] = future
        future.add_done_callback(lambda completed: self._done(job.job_id, completed))

    def _execute(self, job_id: str) -> None:
        self._worker_context.active = True
        try:
            self._execute_attempts(job_id)
        finally:
            self._worker_context.active = False

    def _execute_attempts(self, job_id: str) -> None:
        while True:
            job = self._store._claim_job(job_id, self._owner_id)
            if job is None:
                return
            handler = self._handlers.get(job.kind)
            if handler is None:
                self._store._finish_job(
                    job_id,
                    self._owner_id,
                    status="failed",
                    error="Job handler is unavailable.",
                )
                return
            try:
                result = handler(job.payload)
                encode_object(result)
            except PublicJobError as exc:
                _logger.warning("background_job_input_rejected", extra={"job_id": job_id, "kind": job.kind})
                self._store._finish_job(job_id, self._owner_id, status="failed", error=exc.public_message)
                return
            except RetryableJobError:
                _logger.exception(
                    "background_job_retryable_failure",
                    extra={"job_id": job_id, "tenant_id": job.tenant_id, "kind": job.kind},
                )
                retried = self._store._retry_job(job_id, self._owner_id)
                if retried is None or retried.status != "queued" or self._stop_retries.is_set():
                    return
            except Exception:
                _logger.exception(
                    "background_job_failed",
                    extra={"job_id": job_id, "tenant_id": job.tenant_id, "kind": job.kind},
                )
                self._store._finish_job(
                    job_id, self._owner_id, status="failed", error=_EXECUTION_ERROR
                )
                return
            else:
                self._store._finish_job(
                    job_id, self._owner_id, status="succeeded", result=result
                )
                return

    def _done(self, job_id: str, future: Future[None]) -> None:
        try:
            if not future.cancelled():
                error = future.exception()
                if error is not None:
                    _logger.error(
                        "background_job_runtime_failed",
                        extra={"job_id": job_id},
                        exc_info=(type(error), error, error.__traceback__),
                    )
                    try:
                        self._store._finish_job(
                            job_id, self._owner_id, status="failed", error=_EXECUTION_ERROR
                        )
                    except Exception:
                        _logger.exception(
                            "background_job_failure_persistence_failed", extra={"job_id": job_id}
                        )
        finally:
            with self._lock:
                self._futures.pop(job_id, None)
                if self._closed:
                    self._release_if_idle_locked()
                elif self._recovery_enabled:
                    self._wake.set()

    def _dispatch(self) -> None:
        while True:
            self._wake.wait()
            self._wake.clear()
            retry = False
            with self._lock:
                if self._closed:
                    return
                if not self._recovery_enabled or len(self._futures) >= self._capacity:
                    continue
                try:
                    for job in self._store._queued_jobs(self._capacity):
                        if len(self._futures) >= self._capacity:
                            break
                        if job.job_id not in self._futures:
                            self._schedule_locked(job)
                except Exception:
                    _logger.exception("background_job_recovery_scheduling_failed")
                    retry = True
            if retry:
                self._wake.wait(timeout=1.0)
                self._wake.set()

    def _release_if_idle_locked(self) -> None:
        if not self._futures:
            self._lease.release()

    def close(self, wait: bool = True) -> None:
        """Stop admissions and release resources.

        wait=True drains admitted jobs, including their explicit retries.
        wait=False leaves not-yet-running jobs queued for the next recover().
        Running threads finish normally; the exclusive lease is held until
        their callbacks finish. Durable backlog not yet admitted stays queued.
        """
        if not isinstance(wait, bool):
            raise ValueError("wait must be a boolean")
        if wait and getattr(self._worker_context, "active", False):
            raise RuntimeError("A job handler cannot wait for its own runner to close")
        with self._lock:
            self._closed = True
            if not wait:
                self._stop_retries.set()
            self._wake.set()
        if wait and threading.current_thread() is not self._dispatcher:
            self._dispatcher.join()
        self._executor.shutdown(wait=wait, cancel_futures=not wait)
        with self._lock:
            self._release_if_idle_locked()

    def __enter__(self) -> BackgroundJobs:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
