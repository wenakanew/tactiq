"""One connection admission gate shared by every adapter in a cloud process."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import ssl
from tempfile import TemporaryDirectory
from threading import BoundedSemaphore


class DatabaseCapacityError(RuntimeError):
    """No database connection was opened; retry the same idempotent operation."""


class BoundedPostgresConnections:
    def __init__(self, connect, *, maximum: int = 4):
        if type(maximum) is not int or not 1 <= maximum <= 16:
            raise ValueError("Per-process PostgreSQL connections must be between 1 and 16")
        self._connect = connect
        self.maximum = maximum
        self._admission = BoundedSemaphore(maximum)

    @classmethod
    def from_dsn(
        cls, dsn: str, *, maximum: int = 4, managed_identity: bool = False,
        client_id: str | None = None, ca_pem: str | None = None,
    ):
        from psycopg import connect
        from psycopg.conninfo import conninfo_to_dict
        from psycopg.rows import dict_row

        if ca_pem:
            options = conninfo_to_dict(dsn)
            if managed_identity or options.get("sslmode") != "verify-full":
                raise ValueError("Private database CA requires password authentication with sslmode=verify-full")
            ssl.create_default_context(cadata=ca_pem)

            def connect_with_private_ca():
                with TemporaryDirectory(prefix="tactiq-postgres-ca-") as root:
                    path = Path(root) / "ca.pem"
                    path.write_text(ca_pem, encoding="ascii")
                    path.chmod(0o600)
                    return connect(dsn, sslrootcert=str(path), row_factory=dict_row, connect_timeout=10)

            return cls(connect_with_private_ca, maximum=maximum)
        if managed_identity:
            options = conninfo_to_dict(dsn)
            if (
                options.get("sslmode") != "verify-full"
                or not options.get("host", "").endswith(".postgres.database.azure.com")
                or "," in options["host"] or "hostaddr" in options
                or not options.get("user") or options.get("password") or options.get("passfile")
            ):
                raise ValueError("Managed-identity PostgreSQL requires a password-free Azure DSN with sslmode=verify-full")

            def connect_with_identity():
                from azure.identity import ManagedIdentityCredential

                with ManagedIdentityCredential(client_id=client_id) as credential:
                    token = credential.get_token("https://ossrdbms-aad.database.windows.net/.default").token
                return connect(dsn, password=token, row_factory=dict_row, connect_timeout=10)

            return cls(connect_with_identity, maximum=maximum)
        return cls(lambda: connect(dsn, row_factory=dict_row, connect_timeout=10), maximum=maximum)

    @contextmanager
    def __call__(self):
        # Waiting 100 request threads here would turn a small database into an
        # unbounded queue. Replicas/workers also need approved deployment caps.
        if not self._admission.acquire(blocking=False):
            raise DatabaseCapacityError("PostgreSQL request capacity is full")
        try:
            with self._connect() as connection:
                yield connection
        finally:
            self._admission.release()
