"""Reusable HTTP/WebSocket bearer authentication and hierarchical RBAC.

Environment:
    APP_ENV=production (also accepts prod) requires authentication.
    TACTIQ_AUTH_ENABLED=false by default, for local use only.
    TACTIQ_API_TOKENS_JSON is an array of objects with exactly token_sha256,
        tenant_id, subject and role (viewer, analyst or admin).
    TACTIQ_COOKIE_SECURE defaults to true in production; false is forbidden there.
    TACTIQ_AUTH_COOKIE_NAME defaults to tactiq_session.

Only SHA-256 digests are configured. Bearer values are never persisted or
included in errors. HTTP Authorization takes precedence over a cookie; an
invalid header cannot silently fall back to cookie authentication.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, field_validator

from ._validation import RecordValidationError, identifier, positive_integer


Role = Literal["viewer", "analyst", "admin"]
_ROLE_RANK = {"viewer": 0, "analyst": 1, "admin": 2}
_DIGEST_PATTERN = re.compile(r"[0-9a-fA-F]{64}\Z")
_COOKIE_PATTERN = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]{1,128}\Z")
_BEARER_PATTERN = re.compile(r"[A-Za-z0-9._~+/\-]+=*\Z")


class AuthConfigurationError(ValueError):
    """Authentication is misconfigured; startup should fail rather than bypass it."""


class Principal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    tenant_id: str
    subject: str
    role: Role

    @field_validator("tenant_id", "subject")
    @classmethod
    def validate_identity(cls, value: str) -> str:
        return identifier(value, "principal identity")


@dataclass(frozen=True)
class _TokenDigest:
    digest: bytes = field(repr=False)
    principal: Principal


def _boolean(value: str | None, name: str, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise AuthConfigurationError(f"{name} must be a boolean")


@dataclass(frozen=True)
class AuthSettings:
    enabled: bool = False
    production: bool = False
    tokens: tuple[_TokenDigest, ...] = field(default=(), repr=False)
    secure_cookie: bool = False
    cookie_name: str = "tactiq_session"
    cookie_max_age_seconds: int = 3600

    def __post_init__(self) -> None:
        if any(type(value) is not bool for value in (self.enabled, self.production, self.secure_cookie)):
            raise AuthConfigurationError("Authentication switches must be booleans")
        if not isinstance(self.tokens, tuple) or any(
            not isinstance(token, _TokenDigest)
            or not isinstance(token.digest, bytes)
            or len(token.digest) != 32
            or not isinstance(token.principal, Principal)
            for token in self.tokens
        ):
            raise AuthConfigurationError("Token configuration must contain validated SHA-256 digests")
        if len({token.digest for token in self.tokens}) != len(self.tokens):
            raise AuthConfigurationError("Token digests must be unique")
        if self.production and not self.enabled:
            raise AuthConfigurationError("Production requires TACTIQ_AUTH_ENABLED=true")
        if self.enabled and not self.tokens:
            raise AuthConfigurationError("Authentication requires nonempty TACTIQ_API_TOKENS_JSON")
        if self.production and not self.secure_cookie:
            raise AuthConfigurationError("Production requires secure authentication cookies")
        if not isinstance(self.cookie_name, str) or not _COOKIE_PATTERN.fullmatch(self.cookie_name):
            raise AuthConfigurationError("TACTIQ_AUTH_COOKIE_NAME is not a valid cookie name")
        if self.cookie_name.startswith("__Host-") and not self.secure_cookie:
            raise AuthConfigurationError("__Host- cookies require secure transport")
        try:
            positive_integer(self.cookie_max_age_seconds, "cookie_max_age_seconds")
        except RecordValidationError as exc:
            raise AuthConfigurationError("Cookie lifetime must be a positive integer") from exc

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> AuthSettings:
        source = os.environ if environ is None else environ
        production = source.get("APP_ENV", "local").strip().casefold() in {"production", "prod"}
        enabled = _boolean(source.get("TACTIQ_AUTH_ENABLED"), "TACTIQ_AUTH_ENABLED", False)
        secure_cookie = _boolean(
            source.get("TACTIQ_COOKIE_SECURE"), "TACTIQ_COOKIE_SECURE", production
        )
        raw = source.get("TACTIQ_API_TOKENS_JSON")
        credentials: list[_TokenDigest] = []
        if raw:
            try:
                entries = json.loads(raw)
            except (TypeError, ValueError, RecursionError) as exc:
                raise AuthConfigurationError("TACTIQ_API_TOKENS_JSON must be valid JSON") from exc
            if not isinstance(entries, list):
                raise AuthConfigurationError("TACTIQ_API_TOKENS_JSON must be an array")
            for entry in entries:
                if not isinstance(entry, dict) or set(entry) != {
                    "token_sha256", "tenant_id", "subject", "role"
                }:
                    raise AuthConfigurationError(
                        "Each token entry requires token_sha256, tenant_id, subject and role"
                    )
                digest = entry["token_sha256"]
                if not isinstance(digest, str) or not _DIGEST_PATTERN.fullmatch(digest):
                    raise AuthConfigurationError("token_sha256 must be a 64-character SHA-256 digest")
                try:
                    principal = Principal(
                        tenant_id=entry["tenant_id"], subject=entry["subject"], role=entry["role"]
                    )
                except ValueError as exc:
                    raise AuthConfigurationError("Token principal configuration is invalid") from exc
                credentials.append(_TokenDigest(bytes.fromhex(digest), principal))
        return cls(
            enabled=enabled,
            production=production,
            tokens=tuple(credentials),
            secure_cookie=secure_cookie,
            cookie_name=source.get("TACTIQ_AUTH_COOKIE_NAME", "tactiq_session"),
        )

    def authenticate(
        self, authorization_header: str | None, cookie_value: str | None = None
    ) -> Principal:
        return authenticate(authorization_header, cookie_value, settings=self)

    def cookie_options(self) -> dict[str, str | bool | int]:
        """Pass with value=... to Response.set_cookie; no token is kept in settings."""
        return {
            "key": self.cookie_name,
            "secure": self.secure_cookie,
            "httponly": True,
            "samesite": "lax",
            "path": "/",
            "max_age": self.cookie_max_age_seconds,
        }


def _unauthenticated() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="Valid authentication credentials are required",
        headers={"WWW-Authenticate": "Bearer"},
    )


def authenticate(
    authorization_header: str | None,
    cookie_value: str | None = None,
    *,
    settings: AuthSettings | None = None,
) -> Principal:
    """Authenticate without request-scoped state; also usable before WebSocket.accept."""
    configured = AuthSettings.from_env() if settings is None else settings
    if not configured.enabled:
        return Principal(tenant_id="local", subject="local", role="admin")
    if authorization_header is not None:
        if (
            not isinstance(authorization_header, str)
            or len(authorization_header) > 8256
            or any(ord(character) < 32 or ord(character) == 127 for character in authorization_header)
        ):
            raise _unauthenticated()
        parts = authorization_header.split()
        if len(parts) != 2 or parts[0].casefold() != "bearer":
            raise _unauthenticated()
        token = parts[1]
    else:
        token = cookie_value
    if (
        not isinstance(token, str)
        or not 1 <= len(token) <= 8192
        or _BEARER_PATTERN.fullmatch(token) is None
    ):
        raise _unauthenticated()
    digest = hashlib.sha256(token.encode("ascii")).digest()
    principal: Principal | None = None
    for credential in configured.tokens:
        if hmac.compare_digest(digest, credential.digest):
            principal = credential.principal
    if principal is None:
        raise _unauthenticated()
    return principal


def require_role(principal: Principal | None, minimum_role: Role) -> Principal:
    """viewer <= analyst <= admin. A missing identity is 401, insufficient role 403."""
    if minimum_role not in _ROLE_RANK:
        raise ValueError("Unknown minimum role")
    if principal is None:
        raise _unauthenticated()
    if _ROLE_RANK[principal.role] < _ROLE_RANK[minimum_role]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return principal


def require_tenant(principal: Principal | None, tenant_id: str) -> Principal:
    if principal is None:
        raise _unauthenticated()
    identifier(tenant_id, "tenant_id")
    if principal.tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return principal
