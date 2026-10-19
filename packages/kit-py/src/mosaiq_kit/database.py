"""Small psycopg pool with audit attribution and stable availability errors."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from flask import g, has_request_context
from psycopg_pool import ConnectionPool, PoolTimeout

from .types import ApiError


class Database:
    """The service database pool exposed as ``kit.db``."""

    def __init__(self, dsn: str | None = None):
        self.dsn = dsn or os.environ.get("DATABASE_URL")
        self.timeout = float(os.environ.get("DATABASE_POOL_TIMEOUT_SECONDS", "2"))
        connect_timeout = int(os.environ.get("DATABASE_CONNECT_TIMEOUT_SECONDS", "2"))
        self.pool = (
            ConnectionPool(
                self.dsn,
                min_size=0,
                max_size=3,
                timeout=self.timeout,
                kwargs={"connect_timeout": connect_timeout},
                open=True,
            )
            if self.dsn
            else None
        )

    @staticmethod
    def _unavailable() -> ApiError:
        return ApiError(
            503, "database_unavailable", "The service database is unavailable."
        )

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection]:
        """Check out a connection for reads."""

        if self.pool is None:
            raise self._unavailable()
        try:
            with self.pool.connection(timeout=self.timeout) as connection:
                yield connection
        except (PoolTimeout, psycopg.OperationalError, psycopg.InterfaceError):
            raise self._unavailable() from None

    @contextmanager
    def transaction(self) -> Iterator[psycopg.Connection]:
        """Open a transaction and attach the authenticated caller for auditing."""

        try:
            with self.connection() as connection:
                with connection.transaction():
                    principal = (
                        getattr(g, "principal", None) if has_request_context() else None
                    )
                    if principal:
                        connection.execute(
                            "SELECT set_config('mosaiq.user_id', %s, true)",
                            (str(principal["sub"]),),
                        )
                    yield connection
        except (PoolTimeout, psycopg.OperationalError, psycopg.InterfaceError):
            raise self._unavailable() from None

    def close(self) -> None:
        if self.pool is not None:
            self.pool.close()
