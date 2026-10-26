from __future__ import annotations

import hashlib
import json
import secrets

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from tactiq.runtime import (
    AuthConfigurationError,
    AuthSettings,
    Principal,
    authenticate,
    require_role,
    require_tenant,
)
from tactiq.runtime import auth as auth_module


def credential(role="viewer", tenant="tenant-a"):
    token = secrets.token_urlsafe(32)
    return token, {
        "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
        "tenant_id": tenant,
        "subject": f"{role}-user",
        "role": role,
    }


def settings_for(*entries, production=False, **overrides):
    return AuthSettings.from_env(
        {
            "APP_ENV": "production" if production else "local",
            "TACTIQ_AUTH_ENABLED": "true",
            "TACTIQ_API_TOKENS_JSON": json.dumps(entries),
            **overrides,
        }
    )


def test_local_default_is_explicit_local_admin():
    settings = AuthSettings.from_env({})
    principal = settings.authenticate(None)
    assert principal == Principal(tenant_id="local", subject="local", role="admin")
    assert not settings.secure_cookie
    assert settings.authenticate("not a bearer header") == principal
    with pytest.raises(ValidationError):
        principal.tenant_id = "elsewhere"


def test_authentication_uses_digest_and_accepts_header_or_cookie():
    token, entry = credential("analyst")
    settings = settings_for(entry, production=True)
    expected = Principal(tenant_id="tenant-a", subject="analyst-user", role="analyst")
    assert settings.authenticate(f"Bearer {token}") == expected
    assert authenticate(f"bearer {token}", settings=settings) == expected
    assert settings.authenticate(None, token) == expected
    assert token not in repr(settings)
    assert entry["token_sha256"] not in repr(settings)
    assert settings.secure_cookie
    assert settings.cookie_options() == {
        "key": "tactiq_session",
        "secure": True,
        "httponly": True,
        "samesite": "lax",
        "path": "/",
        "max_age": 3600,
    }


def test_header_takes_precedence_and_invalid_header_never_falls_back_to_cookie():
    token, entry = credential()
    other, other_entry = credential("admin", "tenant-b")
    settings = settings_for(entry, other_entry)
    assert settings.authenticate(f"Bearer {other}", token).tenant_id == "tenant-b"
    for header in ("Basic ignored", "", "Bearer not-a-configured-value"):
        with pytest.raises(HTTPException) as error:
            settings.authenticate(header, token)
        assert error.value.status_code == 401


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "Basic ignored",
        "Bearer",
        "Bearer first second",
        "Bearer invalid!",
        "Bearer " + "a" * 8193,
        "Bearer not-a-configured-value",
    ],
)
def test_unauthenticated_is_401_with_bearer_challenge(header):
    _, entry = credential()
    settings = settings_for(entry)
    with pytest.raises(HTTPException) as error:
        settings.authenticate(header)
    assert error.value.status_code == 401
    assert error.value.headers == {"WWW-Authenticate": "Bearer"}
    assert error.value.detail == "Valid authentication credentials are required"


def test_header_line_break_is_rejected_even_for_valid_digest():
    token, entry = credential()
    settings = settings_for(entry)
    with pytest.raises(HTTPException) as error:
        settings.authenticate(f"Bearer\r\n{token}")
    assert error.value.status_code == 401


def test_all_configured_digests_are_compared_in_constant_time(monkeypatch):
    credentials = [credential(role) for role in ("viewer", "analyst", "admin")]
    settings = settings_for(*(entry for _, entry in credentials))
    real_compare = auth_module.hmac.compare_digest
    comparisons = []

    def compare(left, right):
        comparisons.append((left, right))
        return real_compare(left, right)

    monkeypatch.setattr(auth_module.hmac, "compare_digest", compare)
    assert settings.authenticate(f"Bearer {credentials[0][0]}").role == "viewer"
    assert len(comparisons) == 3
    assert all(len(left) == len(right) == 32 for left, right in comparisons)


def test_uppercase_hex_digest_is_valid():
    token, entry = credential()
    entry["token_sha256"] = entry["token_sha256"].upper()
    assert settings_for(entry).authenticate(f"Bearer {token}").role == "viewer"


def test_default_authenticator_reads_current_environment(monkeypatch):
    token, entry = credential()
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("TACTIQ_AUTH_ENABLED", "true")
    monkeypatch.setenv("TACTIQ_API_TOKENS_JSON", json.dumps([entry]))
    monkeypatch.delenv("TACTIQ_COOKIE_SECURE", raising=False)
    monkeypatch.delenv("TACTIQ_AUTH_COOKIE_NAME", raising=False)
    assert authenticate(f"Bearer {token}").tenant_id == "tenant-a"


@pytest.mark.parametrize(
    "environment",
    [
        {"APP_ENV": "production"},
        {"APP_ENV": "prod", "TACTIQ_AUTH_ENABLED": "false"},
        {"APP_ENV": "production", "TACTIQ_AUTH_ENABLED": "true"},
        {"TACTIQ_AUTH_ENABLED": "true", "TACTIQ_API_TOKENS_JSON": "[]"},
        {"TACTIQ_AUTH_ENABLED": "maybe"},
        {"TACTIQ_COOKIE_SECURE": "perhaps"},
        {"TACTIQ_API_TOKENS_JSON": "{"},
        {"TACTIQ_API_TOKENS_JSON": "{}"},
        {"TACTIQ_API_TOKENS_JSON": "null"},
        {"TACTIQ_API_TOKENS_JSON": '["not-an-object"]'},
        {"TACTIQ_API_TOKENS_JSON": "[{}]"},
        {"TACTIQ_AUTH_COOKIE_NAME": "bad;cookie"},
        {"TACTIQ_AUTH_COOKIE_NAME": "__Host-tactiq"},
    ],
)
def test_invalid_configuration_fails_even_before_a_request(environment):
    with pytest.raises(AuthConfigurationError):
        AuthSettings.from_env(environment)


@pytest.mark.parametrize(
    "changes",
    [
        {"token_sha256": ""},
        {"token_sha256": "g" * 64},
        {"token_sha256": "a" * 63},
        {"token_sha256": "a" * 65},
        {"token_sha256": 1},
        {"tenant_id": ""},
        {"tenant_id": " tenant"},
        {"tenant_id": 1},
        {"subject": "invalid\nsubject"},
        {"subject": None},
        {"role": "super-admin"},
        {"role": 1},
        {"unexpected": "field"},
    ],
)
def test_invalid_token_entries_fail_without_echoing_configuration(changes):
    token, entry = credential()
    entry.update(changes)
    with pytest.raises(AuthConfigurationError) as error:
        settings_for(entry)
    assert token not in str(error.value)
    assert entry.get("token_sha256", "") != str(error.value)


def test_duplicate_digests_are_rejected_instead_of_ambiguous_tenant_assignment():
    _, entry = credential()
    other = {**entry, "tenant_id": "tenant-b", "token_sha256": entry["token_sha256"].upper()}
    with pytest.raises(AuthConfigurationError, match="unique"):
        settings_for(entry, other)


def test_production_cannot_disable_secure_cookies():
    _, entry = credential()
    with pytest.raises(AuthConfigurationError, match="secure"):
        settings_for(entry, production=True, TACTIQ_COOKIE_SECURE="false")


def test_cookie_name_and_local_secure_transport_are_configurable():
    _, entry = credential()
    settings = settings_for(
        entry, TACTIQ_COOKIE_SECURE="true", TACTIQ_AUTH_COOKIE_NAME="__Host-tactiq"
    )
    assert settings.cookie_options()["key"] == "__Host-tactiq"
    assert settings.cookie_options()["path"] == "/"
    assert settings.cookie_options()["secure"] is True
    assert "domain" not in settings.cookie_options()


@pytest.mark.parametrize("role", ["viewer", "analyst", "admin"])
@pytest.mark.parametrize("minimum", ["viewer", "analyst", "admin"])
def test_hierarchical_role_matrix(role, minimum):
    principal = Principal(tenant_id="tenant-a", subject="user", role=role)
    levels = {"viewer": 0, "analyst": 1, "admin": 2}
    if levels[role] >= levels[minimum]:
        assert require_role(principal, minimum) is principal
    else:
        with pytest.raises(HTTPException) as error:
            require_role(principal, minimum)
        assert error.value.status_code == 403


def test_missing_principal_is_401_not_403_and_unknown_role_is_configuration_error():
    with pytest.raises(HTTPException) as error:
        require_role(None, "viewer")
    assert error.value.status_code == 401
    with pytest.raises(ValueError, match="Unknown"):
        require_role(None, "owner")


def test_even_admin_role_cannot_cross_tenant_boundary():
    principal = Principal(tenant_id="tenant-a", subject="user", role="admin")
    assert require_tenant(principal, "tenant-a") is principal
    with pytest.raises(HTTPException) as error:
        require_tenant(principal, "tenant-b")
    assert error.value.status_code == 403
    with pytest.raises(HTTPException) as error:
        require_tenant(None, "tenant-a")
    assert error.value.status_code == 401
