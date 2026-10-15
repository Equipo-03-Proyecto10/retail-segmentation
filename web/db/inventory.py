"""Data access for inventory (F3-05 / RF-11, HU-11).

Stock is quantity on hand per store and product. The consultation module reads
it, and sales ingestion decrements it in the same transaction as the accepted
transaction header and line.

The filtered queries use fixed SQL with nullable bound parameters, following
the same rule as web/db/audit.py: no text is interpolated into a statement.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psycopg import Connection

from web.db.search import ilike_pattern

# HU-11: a quantity below this is shown as needing attention. A presentation
# threshold, not a business rule — tune it here rather than in a template.
LOW_STOCK_THRESHOLD = 20


class StockUnavailable(Exception):
    """A sale cannot consume the requested store/product stock."""

    def __init__(
        self, *, store_id: int, product_id: int, available: int | None
    ) -> None:
        self.store_id = store_id
        self.product_id = product_id
        self.available = available
        super().__init__(
            "inventory row is missing"
            if available is None
            else f"only {available} units are available"
        )


def decrement_stock(
    connection: Connection,
    *,
    store_id: int,
    product_id: int,
    quantity: int,
) -> int:
    """Atomically consume stock, returning the remaining quantity.

    PostgreSQL locks the matching inventory row for the conditional update and
    rechecks the quantity after a concurrent updater commits. If the update
    affects no row, the follow-up lock distinguishes an absent inventory row
    from insufficient stock. The caller owns the surrounding transaction, so
    any later refusal rolls this decrement back with the sale.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE inventory
            SET quantity_on_hand = quantity_on_hand - %s,
                updated_at = now()
            WHERE store_id = %s
              AND product_id = %s
              AND quantity_on_hand >= %s
            RETURNING quantity_on_hand
            """,
            (quantity, store_id, product_id, quantity),
        )
        row = cursor.fetchone()
        if row is not None:
            return row[0]

        cursor.execute(
            """
            SELECT quantity_on_hand
            FROM inventory
            WHERE store_id = %s AND product_id = %s
            FOR UPDATE
            """,
            (store_id, product_id),
        )
        row = cursor.fetchone()

    raise StockUnavailable(
        store_id=store_id,
        product_id=product_id,
        available=None if row is None else row[0],
    )


@dataclass(frozen=True)
class StockRow:
    store_id: int
    store_name: str
    product_id: int
    sku: str
    product_name: str
    quantity_on_hand: int
    updated_at: datetime

    @property
    def is_low(self) -> bool:
        return self.quantity_on_hand < LOW_STOCK_THRESHOLD


def list_stock(
    connection: Connection,
    *,
    store_id: int | None,
    search: str | None,
    page: int,
    per_page: int,
) -> tuple[list[StockRow], int]:
    """Return a page of stock rows joined to their store and product, and the
    total. Optionally narrowed to one store and/or a product SKU/name match."""
    offset = (page - 1) * per_page
    parameters = {
        "store_id": store_id,
        "search": ilike_pattern(search) if search else None,
    }

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT i.store_id, s.name, i.product_id, p.sku, p.name,
                   i.quantity_on_hand, i.updated_at
            FROM inventory AS i
            JOIN store AS s ON s.store_id = i.store_id
            JOIN product AS p ON p.product_id = i.product_id
            WHERE (%(store_id)s::int IS NULL OR i.store_id = %(store_id)s)
              AND (%(search)s::text IS NULL
                   OR p.sku ILIKE %(search)s OR p.name ILIKE %(search)s)
            ORDER BY i.store_id, i.product_id
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            parameters | {"limit": per_page, "offset": offset},
        )
        rows = cursor.fetchall()

        cursor.execute(
            """
            SELECT count(*)
            FROM inventory AS i
            JOIN store AS s ON s.store_id = i.store_id
            JOIN product AS p ON p.product_id = i.product_id
            WHERE (%(store_id)s::int IS NULL OR i.store_id = %(store_id)s)
              AND (%(search)s::text IS NULL
                   OR p.sku ILIKE %(search)s OR p.name ILIKE %(search)s)
            """,
            parameters,
        )
        total = cursor.fetchone()[0]

    return [StockRow(*row) for row in rows], total
