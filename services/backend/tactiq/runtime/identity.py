"""Optional Microsoft OIDC foundation; no routes or legacy-auth replacement.

Uses supported MSAL authorization-code flow APIs (state/nonce/PKCE) and explicit
signed ID-token validation. MSAL 1.38+ does NOT itself validate ID-token claims.
No token cache, access/refresh token persistence, claims logging or Azure writes.
Install the optional identity dependencies before constructing the live adapter.
Only a real configured Entra app and browser round trip can verify integration.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol
from urllib.parse import urlsplit

from .access import AccessDenied, AccessStore, IdentityKey, IssuedSession


class IdentityUnavailable(RuntimeError):
    """The optional provider is missing or authentication failed."""


@dataclass(frozen=True)
class MicrosoftOIDCSettings:
    client_id: str
    client_secret: str = field(repr=False)
    redirect_uri: str
    audience: str = "common"
    certificate_pem: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        try:
            if str(uuid.UUID(self.client_id)) != self.client_id:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise ValueError("Microsoft client ID must be a canonical UUID") from None
        if not isinstance(self.client_secret, str) or (not self.client_secret.strip() and not self.certificate_pem):
            raise ValueError("A confidential-client credential is required")
        if self.certificate_pem and self.client_secret:
            raise ValueError("Configure exactly one confidential-client credential")
        if self.certificate_pem:
            self.client_credential()
        uri = urlsplit(self.redirect_uri)
        if (
            uri.scheme != "https" or not uri.hostname or uri.username or uri.password
            or uri.fragment or uri.query
        ):
            raise ValueError("OIDC redirect must be a fixed registered HTTPS URI")
        if self.audience not in {"common", "organizations", "consumers"}:
            try:
                if str(uuid.UUID(self.audience)) != self.audience:
                    raise ValueError
            except (ValueError, TypeError, AttributeError):
                raise ValueError("Unsupported Microsoft account audience") from None

    def client_credential(self) -> str | dict[str, str]:
        if not self.certificate_pem:
            return self.client_secret
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        try:
            pem = self.certificate_pem.encode("ascii")
            certificate = x509.load_pem_x509_certificate(pem)
            key = serialization.load_pem_private_key(pem, password=None)
            now = datetime.now(timezone.utc)
            if (not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048
                    or not certificate.not_valid_before_utc <= now < certificate.not_valid_after_utc):
                raise ValueError
            encoding = serialization.Encoding.DER
            format_ = serialization.PublicFormat.SubjectPublicKeyInfo
            if key.public_key().public_bytes(encoding, format_) != certificate.public_key().public_bytes(encoding, format_):
                raise ValueError
        except (ValueError, TypeError, UnicodeError):
            raise ValueError("Invalid confidential-client certificate") from None
        return {
            "private_key": self.certificate_pem,
            # MSAL's certificate identifier uses SHA1; it is not a signing algorithm.
            "thumbprint": certificate.fingerprint(hashes.SHA1()).hex(),  # nosec B303
        }


class OIDCProvider(Protocol):
    """Trusted adapter; complete returns only cryptographically verified identity."""

    def begin(self) -> Mapping: ...
    def complete(self, flow: Mapping, response: Mapping[str, str]) -> IdentityKey: ...


class MicrosoftOIDCProvider:
    _CONSUMER_TENANT = "9188040d-6c67-4c5b-b112-36a304b66dad"
    _JWKS = "https://login.microsoftonline.com/common/discovery/v2.0/keys"

    def __init__(self, settings: MicrosoftOIDCSettings) -> None:
        # Lazy imports keep local-only production auth unchanged and import-safe.
        try:
            import jwt
            import msal
        except ImportError:
            raise IdentityUnavailable("Install the optional identity dependencies") from None
        self.settings = settings
        self._jwt = jwt
        class DiscardingTokenCache(msal.TokenCache):
            def add(self, event, now=None):
                # This adapter signs in only; it never needs reusable API tokens.
                return None

        try:
            self._application = msal.ConfidentialClientApplication(
                settings.client_id, client_credential=settings.client_credential(),
                authority=f"https://login.microsoftonline.com/{settings.audience}",
                enable_pii_log=False,
                token_cache=DiscardingTokenCache(),
            )
            self._jwks = jwt.PyJWKClient(self._JWKS, timeout=10)
        except Exception:
            raise IdentityUnavailable("Microsoft identity configuration is unavailable") from None

    def begin(self) -> Mapping:
        try:
            flow = self._application.initiate_auth_code_flow(
                scopes=[], redirect_uri=self.settings.redirect_uri, response_mode="form_post",
            )
            if not all(flow.get(key) for key in ("state", "nonce", "code_verifier", "auth_uri")):
                raise ValueError
            return flow
        except Exception:
            raise IdentityUnavailable("Unable to begin Microsoft sign-in") from None

    def _validate_token(self, raw_token: str, flow: Mapping) -> IdentityKey:
        """Fixed JWKS host + signed tenant-specific issuer; never fetch a claim URL."""
        if not isinstance(raw_token, str) or len(raw_token) > 32_768:
            raise ValueError("Invalid ID token")
        unverified = self._jwt.decode(raw_token, options={"verify_signature": False})
        tenant = unverified.get("tid")
        if not isinstance(tenant, str) or not re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", tenant
        ):
            raise ValueError("Invalid tenant claim")
        audience = self.settings.audience
        if (
            (audience == "organizations" and tenant == self._CONSUMER_TENANT)
            or (audience == "consumers" and tenant != self._CONSUMER_TENANT)
            or (audience not in {"common", "organizations", "consumers"} and tenant != audience)
        ):
            raise ValueError("Unsupported account audience")
        issuer = f"https://login.microsoftonline.com/{tenant}/v2.0"
        header = self._jwt.get_unverified_header(raw_token)
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
            raise ValueError("Invalid signing key selection")
        # Read raw JWK metadata via public APIs: PyJWK exposes no issuer accessor.
        # A common-endpoint key is scoped to its exact tenant or this template,
        # not to every issuer whose token happens to verify with that key.
        key_issuers = {issuer, "https://login.microsoftonline.com/{tenantid}/v2.0"}
        candidates = [
            candidate for candidate in self._jwks.fetch_data()["keys"]
            if isinstance(candidate, Mapping)
            and candidate.get("kid") == header["kid"]
            and isinstance(candidate.get("issuer"), str)
            and candidate["issuer"] in key_issuers
            and candidate.get("kty") == "RSA"
            and candidate.get("use", "sig") == "sig"
            and candidate.get("alg", "RS256") == "RS256"
        ]
        if len(candidates) != 1:
            raise ValueError("Invalid signing key issuer scope")
        key = self._jwt.PyJWK.from_dict(candidates[0], algorithm="RS256")
        claims = self._jwt.decode(
            raw_token, key.key, algorithms=["RS256"], audience=self.settings.client_id,
            issuer=issuer, leeway=60,
            options={"require": ["exp", "iat", "iss", "aud", "sub", "tid", "nonce"]},
        )
        # MSAL oauth2cli sends SHA256(flow nonce), not the raw flow nonce.
        expected_nonce = hashlib.sha256(flow["nonce"].encode("ascii")).hexdigest()
        if not isinstance(claims["nonce"], str) or not hmac.compare_digest(
            claims["nonce"], expected_nonce,
        ):
            raise ValueError("Invalid nonce")
        if claims.get("azp", self.settings.client_id) != self.settings.client_id:
            raise ValueError("Invalid authorized party")
        if isinstance(claims["aud"], list) and len(claims["aud"]) != 1:
            raise ValueError("Ambiguous ID token audience")
        return IdentityKey(issuer, claims["sub"])

    def complete(self, flow: Mapping, response: Mapping[str, str]) -> IdentityKey:
        try:
            # MSAL validates state and exchanges the code with its PKCE verifier.
            result = self._application.acquire_token_by_auth_code_flow(dict(flow), dict(response))
            if "error" in result or "id_token" not in result:
                raise ValueError
            return self._validate_token(result["id_token"], flow)
        except Exception:
            # MSAL exceptions can contain nonce/token claims: never chain/log them.
            raise IdentityUnavailable("Microsoft sign-in could not be verified") from None


@dataclass(frozen=True)
class LoginRedirect:
    authorization_uri: str = field(repr=False)
    browser_binding: str = field(repr=False)
    max_age_seconds: int = 600

    def cookie_options(self) -> dict[str, str | bool | int]:
        """form_post needs SameSite=None; state + independent binding prevent CSRF."""
        return {
            "key": "__Host-tactiq_oidc", "secure": True, "httponly": True,
            "samesite": "none", "path": "/", "max_age": self.max_age_seconds,
        }


class MicrosoftSignIn:
    """No supplied roles, return URLs, IP-based claiming or raw-token login API.

    Route integration must enforce HTTPS, bound request sizes, exact callback URI,
    no callback query/body logging, and remove the binding cookie after callback.
    The login cookie is independent of guest proof captured server-side at begin.
    """

    def __init__(self, store: AccessStore, provider: OIDCProvider) -> None:
        self._store = store
        self._provider = provider

    def begin(self, guest_token: str | None = None) -> LoginRedirect:
        flow = self._provider.begin()
        binding = secrets.token_urlsafe(32)
        self._store.save_login(binding, flow, guest_token)
        return LoginRedirect(flow["auth_uri"], binding)

    def complete(self, browser_binding: str, response: Mapping[str, str]) -> IssuedSession:
        if (
            not isinstance(response, Mapping) or len(response) > 12
            or any(
                not isinstance(key, str) or not isinstance(value, str)
                or len(key) > 128 or len(value) > 8192
                for key, value in response.items()
            )
        ):
            raise AccessDenied("Invalid sign-in response")
        pending = self._store.consume_login(browser_binding, response.get("state", ""))
        identity = self._provider.complete(pending.flow, response)
        return self._store.sign_in(identity, guest_digest=pending.guest_digest)
