from __future__ import annotations

import base64
import builtins
import hashlib
import json
import secrets
import shutil
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from tactiq.runtime.access import AccessDenied, IdentityKey, SQLiteAccessStore
from tactiq.runtime.identity import (
    IdentityUnavailable,
    MicrosoftOIDCProvider,
    MicrosoftOIDCSettings,
    MicrosoftSignIn,
)


CLIENT = "7b4a7b7b-88ba-4e96-a292-6af98e206614"
TENANT = "9188040d-6c67-4c5b-b112-36a304b66dad"
ISSUER = f"https://login.microsoftonline.com/{TENANT}/v2.0"


def settings(**overrides):
    return MicrosoftOIDCSettings(**{
        "client_id": CLIENT, "client_secret": "test-credential",
        "redirect_uri": "https://app.example/identity/callback", **overrides,
    })


@pytest.fixture
def store():
    directory = Path("build") / "identity-reference-tests" / str(uuid.uuid4())
    directory.mkdir(parents=True)
    try:
        yield SQLiteAccessStore(
            directory / "identity.sqlite3",
            ip_hmac_key=b"test-reference-only-not-production-key",
        )
    finally:
        shutil.rmtree(directory)


class VerifiedTestProvider:
    def __init__(self):
        self.calls = []
        self.flow = {
            "state": "test-csrf-" + secrets.token_urlsafe(16),
            "nonce": secrets.token_urlsafe(16),
            "code_verifier": secrets.token_urlsafe(32),
            "auth_uri": "https://login.microsoftonline.com/common/authorize",
        }

    def begin(self):
        return self.flow

    def complete(self, flow, response):
        self.calls.append((flow, response))
        return IdentityKey(ISSUER, "verified-alice")


def test_sign_in_flow_captures_guest_server_side_and_requires_browser_binding(store):
    guest, other = store.issue_guest(), store.issue_guest()
    provider = VerifiedTestProvider()
    login = MicrosoftSignIn(store, provider)
    redirect = login.begin(guest.token)
    assert redirect.browser_binding not in repr(redirect)
    assert redirect.authorization_uri not in repr(redirect)
    assert redirect.cookie_options() == {
        "key": "__Host-tactiq_oidc", "secure": True, "httponly": True,
        "samesite": "none", "path": "/", "max_age": 600,
    }
    response = {"state": provider.flow["state"], "code": "test-authorization-code"}
    with pytest.raises(AccessDenied):
        login.complete(other.token, response)
    with pytest.raises(AccessDenied):
        login.complete(redirect.browser_binding, {**response, "state": "forged"})
    assert provider.calls == []
    issued = login.complete(redirect.browser_binding, response)
    assert not issued.principal.guest
    assert guest.principal.tenant_id in store.authorized_tenants(issued.token)
    assert other.principal.tenant_id not in store.authorized_tenants(issued.token)
    with pytest.raises(AccessDenied):
        store.authenticate(guest.token)
    with pytest.raises(AccessDenied):
        login.complete(redirect.browser_binding, response)
    assert len(provider.calls) == 1
    assert "auth_uri" not in provider.calls[0][0]


def test_failed_provider_cannot_promote_and_flow_cannot_be_replayed(store):
    class FailedProvider(VerifiedTestProvider):
        def complete(self, flow, response):
            raise IdentityUnavailable("Verification failed")

    guest = store.issue_guest()
    provider = FailedProvider()
    login = MicrosoftSignIn(store, provider)
    redirect = login.begin(guest.token)
    response = {"state": provider.flow["state"], "code": "code"}
    with pytest.raises(IdentityUnavailable):
        login.complete(redirect.browser_binding, response)
    assert store.authenticate(guest.token).guest
    with pytest.raises(AccessDenied):
        login.complete(redirect.browser_binding, response)


@pytest.mark.parametrize("response", [{}, {"state": []}, {"state": "x", "code": "a" * 8193}, []])
def test_malformed_callback_cannot_call_provider(store, response):
    provider = VerifiedTestProvider()
    redirect = MicrosoftSignIn(store, provider).begin()
    with pytest.raises((AccessDenied, ValueError)):
        MicrosoftSignIn(store, provider).complete(redirect.browser_binding, response)
    assert provider.calls == []


@pytest.mark.parametrize(
    "changes",
    [
        {"client_id": "not-an-app-id"},
        {"client_secret": ""},
        {"redirect_uri": "http://app.example/callback"},
        {"redirect_uri": "https://user:pass@app.example/callback"},
        {"redirect_uri": "https://app.example/callback?return=https://evil"},
        {"redirect_uri": "https://app.example/callback#fragment"},
        {"audience": "https://evil.example"},
    ],
)
def test_identity_configuration_fails_closed(changes):
    with pytest.raises(ValueError):
        settings(**changes)


def test_optional_dependency_failure_is_lazy_and_explicit(monkeypatch):
    real_import = builtins.__import__

    def no_msal(name, *args, **kwargs):
        if name == "msal":
            raise ImportError("absent")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_msal)
    with pytest.raises(IdentityUnavailable, match="optional identity"):
        MicrosoftOIDCProvider(settings())


def test_msal_supported_flow_uses_state_nonce_and_s256_pkce_without_network(monkeypatch):
    pytest.importorskip("msal")
    from msal.oauth2cli import oauth2
    from msal.oauth2cli.oidc import Client

    client = Client(
        {"authorization_endpoint": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"},
        CLIENT,
    )
    flow = client.initiate_auth_code_flow(
        scope=["openid", "profile"], redirect_uri="https://app.example/callback",
        response_mode="form_post",
    )
    provider = MicrosoftOIDCProvider.__new__(MicrosoftOIDCProvider)
    provider.settings = settings()
    calls = []

    class Application:
        def initiate_auth_code_flow(self, **kwargs):
            calls.append(kwargs)
            return flow

    provider._application = Application()
    assert provider.begin() == flow
    assert calls == [{
        "scopes": [], "redirect_uri": settings().redirect_uri, "response_mode": "form_post",
    }]
    query = parse_qs(urlsplit(flow["auth_uri"]).query)
    expected_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(flow["code_verifier"].encode()).digest(),
    ).decode().rstrip("=")
    assert query["code_challenge"] == [expected_challenge]
    assert query["code_challenge_method"] == ["S256"]
    assert query["state"] == [flow["state"]]
    assert query["nonce"] == [hashlib.sha256(flow["nonce"].encode()).hexdigest()]
    with pytest.raises(ValueError, match="state"):
        client.obtain_token_by_auth_code_flow(flow, {"state": "forged", "code": "code"})
    token_result = {"id_token_claims": {"nonce": query["nonce"][0]}}
    monkeypatch.setattr(
        oauth2.Client, "obtain_token_by_auth_code_flow", lambda *_args, **_kwargs: token_result,
    )
    assert client.obtain_token_by_auth_code_flow(flow, {}) == token_result
    token_result["id_token_claims"]["nonce"] = flow["nonce"]
    with pytest.raises(RuntimeError):
        client.obtain_token_by_auth_code_flow(flow, {})


@pytest.fixture
def signed_provider():
    jwt = pytest.importorskip("jwt")
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives.asymmetric import rsa

    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    provider = MicrosoftOIDCProvider.__new__(MicrosoftOIDCProvider)
    provider.settings = settings()
    provider._jwt = jwt
    public_jwk = {
        **json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key())),
        "kid": "test-key", "issuer": ISSUER,
    }
    provider._jwks = SimpleNamespace(
        fetch_data=lambda: {"keys": [public_jwk]},
    )
    flow = {"nonce": "server-nonce", "state": "state", "code_verifier": "pkce"}
    now = int(time.time())
    claims = {
        "iss": ISSUER, "sub": "verified-alice", "tid": TENANT, "aud": CLIENT,
        "iat": now, "nbf": now - 1, "exp": now + 300,
        "nonce": hashlib.sha256(flow["nonce"].encode()).hexdigest(),
    }

    def token(overrides=None, key=None, algorithm="RS256"):
        return jwt.encode(
            {**claims, **(overrides or {})}, key or private, algorithm=algorithm,
            headers={"kid": "test-key"},
        )

    return provider, flow, token, claims


def test_signed_id_token_is_verified_not_just_msal_claims(signed_provider):
    provider, flow, token, _ = signed_provider
    calls = []

    class Application:
        def acquire_token_by_auth_code_flow(self, saved_flow, response):
            calls.append((saved_flow, response))
            return {
                "id_token": token(),
                "id_token_claims": {"sub": "attacker-alice", "tid": TENANT, "roles": ["admin"]},
                "access_token": "discarded-api-token",
                "refresh_token": "discarded-refresh-token",
            }

    provider._application = Application()
    identity = provider.complete(flow, {"state": "state", "code": "code"})
    assert identity == IdentityKey(ISSUER, "verified-alice")
    assert calls == [(flow, {"state": "state", "code": "code"})]


@pytest.mark.parametrize(
    "key_issuer",
    [ISSUER, "https://login.microsoftonline.com/{tenantid}/v2.0"],
)
def test_signing_key_scope_accepts_exact_tenant_or_microsoft_template(signed_provider, key_issuer):
    provider, flow, token, _ = signed_provider
    provider._jwks.fetch_data()["keys"][0]["issuer"] = key_issuer
    assert provider._validate_token(token(), flow) == IdentityKey(ISSUER, "verified-alice")


@pytest.mark.parametrize(
    "key_issuer",
    [
        "https://login.microsoftonline.com/c1a27bd9-e2d5-4ed4-a44e-0acccfd0def0/v2.0",
        "https://evil.example/{tenantid}/v2.0",
        "https://login.microsoftonline.com/{tenantid}/v1.0",
        "https://login.microsoftonline.com/common/v2.0",
        None,
        ["https://login.microsoftonline.com/{tenantid}/v2.0"],
    ],
)
def test_signed_token_rejects_mismatched_or_missing_key_issuer_sanitized(
    signed_provider, key_issuer,
):
    provider, flow, token, _ = signed_provider
    # Retain the valid signing material: only the key's issuer scope changes.
    public_jwk = provider._jwks.fetch_data()["keys"][0]
    raw = token()
    assert provider._validate_token(raw, flow) == IdentityKey(ISSUER, "verified-alice")
    if key_issuer is None:
        public_jwk.pop("issuer")
    else:
        public_jwk["issuer"] = key_issuer
    provider._application = SimpleNamespace(
        acquire_token_by_auth_code_flow=lambda *_: {"id_token": raw},
    )
    with pytest.raises(IdentityUnavailable) as error:
        provider.complete(flow, {"state": "state", "code": "code"})
    assert str(error.value) == "Microsoft sign-in could not be verified"
    assert raw not in str(error.value)
    assert error.value.__suppress_context__


@pytest.mark.parametrize(
    "overrides",
    [
        {"iss": "https://evil.example"},
        {"aud": "different-client"},
        {"aud": [CLIENT, "different-client"]},
        {"tid": "not-a-tenant"},
        {"nonce": "attacker-nonce"},
        {"exp": 1},
        {"iat": 9_999_999_999},
        {"nbf": 9_999_999_999},
        {"azp": "attacker-app"},
        {"sub": ""},
        {"iss": f"https://login.microsoftonline.com/{TENANT}/v1.0"},
    ],
)
def test_bad_signed_claims_fail_closed_without_token_or_claim_leak(signed_provider, overrides):
    provider, flow, token, _ = signed_provider
    raw = token(overrides)
    provider._application = SimpleNamespace(
        acquire_token_by_auth_code_flow=lambda *_: {"id_token": raw},
    )
    with pytest.raises(IdentityUnavailable) as error:
        provider.complete(flow, {"state": "state", "code": "code"})
    assert str(error.value) == "Microsoft sign-in could not be verified"
    assert raw not in str(error.value)
    assert error.value.__suppress_context__


def test_forged_signature_and_algorithm_confusion_fail(signed_provider):
    from cryptography.hazmat.primitives.asymmetric import rsa

    provider, flow, token, _ = signed_provider
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    for raw in (token(key=attacker), token(key="attacker-secret-that-is-at-least-32-bytes", algorithm="HS256")):
        provider._application = SimpleNamespace(
            acquire_token_by_auth_code_flow=lambda *_: {"id_token": raw},
        )
        with pytest.raises(IdentityUnavailable):
            provider.complete(flow, {"state": "state", "code": "code"})


def test_msal_error_sanitizes_and_never_maps_unverified_identity(signed_provider):
    provider, flow, _, _ = signed_provider

    def unsafe_error(*args):
        raise RuntimeError("Sensitive nonce and ID-token claims")

    provider._application = SimpleNamespace(acquire_token_by_auth_code_flow=unsafe_error)
    with pytest.raises(IdentityUnavailable) as error:
        provider.complete(flow, {"state": "state", "code": "code"})
    assert "Sensitive" not in str(error.value)
    provider._application = SimpleNamespace(
        acquire_token_by_auth_code_flow=lambda *_: {"error": "access_denied"},
    )
    with pytest.raises(IdentityUnavailable):
        provider.complete(flow, {"state": "state", "code": "code"})


def test_account_audience_enforced_and_same_consumer_directory_does_not_define_workspace(signed_provider):
    provider, flow, token, _ = signed_provider
    provider.settings = settings(audience="organizations")
    with pytest.raises(ValueError, match="audience"):
        provider._validate_token(token(), flow)
    provider.settings = settings(audience="consumers")
    assert provider._validate_token(token(), flow) == IdentityKey(ISSUER, "verified-alice")
    work_tenant = "c1a27bd9-e2d5-4ed4-a44e-0acccfd0def0"
    with pytest.raises(ValueError, match="audience"):
        provider._validate_token(token({"tid": work_tenant}), flow)
    provider.settings = settings(audience=work_tenant)
    with pytest.raises(ValueError, match="audience"):
        provider._validate_token(token(), flow)


def test_live_adapter_disables_reusable_token_cache(monkeypatch):
    msal = pytest.importorskip("msal")
    pytest.importorskip("jwt")

    calls = []

    def application(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace()

    monkeypatch.setattr(msal, "ConfidentialClientApplication", application)
    provider = MicrosoftOIDCProvider(settings())
    _, options = calls[0]
    assert options["enable_pii_log"] is False
    cache = options["token_cache"]
    cache.add({"sensitive": "discarded"})
    assert list(cache.search(msal.TokenCache.CredentialType.ACCESS_TOKEN)) == []
    assert "test-credential" not in repr(provider.settings)
