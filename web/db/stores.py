"""Data access for store — full CRUD.

Follows the same parameterized-query rule as the rest of web/db: no SQL
string interpolation, ever.
"""

from __future__ import annotations

from dataclasses import dataclass

from psycopg import Connection


@dataclass(frozen=True)
class Store:
    store_id: int
    name: str
    city: str
    state: str
    is_active: bool


def list_stores(
    connection: Connection, *, search: str | None, page: int, per_page: int
) -> tuple[list[Store], int]:
    """Return a page of stores, optionally filtered by name/city/state, and
    the total row count for building pagination controls."""
    offset = (page - 1) * per_page

    with connection.cursor() as cursor:
        if search:
            pattern = f"%{search}%"
            cursor.execute(
                """
                SELECT store_id, name, city, state, is_active
                FROM store
                WHERE name ILIKE %s OR city ILIKE %s OR state ILIKE %s
                ORDER BY store_id
                LIMIT %s OFFSET %s
                """,
                (pattern, pattern, pattern, per_page, offset),
            )
        else:
            cursor.execute(
                """
                SELECT store_id, name, city, state, is_active
                FROM store
                ORDER BY store_id
                LIMIT %s OFFSET %s
                """,
                (per_page, offset),
            )
        rows = cursor.fetchall()

        if search:
            pattern = f"%{search}%"
            cursor.execute(
                """
                SELECT count(*) FROM store
                WHERE name ILIKE %s OR city ILIKE %s OR state ILIKE %s
                """,
                (pattern, pattern, pattern),
            )
        else:
            cursor.execute("SELECT count(*) FROM store")
        total = cursor.fetchone()[0]

    stores = [Store(*row) for row in rows]
    return stores, total


def list_all_stores(connection: Connection) -> list[Store]:
    """Return every store, unpaginated. Used to populate the store filter on
    the stock consultation view (F3-05)."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT store_id, name, city, state, is_active
            FROM store
            ORDER BY name
            """
        )
        rows = cursor.fetchall()

    return [Store(*row) for row in rows]


def get_store(connection: Connection, store_id: int) -> Store | None:
    """Return one store by id, or None if it does not exist."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT store_id, name, city, state, is_active
            FROM store
            WHERE store_id = %s
            """,
            (store_id,),
        )
        row = cursor.fetchone()

    return Store(*row) if row else None


def create_store(
    connection: Connection, *, store_id: int, name: str, city: str, state: str
) -> None:
    """Create one store; the service owns the transaction."""
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO store (store_id, name, city, state) "
            "VALUES (%s, %s, %s, %s)",
            (store_id, name, city, state),
        )


def update_store(
    connection: Connection,
    store_id: int,
    *,
    name: str,
    city: str,
    state: str,
    is_active: bool,
) -> None:
    """Update one store; the service owns the transaction."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE store
            SET name = %s, city = %s, state = %s, is_active = %s
            WHERE store_id = %s
            """,
            (name, city, state, is_active, store_id),
        )


def delete_store(connection: Connection, store_id: int) -> None:
    """Delete one store; the service owns the transaction."""
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM store WHERE store_id = %s", (store_id,))
