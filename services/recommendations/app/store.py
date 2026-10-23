"""Reads behind a recommendation, from the seven published views only.

recommendations owns no table (ADR-0044, E-07). Every statement names the
`api_*.*_v1` view it reads and the columns it needs, and is parameterized.
Nothing here decides anything: the rule is in rule.py.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from psycopg import Connection

from .rule import Label, Line, Stocked


def customer_exists(connection: Connection[Any], customer_id: str) -> bool:
    row = connection.execute(
        "SELECT customer_id FROM api_customers.customer_ref_v1 WHERE customer_id = %s",
        (customer_id,),
    ).fetchone()
    return row is not None


def current_label(connection: Connection[Any], customer_id: str) -> Label:
    """The customer's open assignment, as segments publishes it."""
    row = connection.execute(
        "SELECT state, label_code, run_id FROM api_segments.current_label_v1"
        " WHERE customer_id = %s",
        (customer_id,),
    ).fetchone()
    if row is None:
        return Label("no_open_assignment", None, None)
    state, label_code, run_id = row
    return Label(state, label_code, run_id)


def purchase_lines(
    connection: Connection[Any], customer_id: str, since: datetime, until: datetime
) -> list[Line]:
    """Every line the customer bought in the window, closed at both ends, with
    the product's own category."""
    rows = connection.execute(
        "SELECT l.transaction_id, l.store_id, l.product_id, p.category_id,"
        " l.quantity, l.unit_price"
        " FROM api_sales.purchase_line_v1 AS l"
        " JOIN api_products.product_ref_v1 AS p ON p.product_id = l.product_id"
        " WHERE l.customer_id = %s AND l.occurred_at >= %s AND l.occurred_at <= %s",
        (customer_id, since, until),
    ).fetchall()
    return [
        Line(txn, store, product, category, quantity, Decimal(price))
        for txn, store, product, category, quantity, price in rows
    ]


def stocked_products(connection: Connection[Any], store_id: int) -> list[Stocked]:
    """Every active product with positive stock in this store, in product order."""
    rows = connection.execute(
        "SELECT s.product_id, p.category_id, s.quantity_on_hand"
        " FROM api_stock.stock_level_v1 AS s"
        " JOIN api_products.product_ref_v1 AS p ON p.product_id = s.product_id"
        " WHERE s.store_id = %s AND s.quantity_on_hand > 0 AND p.is_active"
        " ORDER BY s.product_id",
        (store_id,),
    ).fetchall()
    return [Stocked(*row) for row in rows]


def segment_buyers(
    connection: Connection[Any],
    label_code: str,
    customer_id: str,
    since: datetime,
    until: datetime,
) -> dict[int, int]:
    """For each product, how many other customers whose open label is this one
    bought it in the window. A buyer counts once."""
    rows = connection.execute(
        "SELECT l.product_id, count(DISTINCT l.customer_id)"
        " FROM api_segments.current_label_v1 AS c"
        " JOIN api_sales.purchase_line_v1 AS l ON l.customer_id = c.customer_id"
        " WHERE c.state = 'assigned' AND c.label_code = %s AND c.customer_id <> %s"
        " AND l.occurred_at >= %s AND l.occurred_at <= %s"
        " GROUP BY l.product_id",
        (label_code, customer_id, since, until),
    ).fetchall()
    return {product_id: buyers for product_id, buyers in rows}


def interest_categories(connection: Connection[Any], customer_id: str) -> set[int]:
    rows = connection.execute(
        "SELECT category_id FROM api_preferences.interest_category_v1"
        " WHERE customer_id = %s",
        (customer_id,),
    ).fetchall()
    return {category_id for (category_id,) in rows}


def category_parents(connection: Connection[Any]) -> dict[int, int | None]:
    """The hierarchy, as each category's parent."""
    rows = connection.execute(
        "SELECT category_id, parent_category_id FROM api_categories.category_tree_v1"
    ).fetchall()
    return dict(rows)
