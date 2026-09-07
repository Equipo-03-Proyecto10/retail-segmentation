"""Data access for role — full CRUD."""

from __future__ import annotations

from dataclasses import dataclass

from psycopg import Connection


@dataclass(frozen=True)
class Role:
    role_id: int
    code: str
    description: str | None


def list_roles(
    connection: Connection, *, search: str | None, page: int, per_page: int
) -> tuple[list[Role], int]:
    """Return a page of roles, optionally filtered by code, and the total
    row count for building pagination controls."""
    offset = (page - 1) * per_page

    with connection.cursor() as cursor:
        if search:
            pattern = f"%{search}%"
            cursor.execute(
                """
                SELECT role_id, code, description
                FROM role
                WHERE code ILIKE %s
                ORDER BY role_id
                LIMIT %s OFFSET %s
                """,
                (pattern, per_page, offset),
            )
        else:
            cursor.execute(
                """
                SELECT role_id, code, description
                FROM role
                ORDER BY role_id
                LIMIT %s OFFSET %s
                """,
                (per_page, offset),
            )
        rows = cursor.fetchall()

        if search:
            pattern = f"%{search}%"
            cursor.execute("SELECT count(*) FROM role WHERE code ILIKE %s", (pattern,))
        else:
            cursor.execute("SELECT count(*) FROM role")
        total = cursor.fetchone()[0]

    roles = [Role(*row) for row in rows]
    return roles, total


def get_role(connection: Connection, role_id: int) -> Role | None:
    """Return one role by id, or None if it does not exist."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT role_id, code, description FROM role WHERE role_id = %s", (role_id,)
        )
        row = cursor.fetchone()

    return Role(*row) if row else None


def create_role(
    connection: Connection, *, role_id: int, code: str, description: str | None
) -> None:
    """Create one role; the service owns the transaction."""
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO role (role_id, code, description) VALUES (%s, %s, %s)",
            (role_id, code, description),
        )


def update_role(
    connection: Connection, role_id: int, *, code: str, description: str | None
) -> None:
    """Update one role; the service owns the transaction."""
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE role SET code = %s, description = %s WHERE role_id = %s",
            (code, description, role_id),
        )


def delete_role(connection: Connection, role_id: int) -> None:
    """Delete one role; the service owns the transaction."""
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM role WHERE role_id = %s", (role_id,))
