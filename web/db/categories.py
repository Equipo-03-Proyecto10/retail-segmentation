"""Data access for category — full CRUD.

category is self-referencing (parent_category_id), so deletion can be refused
for two independent reasons: products that reference it, or child categories
that reference it as their parent (RN-06, RN-07).
"""

from __future__ import annotations

from dataclasses import dataclass

from psycopg import Connection


@dataclass(frozen=True)
class Category:
    category_id: int
    name: str
    parent_category_id: int | None


def list_categories(
    connection: Connection, *, search: str | None, page: int, per_page: int
) -> tuple[list[Category], int]:
    """Return a page of categories, optionally filtered by name, and the
    total row count for building pagination controls."""
    offset = (page - 1) * per_page

    with connection.cursor() as cursor:
        if search:
            pattern = f"%{search}%"
            cursor.execute(
                """
                SELECT category_id, name, parent_category_id
                FROM category
                WHERE name ILIKE %s
                ORDER BY category_id
                LIMIT %s OFFSET %s
                """,
                (pattern, per_page, offset),
            )
        else:
            cursor.execute(
                """
                SELECT category_id, name, parent_category_id
                FROM category
                ORDER BY category_id
                LIMIT %s OFFSET %s
                """,
                (per_page, offset),
            )
        rows = cursor.fetchall()

        if search:
            pattern = f"%{search}%"
            cursor.execute(
                "SELECT count(*) FROM category WHERE name ILIKE %s", (pattern,)
            )
        else:
            cursor.execute("SELECT count(*) FROM category")
        total = cursor.fetchone()[0]

    categories = [Category(*row) for row in rows]
    return categories, total


def list_all_categories(connection: Connection) -> list[Category]:
    """Return every category, unpaginated. Used to populate the parent-category
    dropdown in the form."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT category_id, name, parent_category_id FROM category ORDER BY name"
        )
        rows = cursor.fetchall()

    return [Category(*row) for row in rows]


def get_category(connection: Connection, category_id: int) -> Category | None:
    """Return one category by id, or None if it does not exist."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT category_id, name, parent_category_id
            FROM category
            WHERE category_id = %s
            """,
            (category_id,),
        )
        row = cursor.fetchone()

    return Category(*row) if row else None


def create_category(
    connection: Connection,
    *,
    category_id: int,
    name: str,
    parent_category_id: int | None,
) -> None:
    """Create one category; the service owns the transaction."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO category (category_id, name, parent_category_id)
            VALUES (%s, %s, %s)
            """,
            (category_id, name, parent_category_id),
        )


def is_in_subtree(connection: Connection, root_id: int, candidate_id: int) -> bool:
    """Whether candidate_id is root_id itself or one of its descendants.

    Moving root_id under such a candidate would close a cycle (RN-33). UNION,
    not UNION ALL, so the walk still ends if a cycle is somehow already stored.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            WITH RECURSIVE subtree(category_id) AS (
                SELECT %s::smallint
                UNION
                SELECT c.category_id
                FROM category AS c
                JOIN subtree AS s ON c.parent_category_id = s.category_id
            )
            SELECT EXISTS (SELECT 1 FROM subtree WHERE category_id = %s)
            """,
            (root_id, candidate_id),
        )
        return bool(cursor.fetchone()[0])


def update_category(
    connection: Connection,
    category_id: int,
    *,
    name: str,
    parent_category_id: int | None,
) -> None:
    """Update one category; the service owns the transaction."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE category
            SET name = %s, parent_category_id = %s
            WHERE category_id = %s
            """,
            (name, parent_category_id, category_id),
        )


def delete_category(connection: Connection, category_id: int) -> None:
    """Delete one category; the service owns the transaction."""
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM category WHERE category_id = %s", (category_id,))
