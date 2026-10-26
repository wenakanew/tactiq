"""Real Blob SDK checks against an explicitly enabled loopback emulator only."""
import io
import os
from urllib.parse import urlsplit
from uuid import uuid4

import pytest

from tactiq.runtime.blob import PrivateBlobStore


pytestmark = pytest.mark.skipif(
    os.getenv("TACTIQ_ALLOW_BLOB_EMULATOR_WRITES") != "1",
    reason="Real Blob SDK tests require an explicitly enabled disposable loopback emulator",
)


@pytest.fixture
def blobs():
    from azure.storage.blob import BlobServiceClient

    url = os.environ["TACTIQ_TEST_BLOB_ACCOUNT_URL"]
    parsed = urlsplit(url)
    if (
        parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1"}
        or parsed.username or parsed.password or parsed.query or parsed.fragment
    ):
        pytest.fail("Blob emulator tests must not target a cloud or non-loopback account")
    account = os.environ["TACTIQ_TEST_BLOB_ACCOUNT_NAME"]
    key = os.environ["TACTIQ_TEST_BLOB_ACCOUNT_KEY"]
    with BlobServiceClient(
        url, credential={"account_name": account, "account_key": key},
        api_version="2023-11-03",
        retry_total=0, connection_timeout=5, read_timeout=10,
    ) as service:
        container = service.create_container("tactiq-test-" + uuid4().hex)
        try:
            yield PrivateBlobStore(container), container
        finally:
            container.delete_container()


def test_blob_real_sdk_private_upload_exact_ranges_and_delete(blobs):
    store, container = blobs
    content = b"private-byte-range-fixture" * 10000
    ref = store.upload("tenant_a", "match", io.BytesIO(content), size=len(content), content_type="video/mp4")
    assert b"".join(store.download(ref, tenant_id="tenant_a", match_id="match")) == content
    assert b"".join(store.download(ref, tenant_id="tenant_a", match_id="match", offset=123, length=987)) == content[123:1110]
    assert container.get_container_properties()["public_access"] is None
    store.delete(ref, tenant_id="tenant_a", match_id="match")
    assert list(container.list_blobs()) == []


def test_blob_real_sdk_rejects_another_owner_and_changed_version(blobs):
    from azure.core.exceptions import ResourceModifiedError

    store, container = blobs
    content = b"original"
    ref = store.upload("tenant_a", "match", io.BytesIO(content), size=len(content), content_type="video/mp4")
    with pytest.raises(PermissionError):
        list(store.download(ref, tenant_id="tenant_b", match_id="match"))
    entries = list(container.list_blobs())
    assert len(entries) == 1
    container.get_blob_client(entries[0].name).upload_blob(b"replacement", overwrite=True)
    with pytest.raises(ResourceModifiedError):
        list(store.download(ref, tenant_id="tenant_a", match_id="match"))


def test_blob_real_sdk_mismatched_input_does_not_commit_an_object(blobs):
    store, container = blobs
    with pytest.raises(ValueError):
        store.upload("tenant_a", "match", io.BytesIO(b"too much"), size=1, content_type="video/mp4")
    assert list(container.list_blobs()) == []
