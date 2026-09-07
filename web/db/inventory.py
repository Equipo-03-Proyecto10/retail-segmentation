"""Data access for inventory — read only (F3-05 / RF-11, HU-11).

Stock is quantity on hand per store and product. The consultation module reads
it; it is never written here.

The filtered queries follow the same rule as web/db/audit.py: the WHERE
fragments are literals written in this file, and only values ever become
parameters — no caller-supplied text reaches the statement.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

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


def _filters(store_id: int | None, search: str | None) -> tuple[str, list[Any]]:
    """Build the WHERE clause shared by the page and count queries."""
    conditions: list[str] = []
    parameters: list[Any] = []

    if store_id is not None:
        conditions.append("i.store_id = %s")
        parameters.append(store_id)
    if search:
        conditions.append("(p.sku ILIKE %s OR p.name ILIKE %s)")
        parameters.extend([f"%{search}%", f"%{search}%"])

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    return where, parameters


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
    where, parameters = _filters(store_id, search)

    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT i.store_id, s.name, i.product_id, p.sku, p.name,
                   i.quantity_on_hand, i.updated_at
            FROM inventory AS i
            JOIN store AS s ON s.store_id = i.store_id
            JOIN product AS p ON p.product_id = i.product_id
            {where}
            ORDER BY i.store_id, i.product_id
            LIMIT %s OFFSET %s
            """,
            [*parameters, per_page, offset],
        )
        rows = cursor.fetchall()

        cursor.execute(
            f"""
            SELECT count(*)
            FROM inventory AS i
            JOIN store AS s ON s.store_id = i.store_id
            JOIN product AS p ON p.product_id = i.product_id
            {where}
            """,
            parameters,
        )
        total = cursor.fetchone()[0]

    return [StockRow(*row) for row in rows], total
