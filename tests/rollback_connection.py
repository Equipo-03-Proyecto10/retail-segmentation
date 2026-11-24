"""A connection that behaves like one but never commits for real.

The web commits and rolls back through `web/db/transactions.py`, and a request
opens its own connection. A test that drives the web against the shared test
database would therefore leave rows behind: products, files and, through the
triggers, outbox events that tests/test_seed_outboxes_postgres.py expects to find
empty. This stand-in is one real connection inside one transaction that is never
committed. `commit()` releases and re-opens a savepoint, `rollback()` returns to
it, and `close()` does nothing, so every request in a test sees what the earlier
ones committed, a refused write undoes only itself, and `discard()` removes
everything the test did.
"""

from __future__ import annotations

from typing import Any

import psycopg

_SAVEPOINT = "test_committed_point"


class RollbackConnection:
    def __init__(self, url: str) -> None:
        self._real = psycopg.connect(url)
        self._real.execute(f"SAVEPOINT {_SAVEPOINT}")

    def commit(self) -> None:
        self._real.execute(f"RELEASE SAVEPOINT {_SAVEPOINT}")
        self._real.execute(f"SAVEPOINT {_SAVEPOINT}")

    def rollback(self) -> None:
        self._real.execute(f"ROLLBACK TO SAVEPOINT {_SAVEPOINT}")

    def close(self) -> None:
        """The application closes its connection when a request ends."""

    @property
    def closed(self) -> bool:
        return False

    def __enter__(self) -> RollbackConnection:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    def connector(self, _url: str) -> RollbackConnection:
        """For `create_app(database_connector=...)`: every call is this one."""
        return self

    def discard(self) -> None:
        """Throw away everything the test wrote."""
        self._real.rollback()
        self._real.close()
