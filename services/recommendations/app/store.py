"""Reads behind a recommendation, from published views only.

recommendations reads no other boundary's table (ADR-0044, E-07). Every
statement names the `api_*` view it reads and the columns it needs, and is
parameterized. `/v1` reads nine views; `/v2` also reads the availability, the
substitute relations, the store directory, the channels, the caller's store
assignments and the run's model version. Nothing here decides anything: the
rules are in rule.py and rule_v2.py.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from psycopg import Connection

from .rule import Label, Line, Stocked
from .rule_v2 import Here, Product, Store


def customer_exists(connection: Connection[Any], customer_id: str) -> bool:
    row = connection.execute(
        "SELECT customer_id FROM api_customers.customer_ref_v1 WHERE customer_id = %s",
        (customer_id,),
    ).fetchone()
    return row is not None


def personalization_granted(connection: Connection[Any], customer_id: str) -> bool:
    """Only an explicit GRANTED counts; no row is not granted (ADR-0066)."""
    row = connection.execute(
        "SELECT decision FROM api_preferences.consent_state_v1"
        " WHERE customer_id = %s AND purpose = 'PERSONALIZATION'",
        (customer_id,),
    ).fetchone()
    return row is not None and row[0] == "GRANTED"


def restricted_categories(
    connection: Connection[Any], customer_id: str
) -> frozenset[int]:
    """The categories the customer excluded. The subtree is resolved by the
    rule, from the category tree."""
    rows = connection.execute(
        "SELECT target_id FROM api_preferences.restriction_v1"
        " WHERE customer_id = %s AND kind = 'INTEREST_CATEGORY'",
        (customer_id,),
    ).fetchall()
    return frozenset(int(target) for (target,) in rows)


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


# ---------- /v2 (ADR-0067, ADR-0084) ----------


def stores(connection: Connection[Any]) -> dict[int, Store]:
    """Every store, with the facts that say whether it sells and where it is."""
    rows = connection.execute(
        "SELECT store_id, is_active, status, channel_ids, latitude, longitude"
        " FROM api_stores.store_directory_v2"
    ).fetchall()
    return {
        store_id: Store(
            store_id,
            active,
            status,
            frozenset(channels),
            None if latitude is None else float(latitude),
            None if longitude is None else float(longitude),
        )
        for store_id, active, status, channels, latitude, longitude in rows
    }


def store(connection: Connection[Any], store_id: int) -> Store | None:
    """One store of the directory, or None when it does not exist."""
    row = connection.execute(
        "SELECT store_id, is_active, status, channel_ids, latitude, longitude"
        " FROM api_stores.store_directory_v2 WHERE store_id = %s",
        (store_id,),
    ).fetchone()
    if row is None:
        return None
    store_id, active, status, channels, latitude, longitude = row
    return Store(
        store_id,
        active,
        status,
        frozenset(channels),
        None if latitude is None else float(latitude),
        None if longitude is None else float(longitude),
    )


def channel_exists(connection: Connection[Any], channel_id: int) -> bool:
    row = connection.execute(
        "SELECT channel_id FROM api_sales.channel_v1 WHERE channel_id = %s",
        (channel_id,),
    ).fetchone()
    return row is not None


def assigned_stores(connection: Connection[Any], user_id: str) -> frozenset[int]:
    """The caller's stores as of this statement (ADR-0060): read on every
    request that names a store, and kept nowhere."""
    rows = connection.execute(
        "SELECT store_id FROM api_users.store_assignment_v1 WHERE user_id = %s::uuid",
        (user_id,),
    ).fetchall()
    return frozenset(store_id for (store_id,) in rows)


def model_version(connection: Connection[Any], run_id: int) -> int | None:
    """The version the run was scored under, or None while it has none. The
    currently published version is never substituted (ADR-0064)."""
    row = connection.execute(
        "SELECT version_id FROM api_models.run_version_v1 WHERE run_id = %s",
        (run_id,),
    ).fetchone()
    return None if row is None else row[0]


def products(connection: Connection[Any]) -> list[Product]:
    """Every product, in product order, whatever its commercial status."""
    rows = connection.execute(
        "SELECT product_id, category_id, is_active FROM api_products.product_ref_v1"
        " ORDER BY product_id"
    ).fetchall()
    return [Product(*row) for row in rows]


def here(
    connection: Connection[Any], store_id: int, channel_id: int | None
) -> dict[int, Here]:
    """Stock's availability of every product at one store: on the channel when
    one is given, otherwise on any channel the store enables (ADR-0067). The
    quantity is the store's, whatever the channel."""
    rows = connection.execute(
        "SELECT product_id, bool_or(is_available), max(quantity_on_hand)"
        " FROM api_stock.availability_v1"
        " WHERE store_id = %s AND (%s::smallint IS NULL OR channel_id = %s::smallint)"
        " GROUP BY product_id",
        (store_id, channel_id, channel_id),
    ).fetchall()
    return {
        product_id: Here(bool(available), quantity or 0)
        for product_id, available, quantity in rows
    }


def selling_elsewhere(
    connection: Connection[Any],
    product_ids: list[int],
    store_id: int,
    channel_id: int | None,
) -> dict[int, dict[int, int]]:
    """For each product, the other stores that sell it on the channel (any
    channel when none is given), with their quantity on hand."""
    if not product_ids:
        return {}
    rows = connection.execute(
        "SELECT DISTINCT product_id, store_id, quantity_on_hand"
        " FROM api_stock.availability_v1"
        " WHERE product_id = ANY(%s) AND store_id <> %s AND is_available"
        " AND (%s::smallint IS NULL OR channel_id = %s::smallint)",
        (product_ids, store_id, channel_id, channel_id),
    ).fetchall()
    found: dict[int, dict[int, int]] = {}
    for product_id, other, quantity in rows:
        found.setdefault(product_id, {})[other] = quantity
    return found


def substitutes(
    connection: Connection[Any], product_ids: list[int]
) -> dict[int, list[int]]:
    """The declared substitutes of each product, lowest id first. A relation
    is directed: (A, B) makes B a substitute for A, not A for B."""
    if not product_ids:
        return {}
    rows = connection.execute(
        "SELECT product_id, related_product_id FROM api_products.product_relation_v1"
        " WHERE relation_kind = 'substitute' AND product_id = ANY(%s)"
        " ORDER BY product_id, related_product_id",
        (product_ids,),
    ).fetchall()
    found: dict[int, list[int]] = {}
    for product_id, related in rows:
        found.setdefault(product_id, []).append(related)
    return found
