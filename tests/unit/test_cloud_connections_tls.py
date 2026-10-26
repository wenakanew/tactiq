from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
import ssl

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import pytest

from tactiq.runtime.cloud_connections import BoundedPostgresConnections


@pytest.fixture(scope="module")
def certificate():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "db.tactiq.internal")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode("ascii")


def test_private_ca_is_available_during_handshake_and_removed_afterward(monkeypatch, certificate):
    import psycopg

    paths = []

    def connect(dsn, **kwargs):
        assert "sslmode=verify-full" in dsn
        path = Path(kwargs["sslrootcert"])
        assert path.read_text() == certificate
        paths.append(path)
        return nullcontext(object())

    monkeypatch.setattr(psycopg, "connect", connect)
    gate = BoundedPostgresConnections.from_dsn(
        "host=db.tactiq.internal dbname=tactiq sslmode=verify-full", ca_pem=certificate,
    )
    with gate(), gate():
        assert len(paths) == 2
        assert all(not path.exists() for path in paths)


@pytest.mark.parametrize("dsn,identity", [
    ("host=db.tactiq.internal sslmode=disable", False),
    ("host=db.tactiq.internal sslmode=require", False),
    ("host=db.postgres.database.azure.com sslmode=verify-full", True),
])
def test_private_ca_never_weakens_tls_or_changes_managed_identity(dsn, identity, certificate):
    with pytest.raises(ValueError, match="verify-full"):
        BoundedPostgresConnections.from_dsn(dsn, managed_identity=identity, ca_pem=certificate)


def test_invalid_ca_fails_before_database_connection():
    with pytest.raises(ssl.SSLError):
        BoundedPostgresConnections.from_dsn(
            "host=db.tactiq.internal sslmode=verify-full", ca_pem="not a certificate",
        )
