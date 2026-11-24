"""Data access for file_access: who asked for a PRIVATE file, and the answer.

Append-only (the web's role holds no UPDATE or DELETE on it). A row never holds
the signed URL or its signature (ADR-0091, F29-14). Write functions leave the
transaction to the caller.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from psycopg import Connection


def insert_file_access(
    connection: Connection[Any],
    *,
    file_id: uuid.UUID,
    actor_user_id: uuid.UUID | str,
    role_code: str,
    outcome: str,
    occurred_at: datetime,
    expires_at: datetime | None,
) -> None:
    """Record one request for a private file: ISSUED with its expiry, or REFUSED."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO file_access
                (file_id, actor_user_id, role_code, outcome, occurred_at, expires_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (file_id, actor_user_id, role_code, outcome, occurred_at, expires_at),
        )
