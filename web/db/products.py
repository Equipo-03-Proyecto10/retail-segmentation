"""Data access for product — full CRUD.

Product fields and an optional image (its public name and its stored file) are
committed together.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from psycopg import Connection

from web.db.search import ilike_pattern


@dataclass(frozen=True)
class Product:
    product_id: int
    sku: str
    name: str
    category_id: int
    list_price: Decimal
    image_path: str | None
    is_active: bool
    category_name: str = ""
    image_file_id: uuid.UUID | None = None


@dataclass(frozen=True)
class ProductImage:
    """The picture a product holds: its public name and its stored file."""

    image_path: str | None
    image_file_id: uuid.UUID | None


def list_products(
    connection: Connection, *, search: str | None, page: int, per_page: int
) -> tuple[list[Product], int]:
    """Return a page of products, optionally filtered by SKU or name, and
    the total row count for building pagination controls."""
    offset = (page - 1) * per_page

    with connection.cursor() as cursor:
        if search:
            pattern = ilike_pattern(search)
            cursor.execute(
                """
                SELECT p.product_id, p.sku, p.name, p.category_id, p.list_price,
                       p.image_path, p.is_active, c.name, p.image_file_id
                FROM product AS p
                JOIN category AS c ON c.category_id = p.category_id
                WHERE p.sku ILIKE %s OR p.name ILIKE %s
                ORDER BY p.product_id
                LIMIT %s OFFSET %s
                """,
                (pattern, pattern, per_page, offset),
            )
        else:
            cursor.execute(
                """
                SELECT p.product_id, p.sku, p.name, p.category_id, p.list_price,
                       p.image_path, p.is_active, c.name, p.image_file_id
                FROM product AS p
                JOIN category AS c ON c.category_id = p.category_id
                ORDER BY p.product_id LIMIT %s OFFSET %s
                """,
                (per_page, offset),
            )
        rows = cursor.fetchall()

        if search:
            pattern = ilike_pattern(search)
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
            SELECT product_id, sku, name, category_id, list_price, image_path,
                   is_active, image_file_id
            FROM product WHERE product_id = %s
            """,
            (product_id,),
        )
        row = cursor.fetchone()

    if row is None:
        return None
    *fields, image_file_id = row
    return Product(*fields, image_file_id=image_file_id)


def create_product(
    connection: Connection,
    *,
    product_id: int,
    sku: str,
    name: str,
    category_id: int,
    list_price: Decimal,
    image_path: str | None = None,
    image_file_id: uuid.UUID | None = None,
) -> None:
    """Create one product; the service owns the transaction."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO product
                (product_id, sku, name, category_id, list_price, image_path,
                 image_file_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                product_id,
                sku,
                name,
                category_id,
                list_price,
                image_path,
                image_file_id,
            ),
        )


def update_product(
    connection: Connection,
    product_id: int,
    *,
    sku: str,
    name: str,
    category_id: int,
    list_price: Decimal,
    is_active: bool,
    image_path: str | None = None,
    image_file_id: uuid.UUID | None = None,
) -> None:
    """Update one product; the service owns the transaction.

    A new image replaces both columns together; with none, both stay as they
    are."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE product
            SET sku = %s,
                name = %s,
                category_id = %s,
                list_price = %s,
                is_active = %s,
                image_path = COALESCE(%s, image_path),
                image_file_id = COALESCE(%s::uuid, image_file_id)
            WHERE product_id = %s
            """,
            (
                sku,
                name,
                category_id,
                list_price,
                is_active,
                image_path,
                image_file_id,
                product_id,
            ),
        )


def lock_product_image(connection: Connection, product_id: int) -> ProductImage | None:
    """Lock one product's row and return the picture it has *now*, or None if it
    is gone. A write that replaces a picture reads it here, inside its own
    transaction, so what it retires afterwards is what it really displaced and not
    what the page read earlier (two edits, or an edit and a delete, can race)."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT image_path, image_file_id FROM product "
            "WHERE product_id = %s FOR UPDATE",
            (product_id,),
        )
        row = cursor.fetchone()
    return ProductImage(*row) if row else None


def delete_product(connection: Connection, product_id: int) -> ProductImage | None:
    """Delete one product and return the picture it had; the service owns the
    transaction. None if there was no such product."""
    with connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM product WHERE product_id = %s "
            "RETURNING image_path, image_file_id",
            (product_id,),
        )
        row = cursor.fetchone()
    return ProductImage(*row) if row else None
