"""Data access for inventory — read only (F3-05 / RF-11, HU-11).

Stock is quantity on hand per store and product. The consultation module reads
it; it is never written here.

The filtered queries use fixed SQL with nullable bound parameters, following
the same rule as web/db/audit.py: no text is interpolated into a statement.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psycopg import Connection

# HU-11: a quantity below this is shown as needing attention. A presentation
# threshold, not a business rule — tune it here rather than in a template.
LOW_STOCK_THRESHOLD = 20


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
    parameters = {"store_id": store_id, "search": f"%{search}%" if search else None}

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
