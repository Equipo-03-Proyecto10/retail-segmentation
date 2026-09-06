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
    role_code: str
    name: str
    email: str
    password_hash: str
    is_active: bool


def get_user_by_email(connection: Connection, email: str) -> AppUser | None:
    """Return the app_user row matching email, or None if not found.

    The role's `code` is joined in rather than looked up later: it is what the
    authorization matrix in web/middleware/authz.py is keyed by, and reading it
    here keeps the role id an internal detail of the schema.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT u.user_id, u.role_id, r.code, u.name, u.email,
                   u.password_hash, u.is_active
            FROM app_user AS u
            JOIN role AS r ON r.role_id = u.role_id
            WHERE u.email = %s
            """,
            (email,),
        )
        row = cursor.fetchone()

    if row is None:
        return None

    return AppUser(
        user_id=row[0],
        role_id=row[1],
        role_code=row[2],
        name=row[3],
        email=row[4],
        password_hash=row[5],
        is_active=row[6],
    )
