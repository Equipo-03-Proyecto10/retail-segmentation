"""Counts the shell reports on the signed-in landing page (F3-12).

Nothing here interprets the numbers; the service decides which of them the
caller is allowed to see, and the template decides how they are shown.
"""

from __future__ import annotations

from typing import Any

from psycopg import Connection

# Whitelisted at the call site rather than taken from a caller: a table name
# cannot be a query parameter, so the only safe way to vary it is to choose
# from a fixed set written here.
_COUNTABLE = {
    "customer": "SELECT count(*) FROM customer",
    "product": "SELECT count(*) FROM product WHERE is_active",
    "store": "SELECT count(*) FROM store WHERE is_active",
    "app_user": "SELECT count(*) FROM app_user WHERE is_active",
}


def count_rows(connection: Connection[Any], entity: str) -> int:
    """Count the rows of one of the entities named in `_COUNTABLE`."""
    try:
        statement = _COUNTABLE[entity]
    except KeyError:  # pragma: no cover - a typo in application code
        raise ValueError(
            f"{entity!r} is not a countable entity; add it to _COUNTABLE "
            f"in web/db/metrics.py if the shell should report it."
        ) from None

    with connection.cursor() as cursor:
        cursor.execute(statement)
        return int(cursor.fetchone()[0])
