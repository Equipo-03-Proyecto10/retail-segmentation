"""Data access for app_user — authentication reads only.

Follows the same parameterized-query rule as the rest of web/db: no SQL
string interpolation, ever.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from psycopg import Connection


@dataclass(frozen=True)
class AppUser:
    user_id: UUID
    role_id: int
    name: str
    email: str
    password_hash: str
    is_active: bool


def get_user_by_email(connection: Connection, email: str) -> AppUser | None:
    """Return the app_user row matching email, or None if not found."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT user_id, role_id, name, email, password_hash, is_active
            FROM app_user
            WHERE email = %s
            """,
            (email,),
        )
        row = cursor.fetchone()

    if row is None:
        return None

    return AppUser(
        user_id=row[0],
        role_id=row[1],
        name=row[2],
        email=row[3],
        password_hash=row[4],
        is_active=row[5],
    )
