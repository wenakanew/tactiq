"""Real PostgreSQL access checks using explicitly authorized disposable schemas.

Require TACTIQ_ALLOW_POSTGRES_TEST_WRITES=1 and TACTIQ_TEST_POSTGRES_DSN.
Only uniquely named test schemas are created/dropped; no Azure provisioning.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from tactiq.runtime.access import (
    DAY, AccessDenied, AnalysisIntent, BudgetExceeded, IdentityKey,
    ReservationConflict, SignInRequired, UsageIntent, UsagePolicy,
)
from tactiq.runtime.postgres_access import PostgresAccessStore


pytestmark = pytest.mark.skipif(
    os.environ.get("TACTIQ_ALLOW_POSTGRES_TEST_WRITES") != "1"
    or not os.environ.get("TACTIQ_TEST_POSTGRES_DSN"),
    reason="Real PostgreSQL access tests require an explicitly approved disposable database",
)

KEY = b"test-only-shared-reference-hmac-key-not-production"
IP = "203.0.113.19"
ISSUER = "https://login.microsoftonline.com/9188040d-6c67-4c5b-b112-36a304b66dad/v2.0"


def intent(operation="one", kind="scenario", payload="valid"):
    return AnalysisIntent(operation, kind, hashlib.sha256(payload.encode()).hexdigest())


def identity(subject="alice", issuer=ISSUER):
    return IdentityKey(issuer, subject)


def proof(session):
    return hashlib.sha256(session.token.encode()).hexdigest()


def issue_guests(store, count):
    with ThreadPoolExecutor(max_workers=8) as executor:
        return list(executor.map(lambda _: store.issue_guest(), range(count)))


@pytest.fixture
def database():
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql
    from psycopg.rows import dict_row

    dsn = os.environ["TACTIQ_TEST_POSTGRES_DSN"]
    schema = "tactiq_access_test_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True, connect_timeout=60) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))

    def connect():
        connection = psycopg.connect(dsn, row_factory=dict_row, connect_timeout=60)
        try:
            connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            connection.commit()
        except BaseException:
            connection.close()
            raise
        return connection

    now = [1_800_000_000.0]
    policy = UsagePolicy(now[0], now[0] + 30 * DAY, 10_000, 6_000)

    def replica(*, allocation=policy, db_clock=False):
        return PostgresAccessStore(
            connect, ip_hmac_key=KEY, usage_policy=allocation,
            clock=None if db_clock else lambda: now[0],
        )

    store = replica()
    try:
        store.migrate()
        store.migrate()
        yield store, connect, now, replica
    finally:
        with psycopg.connect(dsn, autocommit=True, connect_timeout=60) as admin:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_pg_access_migration_is_explicit_idempotent_and_separate_from_cloud(database):
    store, connect, _, _ = database
    with connect() as db:
        assert db.execute("SELECT version FROM tactiq_access_schema").fetchone()["version"] == 2
        assert db.execute("SELECT to_regclass('tactiq_cloud_schema') AS name").fetchone()["name"] is None
    guest = store.issue_guest()
    store.migrate()
    assert store.authenticate(guest.token) == guest.principal
    with connect() as db:
        db.execute("UPDATE tactiq_access_schema SET version=999")
    with pytest.raises(RuntimeError, match="Unsupported access schema"):
        store.migrate()


def test_pg_access_default_time_is_database_authoritative(database):
    _, connect, _, replica = database
    store = replica(allocation=None, db_clock=True)
    with connect() as db:
        before = db.execute("SELECT EXTRACT(EPOCH FROM clock_timestamp()) AS now").fetchone()["now"]
    guest = store.issue_guest()
    with connect() as db:
        after = db.execute("SELECT EXTRACT(EPOCH FROM clock_timestamp()) AS now").fetchone()["now"]
    assert float(before) + DAY <= guest.principal.expires_at <= float(after) + DAY
    assert store.authenticate(guest.token) == guest.principal


def test_pg_access_from_dsn_constructor_targets_same_authorized_schema(database):
    store, connect, now, _ = database
    from psycopg.conninfo import make_conninfo

    with connect() as db:
        schema = db.execute("SELECT current_schema() AS name").fetchone()["name"]
    scoped_dsn = make_conninfo(
        os.environ["TACTIQ_TEST_POSTGRES_DSN"], options=f"-csearch_path={schema}",
    )
    other = PostgresAccessStore.from_dsn(
        scoped_dsn, ip_hmac_key=KEY, clock=lambda: now[0], migrate=True,
    )
    guest = store.issue_guest()
    assert other.authenticate(guest.token) == guest.principal


def test_pg_guest_tokens_are_hashed_and_browsers_are_not_ip_tenants(database):
    store, connect, _, _ = database
    first, second = store.issue_guest(), store.issue_guest()
    assert first.principal.tenant_id != second.principal.tenant_id
    assert first.token != second.token and len(first.token) == 43
    assert first.token not in repr(first)
    with connect() as db:
        rows = db.execute("SELECT digest FROM tactiq_access_sessions").fetchall()
    assert {row["digest"] for row in rows} == {proof(first), proof(second)}
    result = store.reserve_analysis(first.token, IP, intent())
    assert store.require_analysis(first.token, result.analysis_id) == first.principal
    with pytest.raises(AccessDenied):
        store.require_analysis(second.token, result.analysis_id)
    with pytest.raises(AccessDenied):
        store.authenticate("forged-session")
    store.revoke_session(first.token)
    store.revoke_session(first.token)
    with pytest.raises(AccessDenied):
        store.authenticate(first.token)
    assert store.authenticate(second.token) == second.principal


def test_pg_three_kinds_retries_reads_and_fourth_require_sign_in(database):
    store, connect, now, replica = database
    guest = store.issue_guest()
    assert store.trial_allowance(guest.token, IP).remaining == 3
    assert store.trial_allowance(guest.token, IP).resets_at is None
    now[0] += 120
    results = [
        store.reserve_analysis(guest.token, IP, intent(str(index), kind))
        for index, kind in enumerate(("scenario", "upload", "link"))
    ]
    assert [result.remaining for result in results] == [2, 1, 0]
    assert results[0].resets_at == now[0] + DAY
    for _ in range(3):
        assert replica().require_analysis(guest.token, results[0].analysis_id) == guest.principal
        retried = replica().reserve_analysis(guest.token, IP, intent("0"))
        assert retried.analysis_id == results[0].analysis_id and retried.replayed
    with pytest.raises(SignInRequired) as error:
        replica().reserve_analysis(store.issue_guest().token, IP, intent("fourth"))
    assert error.value.resets_at == now[0] + DAY
    assert store.trial_allowance(guest.token, IP).remaining == 0
    with connect() as db:
        assert db.execute("SELECT COUNT(*) AS count FROM tactiq_access_analyses").fetchone()["count"] == 3
        row = db.execute("SELECT * FROM tactiq_access_trial_windows").fetchone()
        assert row["used"] == 3 and IP not in str(row)


def test_pg_100_concurrent_distinct_sessions_admit_exactly_three(database):
    store, _, _, replica = database
    guests = issue_guests(store, 100)

    def admit(index):
        try:
            return replica().reserve_analysis(guests[index].token, IP, intent(str(index)))
        except SignInRequired:
            return None

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(admit, range(100)))
    accepted = [result for result in results if result is not None]
    assert len(accepted) == 3
    assert sorted(result.remaining for result in accepted) == [0, 1, 2]


def test_pg_concurrent_idempotent_retry_and_conflicts_are_atomic(database):
    store, connect, _, replica = database
    guest = store.issue_guest()
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(
            lambda _: replica().reserve_analysis(guest.token, IP, intent()), range(24),
        ))
    assert len({result.analysis_id for result in results}) == 1
    assert len([result for result in results if not result.replayed]) == 1
    with pytest.raises(ReservationConflict):
        store.reserve_analysis(guest.token, IP, intent(payload="different"))
    with pytest.raises(ReservationConflict):
        store.reserve_analysis(guest.token, IP, intent(kind="link"))
    with connect() as db:
        assert db.execute("SELECT used FROM tactiq_access_trial_windows").fetchone()["used"] == 1


def test_pg_malformed_request_and_untrusted_intent_cannot_use_quota(database):
    store, connect, _, _ = database
    guest = store.issue_guest()
    with pytest.raises(ValueError):
        AnalysisIntent("operation", "listen", "a" * 64)
    with pytest.raises(ValueError):
        AnalysisIntent("operation", "upload", "malformed")
    with pytest.raises(ValueError):
        store.reserve_analysis(guest.token, IP, {"kind": "upload"})
    with pytest.raises(ValueError):
        store.reserve_analysis(guest.token, "hostname.example", intent())
    with connect() as db:
        assert db.execute("SELECT COUNT(*) AS count FROM tactiq_access_trial_windows").fetchone()["count"] == 0
        assert db.execute("SELECT COUNT(*) AS count FROM tactiq_access_analyses").fetchone()["count"] == 0


def test_pg_trial_window_normalizes_ip_and_resets_independently_of_session(database):
    store, _, now, _ = database
    guest = store.issue_guest()
    first = store.reserve_analysis(guest.token, IP, intent())
    assert store.reserve_analysis(guest.token, "::ffff:" + IP, intent("two")).remaining == 1
    store.reserve_analysis(guest.token, IP, intent("three"))
    store.revoke_session(guest.token)
    now[0] += DAY - 1
    another = store.issue_guest()
    with pytest.raises(SignInRequired):
        store.reserve_analysis(another.token, IP, intent())
    now[0] = first.resets_at
    assert store.trial_allowance(another.token, IP).remaining == 3
    assert store.reserve_analysis(another.token, IP, intent()).remaining == 2


def test_pg_guest_cleanup_never_resets_a_still_active_shared_ip_window(database):
    store, connect, now, _ = database
    guest = store.issue_guest()
    now[0] += 120
    for operation in ("one", "two", "three"):
        store.reserve_analysis(guest.token, IP, intent(operation))
    now[0] = guest.principal.expires_at
    another = store.issue_guest()
    store.expire_guests()
    with pytest.raises(SignInRequired):
        store.reserve_analysis(another.token, IP, intent())
    now[0] += 120
    store.expire_guests()
    with connect() as db:
        assert db.execute("SELECT COUNT(*) AS count FROM tactiq_access_trial_windows").fetchone()["count"] == 0
    assert store.reserve_analysis(another.token, IP, intent()).remaining == 2


def test_pg_job_admission_callback_is_one_transaction_with_quota_and_ownership(database):
    store, connect, _, _ = database
    guest = store.issue_guest()
    with connect() as db:
        db.execute(
            """CREATE TABLE access_test_jobs(
               id text PRIMARY KEY REFERENCES tactiq_access_analyses(id), kind text NOT NULL)"""
        )

    def admit(db, reservation):
        db.execute(
            "INSERT INTO access_test_jobs VALUES(%s,%s) ON CONFLICT(id) DO NOTHING",
            (reservation.analysis_id, reservation.kind),
        )

    def fail_after_insert(db, reservation):
        admit(db, reservation)
        raise RuntimeError("Injected admission failure")

    with pytest.raises(RuntimeError, match="Injected"):
        store.reserve_analysis_and_admit(guest.token, IP, intent(), fail_after_insert)
    with connect() as db:
        for table in ("tactiq_access_analyses", "tactiq_access_trial_windows", "access_test_jobs"):
            assert db.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"] == 0
    first = store.reserve_analysis_and_admit(guest.token, IP, intent(), admit)
    retried = store.reserve_analysis_and_admit(guest.token, IP, intent(), admit)
    assert retried.analysis_id == first.analysis_id and retried.replayed
    with connect() as db:
        assert db.execute("SELECT COUNT(*) AS count FROM access_test_jobs").fetchone()["count"] == 1


def test_pg_concurrent_account_sign_in_is_stable_not_directory_scoped(database):
    store, _, _, replica = database
    with ThreadPoolExecutor(max_workers=8) as executor:
        sessions = list(executor.map(lambda _: replica().sign_in(identity()), range(12)))
    assert len({session.principal.tenant_id for session in sessions}) == 1
    bob = store.sign_in(identity("bob"))
    different_issuer = store.sign_in(identity("alice", "https://issuer.example/other"))
    assert len({
        sessions[0].principal.tenant_id, bob.principal.tenant_id, different_issuer.principal.tenant_id,
    }) == 3
    assert all(session.principal.role == "analyst" for session in sessions)
    with pytest.raises(TypeError):
        store.sign_in(identity(), role="admin")


def test_pg_promotion_proves_same_session_preserves_alias_and_rotates_guest(database):
    store, _, now, replica = database
    guest, other = store.issue_guest(), store.issue_guest()
    result = store.reserve_analysis(guest.token, IP, intent())
    other_result = store.reserve_analysis(other.token, IP, intent())
    member = replica().sign_in(identity(), guest_digest=proof(guest))
    assert not member.principal.guest and member.token != guest.token
    assert guest.principal.tenant_id in store.authorized_tenants(member.token)
    assert store.require_analysis(member.token, result.analysis_id) == member.principal
    with pytest.raises(AccessDenied):
        store.require_analysis(member.token, other_result.analysis_id)
    with pytest.raises(AccessDenied):
        store.authenticate(guest.token)
    retried = store.reserve_analysis(member.token, IP, intent())
    assert retried.replayed and retried.analysis_id == result.analysis_id
    for index in range(5):
        assert store.reserve_analysis(member.token, IP, intent(f"member-{index}")).remaining is None
    now[0] += DAY
    assert [item.tenant_id for item in store.expire_guests()] == [other.principal.tenant_id]
    renewed = store.sign_in(identity())
    assert store.require_analysis(renewed.token, result.analysis_id) == renewed.principal


def test_pg_concurrent_promotion_can_transfer_guest_only_once(database):
    store, _, _, replica = database
    guest = store.issue_guest()

    def promote(subject):
        try:
            return replica().sign_in(identity(subject), guest_digest=proof(guest))
        except AccessDenied:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(promote, ("alice", "bob")))
    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    assert guest.principal.tenant_id in store.authorized_tenants(winners[0].token)


def test_pg_promoted_aliases_keep_ownership_and_reject_ambiguous_retry_keys(database):
    store, _, _, _ = database
    guests = [store.issue_guest(), store.issue_guest()]
    analyses = [store.reserve_analysis(guest.token, IP, intent()) for guest in guests]
    for guest in guests:
        store.reserve_usage(guest.token, UsageIntent("same-work", "processing", 1, 2000))
    sessions = [store.sign_in(identity(), guest_digest=proof(guest)) for guest in guests]
    member = sessions[-1]
    assert sessions[0].principal.tenant_id == member.principal.tenant_id
    assert len(store.authorized_tenants(member.token)) == 3
    for analysis in analyses:
        store.require_analysis(member.token, analysis.analysis_id)
    with pytest.raises(ReservationConflict, match="Ambiguous operation"):
        store.reserve_analysis(member.token, IP, intent())
    with pytest.raises(ReservationConflict, match="Ambiguous usage"):
        store.reserve_usage(member.token, UsageIntent("same-work", "processing", 1, 2000))
    with pytest.raises(BudgetExceeded):
        store.reserve_usage(member.token, UsageIntent("over-cap", "processing", 1, 2001))


def test_pg_expiry_and_concurrent_promotion_never_both_claim_cleanup(database):
    store, _, now, replica = database
    guest = store.issue_guest()
    now[0] = guest.principal.expires_at - 1
    result = store.reserve_analysis(guest.token, IP, intent())
    now[0] += 1

    def late_promote():
        try:
            return replica().sign_in(identity(), guest_digest=proof(guest))
        except AccessDenied:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        promote = executor.submit(late_promote)
        expiry = executor.submit(replica().expire_guests)
        assert promote.result() is None
        pending = expiry.result()
    assert pending[0].analysis_ids == (result.analysis_id,)
    with pytest.raises(AccessDenied):
        store.require_analysis(guest.token, result.analysis_id)
    assert replica().expire_guests() == pending
    replica().acknowledge_cleanup(guest.principal.tenant_id)
    replica().acknowledge_cleanup(guest.principal.tenant_id)
    assert store.expire_guests() == ()


def test_pg_expiry_skip_locked_never_deletes_a_promoted_guest(database, monkeypatch):
    store, _, now, replica = database
    guest = store.issue_guest()
    now[0] = guest.principal.expires_at - 1
    entered, release = threading.Event(), threading.Event()
    original_issue = store._issue

    def pause_before_issuing(*args):
        entered.set()
        assert release.wait(10)
        return original_issue(*args)

    monkeypatch.setattr(store, "_issue", pause_before_issuing)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(store.sign_in, identity(), guest_digest=proof(guest))
        assert entered.wait(10)
        try:
            now[0] += 1
            assert replica().expire_guests() == ()
        finally:
            release.set()
        member = pending.result()
    assert store.expire_guests() == ()
    assert guest.principal.tenant_id in store.authorized_tenants(member.token)


def test_pg_usage_idempotency_bounded_settlement_and_release(database):
    store, _, _, replica = database
    guest = store.issue_guest()
    use = UsageIntent("speech", "speech_characters", 1000, 4000)
    first = store.reserve_usage(guest.token, use)
    retry = replica().reserve_usage(guest.token, use)
    assert retry.reservation_id == first.reservation_id and retry.replayed
    with pytest.raises(ReservationConflict):
        store.reserve_usage(guest.token, UsageIntent("speech", "speech_characters", 1001, 4000))
    for units, cost in ((1001, 4000), (1000, 4001)):
        with pytest.raises(ReservationConflict):
            store.settle_usage(guest.token, first.reservation_id, units, cost)
    settled = store.settle_usage(guest.token, first.reservation_id, 500, 2000)
    assert settled.status == "settled" and settled.actual_cost_microusd == 2000
    assert replica().settle_usage(guest.token, first.reservation_id, 500, 2000).replayed
    with pytest.raises(ReservationConflict):
        store.settle_usage(guest.token, first.reservation_id, 500, 1999)
    with pytest.raises(ReservationConflict):
        store.release_usage(guest.token, first.reservation_id)
    unused = store.reserve_usage(guest.token, UsageIntent("unused", "storage", 1, 4000))
    assert store.release_usage(guest.token, unused.reservation_id).status == "released"
    assert replica().release_usage(guest.token, unused.reservation_id).replayed
    with pytest.raises(ReservationConflict):
        store.settle_usage(guest.token, unused.reservation_id, 0, 0)
    assert store.reserve_usage(guest.token, UsageIntent("replacement", "processing", 1, 4000))


def test_pg_concurrent_usage_retries_reserve_only_once(database):
    store, _, _, replica = database
    guest = store.issue_guest()
    use = UsageIntent("metered", "processing", 1, 6000)
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: replica().reserve_usage(guest.token, use), range(24)))
    assert len({result.reservation_id for result in results}) == 1
    assert len([result for result in results if not result.replayed]) == 1
    with pytest.raises(BudgetExceeded):
        store.reserve_usage(guest.token, UsageIntent("extra", "speech_characters", 1, 1))


def test_pg_concurrent_global_budget_cap_applies_to_all_tenants_and_sign_in(database):
    store, _, _, replica = database
    guests = issue_guests(store, 20)

    def reserve(index):
        try:
            return replica().reserve_usage(
                guests[index].token, UsageIntent(str(index), "model_tokens", 100, 2000),
            )
        except BudgetExceeded:
            return None

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(reserve, range(20)))
    assert len([result for result in results if result is not None]) == 5
    member = store.sign_in(identity())
    with pytest.raises(BudgetExceeded):
        store.reserve_usage(member.token, UsageIntent("signed-in", "speech_characters", 1, 1))


def test_pg_usage_ownership_and_promoted_guest_tenant_limit(database):
    store, _, _, _ = database
    guest, other = store.issue_guest(), store.issue_guest()
    use = UsageIntent("processing", "processing", 1, 6000)
    first = store.reserve_usage(guest.token, use)
    with pytest.raises(AccessDenied):
        store.settle_usage(other.token, first.reservation_id, 1, 1)
    with pytest.raises(AccessDenied):
        store.release_usage(other.token, first.reservation_id)
    member = store.sign_in(identity(), guest_digest=proof(guest))
    assert store.reserve_usage(member.token, use).reservation_id == first.reservation_id
    with pytest.raises(BudgetExceeded):
        store.reserve_usage(member.token, UsageIntent("extra", "storage", 1, 1))
    assert store.settle_usage(member.token, first.reservation_id, 1, 500).status == "settled"


def test_pg_settlement_and_release_race_has_one_terminal_transition(database):
    store, _, _, replica = database
    guest = store.issue_guest()
    use = store.reserve_usage(guest.token, UsageIntent("metered", "processing", 1, 6000))

    def settle():
        try:
            return replica().settle_usage(guest.token, use.reservation_id, 1, 5000).status
        except ReservationConflict:
            return "conflict"

    def release():
        try:
            return replica().release_usage(guest.token, use.reservation_id).status
        except ReservationConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        one, two = executor.submit(settle), executor.submit(release)
        statuses = {one.result(), two.result()}
    assert statuses in ({"settled", "conflict"}, {"released", "conflict"})


def test_pg_allocations_fail_closed_and_conflicting_replicas_cannot_expand_them(database):
    store, _, now, replica = database
    guest = store.issue_guest()
    use = UsageIntent("processing", "processing", 1, 1)
    with pytest.raises(BudgetExceeded):
        replica(allocation=None).reserve_usage(guest.token, use)
    changed = UsagePolicy(now[0], now[0] + 30 * DAY, 20_000, 6000)
    with pytest.raises(ValueError, match="Conflicting"):
        replica(allocation=changed).migrate()
    with pytest.raises(ValueError, match="Conflicting"):
        replica(allocation=changed).reserve_usage(guest.token, use)
    overlapping = UsagePolicy(now[0] + 1, now[0] + 30 * DAY, 10_000, 6000)
    with pytest.raises(ValueError, match="Conflicting"):
        replica(allocation=overlapping).migrate()
    now[0] += 30 * DAY
    with pytest.raises(BudgetExceeded):
        store.reserve_usage(store.issue_guest().token, use)


def test_pg_login_binding_is_single_use_and_minimal_across_replicas(database):
    store, connect, _, replica = database
    guest = store.issue_guest()
    binding = secrets.token_urlsafe(32)
    flow = {"state": "csrf", "nonce": "nonce", "code_verifier": "pkce", "auth_uri": "https://login.example"}
    store.save_login(binding, flow, guest.token)
    with connect() as db:
        row = db.execute("SELECT * FROM tactiq_access_logins").fetchone()
    assert row["binding_hash"] == hashlib.sha256(binding.encode()).hexdigest()
    assert binding not in str(row) and guest.token not in str(row)
    assert "auth_uri" not in row["flow"]
    with pytest.raises(AccessDenied):
        replica().consume_login(binding, "forged")

    def consume(_):
        try:
            return replica().consume_login(binding, "csrf")
        except AccessDenied:
            return None

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(consume, range(12)))
    winners = [result for result in results if result is not None]
    assert len(winners) == 1 and winners[0].guest_digest == proof(guest)
    assert "pkce" not in repr(winners[0])


def test_pg_login_expiry_revocation_and_cleanup_are_safe(database):
    store, connect, now, replica = database
    guest = store.issue_guest()
    binding = secrets.token_urlsafe(32)
    flow = {"state": "csrf", "nonce": "nonce", "code_verifier": "pkce"}
    store.save_login(binding, flow, guest.token)
    now[0] += 600
    with pytest.raises(AccessDenied):
        replica().consume_login(binding, "csrf")
    store.expire_guests()
    with connect() as db:
        assert db.execute("SELECT COUNT(*) AS count FROM tactiq_access_logins").fetchone()["count"] == 0
    store.revoke_session(guest.token)
    with pytest.raises(AccessDenied):
        store.save_login(secrets.token_urlsafe(32), flow, guest.token)
    now[0] += DAY
    pending = store.expire_guests()
    assert len(pending) == 1
    store.acknowledge_cleanup(guest.principal.tenant_id)
    with connect() as db:
        assert db.execute("SELECT COUNT(*) AS count FROM tactiq_access_sessions").fetchone()["count"] == 0


def test_pg_database_constraints_reject_over_quota_and_invalid_cost(database):
    store, connect, _, _ = database
    psycopg = pytest.importorskip("psycopg")
    guest = store.issue_guest()
    store.reserve_analysis(guest.token, IP, intent())
    with pytest.raises(psycopg.errors.CheckViolation):
        with connect() as db:
            db.execute("UPDATE tactiq_access_trial_windows SET used=4")
    usage = store.reserve_usage(guest.token, UsageIntent("work", "processing", 1, 1000))
    with pytest.raises(psycopg.errors.CheckViolation):
        with connect() as db:
            db.execute(
                """UPDATE tactiq_access_usage SET status='settled',actual_units=1,actual_cost=1001
                   WHERE id=%s""", (usage.reservation_id,),
            )
