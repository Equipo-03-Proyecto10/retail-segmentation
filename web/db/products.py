"""Data access for product — full CRUD.

image_path is read-only here: uploading and replacing the file is F3-07
(#67), a separate story. This CRUD never writes to that column.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from psycopg import Connection
from psycopg.errors import ForeignKeyViolation, UniqueViolation


@dataclass(frozen=True)
class Product:
    product_id: int
    sku: str
    name: str
    category_id: int
    list_price: Decimal
    image_path: str | None
    is_active: bool


def list_products(
    connection: Connection, *, search: str | None, page: int, per_page: int
) -> tuple[list[Product], int]:
    """Return a page of products, optionally filtered by SKU or name, and
    the total row count for building pagination controls."""
    offset = (page - 1) * per_page
    columns = "product_id, sku, name, category_id, list_price, image_path, is_active"

    with connection.cursor() as cursor:
        if search:
            pattern = f"%{search}%"
            cursor.execute(
                f"""
                SELECT {columns} FROM product
                WHERE sku ILIKE %s OR name ILIKE %s
                ORDER BY product_id
                LIMIT %s OFFSET %s
                """,
                (pattern, pattern, per_page, offset),
            )
        else:
            cursor.execute(
                f"SELECT {columns} FROM product ORDER BY product_id LIMIT %s OFFSET %s",
                (per_page, offset),
            )
        rows = cursor.fetchall()

        if search:
            pattern = f"%{search}%"
            cursor.execute(
                "SELECT count(*) FROM product WHERE sku ILIKE %s OR name ILIKE %s",
                (pattern, pattern),
            )
        else:
            cursor.execute("SELECT count(*) FROM product")
        total = cursor.fetchone()[0]

    products = [Product(*row) for row in rows]
    return products, total


def get_product(connection: Connection, product_id: int) -> Product | None:
    """Return one product by id, or None if it does not exist."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT product_id, sku, name, category_id, list_price, image_path, is_active
            FROM product WHERE product_id = %s
            """,
            (product_id,),
        )
        row = cursor.fetchone()

    return Product(*row) if row else None


def create_product(
    connection: Connection,
    *,
    product_id: int,
    sku: str,
    name: str,
    category_id: int,
    list_price: Decimal,
) -> str | None:
    """Insert a new product. Returns None on success, or an error message on
    a duplicate id/sku or an invalid category_id."""
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO product (product_id, sku, name, category_id, list_price)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (product_id, sku, name, category_id, list_price),
            )
        connection.commit()
        return None
    except UniqueViolation:
        connection.rollback()
        return "A product with that ID or SKU already exists."
    except ForeignKeyViolation:
        connection.rollback()
        return "That category does not exist."


def update_product(
    connection: Connection,
    product_id: int,
    *,
    sku: str,
    name: str,
    category_id: int,
    list_price: Decimal,
    is_active: bool,
) -> str | None:
    """Update a product's editable fields. Returns None on success, or an
    error message on a duplicate SKU or an invalid category_id."""
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE product
                SET sku = %s, name = %s, category_id = %s, list_price = %s, is_active = %s
                WHERE product_id = %s
                """,
                (sku, name, category_id, list_price, is_active, product_id),
            )
        connection.commit()
        return None
    except UniqueViolation:
        connection.rollback()
        return "A product with that SKU already exists."
    except ForeignKeyViolation:
        connection.rollback()
        return "That category does not exist."


def delete_product(connection: Connection, product_id: int) -> bool:
    """Delete a product. Returns True on success, False if referenced by
    other rows (transaction_line, inventory)."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM product WHERE product_id = %s", (product_id,))
        connection.commit()
        return True
    except ForeignKeyViolation:
        connection.rollback()
        return False