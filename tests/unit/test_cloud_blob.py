"""SDK-boundary fakes: no Azure account or live Blob integration is exercised."""
from __future__ import annotations

from dataclasses import replace
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tactiq.runtime.blob import CHUNK_BYTES, PrivateBlobStore
from tactiq.runtime.contracts import MediaRef


@pytest.fixture(autouse=True)
def azure_options(monkeypatch):
    # Fake only SDK value classes; real-client APIs were checked against source.
    import sys

    monkeypatch.setitem(sys.modules, "azure.storage.blob", SimpleNamespace(
        ContentSettings=lambda **kwargs: kwargs,
    ))
    monkeypatch.setitem(sys.modules, "azure.core", SimpleNamespace(
        MatchConditions=SimpleNamespace(IfNotModified="IfNotModified"),
    ))


class Container:
    def __init__(self, *, public=None, data=b"abcdef", download_chunks=None):
        self.public = public
        self.data = data
        self.download_chunks = download_chunks
        self.calls = []
        self.keys = []
        self.client = SimpleNamespace(
            upload_blob=self.upload_blob, download_blob=self.download_blob, delete_blob=self.delete_blob,
        )

    def get_container_properties(self, **kwargs):
        self.calls.append(("private", kwargs))
        return {"public_access": self.public}

    def get_blob_client(self, key):
        self.keys.append(key)
        return self.client

    def upload_blob(self, chunks, **kwargs):
        self.calls.append(("upload", kwargs))
        self.data = b"".join(chunks)
        assert len(self.data) == kwargs["length"]
        return {"etag": '"0x123ABC"'}

    def download_blob(self, **kwargs):
        self.calls.append(("download", kwargs))
        chunks = self.download_chunks
        if chunks is None:
            chunks = [self.data[kwargs["offset"]:kwargs["offset"] + kwargs["length"]]]
        return SimpleNamespace(chunks=lambda: iter(chunks))

    def delete_blob(self, **kwargs):
        self.calls.append(("delete", kwargs))


def reference(**kwargs):
    return replace(MediaRef("tenant", "match", uuid4().hex, 6, "video/mp4", '"0x123ABC"'), **kwargs)


def test_upload_is_private_bounded_immutable_and_returns_only_opaque_reference():
    container = Container()
    result = PrivateBlobStore(container).upload("tenant", "match", BytesIO(b"abcdef"), size=6, content_type="video/mp4")
    assert result.size == 6 and result.etag == '"0x123ABC"'
    options = container.calls[-1][1]
    assert not options["overwrite"] and options["max_concurrency"] == 1
    assert options["content_settings"]["cache_control"] == "private, no-store"
    key = container.keys[0]
    assert "tenant" not in key and "match" not in key
    assert key.endswith(result.media_id)
    assert not hasattr(result, "url") and not hasattr(result, "path")


@pytest.mark.parametrize("data,size", [(b"short", 6), (b"toolong", 6), (b"", 6)])
def test_upload_rejects_declared_size_mismatch(data, size):
    container = Container()
    with pytest.raises(ValueError):
        PrivateBlobStore(container).upload("tenant", "match", BytesIO(data), size=size, content_type="video/mp4")


def test_upload_rejects_size_before_any_network_call():
    container = Container()
    with pytest.raises(ValueError):
        PrivateBlobStore(container, max_media_bytes=5).upload(
            "tenant", "match", BytesIO(b"abcdef"), size=6, content_type="video/mp4",
        )
    assert container.calls == []


@pytest.mark.parametrize("public", ["blob", "container", ""])
def test_public_or_ambiguous_container_never_uploads(public):
    container = Container(public=public)
    with pytest.raises(PermissionError):
        PrivateBlobStore(container).upload("tenant", "match", BytesIO(b"a"), size=1, content_type="video/mp4")
    assert not container.keys


def test_range_is_owner_scoped_etag_pinned_and_not_decompressed():
    container = Container()
    result = b"".join(PrivateBlobStore(container).download(reference(), tenant_id="tenant", match_id="match", offset=1, length=3))
    assert result == b"bcd"
    options = container.calls[-1][1]
    assert options["offset"] == 1 and options["length"] == 3
    assert options["etag"] == '"0x123ABC"' and options["match_condition"] == "IfNotModified"
    assert options["decompress"] is False and options["max_concurrency"] == 1


@pytest.mark.parametrize("owner", [("other", "match"), ("tenant", "other")])
def test_download_owner_mismatch_fails_before_sdk(owner):
    container = Container()
    with pytest.raises(PermissionError):
        list(PrivateBlobStore(container).download(reference(), tenant_id=owner[0], match_id=owner[1]))
    assert container.calls == []


@pytest.mark.parametrize("offset,length", [(-1, 1), (6, 1), (0, 7), (0, 0), (True, 1)])
def test_invalid_ranges_fail_without_sdk(offset, length):
    container = Container()
    with pytest.raises(ValueError):
        list(PrivateBlobStore(container).download(reference(), tenant_id="tenant", match_id="match", offset=offset, length=length))
    assert container.calls == []


@pytest.mark.parametrize("chunks", [[b"short"], [b"toolong"], [b"abcdef", b"more"], [b""]])
def test_response_cannot_truncate_or_exceed_trusted_size(chunks):
    with pytest.raises(ValueError):
        list(PrivateBlobStore(Container(download_chunks=chunks)).download(reference(), tenant_id="tenant", match_id="match"))


def test_full_download_streams_bounded_chunks_without_readall():
    container = Container(download_chunks=[b"a" * CHUNK_BYTES, b"b"])
    iterator = PrivateBlobStore(container).download(reference(size=CHUNK_BYTES + 1), tenant_id="tenant", match_id="match")
    assert len(next(iterator)) == CHUNK_BYTES
    assert next(iterator) == b"b"
    with pytest.raises(StopIteration):
        next(iterator)


def test_deadline_is_checked_after_private_container_lookup(monkeypatch):
    ticks = iter([0, 0, 2])
    monkeypatch.setattr("tactiq.runtime.blob.time.monotonic", lambda: next(ticks))
    container = Container()
    with pytest.raises(TimeoutError):
        list(PrivateBlobStore(container, deadline_seconds=1).download(reference(), tenant_id="tenant", match_id="match"))
    assert len(container.calls) == 1 and not container.keys


def test_delete_is_owner_and_etag_scoped():
    container = Container()
    PrivateBlobStore(container).delete(reference(), tenant_id="tenant", match_id="match")
    assert container.calls[-1] == ("delete", {
        "etag": '"0x123ABC"', "match_condition": "IfNotModified", "timeout": 30,
    })


@pytest.mark.parametrize("url", [
    "http://account.blob.core.windows.net",
    "https://account.blob.core.windows.net?sig=secret",
    "https://account.blob.core.windows.net/container",
    "https://user:password@account.blob.core.windows.net",
    "https://evil.example",
])
def test_account_factory_refuses_sas_paths_and_non_azure_endpoints(url):
    with pytest.raises(ValueError):
        PrivateBlobStore.from_account_url(url, "private-media")
