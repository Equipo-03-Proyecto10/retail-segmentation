"""Reads behind consumption-shift detection (F8-05).

Read only, and every statement is parameterized. Where web/db/consumption.py
summarises one customer over one window, these read every customer over one
*period* in a single statement each, so detecting shifts costs a fixed number of
round trips however many customers there are.

Nothing here ranks or compares. Each function returns every group a customer has
in the period, keyed by customer, and the service decides which is dominant
(RN-35) and whether it changed.

Accepted sales only: ADR-0020 persists a CSV row only once it is accepted, so
`transaction` and `transaction_line` are the accepted sales, and no status column
is invented to filter on.

A period is **half-open**, [start, end): it includes its first instant and not
its last. Two adjacent periods, one ending where the next begins, therefore never
both count the same instant. web/db/consumption.py's profile window is closed at
both ends because it is one window ending now, not one of a pair.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from psycopg import Connection

from web.db.consumption import CategoryTotal, GroupTotal


def list_channel_totals_by_customer(
    connection: Connection[Any], start: datetime, end: datetime
) -> dict[str, list[GroupTotal]]:
    """Every channel each customer bought through in the period."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT t.customer_id, ch.channel_id, ch.name, count(*), sum(t.total)
            FROM transaction AS t
            JOIN channel AS ch ON ch.channel_id = t.channel_id
            WHERE t.occurred_at >= %(start)s
              AND t.occurred_at < %(end)s
            GROUP BY t.customer_id, ch.channel_id, ch.name
            """,
            {"start": start, "end": end},
        )
        rows = cursor.fetchall()

    grouped: dict[str, list[GroupTotal]] = defaultdict(list)
    for customer_id, *group in rows:
        grouped[str(customer_id)].append(GroupTotal(*group))
    return dict(grouped)


def list_store_totals_by_customer(
    connection: Connection[Any], start: datetime, end: datetime
) -> dict[str, list[GroupTotal]]:
    """Every store each customer bought at in the period."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT t.customer_id, s.store_id, s.name, count(*), sum(t.total)
            FROM transaction AS t
            JOIN store AS s ON s.store_id = t.store_id
            WHERE t.occurred_at >= %(start)s
              AND t.occurred_at < %(end)s
            GROUP BY t.customer_id, s.store_id, s.name
            """,
            {"start": start, "end": end},
        )
        rows = cursor.fetchall()

    grouped: dict[str, list[GroupTotal]] = defaultdict(list)
    for customer_id, *group in rows:
        grouped[str(customer_id)].append(GroupTotal(*group))
    return dict(grouped)


def list_category_totals_by_customer(
    connection: Connection[Any], start: datetime, end: datetime
) -> dict[str, list[CategoryTotal]]:
    """Every category each customer bought from in the period, by the category
    the product itself carries (no roll-up to a parent). A customer whose
    purchases in the period have no product lines has no entry."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT t.customer_id, c.category_id, c.name,
                   count(DISTINCT t.transaction_id),
                   sum(tl.quantity),
                   sum(tl.quantity * tl.unit_price)
            FROM transaction AS t
            JOIN transaction_line AS tl ON tl.transaction_id = t.transaction_id
            JOIN product AS p ON p.product_id = tl.product_id
            JOIN category AS c ON c.category_id = p.category_id
            WHERE t.occurred_at >= %(start)s
              AND t.occurred_at < %(end)s
            GROUP BY t.customer_id, c.category_id, c.name
            """,
            {"start": start, "end": end},
        )
        rows = cursor.fetchall()

    grouped: dict[str, list[CategoryTotal]] = defaultdict(list)
    for customer_id, *group in rows:
        grouped[str(customer_id)].append(CategoryTotal(*group))
    return dict(grouped)
