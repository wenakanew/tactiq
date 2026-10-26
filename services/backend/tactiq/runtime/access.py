"""Opt-in access/admission reference, NOT production authentication middleware.

SQLite is a single-host transactional reference, not a distributed cloud adapter.
Integrators must validate scenario/upload/link inputs before creating AnalysisIntent,
admit work only after reserve_analysis, and enforce require_analysis on every read.
An admitted reservation counts even when subsequent processing fails. Replaying,
listening to, or reopening an owned analysis never calls reserve_analysis.

All clocks and monetary policies are server controlled. A usage policy is an
explicit approved Tactiq allocation, not a claim to enforce subscription spending.
No default allocation assumes the entire USD150 subscription budget is available.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import sqlite3
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol


DAY = 86_400
AnalysisKind = Literal["scenario", "upload", "link"]
Meter = Literal["model_tokens", "speech_characters", "processing", "storage"]


class AccessDenied(PermissionError):
    """Missing, expired or insufficient ownership proof."""


class SignInRequired(AccessDenied):
    def __init__(self, resets_at: float) -> None:
        super().__init__("Microsoft sign-in is required for another new analysis")
        self.resets_at = resets_at


class ReservationConflict(ValueError):
    """An idempotency key was reused for a different request."""


class BudgetExceeded(AccessDenied):
    """The approved application allocation cannot cover the reservation."""


def _text(value: str, name: str, maximum: int = 256) -> str:
    if (
        not isinstance(value, str) or not 1 <= len(value) <= maximum
        or value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        raise ValueError(f"Invalid {name}")
    return value


def _integer(value: int, name: str, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= 2**63 - 1:
        raise ValueError(f"Invalid {name}")
    return value


def _digest(token: str) -> str:
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        raise AccessDenied("Invalid session proof")
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def normalize_ip(value: str) -> str:
    """Accept literal addresses only; fold IPv4-mapped IPv6 into the same quota."""
    if not isinstance(value, str) or "%" in value:
        raise ValueError("Invalid client address")
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise ValueError("Invalid client address") from None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return str(address.ipv4_mapped)
    return address.compressed


@dataclass(frozen=True)
class ClientIPPolicy:
    """Trust X-Forwarded-For ONLY from explicitly configured network proxies.

    The ASGI server must preserve the socket peer (disable its own indiscriminate
    forwarded-header rewriting). The trusted ingress must append/replace XFF.
    Walk from the socket towards the client, stopping at the first untrusted hop.
    Caller-controlled hops to its left cannot choose a quota identity. RFC7239
    Forwarded and other headers are deliberately not accepted by this contract.
    """

    trusted_proxy_cidrs: tuple[str, ...] = ()
    _networks: tuple = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.trusted_proxy_cidrs, tuple):
            raise ValueError("Trusted proxy networks must be explicitly configured")
        networks = tuple(ipaddress.ip_network(cidr, strict=True) for cidr in self.trusted_proxy_cidrs)
        if any(network.prefixlen == 0 for network in networks):
            raise ValueError("Trusting every peer would allow forwarding spoofing")
        object.__setattr__(
            self, "_networks", networks,
        )

    def _trusted(self, address: str) -> bool:
        parsed = ipaddress.ip_address(address)
        return any(parsed in network for network in self._networks)

    def resolve(self, actual_peer: str, forwarded_for: str | None = None) -> str:
        peer = normalize_ip(actual_peer)
        if not self._trusted(peer) or forwarded_for is None:
            return peer
        if not isinstance(forwarded_for, str) or len(forwarded_for) > 4096:
            raise ValueError("Invalid forwarding chain")
        hops = forwarded_for.split(",")
        if not 1 <= len(hops) <= 32:
            raise ValueError("Invalid forwarding chain")
        chain = [normalize_ip(hop.strip()) for hop in hops]
        current = peer
        for hop in reversed(chain):
            if not self._trusted(current):
                break
            current = hop
        return current


class IPPseudonymizer:
    """Use a separately managed, stable >=256-bit key; rotation resets quota keys."""

    def __init__(self, key: bytes) -> None:
        if not isinstance(key, bytes) or len(key) < 32:
            raise ValueError("IP HMAC key must contain at least 32 bytes")
        self._key = key

    def hash(self, verified_ip: str) -> str:
        return hmac.new(
            self._key, b"tactiq-trial-ip-v1\0" + normalize_ip(verified_ip).encode("ascii"),
            hashlib.sha256,
        ).hexdigest()


@dataclass(frozen=True)
class SessionPrincipal:
    tenant_id: str
    subject: str
    guest: bool
    expires_at: float
    role: Literal["analyst"] = field(default="analyst", init=False)


@dataclass(frozen=True)
class IssuedSession:
    token: str = field(repr=False)
    principal: SessionPrincipal

    def cookie_options(self) -> dict[str, str | bool | int]:
        """HTTPS only. Integration chooses its own non-legacy cookie name."""
        return {
            "key": "__Host-tactiq_access", "secure": True, "httponly": True,
            "samesite": "lax", "path": "/", "max_age": DAY,
        }


@dataclass(frozen=True)
class AnalysisIntent:
    """Construct only from fully validated input; fingerprint canonical payload.

    operation_id is client retry correlation, never a match ID/ownership claim.
    The SHA256 fingerprint must cover every effective kind-specific input and
    recipe version; this boundary cannot validate media or scenario schemas.
    """

    operation_id: str
    kind: AnalysisKind
    payload_sha256: str

    def __post_init__(self) -> None:
        _text(self.operation_id, "operation ID", 128)
        if not isinstance(self.kind, str) or self.kind not in {"scenario", "upload", "link"}:
            raise ValueError("Only new analyses are counted")
        if not isinstance(self.payload_sha256, str) or not re.fullmatch(
            r"[0-9a-f]{64}", self.payload_sha256
        ):
            raise ValueError("Invalid validated payload fingerprint")


@dataclass(frozen=True)
class AnalysisReservation:
    analysis_id: str
    tenant_id: str
    operation_id: str
    kind: AnalysisKind
    remaining: int | None
    resets_at: float | None
    replayed: bool = False


@dataclass(frozen=True)
class TrialAllowance:
    remaining: int | None
    resets_at: float | None


@dataclass(frozen=True)
class IdentityKey:
    """Trusted provider output only, never deserialize this from request claims."""

    issuer: str
    subject: str

    def __post_init__(self) -> None:
        _text(self.issuer, "issuer", 512)
        _text(self.subject, "subject")


@dataclass(frozen=True)
class UsagePolicy:
    period_start: float
    period_end: float
    global_limit_microusd: int
    tenant_limit_microusd: int

    def __post_init__(self) -> None:
        if not (0 <= self.period_start < self.period_end < float("inf")):
            raise ValueError("Invalid budget period")
        _integer(self.global_limit_microusd, "global limit")
        _integer(self.tenant_limit_microusd, "tenant limit")
        if not self.tenant_limit_microusd <= self.global_limit_microusd <= 150_000_000:
            raise ValueError("Allocation must not exceed the USD150 subscription ceiling")


@dataclass(frozen=True)
class UsageIntent:
    """Server-priced worst-case cost and units, never client-supplied estimates."""

    operation_id: str
    meter: Meter
    units: int
    maximum_cost_microusd: int

    def __post_init__(self) -> None:
        _text(self.operation_id, "usage operation ID", 128)
        if not isinstance(self.meter, str) or self.meter not in {
            "model_tokens", "speech_characters", "processing", "storage",
        }:
            raise ValueError("Invalid meter")
        _integer(self.units, "units", 1)
        _integer(self.maximum_cost_microusd, "maximum cost", 1)


@dataclass(frozen=True)
class UsageReservation:
    reservation_id: str
    tenant_id: str
    meter: Meter
    units: int
    maximum_cost_microusd: int
    status: Literal["reserved", "settled", "released"]
    actual_cost_microusd: int | None
    replayed: bool = False


@dataclass(frozen=True)
class ExpiredGuest:
    tenant_id: str
    analysis_ids: tuple[str, ...]


@dataclass(frozen=True)
class PendingLogin:
    flow: Mapping = field(repr=False)
    guest_digest: str | None = field(default=None, repr=False)


class AccessStore(Protocol):
    """Cloud adapters must preserve these transaction and ownership semantics.

    A replayed reservation identifies existing work; it is not permission to
    enqueue or call a paid provider again. Use the durable worker boundary for
    execution/lease recovery, not this admission store as a job runner.
    """

    def issue_guest(self) -> IssuedSession: ...
    def authenticate(self, token: str) -> SessionPrincipal: ...
    def revoke_session(self, token: str) -> None: ...
    def authorized_tenants(self, token: str) -> tuple[str, ...]: ...
    def trial_allowance(self, token: str, verified_ip: str) -> TrialAllowance: ...
    def reserve_analysis(
        self, token: str, verified_ip: str, intent: AnalysisIntent,
    ) -> AnalysisReservation: ...
    def require_analysis(self, token: str, analysis_id: str) -> SessionPrincipal: ...
    def sign_in(self, identity: IdentityKey, *, guest_digest: str | None = None) -> IssuedSession: ...
    def reserve_usage(self, token: str, intent: UsageIntent) -> UsageReservation: ...
    def settle_usage(
        self, token: str, reservation_id: str, actual_units: int, actual_cost_microusd: int,
    ) -> UsageReservation: ...
    def release_usage(self, token: str, reservation_id: str) -> UsageReservation: ...
    def expire_guests(self, limit: int = 100) -> tuple[ExpiredGuest, ...]: ...
    def acknowledge_cleanup(self, tenant_id: str) -> None: ...
    def save_login(
        self, binding: str, flow: Mapping, guest_token: str | None = None,
    ) -> None: ...
    def consume_login(self, binding: str, state: str) -> PendingLogin: ...


_SCHEMA = """
CREATE TABLE IF NOT EXISTS access_tenants(
 id TEXT PRIMARY KEY, guest INTEGER NOT NULL, expires REAL,
 promoted_to TEXT, expired INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS access_accounts(
 issuer TEXT NOT NULL, subject TEXT NOT NULL, account_id TEXT NOT NULL,
 tenant_id TEXT NOT NULL, UNIQUE(issuer,subject));
CREATE TABLE IF NOT EXISTS access_sessions(
 digest TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, subject TEXT NOT NULL,
 expires REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS access_trial_windows(
 ip_hash TEXT PRIMARY KEY, starts REAL NOT NULL, used INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS access_analyses(
 id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, operation_id TEXT NOT NULL,
 kind TEXT NOT NULL, fingerprint TEXT NOT NULL,
 UNIQUE(tenant_id,operation_id));
CREATE TABLE IF NOT EXISTS access_usage(
 id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, operation_id TEXT NOT NULL,
 meter TEXT NOT NULL, units INTEGER NOT NULL, maximum_cost INTEGER NOT NULL,
 period_start REAL NOT NULL, period_end REAL NOT NULL, status TEXT NOT NULL,
 actual_units INTEGER, actual_cost INTEGER, UNIQUE(tenant_id,operation_id));
CREATE TABLE IF NOT EXISTS access_cleanup(
 tenant_id TEXT PRIMARY KEY, analysis_ids TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS access_logins(
 binding_hash TEXT PRIMARY KEY, state_hash TEXT NOT NULL, flow TEXT NOT NULL,
 guest_digest TEXT, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS access_budget_policies(
 period_start REAL NOT NULL, period_end REAL NOT NULL,
 global_limit INTEGER NOT NULL, tenant_limit INTEGER NOT NULL,
 PRIMARY KEY(period_start,period_end));
CREATE INDEX IF NOT EXISTS access_usage_period ON access_usage(period_start,period_end);
CREATE INDEX IF NOT EXISTS access_tenant_expiry ON access_tenants(guest,expired,expires);
"""


class SQLiteAccessStore:
    """Durable file-backed reference. Each mutation uses BEGIN IMMEDIATE.

    Keep DB private/encrypted at rest: login flows contain short-lived nonce and
    PKCE verifier material. Never log them. Sessions contain only token hashes.
    Clock must be a trusted synchronized server clock; callers cannot supply it.
    """

    def __init__(
        self, path: str | Path, *, ip_hmac_key: bytes,
        usage_policy: UsagePolicy | None = None, clock: Callable[[], float] = time.time,
    ) -> None:
        if str(path) == ":memory:":
            raise ValueError("A durable file path is required")
        self.path = Path(path)
        self._ips = IPPseudonymizer(ip_hmac_key)
        self._usage_policy = usage_policy
        self._clock = clock
        with closing(self._connect()) as db:
            db.executescript(_SCHEMA)
        if usage_policy is not None:
            with self._transaction() as db:
                existing = db.execute(
                    """SELECT * FROM access_budget_policies
                       WHERE period_start < ? AND period_end > ?""",
                    (usage_policy.period_end, usage_policy.period_start),
                ).fetchall()
                configured = (
                    usage_policy.period_start, usage_policy.period_end,
                    usage_policy.global_limit_microusd, usage_policy.tenant_limit_microusd,
                )
                if existing and (len(existing) != 1 or tuple(existing[0]) != configured):
                    raise ValueError("Conflicting approved usage allocation")
                if not existing:
                    db.execute("INSERT INTO access_budget_policies VALUES(?,?,?,?)", configured)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _principal(self, db: sqlite3.Connection, digest: str) -> SessionPrincipal:
        row = db.execute(
            """SELECT s.*, t.guest, t.expired, t.promoted_to FROM access_sessions s
               JOIN access_tenants t ON t.id=s.tenant_id WHERE s.digest=?""", (digest,),
        ).fetchone()
        if (
            row is None or row["revoked"] or row["expired"] or row["promoted_to"]
            or row["expires"] <= self._clock()
        ):
            raise AccessDenied("Session is missing or expired")
        return SessionPrincipal(row["tenant_id"], row["subject"], bool(row["guest"]), row["expires"])

    def _issue(
        self, db: sqlite3.Connection, tenant: str, subject: str, guest: bool, expires: float,
    ) -> IssuedSession:
        token = secrets.token_urlsafe(32)
        db.execute(
            "INSERT INTO access_sessions(digest,tenant_id,subject,expires) VALUES(?,?,?,?)",
            (_digest(token), tenant, subject, expires),
        )
        return IssuedSession(token, SessionPrincipal(tenant, subject, guest, expires))

    def issue_guest(self) -> IssuedSession:
        with self._transaction() as db:
            tenant, subject = str(uuid.uuid4()), str(uuid.uuid4())
            expires = self._clock() + DAY
            db.execute(
                "INSERT INTO access_tenants(id,guest,expires) VALUES(?,1,?)", (tenant, expires),
            )
            return self._issue(db, tenant, subject, True, expires)

    def authenticate(self, token: str) -> SessionPrincipal:
        with self._transaction() as db:
            return self._principal(db, _digest(token))

    def revoke_session(self, token: str) -> None:
        with self._transaction() as db:
            db.execute("UPDATE access_sessions SET revoked=1 WHERE digest=?", (_digest(token),))

    @staticmethod
    def _tenants(db: sqlite3.Connection, principal: SessionPrincipal) -> tuple[str, ...]:
        return (principal.tenant_id,) + tuple(
            row["id"] for row in db.execute(
                "SELECT id FROM access_tenants WHERE promoted_to=?", (principal.tenant_id,),
            )
        )

    def authorized_tenants(self, token: str) -> tuple[str, ...]:
        with self._transaction() as db:
            return self._tenants(db, self._principal(db, _digest(token)))

    def trial_allowance(self, token: str, verified_ip: str) -> TrialAllowance:
        """Read-only display information: never starts a window or reserves a use."""
        ip_hash = self._ips.hash(verified_ip)
        with self._transaction() as db:
            principal = self._principal(db, _digest(token))
            if not principal.guest:
                return TrialAllowance(None, None)
            window = db.execute(
                "SELECT * FROM access_trial_windows WHERE ip_hash=?", (ip_hash,),
            ).fetchone()
            if window is None or window["starts"] + DAY <= self._clock():
                return TrialAllowance(3, None)
            return TrialAllowance(3 - window["used"], window["starts"] + DAY)

    def reserve_analysis(
        self, token: str, verified_ip: str, intent: AnalysisIntent,
    ) -> AnalysisReservation:
        if not isinstance(intent, AnalysisIntent):
            raise ValueError("A validated analysis intent is required")
        ip_hash = self._ips.hash(verified_ip)
        with self._transaction() as db:
            principal = self._principal(db, _digest(token))
            prior = db.execute(
                """SELECT * FROM access_analyses WHERE
                   (tenant_id=? OR tenant_id IN (SELECT id FROM access_tenants WHERE promoted_to=?))
                   AND operation_id=?""",
                (principal.tenant_id, principal.tenant_id, intent.operation_id),
            ).fetchall()
            if len(prior) > 1:
                raise ReservationConflict("Ambiguous operation after promotion")
            if prior and (
                prior[0]["kind"] != intent.kind or prior[0]["fingerprint"] != intent.payload_sha256
            ):
                raise ReservationConflict("Operation input changed")
            remaining, resets = None, None
            if principal.guest:
                now = self._clock()
                window = db.execute(
                    "SELECT * FROM access_trial_windows WHERE ip_hash=?", (ip_hash,),
                ).fetchone()
                if window is None or window["starts"] + DAY <= now:
                    used, starts = 0, now
                else:
                    used, starts = window["used"], window["starts"]
                if not prior:
                    if used >= 3:
                        raise SignInRequired(starts + DAY)
                    used += 1
                    db.execute(
                        """INSERT INTO access_trial_windows VALUES(?,?,?)
                           ON CONFLICT(ip_hash) DO UPDATE SET starts=excluded.starts,used=excluded.used""",
                        (ip_hash, starts, used),
                    )
                remaining, resets = 3 - used, starts + DAY
            if prior:
                row = prior[0]
                return AnalysisReservation(
                    row["id"], row["tenant_id"], intent.operation_id, intent.kind,
                    remaining, resets, True,
                )
            analysis_id = str(uuid.uuid4())
            db.execute(
                "INSERT INTO access_analyses VALUES(?,?,?,?,?)",
                (analysis_id, principal.tenant_id, intent.operation_id, intent.kind, intent.payload_sha256),
            )
            return AnalysisReservation(
                analysis_id, principal.tenant_id, intent.operation_id, intent.kind, remaining, resets,
            )

    def require_analysis(self, token: str, analysis_id: str) -> SessionPrincipal:
        _text(analysis_id, "analysis ID")
        with self._transaction() as db:
            principal = self._principal(db, _digest(token))
            row = db.execute(
                "SELECT tenant_id FROM access_analyses WHERE id=?", (analysis_id,),
            ).fetchone()
            if row is None or row["tenant_id"] not in self._tenants(db, principal):
                raise AccessDenied("Analysis is not owned by this session")
            return principal

    def sign_in(self, identity: IdentityKey, *, guest_digest: str | None = None) -> IssuedSession:
        """Called ONLY with validated OIDC identity and consumed flow's guest proof.

        Directory IDs, IP addresses, request-supplied roles and raw claims are not
        accepted. Keep original guest tenant as an alias for external data adapters.
        Promotion and expiry compete under the same transaction lock.
        """
        if not isinstance(identity, IdentityKey):
            raise ValueError("A verified provider identity is required")
        with self._transaction() as db:
            guest = self._principal(db, guest_digest) if guest_digest is not None else None
            if guest is not None and not guest.guest:
                raise AccessDenied("Promotion requires the original guest session")
            account = db.execute(
                "SELECT * FROM access_accounts WHERE issuer=? AND subject=?",
                (identity.issuer, identity.subject),
            ).fetchone()
            if account is None:
                tenant, subject = str(uuid.uuid4()), str(uuid.uuid4())
                db.execute("INSERT INTO access_tenants(id,guest) VALUES(?,0)", (tenant,))
                db.execute(
                    "INSERT INTO access_accounts VALUES(?,?,?,?)",
                    (identity.issuer, identity.subject, subject, tenant),
                )
            else:
                tenant, subject = account["tenant_id"], account["account_id"]
            if guest is not None:
                db.execute(
                    "UPDATE access_tenants SET promoted_to=? WHERE id=?", (tenant, guest.tenant_id),
                )
                db.execute(
                    "UPDATE access_sessions SET revoked=1 WHERE tenant_id=?", (guest.tenant_id,),
                )
            return self._issue(db, tenant, subject, False, self._clock() + DAY)

    @staticmethod
    def _usage(row: sqlite3.Row, replayed: bool = False) -> UsageReservation:
        return UsageReservation(
            row["id"], row["tenant_id"], row["meter"], row["units"], row["maximum_cost"],
            row["status"], row["actual_cost"], replayed,
        )

    def reserve_usage(self, token: str, intent: UsageIntent) -> UsageReservation:
        if not isinstance(intent, UsageIntent):
            raise ValueError("A validated usage intent is required")
        policy = self._usage_policy
        with self._transaction() as db:
            if policy is None or not policy.period_start <= self._clock() < policy.period_end:
                raise BudgetExceeded("No active approved application allocation")
            principal = self._principal(db, _digest(token))
            tenants = self._tenants(db, principal)
            prior = db.execute(
                """SELECT * FROM access_usage WHERE
                   (tenant_id=? OR tenant_id IN (SELECT id FROM access_tenants WHERE promoted_to=?))
                   AND operation_id=?""",
                (principal.tenant_id, principal.tenant_id, intent.operation_id),
            ).fetchall()
            if len(prior) > 1:
                raise ReservationConflict("Ambiguous usage operation after promotion")
            if prior:
                row = prior[0]
                if (
                    row["meter"], row["units"], row["maximum_cost"],
                    row["period_start"], row["period_end"],
                ) != (
                    intent.meter, intent.units, intent.maximum_cost_microusd,
                    policy.period_start, policy.period_end,
                ):
                    raise ReservationConflict("Usage operation input changed")
                return self._usage(row, True)
            rows = db.execute(
                """SELECT tenant_id, status, maximum_cost, actual_cost FROM access_usage
                   WHERE period_start < ? AND period_end > ?""",
                (policy.period_end, policy.period_start),
            ).fetchall()
            total, tenant_total = 0, 0
            for row in rows:
                cost = (
                    row["maximum_cost"] if row["status"] == "reserved"
                    else row["actual_cost"] if row["status"] == "settled" else 0
                )
                total += cost
                if row["tenant_id"] in tenants:
                    tenant_total += cost
            maximum = intent.maximum_cost_microusd
            if (
                total + maximum > policy.global_limit_microusd
                or tenant_total + maximum > policy.tenant_limit_microusd
            ):
                raise BudgetExceeded("Application usage allocation exhausted")
            reservation_id = str(uuid.uuid4())
            db.execute(
                """INSERT INTO access_usage
                   (id,tenant_id,operation_id,meter,units,maximum_cost,period_start,period_end,status)
                   VALUES(?,?,?,?,?,?,?,?,'reserved')""",
                (reservation_id, principal.tenant_id, intent.operation_id, intent.meter,
                 intent.units, maximum, policy.period_start, policy.period_end),
            )
            return self._usage(db.execute(
                "SELECT * FROM access_usage WHERE id=?", (reservation_id,),
            ).fetchone())

    def _owned_usage(
        self, db: sqlite3.Connection, token: str, reservation_id: str,
    ) -> sqlite3.Row:
        principal = self._principal(db, _digest(token))
        row = db.execute("SELECT * FROM access_usage WHERE id=?", (reservation_id,)).fetchone()
        if row is None or row["tenant_id"] not in self._tenants(db, principal):
            raise AccessDenied("Usage reservation is not owned by this session")
        return row

    def settle_usage(
        self, token: str, reservation_id: str, actual_units: int, actual_cost_microusd: int,
    ) -> UsageReservation:
        """Provider adapter must enforce reserved worst-case limits BEFORE calling.

        Settlement never hides an overrun. Metering provider usage and deciding
        whether a timed-out call incurred cost remain the integrator's responsibility.
        """
        _integer(actual_units, "actual units")
        _integer(actual_cost_microusd, "actual cost")
        with self._transaction() as db:
            row = self._owned_usage(db, token, reservation_id)
            if actual_units > row["units"] or actual_cost_microusd > row["maximum_cost"]:
                raise ReservationConflict("Usage exceeded its worst-case reservation")
            if row["status"] == "settled":
                if (row["actual_units"], row["actual_cost"]) != (actual_units, actual_cost_microusd):
                    raise ReservationConflict("Usage settlement changed")
                return self._usage(row, True)
            if row["status"] != "reserved":
                raise ReservationConflict("Released usage cannot be settled")
            db.execute(
                "UPDATE access_usage SET status='settled',actual_units=?,actual_cost=? WHERE id=?",
                (actual_units, actual_cost_microusd, reservation_id),
            )
            return self._usage(db.execute(
                "SELECT * FROM access_usage WHERE id=?", (reservation_id,),
            ).fetchone())

    def release_usage(self, token: str, reservation_id: str) -> UsageReservation:
        """Release ONLY when provider work was not invoked / cannot incur a charge."""
        with self._transaction() as db:
            row = self._owned_usage(db, token, reservation_id)
            if row["status"] == "settled":
                raise ReservationConflict("Settled usage cannot be released")
            if row["status"] == "released":
                return self._usage(row, True)
            db.execute("UPDATE access_usage SET status='released' WHERE id=?", (reservation_id,))
            return self._usage(db.execute(
                "SELECT * FROM access_usage WHERE id=?", (reservation_id,),
            ).fetchone())

    def expire_guests(self, limit: int = 100) -> tuple[ExpiredGuest, ...]:
        """Return durable cleanup intents until acknowledged; no external IO here.

        Access is denied by the clock even before this sweep. External metadata/
        media deletion must be idempotent. Promoted tenants are never selected.
        Backup and soft-delete retention are outside this reference contract.
        """
        _integer(limit, "cleanup limit", 1)
        if limit > 1000:
            raise ValueError("Cleanup batch is too large")
        with self._transaction() as db:
            db.execute("DELETE FROM access_logins WHERE expires<=?", (self._clock(),))
            db.execute(
                "DELETE FROM access_trial_windows WHERE starts<=?", (self._clock() - DAY,),
            )
            rows = db.execute(
                """SELECT id FROM access_tenants WHERE guest=1 AND expired=0
                   AND promoted_to IS NULL AND expires<=? LIMIT ?""",
                (self._clock(), limit),
            ).fetchall()
            for row in rows:
                tenant = row["id"]
                ids = [item["id"] for item in db.execute(
                    "SELECT id FROM access_analyses WHERE tenant_id=? ORDER BY id", (tenant,),
                )]
                db.execute("UPDATE access_tenants SET expired=1 WHERE id=?", (tenant,))
                db.execute("UPDATE access_sessions SET revoked=1 WHERE tenant_id=?", (tenant,))
                db.execute("INSERT INTO access_cleanup VALUES(?,?)", (tenant, json.dumps(ids)))
            return tuple(
                ExpiredGuest(row["tenant_id"], tuple(json.loads(row["analysis_ids"])))
                for row in db.execute("SELECT * FROM access_cleanup ORDER BY tenant_id LIMIT ?", (limit,))
            )

    def acknowledge_cleanup(self, tenant_id: str) -> None:
        with self._transaction() as db:
            pending = db.execute(
                "SELECT 1 FROM access_cleanup WHERE tenant_id=?", (tenant_id,),
            ).fetchone()
            if pending is None:
                return
            db.execute("DELETE FROM access_analyses WHERE tenant_id=?", (tenant_id,))
            db.execute(
                """DELETE FROM access_logins WHERE guest_digest IN
                   (SELECT digest FROM access_sessions WHERE tenant_id=?)""", (tenant_id,),
            )
            db.execute("DELETE FROM access_sessions WHERE tenant_id=?", (tenant_id,))
            db.execute("DELETE FROM access_cleanup WHERE tenant_id=?", (tenant_id,))

    def save_login(
        self, binding: str, flow: Mapping, guest_token: str | None = None,
    ) -> None:
        binding_hash = _digest(binding)
        state = _text(flow.get("state"), "OIDC state", 512)
        # Preserve the library's opaque flow internals, not tokens or login PII.
        saved = dict(flow)
        saved.pop("auth_uri", None)
        serialized = json.dumps(saved)
        if len(serialized) > 16_384:
            raise ValueError("OIDC flow exceeds storage limit")
        with self._transaction() as db:
            guest_digest = _digest(guest_token) if guest_token is not None else None
            if guest_digest is not None and not self._principal(db, guest_digest).guest:
                raise AccessDenied("Expected a guest session")
            db.execute(
                "INSERT INTO access_logins VALUES(?,?,?,?,?)",
                (binding_hash, hashlib.sha256(state.encode()).hexdigest(),
                 serialized, guest_digest, self._clock() + 600),
            )

    def consume_login(self, binding: str, state: str) -> PendingLogin:
        state = _text(state, "OIDC state", 512)
        with self._transaction() as db:
            row = db.execute(
                "SELECT * FROM access_logins WHERE binding_hash=?", (_digest(binding),),
            ).fetchone()
            if row is None or row["expires"] <= self._clock() or not hmac.compare_digest(
                row["state_hash"], hashlib.sha256(state.encode()).hexdigest(),
            ):
                raise AccessDenied("Invalid or expired sign-in flow")
            db.execute("DELETE FROM access_logins WHERE binding_hash=?", (_digest(binding),))
            return PendingLogin(json.loads(row["flow"]), row["guest_digest"])
