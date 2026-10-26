"""One connection admission gate shared by every adapter in a cloud process."""
from __future__ import annotations

from contextlib import contextmanager
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
    def from_dsn(cls, dsn: str, *, maximum: int = 4):
        from psycopg import connect
        from psycopg.rows import dict_row

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
