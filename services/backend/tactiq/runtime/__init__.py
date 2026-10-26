"""Production-hardening primitives, with no import-time app or disk activity.

Initialize during the application's lifespan, not at module import::

    auth = AuthSettings.from_env()
    store = SQLiteStore(database_path)
    jobs = BackgroundJobs(store, {"video": ingest_json_options})
    jobs.recover()

    principal = auth.authenticate(authorization_header, cookie_value)
    require_role(principal, "analyst")
    job = jobs.submit(principal.tenant_id, "video", {"source_path": source_path})
    response_body = job.model_dump(mode="json")

    # On shutdown, in this order:
    jobs.close()
    store.close()

Use principal.tenant_id for every storage/job call. list_matches returns
metadata envelopes (tenant_id, match_id, created_at, updated_at), not snapshots.
For browser login, Response.set_cookie(value=token, **auth.cookie_options())
sets an HTTP-only, SameSite=Lax cookie, always Secure in production. Never put
tokens in query strings or log raw headers. WebSocket code can reuse the same
auth/role helpers and translate HTTPException(401/403) into its close protocol.

This package supplies a local SQLite/threaded runtime, not a distributed queue,
object store, full telemetry exporter, or a complete production launch gate.
"""

from .auth import (
    AuthConfigurationError,
    AuthSettings,
    Principal,
    Role,
    authenticate,
    require_role,
    require_tenant,
)
from ._validation import identifier as validate_identifier
from .jobs import BackgroundJobs, RetryableJobError
from .models import Job, JobStatus
from .observability import (
    RequestMetrics,
    ensure_request_id,
    is_valid_request_id,
    log_event,
)
from .storage import (
    RecordValidationError,
    SQLiteStore,
    StorageCorruptionError,
    StorageVersionError,
    TenantConflictError,
)

__all__ = [
    "AuthConfigurationError",
    "AuthSettings",
    "BackgroundJobs",
    "Job",
    "JobStatus",
    "Principal",
    "RecordValidationError",
    "RequestMetrics",
    "RetryableJobError",
    "Role",
    "SQLiteStore",
    "StorageCorruptionError",
    "StorageVersionError",
    "TenantConflictError",
    "authenticate",
    "ensure_request_id",
    "is_valid_request_id",
    "log_event",
    "require_role",
    "require_tenant",
    "validate_identifier",
]
