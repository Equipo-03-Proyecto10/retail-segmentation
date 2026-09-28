"""Reads behind product recommendations (F10-01).

Read only, and every statement is parameterized. Two things are read here; the rest
of what a recommendation needs is read by the consumption profile (F8-03), so that
"usual store", "categories they buy from" and the open assignment mean the same
thing in both places.

**What a store has in stock.** A product is available when its quantity on hand is
*positive* in the named store and the product is active. Stock in another store does
not count, and neither does a row at zero.

**What the customer's segment bought.** A customer's segment is the customers whose
*open* assignment carries the same label. A customer who has since moved on is not in
the segment any more, so what they bought does not speak for it, and the customer
being recommended to is left out of their own segment's popularity. Only the label is
read: nothing here asks how a run was produced (ADR-0018).

Accepted sales only: ADR-0020 persists a CSV row only once it is accepted, so
`transaction` and `transaction_line` are the accepted sales.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg import Connection


@dataclass(frozen=True)
class StockedProduct:
    """An active product a store has on the shelf, with its category."""

    product_id: int
    name: str
    category_id: int
    category_name: str
    quantity_on_hand: int


def list_stocked_products(
    connection: Connection[Any], store_id: int
) -> list[StockedProduct]:
    """Every active product with stock in this store, in product order."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT p.product_id, p.name, p.category_id, c.name, i.quantity_on_hand
            FROM inventory AS i
            JOIN product AS p ON p.product_id = i.product_id
            JOIN category AS c ON c.category_id = p.category_id
            WHERE i.store_id = %(store_id)s
              AND i.quantity_on_hand > 0
              AND p.is_active
            ORDER BY p.product_id
            """,
            {"store_id": store_id},
        )
        rows = cursor.fetchall()

    return [StockedProduct(*row) for row in rows]


def list_segment_buyers(
    connection: Connection[Any],
    label_code: str,
    customer_id: Any,
    since: datetime,
    until: datetime,
) -> dict[int, int]:
    """For each product, how many *other* customers now in the segment bought it in
    the window. A buyer counts once however many times they bought.

    The window is closed at both ends, like the consumption profile's.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT tl.product_id, count(DISTINCT t.customer_id)
            FROM customer_segment_history AS h
            JOIN transaction AS t ON t.customer_id = h.customer_id
            JOIN transaction_line AS tl ON tl.transaction_id = t.transaction_id
            WHERE h.valid_to IS NULL
              AND h.label_code = %(label_code)s
              AND h.customer_id <> %(customer_id)s
              AND t.occurred_at >= %(since)s
              AND t.occurred_at <= %(until)s
            GROUP BY tl.product_id
            """,
            {
                "label_code": label_code,
                "customer_id": str(customer_id),
                "since": since,
                "until": until,
            },
        )
        rows = cursor.fetchall()

    return {product_id: buyers for product_id, buyers in rows}
