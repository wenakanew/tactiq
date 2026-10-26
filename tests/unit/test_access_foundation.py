from __future__ import annotations

import hashlib
import secrets
import shutil
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import pytest

from tactiq.runtime.access import (
    DAY,
    AccessDenied,
    AnalysisIntent,
    BudgetExceeded,
    ClientIPPolicy,
    IPPseudonymizer,
    IdentityKey,
    ReservationConflict,
    SignInRequired,
    SQLiteAccessStore,
    UsageIntent,
    UsagePolicy,
    normalize_ip,
)


KEY = b"reference-test-only-key-not-for-production"
IP = "203.0.113.7"


@pytest.fixture
def reference():
    # Deliberately use a project-relative test artifact, not system temp directories.
    directory = Path("build") / "access-reference-tests" / str(uuid.uuid4())
    directory.mkdir(parents=True)
    now = [1_800_000_000.0]
    policy = UsagePolicy(now[0], now[0] + 30 * DAY, 10_000, 6_000)
    store = SQLiteAccessStore(
        directory / "access.sqlite3", ip_hmac_key=KEY,
        usage_policy=policy, clock=lambda: now[0],
    )
    try:
        yield store, now, directory, policy
    finally:
        shutil.rmtree(directory)


def intent(operation="one", kind="scenario", payload="valid"):
    return AnalysisIntent(operation, kind, hashlib.sha256(payload.encode()).hexdigest())


def account(subject="alice", issuer="https://issuer.example/consumer/v2.0"):
    return IdentityKey(issuer, subject)


def test_sessions_are_opaque_hashed_isolated_and_secure(reference):
    store, now, _, _ = reference
    first, second = store.issue_guest(), store.issue_guest()
    assert len(first.token) == 43 and first.token != second.token
    assert first.principal.tenant_id != second.principal.tenant_id
    assert first.principal.expires_at == now[0] + DAY
    assert first.principal.role == "analyst"
    assert first.token not in repr(first)
    assert first.cookie_options() == {
        "key": "__Host-tactiq_access", "secure": True, "httponly": True,
        "samesite": "lax", "path": "/", "max_age": DAY,
    }
    assert store.authenticate(first.token) == first.principal
    assert first.token.encode() not in store.path.read_bytes()
    store.revoke_session(first.token)
    with pytest.raises(AccessDenied):
        store.authenticate(first.token)
    with pytest.raises(AccessDenied):
        store.authenticate("caller-created-session")
    assert store.authenticate(second.token) == second.principal


def test_trial_counts_all_three_new_kinds_but_not_reads_or_retries(reference):
    store, now, _, _ = reference
    guest = store.issue_guest()
    reservations = [
        store.reserve_analysis(guest.token, IP, intent(str(i), kind))
        for i, kind in enumerate(("scenario", "upload", "link"))
    ]
    assert [reservation.remaining for reservation in reservations] == [2, 1, 0]
    assert reservations[0].resets_at == now[0] + DAY
    for _ in range(5):
        assert store.require_analysis(guest.token, reservations[0].analysis_id) == guest.principal
        retried = store.reserve_analysis(guest.token, IP, intent("0"))
        assert retried.replayed and retried.analysis_id == reservations[0].analysis_id
    with pytest.raises(SignInRequired) as error:
        store.reserve_analysis(guest.token, IP, intent("four"))
    assert error.value.resets_at == now[0] + DAY
    with closing(sqlite3.connect(store.path)) as db:
        assert db.execute("SELECT count(*) FROM access_analyses").fetchone()[0] == 3


def test_allowance_display_does_not_start_or_consume_window(reference):
    store, now, *_ = reference
    guest = store.issue_guest()
    for _ in range(3):
        allowance = store.trial_allowance(guest.token, IP)
        assert allowance.remaining == 3 and allowance.resets_at is None
    now[0] += 500
    admitted = store.reserve_analysis(guest.token, IP, intent())
    assert store.trial_allowance(guest.token, IP).remaining == 2
    assert store.trial_allowance(guest.token, IP).resets_at == admitted.resets_at == now[0] + DAY
    member = store.sign_in(account())
    assert store.trial_allowance(member.token, IP).remaining is None


def test_new_browser_same_ip_does_not_reset_allowance_or_share_ownership(reference):
    store, *_ = reference
    first, second = store.issue_guest(), store.issue_guest()
    first_result = store.reserve_analysis(first.token, IP, intent("same-key"))
    other_result = store.reserve_analysis(second.token, IP, intent("same-key"))
    assert other_result.analysis_id != first_result.analysis_id
    assert other_result.remaining == 1
    with pytest.raises(AccessDenied):
        store.require_analysis(second.token, first_result.analysis_id)
    store.reserve_analysis(second.token, IP, intent("third"))
    with pytest.raises(SignInRequired):
        store.reserve_analysis(store.issue_guest().token, IP, intent("fourth"))


@pytest.mark.parametrize("kind", ["replay", "listen", "reopen", "unknown", None])
def test_invalid_kind_cannot_be_reserved_and_consumes_nothing(reference, kind):
    store, *_ = reference
    with pytest.raises(ValueError):
        intent(kind=kind)
    guest = store.issue_guest()
    assert store.reserve_analysis(guest.token, IP, intent()).remaining == 2


@pytest.mark.parametrize("fingerprint", ["", "bad", "a" * 63, "A" * 64, None])
def test_malformed_intent_does_not_consume_allowance(reference, fingerprint):
    store, *_ = reference
    with pytest.raises(ValueError):
        AnalysisIntent("operation", "upload", fingerprint)
    guest = store.issue_guest()
    with pytest.raises(ValueError):
        store.reserve_analysis(guest.token, IP, {"kind": "scenario"})
    assert store.reserve_analysis(guest.token, IP, intent()).remaining == 2


def test_idempotency_mismatch_does_not_consume_more(reference):
    store, *_ = reference
    guest = store.issue_guest()
    store.reserve_analysis(guest.token, IP, intent())
    with pytest.raises(ReservationConflict):
        store.reserve_analysis(guest.token, IP, intent(payload="different"))
    with pytest.raises(ReservationConflict):
        store.reserve_analysis(guest.token, IP, intent(kind="link"))
    assert store.reserve_analysis(guest.token, IP, intent("second")).remaining == 1


def test_window_begins_at_first_admission_and_resets_after_24h(reference):
    store, now, *_ = reference
    guest = store.issue_guest()
    now[0] += 120
    first = store.reserve_analysis(guest.token, IP, intent())
    assert first.resets_at == now[0] + DAY
    for op in ("two", "three"):
        store.reserve_analysis(guest.token, IP, intent(op))
    now[0] += DAY - 1
    with pytest.raises(SignInRequired):
        store.reserve_analysis(store.issue_guest().token, IP, intent("blocked"))
    now[0] += 1
    new_guest = store.issue_guest()
    assert store.reserve_analysis(new_guest.token, IP, intent()).remaining == 2


def test_concurrent_100_requests_from_separate_connections_admit_only_three(reference):
    store, _, _, policy = reference
    sessions = [store.issue_guest() for _ in range(100)]

    def admit(index):
        replica = SQLiteAccessStore(store.path, ip_hmac_key=KEY, usage_policy=policy)
        # Same trusted test clock as original session issuer, not wall clock.
        replica._clock = store._clock
        try:
            return replica.reserve_analysis(sessions[index].token, IP, intent(str(index)))
        except SignInRequired:
            return None

    with ThreadPoolExecutor(max_workers=16) as executor:
        results = list(executor.map(admit, range(100)))
    assert len([result for result in results if result is not None]) == 3
    with closing(sqlite3.connect(store.path)) as db:
        assert db.execute("SELECT used FROM access_trial_windows").fetchone()[0] == 3


def test_concurrent_same_operation_is_one_reservation(reference):
    store, *_ = reference
    guest = store.issue_guest()
    with ThreadPoolExecutor(max_workers=12) as executor:
        results = list(executor.map(
            lambda _: store.reserve_analysis(guest.token, IP, intent()), range(24),
        ))
    assert len({result.analysis_id for result in results}) == 1
    assert len([result for result in results if not result.replayed]) == 1
    assert all(result.remaining == 2 for result in results)


def test_failed_metadata_insert_rolls_back_quota_reservation(reference):
    store, *_ = reference
    guest = store.issue_guest()
    with closing(sqlite3.connect(store.path)) as db:
        db.execute(
            """CREATE TRIGGER reject_analysis BEFORE INSERT ON access_analyses
               BEGIN SELECT RAISE(ABORT, 'injected write failure'); END"""
        )
        db.commit()
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        store.reserve_analysis(guest.token, IP, intent())
    assert store.trial_allowance(guest.token, IP).remaining == 3
    with closing(sqlite3.connect(store.path)) as db:
        db.execute("DROP TRIGGER reject_analysis")
        db.commit()
    assert store.reserve_analysis(guest.token, IP, intent()).remaining == 2


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("::ffff:203.0.113.7", IP),
        ("2001:0db8:0000:0:0:0:0:1", "2001:db8::1"),
        (IP, IP),
    ],
)
def test_normalized_hmac_ip_is_private_and_stable(address, expected):
    assert normalize_ip(address) == expected
    hashed = IPPseudonymizer(KEY).hash(address)
    assert hashed == IPPseudonymizer(KEY).hash(expected)
    assert expected not in hashed
    assert hashed != IPPseudonymizer(b"another-test-only-private-key-32-bytes").hash(expected)


@pytest.mark.parametrize("address", ["host.example", "203.0.113.7:80", "[::1]", "fe80::1%eth0", ""])
def test_reject_non_literal_or_ambiguous_addresses(address):
    with pytest.raises(ValueError):
        normalize_ip(address)


def test_forwarding_is_ignored_unless_socket_peer_is_explicitly_trusted():
    assert ClientIPPolicy().resolve(IP, "1.2.3.4") == IP
    assert ClientIPPolicy().resolve(IP, "malformed") == IP
    policy = ClientIPPolicy(("10.0.0.0/8", "2001:db8:1::/48"))
    assert policy.resolve(IP, "1.2.3.4") == IP
    assert policy.resolve("10.0.0.8", f"1.2.3.4,{IP},10.0.0.9") == IP
    assert policy.resolve("10.0.0.8", IP) == IP
    assert policy.resolve("2001:db8:1::8", f"{IP},2001:db8:1::9") == IP
    assert policy.resolve("10.0.0.8") == "10.0.0.8"
    for chain in ("", "unknown", f"{IP},", "1.2.3.4:80", ",".join([IP] * 33)):
        with pytest.raises(ValueError):
            policy.resolve("10.0.0.8", chain)
    with pytest.raises(ValueError):
        ClientIPPolicy(("0.0.0.0/0",))
    with pytest.raises(ValueError):
        ClientIPPolicy(("::/0",))


def test_normalized_ip_shared_across_ipv4_and_mapped_ipv6_and_not_saved_raw(reference):
    store, *_ = reference
    guest = store.issue_guest()
    store.reserve_analysis(guest.token, IP, intent())
    assert store.reserve_analysis(guest.token, "::ffff:" + IP, intent("two")).remaining == 1
    assert IP.encode() not in store.path.read_bytes()


def test_same_directory_accounts_are_distinct_and_no_caller_role_assignment(reference):
    store, *_ = reference
    alice = store.sign_in(account("alice"))
    bob = store.sign_in(account("bob"))
    issuer_other = store.sign_in(account("alice", "https://issuer.example/other/v2.0"))
    assert len({
        alice.principal.tenant_id, bob.principal.tenant_id, issuer_other.principal.tenant_id,
    }) == 3
    assert store.sign_in(account("alice")).principal == alice.principal
    with pytest.raises(TypeError):
        store.sign_in(account("alice"), role="admin")
    with pytest.raises(ValueError):
        store.sign_in({"tid": "same-directory"})


def test_guest_promotion_requires_same_proof_rotates_session_and_survives_expiry(reference):
    store, now, *_ = reference
    guest, other = store.issue_guest(), store.issue_guest()
    analysis = store.reserve_analysis(guest.token, IP, intent())
    other_analysis = store.reserve_analysis(other.token, IP, intent())
    member = store.sign_in(account(), guest_digest=hashlib.sha256(guest.token.encode()).hexdigest())
    assert member.token != guest.token
    assert store.require_analysis(member.token, analysis.analysis_id) == member.principal
    assert guest.principal.tenant_id in store.authorized_tenants(member.token)
    with pytest.raises(AccessDenied):
        store.authenticate(guest.token)
    with pytest.raises(AccessDenied):
        store.require_analysis(member.token, other_analysis.analysis_id)
    # Retry remains the same admitted operation after promotion.
    retried = store.reserve_analysis(member.token, IP, intent())
    assert retried.analysis_id == analysis.analysis_id and retried.replayed
    now[0] += DAY
    expired = store.expire_guests()
    assert [entry.tenant_id for entry in expired] == [other.principal.tenant_id]
    renewed = store.sign_in(account())
    assert store.require_analysis(renewed.token, analysis.analysis_id) == renewed.principal


def test_concurrent_promotion_can_transfer_guest_only_once(reference):
    store, *_ = reference
    guest = store.issue_guest()
    proof = hashlib.sha256(guest.token.encode()).hexdigest()

    def promote(subject):
        try:
            return store.sign_in(account(subject), guest_digest=proof)
        except AccessDenied:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(promote, ("alice", "bob")))
    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    assert guest.principal.tenant_id in store.authorized_tenants(winners[0].token)


def test_expiry_denies_access_and_promotion_and_has_durable_idempotent_cleanup(reference):
    store, now, *_ = reference
    guest = store.issue_guest()
    result = store.reserve_analysis(guest.token, IP, intent())
    now[0] += DAY
    with pytest.raises(AccessDenied):
        store.require_analysis(guest.token, result.analysis_id)
    with pytest.raises(AccessDenied):
        store.sign_in(account(), guest_digest=hashlib.sha256(guest.token.encode()).hexdigest())
    first = store.expire_guests()
    assert len(first) == 1
    assert first[0].analysis_ids == (result.analysis_id,)
    assert store.expire_guests() == first
    reopened = SQLiteAccessStore(store.path, ip_hmac_key=KEY, clock=lambda: now[0])
    assert reopened.expire_guests() == first
    store.acknowledge_cleanup(first[0].tenant_id)
    store.acknowledge_cleanup(first[0].tenant_id)
    assert store.expire_guests() == ()


def test_member_analyses_bypass_trial_but_not_shared_spending(reference):
    store, *_ = reference
    member = store.sign_in(account())
    for i in range(5):
        assert store.reserve_analysis(member.token, IP, intent(str(i))).remaining is None
    store.reserve_usage(member.token, UsageIntent("speech", "speech_characters", 1000, 6000))
    with pytest.raises(BudgetExceeded):
        store.reserve_usage(member.token, UsageIntent("extra", "model_tokens", 100, 1))


def test_usage_idempotency_settlement_and_release_are_bounded(reference):
    store, *_ = reference
    guest = store.issue_guest()
    usage_intent = UsageIntent("speech", "speech_characters", 1000, 4000)
    reservation = store.reserve_usage(guest.token, usage_intent)
    assert store.reserve_usage(guest.token, usage_intent).replayed
    with pytest.raises(ReservationConflict):
        store.reserve_usage(guest.token, UsageIntent("speech", "speech_characters", 1001, 4000))
    with pytest.raises(ReservationConflict):
        store.settle_usage(guest.token, reservation.reservation_id, 1001, 1000)
    with pytest.raises(ReservationConflict):
        store.settle_usage(guest.token, reservation.reservation_id, 100, 4001)
    settled = store.settle_usage(guest.token, reservation.reservation_id, 500, 2000)
    assert settled.status == "settled" and settled.actual_cost_microusd == 2000
    assert store.settle_usage(guest.token, reservation.reservation_id, 500, 2000).replayed
    with pytest.raises(ReservationConflict):
        store.settle_usage(guest.token, reservation.reservation_id, 500, 1999)
    with pytest.raises(ReservationConflict):
        store.release_usage(guest.token, reservation.reservation_id)
    unused = store.reserve_usage(guest.token, UsageIntent("unused", "processing", 1, 4000))
    assert store.release_usage(guest.token, unused.reservation_id).status == "released"
    assert store.release_usage(guest.token, unused.reservation_id).replayed
    with pytest.raises(ReservationConflict):
        store.settle_usage(guest.token, unused.reservation_id, 0, 0)
    assert store.reserve_usage(guest.token, UsageIntent("replacement", "storage", 10, 4000))


def test_global_usage_cap_is_atomic_across_tenants_and_meters(reference):
    store, *_ = reference
    sessions = [store.issue_guest() for _ in range(12)]

    def reserve(index):
        try:
            return store.reserve_usage(
                sessions[index].token, UsageIntent(str(index), "model_tokens", 100, 2000),
            )
        except BudgetExceeded:
            return None

    with ThreadPoolExecutor(max_workers=12) as executor:
        results = list(executor.map(reserve, range(12)))
    assert len([result for result in results if result]) == 5
    member = store.sign_in(account())
    with pytest.raises(BudgetExceeded):
        store.reserve_usage(member.token, UsageIntent("signed-in", "speech_characters", 1, 1))


def test_usage_cannot_be_read_or_settled_by_another_tenant_and_promotion_keeps_cap(reference):
    store, *_ = reference
    guest, other = store.issue_guest(), store.issue_guest()
    usage_intent = UsageIntent("work", "processing", 10, 6000)
    reservation = store.reserve_usage(guest.token, usage_intent)
    with pytest.raises(AccessDenied):
        store.settle_usage(other.token, reservation.reservation_id, 1, 1)
    with pytest.raises(AccessDenied):
        store.release_usage(other.token, reservation.reservation_id)
    member = store.sign_in(account(), guest_digest=hashlib.sha256(guest.token.encode()).hexdigest())
    assert store.reserve_usage(member.token, usage_intent).reservation_id == reservation.reservation_id
    with pytest.raises(BudgetExceeded):
        store.reserve_usage(member.token, UsageIntent("second", "processing", 1, 1))
    assert store.settle_usage(member.token, reservation.reservation_id, 1, 1000).status == "settled"


def test_usage_fails_closed_without_approved_allocation_and_after_period(reference):
    store, now, _, policy = reference
    no_policy = SQLiteAccessStore(store.path, ip_hmac_key=KEY, clock=lambda: now[0])
    guest = store.issue_guest()
    with pytest.raises(BudgetExceeded):
        no_policy.reserve_usage(guest.token, UsageIntent("work", "processing", 1, 1))
    now[0] = policy.period_end
    with pytest.raises(BudgetExceeded):
        store.reserve_usage(store.issue_guest().token, UsageIntent("work", "processing", 1, 1))
    with pytest.raises(ValueError):
        UsagePolicy(0, 1, 150_000_001, 1)
    with pytest.raises(ValueError):
        UsagePolicy(0, 1, 10, 11)


def test_replica_policy_disagreement_cannot_increase_approved_budget(reference):
    store, _, _, policy = reference
    changed = UsagePolicy(policy.period_start, policy.period_end, 15_000, 6_000)
    with pytest.raises(ValueError, match="Conflicting"):
        SQLiteAccessStore(store.path, ip_hmac_key=KEY, usage_policy=changed)
    overlapping = UsagePolicy(policy.period_start + 1, policy.period_end, 10_000, 6_000)
    with pytest.raises(ValueError, match="Conflicting"):
        SQLiteAccessStore(store.path, ip_hmac_key=KEY, usage_policy=overlapping)


def test_login_storage_is_bound_single_use_and_expires(reference):
    store, now, *_ = reference
    binding = secrets.token_urlsafe(32)
    guest = store.issue_guest()
    flow = {
        "state": "random-csrf", "nonce": "nonce", "code_verifier": "pkce",
        "auth_uri": "https://login.example/authorize",
    }
    store.save_login(binding, flow, guest.token)
    with pytest.raises(AccessDenied):
        store.consume_login(secrets.token_urlsafe(32), flow["state"])
    with pytest.raises(AccessDenied):
        store.consume_login(binding, "wrong-state")
    pending = store.consume_login(binding, flow["state"])
    assert pending.guest_digest == hashlib.sha256(guest.token.encode()).hexdigest()
    assert "auth_uri" not in pending.flow
    assert pending.flow["code_verifier"] == "pkce"
    assert "pkce" not in repr(pending)
    with pytest.raises(AccessDenied):
        store.consume_login(binding, flow["state"])
    store.save_login(binding, flow)
    now[0] += 600
    with pytest.raises(AccessDenied):
        store.consume_login(binding, flow["state"])
