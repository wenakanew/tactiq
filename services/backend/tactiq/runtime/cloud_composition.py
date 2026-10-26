"""Explicit cloud dependency composition. No migrations or provider writes at import."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .access import AccessStore, ClientIPPolicy
from .blob import PrivateBlobStore
from .cloud_connections import BoundedPostgresConnections
from .cloud_store import CloudApplicationStore
from .cloud_generation import CloudGeneration, GenerationSettings
from .contracts import MediaStore
from .identity import MicrosoftOIDCProvider, MicrosoftOIDCSettings, MicrosoftSignIn


@dataclass(frozen=True)
class CloudSettings:
    public_origin: str
    allowed_origins: tuple[str, ...] = ()
    trusted_proxy_cidrs: tuple[str, ...] = ()
    max_upload_bytes: int = 200 * 1024 * 1024
    max_pending: int = 8
    processing_limit_per_day: int | None = None
    database_max_connections: int = 4
    microsoft_redirect_uri: str | None = None
    generation: GenerationSettings = field(default_factory=GenerationSettings)

    def __post_init__(self) -> None:
        for origin in (self.public_origin, *self.allowed_origins):
            parsed = urlsplit(origin)
            if (
                parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path
            ):
                raise ValueError("Cloud browser origins must be exact HTTPS origins without paths")
        ClientIPPolicy(self.trusted_proxy_cidrs)
        if self.microsoft_redirect_uri is not None:
            callback = urlsplit(self.microsoft_redirect_uri)
            if (
                callback.scheme != "https" or not callback.hostname or callback.username or callback.password
                or callback.path != "/api/v1/auth/microsoft/callback" or callback.query or callback.fragment
            ):
                raise ValueError("Microsoft callback must be the fixed HTTPS callback route")
        if not 1 <= self.max_pending <= 100 or not 1 <= self.max_upload_bytes <= 2 * 1024**3:
            raise ValueError("Invalid cloud admission limits")
        if self.processing_limit_per_day is not None and (
            type(self.processing_limit_per_day) is not int or not 1 <= self.processing_limit_per_day <= 10000
        ):
            raise ValueError("An explicit, positive native-processing allocation is required")
        if type(self.database_max_connections) is not int or not 1 <= self.database_max_connections <= 16:
            raise ValueError("Database connections must be bounded between 1 and 16 per process")


@dataclass
class CloudDependencies:
    settings: CloudSettings
    store: CloudApplicationStore
    access: AccessStore
    media: MediaStore
    sign_in: MicrosoftSignIn | None = None
    owned: bool = field(default=False, repr=False)
    generation: CloudGeneration | None = None

    def close(self) -> None:
        if self.owned and callable(getattr(self.media, "close", None)):
            self.media.close()


def compose_cloud(*, include_identity: bool = True) -> CloudDependencies:
    """Called only after explicit selection. Missing configuration fails closed."""
    def required(name: str) -> str:
        value = os.environ.get(name, "").strip()
        if not value:
            raise ValueError(f"{name} is required for cloud runtime")
        return value

    from .postgres_access import PostgresAccessStore

    dsn = required("TACTIQ_POSTGRES_DSN")
    try:
        hmac_key = bytes.fromhex(required("TACTIQ_IP_HMAC_KEY_HEX"))
    except ValueError:
        raise ValueError("TACTIQ_IP_HMAC_KEY_HEX must be at least 32 random bytes encoded as hex") from None
    if len(hmac_key) < 32:
        raise ValueError("TACTIQ_IP_HMAC_KEY_HEX must encode at least 32 random bytes")
    processing_allocation = required("TACTIQ_CLOUD_ANALYSES_PER_DAY")
    settings = CloudSettings(
        public_origin=required("TACTIQ_PUBLIC_ORIGIN"),
        allowed_origins=tuple(filter(None, (item.strip() for item in os.getenv("TACTIQ_CORS_ALLOW_ORIGINS", "").split(",")))),
        trusted_proxy_cidrs=tuple(filter(None, (item.strip() for item in os.getenv("TACTIQ_TRUSTED_PROXY_CIDRS", "").split(",")))),
        max_upload_bytes=int(os.getenv("VIDEO_UPLOAD_MAX_BYTES", str(200 * 1024 * 1024))),
        max_pending=int(os.getenv("TACTIQ_CLOUD_JOB_CAPACITY", "8")),
        processing_limit_per_day=None if processing_allocation == "disabled" else int(processing_allocation),
        database_max_connections=int(os.getenv("TACTIQ_POSTGRES_MAX_CONNECTIONS", "4")),
        microsoft_redirect_uri=os.getenv("TACTIQ_MICROSOFT_REDIRECT_URI") or None,
        generation=GenerationSettings.from_environment(),
    )
    database_auth = os.getenv("TACTIQ_POSTGRES_AUTH", "password").strip()
    if database_auth not in {"password", "managed_identity"}:
        raise ValueError("TACTIQ_POSTGRES_AUTH must be password or managed_identity")
    connections = BoundedPostgresConnections.from_dsn(
        dsn, maximum=settings.database_max_connections,
        managed_identity=database_auth == "managed_identity", client_id=os.getenv("AZURE_CLIENT_ID") or None,
        ca_pem=os.getenv("TACTIQ_POSTGRES_CA_PEM") or None,
    )
    access = PostgresAccessStore(connections, ip_hmac_key=hmac_key, usage_policy=settings.generation.policy)
    sign_in = None
    if include_identity:
        certificate = os.getenv("TACTIQ_MICROSOFT_CLIENT_CERTIFICATE_PEM") or None
        oidc = MicrosoftOIDCSettings(
            required("TACTIQ_MICROSOFT_CLIENT_ID"),
            os.getenv("TACTIQ_MICROSOFT_CLIENT_SECRET", "") if certificate else required("TACTIQ_MICROSOFT_CLIENT_SECRET"),
            required("TACTIQ_MICROSOFT_REDIRECT_URI"),
            audience=os.getenv("TACTIQ_MICROSOFT_AUDIENCE", "common"),
            certificate_pem=certificate,
        )
        sign_in = MicrosoftSignIn(access, MicrosoftOIDCProvider(oidc))
    media = PrivateBlobStore.from_account_url(
        required("TACTIQ_BLOB_ACCOUNT_URL"), required("TACTIQ_BLOB_CONTAINER"),
        client_id=os.getenv("AZURE_CLIENT_ID") or None, max_media_bytes=settings.max_upload_bytes,
    )
    return CloudDependencies(    settings, CloudApplicationStore(connections, max_running=1), access, media, sign_in, owned=True)
