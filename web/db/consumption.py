"""Reads behind the customer consumption profile (F8-03).

Read only, and every statement is parameterized. The rules that turn these
rows into a profile -- which channel is "dominant", which categories are
"favourite", what "discount" means -- live in
`web/services/consumption_profile.py` (ADR-0003). Nothing here ranks or
breaks a tie: each function returns every group the customer has, and the
service decides.

Accepted sales only. ADR-0020 persists a CSV row only once it is accepted, and
rejected rows live in the load's rejection report, never in `transaction`. So
reading `transaction` and `transaction_line` *is* reading accepted sales; there
is no status column to filter on and none is invented here.

Every window is the closed interval [since, until]. The caller supplies both
ends, so the same statement is repeatable for a fixed `until` instead of
depending on the clock of whichever connection ran it.

The current and previous segment are read from `customer_segment_history`
(ADR-0017): the open row, and the most recently closed one. One statement reads
both so a concurrent segment run cannot make two READ COMMITTED snapshots
return the same row as current and previous. It never reads
`segmentation_run.method` -- ADR-0018 says a consumer of assignments never
learns which strategy produced them. The run is joined only for `run_at` and
`window_days`, so a page can say what window an R/F/M value was measured over.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from psycopg import Connection


@dataclass(frozen=True)
class SalesTotals:
    """A customer's accepted sales in one window, summed over the headers.

    `spend` is the sum of `transaction.total`, the same quantity the segment
    run scores as Monetary, so a profile's spend and its M value are one
    measurement and not two that can drift apart. For sales that came in
    through CSV the header total is derived from the lines (ADR-0020), so it
    equals the sum of quantity times unit price. `spend` and `last_purchase_at`
    are None, not zero, when there are no purchases.
    """

    purchases: int
    spend: Decimal | None
    last_purchase_at: datetime | None


@dataclass(frozen=True)
class GroupTotal:
    """One channel or one store a customer bought through, with its totals."""

    item_id: int
    name: str
    purchases: int
    spend: Decimal


@dataclass(frozen=True)
class CategoryTotal:
    """One product category a customer bought from.

    `purchases` counts distinct purchases containing the category, so a basket
    with two products of one category still counts once for it.
    """

    category_id: int
    name: str
    purchases: int
    units: int
    spend: Decimal


@dataclass(frozen=True)
class ProductTotal:
    """One product a customer bought. `purchases` is the number of purchases
    that included it; a purchase holds at most one line per product."""

    product_id: int
    name: str
    purchases: int
    units: int


@dataclass(frozen=True)
class DiscountTotals:
    """What was paid, and what the same units cost at the catalog list price.

    Both are None when no line in the window has a positive list price -- a
    product listed at zero has nothing to discount from and is left out of both
    sums rather than dividing by it.
    """

    paid: Decimal | None
    at_list: Decimal | None


@dataclass(frozen=True)
class HistoryRow:
    """One row of a customer's segment assignment history, with the run that
    wrote it. `label_code` is None for an unassigned result (RN-21)."""

    run_id: int
    label_code: str | None
    label_name: str | None
    last_purchase_at: datetime | None
    frequency_count: int | None
    monetary_total: Decimal | None
    r_score: int | None
    f_score: int | None
    m_score: int | None
    valid_from: datetime
    valid_to: datetime | None
    run_at: datetime
    window_days: int


def get_sales_totals(
    connection: Connection[Any], customer_id: Any, since: datetime, until: datetime
) -> SalesTotals:
    """Count and sum a customer's accepted sales inside the window."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT count(*), sum(total), max(occurred_at)
            FROM transaction
            WHERE customer_id = %(customer_id)s
              AND occurred_at >= %(since)s
              AND occurred_at <= %(until)s
            """,
            {"customer_id": str(customer_id), "since": since, "until": until},
        )
        purchases, spend, last_purchase_at = cursor.fetchone()

    return SalesTotals(
        purchases=purchases, spend=spend, last_purchase_at=last_purchase_at
    )


def list_channel_totals(
    connection: Connection[Any], customer_id: Any, since: datetime, until: datetime
) -> list[GroupTotal]:
    """Every channel the customer bought through in the window."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT ch.channel_id, ch.name, count(*), sum(t.total)
            FROM transaction AS t
            JOIN channel AS ch ON ch.channel_id = t.channel_id
            WHERE t.customer_id = %(customer_id)s
              AND t.occurred_at >= %(since)s
              AND t.occurred_at <= %(until)s
            GROUP BY ch.channel_id, ch.name
            """,
            {"customer_id": str(customer_id), "since": since, "until": until},
        )
        rows = cursor.fetchall()

    return [GroupTotal(*row) for row in rows]


def list_store_totals(
    connection: Connection[Any], customer_id: Any, since: datetime, until: datetime
) -> list[GroupTotal]:
    """Every store the customer bought at in the window."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT s.store_id, s.name, count(*), sum(t.total)
            FROM transaction AS t
            JOIN store AS s ON s.store_id = t.store_id
            WHERE t.customer_id = %(customer_id)s
              AND t.occurred_at >= %(since)s
              AND t.occurred_at <= %(until)s
            GROUP BY s.store_id, s.name
            """,
            {"customer_id": str(customer_id), "since": since, "until": until},
        )
        rows = cursor.fetchall()

    return [GroupTotal(*row) for row in rows]


def list_category_totals(
    connection: Connection[Any], customer_id: Any, since: datetime, until: datetime
) -> list[CategoryTotal]:
    """Every category the customer bought from in the window, by the
    category the product itself carries (no roll-up to a parent)."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT c.category_id, c.name,
                   count(DISTINCT t.transaction_id),
                   sum(tl.quantity),
                   sum(tl.quantity * tl.unit_price)
            FROM transaction AS t
            JOIN transaction_line AS tl ON tl.transaction_id = t.transaction_id
            JOIN product AS p ON p.product_id = tl.product_id
            JOIN category AS c ON c.category_id = p.category_id
            WHERE t.customer_id = %(customer_id)s
              AND t.occurred_at >= %(since)s
              AND t.occurred_at <= %(until)s
            GROUP BY c.category_id, c.name
            """,
            {"customer_id": str(customer_id), "since": since, "until": until},
        )
        rows = cursor.fetchall()

    return [CategoryTotal(*row) for row in rows]


def list_product_totals(
    connection: Connection[Any], customer_id: Any, since: datetime, until: datetime
) -> list[ProductTotal]:
    """Every product the customer bought in the window."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT p.product_id, p.name, count(*), sum(tl.quantity)
            FROM transaction AS t
            JOIN transaction_line AS tl ON tl.transaction_id = t.transaction_id
            JOIN product AS p ON p.product_id = tl.product_id
            WHERE t.customer_id = %(customer_id)s
              AND t.occurred_at >= %(since)s
              AND t.occurred_at <= %(until)s
            GROUP BY p.product_id, p.name
            """,
            {"customer_id": str(customer_id), "since": since, "until": until},
        )
        rows = cursor.fetchall()

    return [ProductTotal(*row) for row in rows]


def get_discount_totals(
    connection: Connection[Any], customer_id: Any, since: datetime, until: datetime
) -> DiscountTotals:
    """Sum what the customer paid and what the same units list for today.

    `unit_price` is what was charged (RN-13); `list_price` is the catalog's
    *current* price, because the schema does not keep the list price a sale
    was made against. That is why the service calls the result a comparison
    with the current list price rather than the discount actually granted.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT sum(tl.quantity * tl.unit_price),
                   sum(tl.quantity * p.list_price)
            FROM transaction AS t
            JOIN transaction_line AS tl ON tl.transaction_id = t.transaction_id
            JOIN product AS p ON p.product_id = tl.product_id
            WHERE t.customer_id = %(customer_id)s
              AND t.occurred_at >= %(since)s
              AND t.occurred_at <= %(until)s
              AND p.list_price > 0
            """,
            {"customer_id": str(customer_id), "since": since, "until": until},
        )
        paid, at_list = cursor.fetchone()

    return DiscountTotals(paid=paid, at_list=at_list)


def get_current_and_previous_history_rows(
    connection: Connection[Any], customer_id: Any
) -> tuple[HistoryRow | None, HistoryRow | None]:
    """The open assignment and most recently closed assignment in one read."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT h.run_id, h.label_code, sl.name,
                   h.recency_last_purchase_at, h.frequency_count,
                   h.monetary_total, h.r_score, h.f_score, h.m_score,
                   h.valid_from, h.valid_to, r.run_at, r.window_days
            FROM customer_segment_history AS h
            JOIN segmentation_run AS r ON r.run_id = h.run_id
            LEFT JOIN segment_label AS sl ON sl.label_code = h.label_code
            WHERE h.customer_id = %(customer_id)s
            ORDER BY (h.valid_to IS NULL) DESC,
                     h.valid_to DESC,
                     h.history_id DESC
            LIMIT 2
            """,
            {"customer_id": str(customer_id)},
        )
        rows = [HistoryRow(*row) for row in cursor.fetchall()]

    if not rows:
        return None, None
    if rows[0].valid_to is None:
        return rows[0], rows[1] if len(rows) > 1 else None
    return None, rows[0]
