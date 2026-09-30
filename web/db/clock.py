"""Database-backed business time.

PostgreSQL timestamps exposure events and evaluates other date-sensitive data,
so application rules use the same connection and session time zone instead of
the web server's local calendar.
"""

from datetime import date
from typing import Any

from psycopg import Connection


def current_date(connection: Connection[Any]) -> date:
    """Return PostgreSQL's current date in this connection's time zone."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT CURRENT_DATE", ())
        row = cursor.fetchone()
    return row[0]
