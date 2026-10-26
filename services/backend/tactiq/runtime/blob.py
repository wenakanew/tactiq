"""Private managed-identity Blob media, with no persisted URLs, SAS or raw paths.

References must come from the owner's trusted metadata registry, not request
JSON. The caller authorizes workspace membership before invoking this adapter.
Deadlines are checked between SDK operations/chunks; blocking network calls
also have bounded transport timeouts, not a hard process-level wall-clock kill.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Iterator
from typing import Any, BinaryIO
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from .contracts import MediaRef, bounded_integer, cloud_identifier

CHUNK_BYTES = 4 * 1024 * 1024
MAX_MEDIA_BYTES = 2 * 1024 * 1024 * 1024
CONTENT_TYPES = frozenset({
    "video/mp4", "video/webm", "video/x-matroska",
    "audio/mpeg", "audio/wav", "audio/ogg", "application/octet-stream",
})


def validate_reference(reference: MediaRef) -> None:
    cloud_identifier(reference.tenant_id, "tenant_id")
    cloud_identifier(reference.match_id, "match_id")
    if UUID(reference.media_id).hex != reference.media_id:
        raise ValueError("media_id must be a canonical lowercase UUID hex")
    bounded_integer(reference.size, "media size", 1, MAX_MEDIA_BYTES)
    if reference.content_type not in CONTENT_TYPES:
        raise ValueError("Unsupported media content type")
    if (
        not isinstance(reference.etag, str) or not 1 <= len(reference.etag) <= 256
        or not re.fullmatch(r'"?[A-Za-z0-9_-]+"?', reference.etag)
    ):
        raise ValueError("A bounded Blob ETag is required")


def _key(tenant_id: str, match_id: str, media_id: str) -> str:
    cloud_identifier(tenant_id, "tenant_id")
    cloud_identifier(match_id, "match_id")
    if UUID(media_id).hex != media_id:
        raise ValueError("Invalid media ID")
    tenant = hashlib.sha256(tenant_id.encode("ascii")).hexdigest()
    match = hashlib.sha256(match_id.encode("ascii")).hexdigest()
    return f"v1/{tenant}/{match}/{media_id}"


class PrivateBlobStore:
    def __init__(
        self, container: Any, *, max_media_bytes: int = MAX_MEDIA_BYTES,
        deadline_seconds: int = 300,
    ) -> None:
        bounded_integer(max_media_bytes, "max_media_bytes", 1, MAX_MEDIA_BYTES)
        bounded_integer(deadline_seconds, "deadline_seconds", 1, 3600)
        self.container = container
        self.max_media_bytes = max_media_bytes
        self.deadline_seconds = deadline_seconds
        self._owned_clients: tuple[Any, ...] = ()

    @classmethod
    def from_account_url(
        cls, account_url: str, container_name: str, *, client_id: str | None = None,
        max_media_bytes: int = MAX_MEDIA_BYTES, deadline_seconds: int = 300,
    ) -> PrivateBlobStore:
        """No provisioning. Uses managed identity only, not shared keys or SAS."""
        parsed = urlsplit(account_url)
        if (
            parsed.scheme != "https" or parsed.username or parsed.password or parsed.port
            or parsed.query or parsed.fragment or parsed.path not in ("", "/")
            or not re.fullmatch(
                r"[a-z0-9]{3,24}\.blob\.core\.(windows\.net|usgovcloudapi\.net|chinacloudapi\.cn)",
                parsed.hostname or "",
            )
        ):
            raise ValueError("An HTTPS Azure Blob account endpoint without credentials is required")
        if (
            not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", container_name)
            or "--" in container_name
        ):
            raise ValueError("Invalid private container name")
        from azure.identity import ManagedIdentityCredential
        from azure.storage.blob import BlobServiceClient

        credential = ManagedIdentityCredential(client_id=client_id) if client_id else ManagedIdentityCredential()
        service = BlobServiceClient(
            account_url=account_url, credential=credential, retry_total=2,
            connection_timeout=10, read_timeout=30,
            max_single_put_size=CHUNK_BYTES, max_block_size=CHUNK_BYTES,
            max_single_get_size=CHUNK_BYTES, max_chunk_get_size=CHUNK_BYTES,
        )
        store = cls(
            service.get_container_client(container_name),
            max_media_bytes=max_media_bytes, deadline_seconds=deadline_seconds,
        )
        store._owned_clients = (service, credential)
        return store

    def close(self) -> None:
        for client in self._owned_clients:
            client.close()
        self._owned_clients = ()

    def healthcheck(self) -> None:
        """Read-only startup verification of an existing private container."""
        self._private(time.monotonic() + self.deadline_seconds)

    def _deadline(self, deadline: float) -> None:
        if time.monotonic() >= deadline:
            raise TimeoutError("Media transfer deadline exceeded")

    def _private(self, deadline: float) -> None:
        self._deadline(deadline)
        properties = self.container.get_container_properties(timeout=30)
        self._deadline(deadline)
        if "public_access" not in properties or properties["public_access"] is not None:
            raise PermissionError("Media container must have anonymous access disabled")

    @staticmethod
    def _owned(reference: MediaRef, tenant_id: str, match_id: str) -> None:
        validate_reference(reference)
        if (reference.tenant_id, reference.match_id) != (tenant_id, match_id):
            raise PermissionError("Media is unavailable to this owner")

    def upload(
        self, tenant_id: str, match_id: str, source: BinaryIO, *,
        size: int, content_type: str,
    ) -> MediaRef:
        """Consumes exactly size bytes and refuses an extra byte before commit.

        Returns a reference for subsequent transactional metadata registration.
        A crash between upload and registration can leave an orphan; lifecycle
        reconciliation is a separate deployment requirement.
        """
        cloud_identifier(tenant_id, "tenant_id")
        cloud_identifier(match_id, "match_id")
        bounded_integer(size, "media size", 1, self.max_media_bytes)
        if content_type not in CONTENT_TYPES:
            raise ValueError("Unsupported media content type")
        deadline = time.monotonic() + self.deadline_seconds
        self._private(deadline)
        media_id = uuid4().hex
        client = self.container.get_blob_client(_key(tenant_id, match_id, media_id))
        consumed = 0

        def chunks() -> Iterator[bytes]:
            nonlocal consumed
            while consumed < size:
                self._deadline(deadline)
                chunk = source.read(min(CHUNK_BYTES, size - consumed))
                if not isinstance(chunk, bytes) or not chunk or len(chunk) > min(CHUNK_BYTES, size - consumed):
                    raise ValueError("Upload stream does not match its declared size")
                consumed += len(chunk)
                if consumed == size:
                    extra = source.read(1)
                    if extra != b"":
                        raise ValueError("Upload stream exceeds its declared size")
                self._deadline(deadline)
                yield chunk

        from azure.storage.blob import ContentSettings

        response = client.upload_blob(
            chunks(), length=size, blob_type="BlockBlob", overwrite=False, max_concurrency=1,
            content_settings=ContentSettings(content_type=content_type, cache_control="private, no-store"),
            timeout=30, progress_hook=lambda *_: self._deadline(deadline),
        )
        self._deadline(deadline)
        if consumed != size:
            raise ValueError("Upload did not consume its declared size")
        reference = MediaRef(tenant_id, match_id, media_id, size, content_type, response["etag"])
        validate_reference(reference)
        return reference

    def download(
        self, reference: MediaRef, *, tenant_id: str, match_id: str,
        offset: int = 0, length: int | None = None,
    ) -> Iterator[bytes]:
        """Stream an owned full blob or a single validated byte range.

        HTTP Range parsing/status/content-range headers belong to the API.
        ETag conditions pin all ranged SDK requests to the registered object.
        """
        self._owned(reference, tenant_id, match_id)
        bounded_integer(reference.size, "media size", 1, self.max_media_bytes)
        bounded_integer(offset, "offset", 0, reference.size - 1)
        count = reference.size - offset if length is None else length
        bounded_integer(count, "length", 1, reference.size - offset)
        deadline = time.monotonic() + self.deadline_seconds
        self._private(deadline)
        client = self.container.get_blob_client(_key(tenant_id, match_id, reference.media_id))
        from azure.core import MatchConditions

        downloader = client.download_blob(
            offset=offset, length=count, etag=reference.etag,
            match_condition=MatchConditions.IfNotModified,
            max_concurrency=1, decompress=False, timeout=30,
        )
        received = 0
        for chunk in downloader.chunks():
            self._deadline(deadline)
            if not isinstance(chunk, bytes) or not chunk or len(chunk) > CHUNK_BYTES:
                raise ValueError("Invalid bounded media response")
            received += len(chunk)
            if received > count:
                raise ValueError("Media response exceeds requested range")
            yield chunk
        self._deadline(deadline)
        if received != count:
            raise ValueError("Media response was truncated")

    def delete(self, reference: MediaRef, *, tenant_id: str, match_id: str) -> None:
        """Delete only the registered generation; soft-delete retention is external."""
        self._owned(reference, tenant_id, match_id)
        deadline = time.monotonic() + self.deadline_seconds
        self._private(deadline)
        from azure.core import MatchConditions

        self.container.get_blob_client(_key(tenant_id, match_id, reference.media_id)).delete_blob(
            etag=reference.etag, match_condition=MatchConditions.IfNotModified, timeout=30,
        )
        self._deadline(deadline)
