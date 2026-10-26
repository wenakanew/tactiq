"""PostgreSQL implementation of the complete opt-in AccessStore contract.

No SQLite fallback, implicit migrations, credentials in records, provider calls,
or legacy-auth changes. A fresh psycopg connection owns each transaction.
Tenant rows serialize session/ownership changes; IP rows serialize trial windows.
A dedicated budget row serializes global allocation decisions and promotion,
including policy registration, so account sign-in cannot bypass spending limits.

Migrate explicitly using an authorized identity. Production uses PostgreSQL time;
clock injection exists for deterministic integration tests only. Connection
factories must return fresh psycopg3 connections with dict_row and may refresh
managed-identity credentials without persisting them. Secure DB transport/access,
retention of backups, and endpoint composition remain deployment responsibilities.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .access import (
    DAY,
    AccessDenied,
    AnalysisIntent,
    AnalysisReservation,
    BudgetExceeded,
    ExpiredGuest,
    IdentityKey,
    IPPseudonymizer,
    IssuedSession,
    PendingLogin,
    ReservationConflict,
    SessionPrincipal,
    SignInRequired,
    TrialAllowance,
    UsageIntent,
    UsagePolicy,
    UsageReservation,
    _digest,
    _integer,
    _text,
)


class PostgresAccessStore:
    def __init__(
        self, connect: Callable[[], Any], *, ip_hmac_key: bytes,
        usage_policy: UsagePolicy | None = None, clock: Callable[[], float] | None = None,
    ) -> None:
        self._connect = connect
        self._ips = IPPseudonymizer(ip_hmac_key)
        if usage_policy is not None and not isinstance(usage_policy, UsagePolicy):
            raise ValueError("A validated usage policy is required")
        self._usage_policy = usage_policy
        self._clock = clock

    @classmethod
    def from_dsn(
        cls, dsn: str, *, ip_hmac_key: bytes, usage_policy: UsagePolicy | None = None,
        clock: Callable[[], float] | None = None, migrate: bool = False,
    ) -> PostgresAccessStore:
        """DSN is private operator configuration, never session/client input."""
        try:
            from psycopg import connect
            from psycopg.rows import dict_row
        except ImportError:
            raise RuntimeError("Install the optional PostgreSQL dependencies") from None
        store = cls(
            lambda: connect(dsn, row_factory=dict_row, connect_timeout=10),
            ip_hmac_key=ip_hmac_key, usage_policy=usage_policy, clock=clock,
        )
        if migrate:
            store.migrate()
        return store

    @contextmanager
    def _transaction(self) -> Iterator[Any]:
        with self._connect() as connection:
            with connection.transaction():
                connection.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
                connection.execute("SELECT set_config('statement_timeout', %s, true)", ("30000",))
                connection.execute("SELECT set_config('lock_timeout', %s, true)", ("10000",))
                yield connection

    def _now(self, db: Any) -> float:
        if self._clock is not None:
            return self._clock()
        return db.execute(
            "SELECT EXTRACT(EPOCH FROM clock_timestamp())::double precision AS now"
        ).fetchone()["now"]

    @staticmethod
    def _budget_lock(db: Any) -> None:
        row = db.execute(
            "SELECT singleton FROM tactiq_access_budget_guard WHERE singleton FOR UPDATE"
        ).fetchone()
        if row is None:
            raise RuntimeError("Access schema requires migration")

    def _configure_policy(self, db: Any) -> None:
        policy = self._usage_policy
        if policy is None:
            return
        existing = db.execute(
            """SELECT period_start,period_end,global_limit,tenant_limit
               FROM tactiq_access_budget_policies
               WHERE period_start < %s AND period_end > %s""",
            (policy.period_end, policy.period_start),
        ).fetchall()
        configured = (
            policy.period_start, policy.period_end,
            policy.global_limit_microusd, policy.tenant_limit_microusd,
        )
        if existing and (
            len(existing) != 1
            or tuple(existing[0][key] for key in (
                "period_start", "period_end", "global_limit", "tenant_limit",
            )) != configured
        ):
            raise ValueError("Conflicting approved usage allocation")
        if not existing:
            db.execute(
                "INSERT INTO tactiq_access_budget_policies VALUES(%s,%s,%s,%s)", configured,
            )

    def migrate(self) -> None:
        """Idempotent migration of only 002_access.sql, independent of 001_cloud."""
        with self._transaction() as db:
            db.execute("SELECT pg_advisory_xact_lock(854917020602)")
            exists = db.execute(
                "SELECT to_regclass('tactiq_access_schema') AS name"
            ).fetchone()["name"]
            if exists:
                row = db.execute(
                    "SELECT version FROM tactiq_access_schema WHERE singleton"
                ).fetchone()
                if row is None or row["version"] != 2:
                    raise RuntimeError("Unsupported access schema version")
            else:
                migration = Path(__file__).with_name("migrations") / "002_access.sql"
                db.execute(migration.read_text(encoding="utf-8"))
            self._budget_lock(db)
            self._configure_policy(db)

    @staticmethod
    def _session_rows(db: Any, digest: str) -> tuple[Mapping, Mapping]:
        location = db.execute(
            "SELECT tenant_id FROM tactiq_access_sessions WHERE digest=%s", (digest,),
        ).fetchone()
        if location is None:
            raise AccessDenied("Session is missing or expired")
        # Every ownership mutation acquires tenant before session, including expiry.
        tenant = db.execute(
            "SELECT * FROM tactiq_access_tenants WHERE id=%s FOR UPDATE",
            (location["tenant_id"],),
        ).fetchone()
        session = db.execute(
            "SELECT * FROM tactiq_access_sessions WHERE digest=%s FOR UPDATE", (digest,),
        ).fetchone()
        if tenant is None or session is None:
            raise AccessDenied("Session is missing or expired")
        return tenant, session

    def _principal(self, db: Any, digest: str) -> SessionPrincipal:
        tenant, session = self._session_rows(db, digest)
        now = self._now(db)
        if (
            session["revoked"] or tenant["expired"] or tenant["promoted_to"]
            or session["expires"] <= now
            or (tenant["guest"] and tenant["expires"] <= now)
        ):
            raise AccessDenied("Session is missing or expired")
        return SessionPrincipal(
            tenant["id"], session["subject"], tenant["guest"], session["expires"],
        )

    @staticmethod
    def _issue(
        db: Any, tenant: str, subject: str, guest: bool, expires: float,
    ) -> IssuedSession:
        token = secrets.token_urlsafe(32)
        db.execute(
            """INSERT INTO tactiq_access_sessions(digest,tenant_id,subject,expires)
               VALUES(%s,%s,%s,%s)""",
            (_digest(token), tenant, subject, expires),
        )
        return IssuedSession(token, SessionPrincipal(tenant, subject, guest, expires))

    @staticmethod
    def _tenants(db: Any, principal: SessionPrincipal) -> tuple[str, ...]:
        return (principal.tenant_id,) + tuple(
            row["id"] for row in db.execute(
                "SELECT id FROM tactiq_access_tenants WHERE promoted_to=%s ORDER BY id",
                (principal.tenant_id,),
            )
        )

    def issue_guest(self) -> IssuedSession:
        with self._transaction() as db:
            tenant, subject = str(uuid.uuid4()), str(uuid.uuid4())
            expires = self._now(db) + DAY
            db.execute(
                "INSERT INTO tactiq_access_tenants(id,guest,expires) VALUES(%s,true,%s)",
                (tenant, expires),
            )
            return self._issue(db, tenant, subject, True, expires)

    def authenticate(self, token: str) -> SessionPrincipal:
        digest = _digest(token)
        with self._transaction() as db:
            return self._principal(db, digest)

    def revoke_session(self, token: str) -> None:
        digest = _digest(token)
        with self._transaction() as db:
            found = db.execute(
                "SELECT tenant_id FROM tactiq_access_sessions WHERE digest=%s", (digest,),
            ).fetchone()
            if found is None:
                return
            db.execute(
                "SELECT id FROM tactiq_access_tenants WHERE id=%s FOR UPDATE", (found["tenant_id"],),
            )
            db.execute(
                "UPDATE tactiq_access_sessions SET revoked=true WHERE digest=%s", (digest,),
            )

    def authorized_tenants(self, token: str) -> tuple[str, ...]:
        digest = _digest(token)
        with self._transaction() as db:
            return self._tenants(db, self._principal(db, digest))

    def trial_allowance(self, token: str, verified_ip: str) -> TrialAllowance:
        digest, ip_hash = _digest(token), self._ips.hash(verified_ip)
        with self._transaction() as db:
            if not self._principal(db, digest).guest:
                return TrialAllowance(None, None)
            window = db.execute(
                "SELECT * FROM tactiq_access_trial_windows WHERE ip_hash=%s", (ip_hash,),
            ).fetchone()
            if window is None or window["starts"] + DAY <= self._now(db):
                return TrialAllowance(3, None)
            return TrialAllowance(3 - window["used"], window["starts"] + DAY)

    def reserve_analysis(
        self, token: str, verified_ip: str, intent: AnalysisIntent,
    ) -> AnalysisReservation:
        return self.reserve_analysis_and_admit(token, verified_ip, intent, None)

    def reserve_analysis_and_admit(
        self, token: str, verified_ip: str, intent: AnalysisIntent,
        admit: Callable[[Any, AnalysisReservation], None] | None,
    ) -> AnalysisReservation:
        """Atomically compose ownership/quota with job+outbox admission.

        admit receives this transaction's connection and must perform only DB
        writes, idempotently keyed by reservation.analysis_id. It runs for retries
        too, allowing safe reconciliation, and must never call a paid provider.
        Callback failure rolls back ALL changes, including the trial use.
        """
        if not isinstance(intent, AnalysisIntent):
            raise ValueError("A validated analysis intent is required")
        digest, ip_hash = _digest(token), self._ips.hash(verified_ip)
        with self._transaction() as db:
            principal = self._principal(db, digest)
            prior = db.execute(
                """SELECT * FROM tactiq_access_analyses WHERE
                   (tenant_id=%s OR tenant_id IN
                       (SELECT id FROM tactiq_access_tenants WHERE promoted_to=%s))
                   AND operation_id=%s LIMIT 2""",
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
                now = self._now(db)
                if not prior:
                    db.execute(
                        """INSERT INTO tactiq_access_trial_windows(ip_hash,starts,used)
                           VALUES(%s,%s,0) ON CONFLICT(ip_hash) DO NOTHING""", (ip_hash, now),
                    )
                window = db.execute(
                    "SELECT * FROM tactiq_access_trial_windows WHERE ip_hash=%s FOR UPDATE",
                    (ip_hash,),
                ).fetchone()
                now = self._now(db)
                if window is None or window["starts"] + DAY <= now:
                    used, starts = 0, now
                else:
                    used, starts = window["used"], window["starts"]
                if not prior:
                    if used >= 3:
                        raise SignInRequired(starts + DAY)
                    used += 1
                    db.execute(
                        "UPDATE tactiq_access_trial_windows SET starts=%s,used=%s WHERE ip_hash=%s",
                        (starts, used, ip_hash),
                    )
                remaining, resets = 3 - used, starts + DAY
            if prior:
                row = prior[0]
                reservation = AnalysisReservation(
                    row["id"], row["tenant_id"], intent.operation_id, intent.kind,
                    remaining, resets, True,
                )
            else:
                analysis_id = str(uuid.uuid4())
                db.execute(
                    "INSERT INTO tactiq_access_analyses VALUES(%s,%s,%s,%s,%s)",
                    (analysis_id, principal.tenant_id, intent.operation_id,
                     intent.kind, intent.payload_sha256),
                )
                reservation = AnalysisReservation(
                    analysis_id, principal.tenant_id, intent.operation_id,
                    intent.kind, remaining, resets,
                )
            if admit is not None:
                admit(db, reservation)
            return reservation

    def require_analysis(self, token: str, analysis_id: str) -> SessionPrincipal:
        _text(analysis_id, "analysis ID")
        digest = _digest(token)
        with self._transaction() as db:
            principal = self._principal(db, digest)
            row = db.execute(
                "SELECT tenant_id FROM tactiq_access_analyses WHERE id=%s", (analysis_id,),
            ).fetchone()
            if row is None or row["tenant_id"] not in self._tenants(db, principal):
                raise AccessDenied("Analysis is not owned by this session")
            return principal

    def sign_in(self, identity: IdentityKey, *, guest_digest: str | None = None) -> IssuedSession:
        """Only validated provider identity and the consumed login flow's proof."""
        if not isinstance(identity, IdentityKey):
            raise ValueError("A verified provider identity is required")
        with self._transaction() as db:
            self._budget_lock(db)
            guest = self._principal(db, guest_digest) if guest_digest is not None else None
            if guest is not None and not guest.guest:
                raise AccessDenied("Promotion requires the original guest session")
            account = db.execute(
                "SELECT * FROM tactiq_access_accounts WHERE issuer=%s AND subject=%s",
                (identity.issuer, identity.subject),
            ).fetchone()
            if account is None:
                tenant, subject = str(uuid.uuid4()), str(uuid.uuid4())
                db.execute(
                    "INSERT INTO tactiq_access_tenants(id,guest) VALUES(%s,false)", (tenant,),
                )
                db.execute(
                    "INSERT INTO tactiq_access_accounts VALUES(%s,%s,%s,%s)",
                    (identity.issuer, identity.subject, subject, tenant),
                )
            else:
                tenant, subject = account["tenant_id"], account["account_id"]
                db.execute(
                    "SELECT id FROM tactiq_access_tenants WHERE id=%s FOR UPDATE", (tenant,),
                )
            if guest is not None:
                db.execute(
                    "UPDATE tactiq_access_tenants SET promoted_to=%s WHERE id=%s",
                    (tenant, guest.tenant_id),
                )
                db.execute(
                    "UPDATE tactiq_access_sessions SET revoked=true WHERE tenant_id=%s",
                    (guest.tenant_id,),
                )
            return self._issue(db, tenant, subject, False, self._now(db) + DAY)

    @staticmethod
    def _usage(row: Mapping, replayed: bool = False) -> UsageReservation:
        return UsageReservation(
            row["id"], row["tenant_id"], row["meter"], row["units"],
            row["maximum_cost"], row["status"], row["actual_cost"], replayed,
        )

    def reserve_usage(self, token: str, intent: UsageIntent) -> UsageReservation:
        if not isinstance(intent, UsageIntent):
            raise ValueError("A validated usage intent is required")
        digest = _digest(token)
        policy = self._usage_policy
        with self._transaction() as db:
            self._budget_lock(db)
            if policy is None or not policy.period_start <= self._now(db) < policy.period_end:
                raise BudgetExceeded("No active approved application allocation")
            self._configure_policy(db)
            principal = self._principal(db, digest)
            prior = db.execute(
                """SELECT * FROM tactiq_access_usage WHERE
                   (tenant_id=%s OR tenant_id IN
                       (SELECT id FROM tactiq_access_tenants WHERE promoted_to=%s))
                   AND operation_id=%s LIMIT 2""",
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
            amounts = db.execute(
                """SELECT COALESCE(SUM(CASE status WHEN 'reserved' THEN maximum_cost
                           WHEN 'settled' THEN actual_cost ELSE 0 END),0) AS total,
                   COALESCE(SUM(CASE WHEN tenant_id=%s OR tenant_id IN
                       (SELECT id FROM tactiq_access_tenants WHERE promoted_to=%s)
                       THEN CASE status WHEN 'reserved' THEN maximum_cost
                           WHEN 'settled' THEN actual_cost ELSE 0 END ELSE 0 END),0) AS tenant_total
                   FROM tactiq_access_usage WHERE period_start < %s AND period_end > %s""",
                (principal.tenant_id, principal.tenant_id, policy.period_end, policy.period_start),
            ).fetchone()
            maximum = intent.maximum_cost_microusd
            if (
                amounts["total"] + maximum > policy.global_limit_microusd
                or amounts["tenant_total"] + maximum > policy.tenant_limit_microusd
            ):
                raise BudgetExceeded("Application usage allocation exhausted")
            reservation_id = str(uuid.uuid4())
            row = db.execute(
                """INSERT INTO tactiq_access_usage
                   (id,tenant_id,operation_id,meter,units,maximum_cost,period_start,period_end,status)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'reserved') RETURNING *""",
                (reservation_id, principal.tenant_id, intent.operation_id, intent.meter,
                 intent.units, maximum, policy.period_start, policy.period_end),
            ).fetchone()
            return self._usage(row)

    def _owned_usage(self, db: Any, token: str, reservation_id: str) -> Mapping:
        principal = self._principal(db, _digest(token))
        row = db.execute(
            "SELECT * FROM tactiq_access_usage WHERE id=%s FOR UPDATE", (reservation_id,),
        ).fetchone()
        if row is None or row["tenant_id"] not in self._tenants(db, principal):
            raise AccessDenied("Usage reservation is not owned by this session")
        return row

    def settle_usage(
        self, token: str, reservation_id: str, actual_units: int, actual_cost_microusd: int,
    ) -> UsageReservation:
        _integer(actual_units, "actual units")
        _integer(actual_cost_microusd, "actual cost")
        with self._transaction() as db:
            self._budget_lock(db)
            row = self._owned_usage(db, token, reservation_id)
            if actual_units > row["units"] or actual_cost_microusd > row["maximum_cost"]:
                raise ReservationConflict("Usage exceeded its worst-case reservation")
            if row["status"] == "settled":
                if (row["actual_units"], row["actual_cost"]) != (actual_units, actual_cost_microusd):
                    raise ReservationConflict("Usage settlement changed")
                return self._usage(row, True)
            if row["status"] != "reserved":
                raise ReservationConflict("Released usage cannot be settled")
            row = db.execute(
                """UPDATE tactiq_access_usage SET status='settled',actual_units=%s,actual_cost=%s
                   WHERE id=%s RETURNING *""", (actual_units, actual_cost_microusd, reservation_id),
            ).fetchone()
            return self._usage(row)

    def release_usage(self, token: str, reservation_id: str) -> UsageReservation:
        with self._transaction() as db:
            self._budget_lock(db)
            row = self._owned_usage(db, token, reservation_id)
            if row["status"] == "settled":
                raise ReservationConflict("Settled usage cannot be released")
            if row["status"] == "released":
                return self._usage(row, True)
            row = db.execute(
                "UPDATE tactiq_access_usage SET status='released' WHERE id=%s RETURNING *",
                (reservation_id,),
            ).fetchone()
            return self._usage(row)

    def expire_guests(self, limit: int = 100) -> tuple[ExpiredGuest, ...]:
        _integer(limit, "cleanup limit", 1)
        if limit > 1000:
            raise ValueError("Cleanup batch is too large")
        with self._transaction() as db:
            now = self._now(db)
            rows = db.execute(
                """SELECT id FROM tactiq_access_tenants WHERE guest AND NOT expired
                   AND promoted_to IS NULL AND expires<=%s ORDER BY expires,id
                   LIMIT %s FOR UPDATE SKIP LOCKED""", (now, limit),
            ).fetchall()
            for row in rows:
                tenant = row["id"]
                ids = [item["id"] for item in db.execute(
                    "SELECT id FROM tactiq_access_analyses WHERE tenant_id=%s ORDER BY id", (tenant,),
                )]
                db.execute("UPDATE tactiq_access_tenants SET expired=true WHERE id=%s", (tenant,))
                db.execute(
                    "UPDATE tactiq_access_sessions SET revoked=true WHERE tenant_id=%s", (tenant,),
                )
                db.execute(
                    "INSERT INTO tactiq_access_cleanup VALUES(%s,%s::jsonb)",
                    (tenant, json.dumps(ids)),
                )
            db.execute("DELETE FROM tactiq_access_logins WHERE expires<=%s", (now,))
            # Lock only idle, fully expired windows; active admissions are skipped.
            # Guest expiry alone must NEVER remove a still-active shared IP quota.
            db.execute(
                """WITH expired_windows AS (
                       SELECT ip_hash FROM tactiq_access_trial_windows WHERE starts<=%s
                       ORDER BY starts,ip_hash LIMIT 1000 FOR UPDATE SKIP LOCKED)
                   DELETE FROM tactiq_access_trial_windows AS trial_window USING expired_windows
                   WHERE trial_window.ip_hash=expired_windows.ip_hash""", (now - DAY,),
            )
            return tuple(
                ExpiredGuest(row["tenant_id"], tuple(row["analysis_ids"]))
                for row in db.execute(
                    "SELECT * FROM tactiq_access_cleanup ORDER BY tenant_id LIMIT %s", (limit,),
                )
            )

    def acknowledge_cleanup(self, tenant_id: str) -> None:
        _text(tenant_id, "tenant ID")
        with self._transaction() as db:
            db.execute(
                "SELECT id FROM tactiq_access_tenants WHERE id=%s FOR UPDATE", (tenant_id,),
            )
            pending = db.execute(
                "SELECT tenant_id FROM tactiq_access_cleanup WHERE tenant_id=%s FOR UPDATE",
                (tenant_id,),
            ).fetchone()
            if pending is None:
                return
            db.execute("DELETE FROM tactiq_access_analyses WHERE tenant_id=%s", (tenant_id,))
            db.execute(
                """DELETE FROM tactiq_access_logins WHERE guest_digest IN
                   (SELECT digest FROM tactiq_access_sessions WHERE tenant_id=%s)""", (tenant_id,),
            )
            db.execute("DELETE FROM tactiq_access_sessions WHERE tenant_id=%s", (tenant_id,))
            db.execute("DELETE FROM tactiq_access_cleanup WHERE tenant_id=%s", (tenant_id,))

    def save_login(
        self, binding: str, flow: Mapping, guest_token: str | None = None,
    ) -> None:
        binding_hash = _digest(binding)
        state = _text(flow.get("state"), "OIDC state", 512)
        saved = dict(flow)
        saved.pop("auth_uri", None)
        serialized = json.dumps(saved)
        if len(serialized.encode("utf-8")) > 16_384:
            raise ValueError("OIDC flow exceeds storage limit")
        guest_digest = _digest(guest_token) if guest_token is not None else None
        with self._transaction() as db:
            if guest_digest is not None and not self._principal(db, guest_digest).guest:
                raise AccessDenied("Expected a guest session")
            db.execute(
                "INSERT INTO tactiq_access_logins VALUES(%s,%s,%s::jsonb,%s,%s)",
                (binding_hash, hashlib.sha256(state.encode()).hexdigest(),
                 serialized, guest_digest, self._now(db) + 600),
            )

    def consume_login(self, binding: str, state: str) -> PendingLogin:
        binding_hash = _digest(binding)
        state = _text(state, "OIDC state", 512)
        with self._transaction() as db:
            row = db.execute(
                "SELECT * FROM tactiq_access_logins WHERE binding_hash=%s FOR UPDATE",
                (binding_hash,),
            ).fetchone()
            if row is None or row["expires"] <= self._now(db) or not hmac.compare_digest(
                row["state_hash"], hashlib.sha256(state.encode()).hexdigest(),
            ):
                raise AccessDenied("Invalid or expired sign-in flow")
            db.execute("DELETE FROM tactiq_access_logins WHERE binding_hash=%s", (binding_hash,))
            return PendingLogin(row["flow"], row["guest_digest"])
