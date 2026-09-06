"""Data access for app_user — authentication reads only.

Follows the same parameterized-query rule as the rest of web/db: no SQL
string interpolation, ever.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from psycopg import Connection

# The role that RN-01 allows exactly one of. Named here rather than repeated as
# a string literal, and matched against the schema's partial unique index by
# tests/test_single_administrator.py.
ADMINISTRATOR_ROLE_CODE = "ADMIN"


@dataclass(frozen=True)
class AppUser:
    user_id: UUID
    role_id: int
    role_code: str
    role_description: str | None
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
            SELECT u.user_id, u.role_id, r.code, r.description, u.name,
                   u.email, u.password_hash, u.is_active
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
        role_description=row[3],
        name=row[4],
        email=row[5],
        password_hash=row[6],
        is_active=row[7],
    )


def get_user_by_id(connection: Connection, user_id: UUID | str) -> AppUser | None:
    """Return the app_user row with this id, or None if there is none."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT u.user_id, u.role_id, r.code, r.description, u.name,
                   u.email, u.password_hash, u.is_active
            FROM app_user AS u
            JOIN role AS r ON r.role_id = u.role_id
            WHERE u.user_id = %s
            """,
            (str(user_id),),
        )
        row = cursor.fetchone()

    if row is None:
        return None

    return AppUser(
        user_id=row[0],
        role_id=row[1],
        role_code=row[2],
        role_description=row[3],
        name=row[4],
        email=row[5],
        password_hash=row[6],
        is_active=row[7],
    )


def get_role_id_by_code(connection: Connection, role_code: str) -> int | None:
    """Translate a role code into its id, or None when no such role exists."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT role_id FROM role WHERE code = %s", (role_code,))
        row = cursor.fetchone()

    return None if row is None else row[0]


def count_administrators(connection: Connection) -> int:
    """How many users hold the administrator role.

    Counts every administrator, active or not: a deactivated administrator
    still occupies the single seat the partial unique index reserves, and
    treating them as absent would let the application invite a refusal from
    the database it could have explained itself.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT count(*)
            FROM app_user AS u
            JOIN role AS r ON r.role_id = u.role_id
            WHERE r.code = %s
            """,
            (ADMINISTRATOR_ROLE_CODE,),
        )
        return int(cursor.fetchone()[0])


def insert_user(
    connection: Connection,
    *,
    role_id: int,
    name: str,
    email: str,
    password_hash: str,
) -> UUID:
    """Insert a user and return the id the database generated."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO app_user (role_id, name, email, password_hash)
            VALUES (%s, %s, %s, %s)
            RETURNING user_id
            """,
            (role_id, name, email, password_hash),
        )
        return cursor.fetchone()[0]


def update_role(connection: Connection, user_id: UUID | str, role_id: int) -> None:
    """Move a user to another role."""
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE app_user SET role_id = %s WHERE user_id = %s",
            (role_id, str(user_id)),
        )


def update_active(connection: Connection, user_id: UUID | str, is_active: bool) -> None:
    """Activate or deactivate a user. Deletion is never how access is removed."""
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE app_user SET is_active = %s WHERE user_id = %s",
            (is_active, str(user_id)),
        )


DEMONSTRATION_EMAIL_DOMAIN = "@mosaiq-demo.com"


def deactivate_demonstration_accounts(connection: Connection) -> list[str]:
    """Deactivate every seeded demonstration account, and say which.

    Deactivation, not deletion: RN-04 keeps a user who has history so the
    history keeps its actor, and `audit_log.user_id` points at these rows.
    `authenticate` refuses an inactive account, so the published password stops
    opening anything while the log stays readable.

    The administrator is never touched — F4-06 moves the role to a real account
    before this runs, and refusing here as well means the procedure cannot
    leave the instance with nobody able to sign in.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE app_user AS u
               SET is_active = FALSE
              FROM role AS r
             WHERE r.role_id = u.role_id
               AND u.is_active
               AND r.code <> %s
               AND u.email LIKE %s
            RETURNING u.email
            """,
            (ADMINISTRATOR_ROLE_CODE, f"%{DEMONSTRATION_EMAIL_DOMAIN}"),
        )
        return sorted(row[0] for row in cursor.fetchall())


def count_active_demonstration_accounts(connection: Connection) -> int:
    """How many seeded accounts can still be signed in to."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT count(*)
            FROM app_user
            WHERE is_active AND email LIKE %s
            """,
            (f"%{DEMONSTRATION_EMAIL_DOMAIN}",),
        )
        return int(cursor.fetchone()[0])


def list_users(
    connection: Connection, *, search: str | None, page: int, per_page: int
) -> tuple[list[AppUser], int]:
    """Return a page of users, optionally filtered by name or email, and the
    total row count for building pagination controls.

    Ordered by email so the listing is stable across pages regardless of when
    each account was created.
    """
    offset = (page - 1) * per_page
    columns = (
        "u.user_id, u.role_id, r.code, r.description, u.name, u.email, "
        "u.password_hash, u.is_active"
    )

    with connection.cursor() as cursor:
        if search:
            pattern = f"%{search}%"
            cursor.execute(
                f"""
                SELECT {columns}
                FROM app_user AS u
                JOIN role AS r ON r.role_id = u.role_id
                WHERE u.name ILIKE %s OR u.email ILIKE %s
                ORDER BY u.email
                LIMIT %s OFFSET %s
                """,
                (pattern, pattern, per_page, offset),
            )
        else:
            cursor.execute(
                f"""
                SELECT {columns}
                FROM app_user AS u
                JOIN role AS r ON r.role_id = u.role_id
                ORDER BY u.email
                LIMIT %s OFFSET %s
                """,
                (per_page, offset),
            )
        rows = cursor.fetchall()

        if search:
            pattern = f"%{search}%"
            cursor.execute(
                "SELECT count(*) FROM app_user WHERE name ILIKE %s OR email ILIKE %s",
                (pattern, pattern),
            )
        else:
            cursor.execute("SELECT count(*) FROM app_user")
        total = cursor.fetchone()[0]

    users = [
        AppUser(
            user_id=row[0],
            role_id=row[1],
            role_code=row[2],
            role_description=row[3],
            name=row[4],
            email=row[5],
            password_hash=row[6],
            is_active=row[7],
        )
        for row in rows
    ]
    return users, total


def list_role_options(connection: Connection) -> list[tuple[str, str]]:
    """Return (code, description) for every role, for the role dropdown."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT code, description FROM role ORDER BY code")
        return [(row[0], row[1]) for row in cursor.fetchall()]
