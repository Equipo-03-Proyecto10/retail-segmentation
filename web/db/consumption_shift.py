"""The single read behind consumption-shift detection (F8-05).

The detector needs channel, store and category totals for two periods. They are
read by one parameterized statement so PostgreSQL evaluates all six groupings
against one READ COMMITTED snapshot. A CSV load can contain back-dated sales;
separate statements could therefore mix states and invent or hide a shift.

Nothing here ranks or compares. The rows are tagged by period and dimension,
then mapped back to the inputs used by the RN-35 ranking functions in the
service. Channel and store totals come from transaction headers. Category
totals additionally join transaction_line, product and category.

Accepted sales only: ADR-0020 persists a CSV row only once it is accepted. A
period is half-open, [start, end), so adjacent periods never share an instant.

A fourth grouping counts each customer's purchases that have at least one
product line: the denominator of a category's share and the purchases RN-50's
minimum counts for categories (#341). `customer_id` narrows every grouping to
one customer, which is how the consumption profile reads the same totals the
shift report does.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from psycopg import Connection

from web.db.consumption import CategoryTotal, GroupTotal

DimensionTotals = tuple[
    dict[str, list[GroupTotal]],
    dict[str, list[GroupTotal]],
    dict[str, list[CategoryTotal]],
    dict[str, int],
]


def list_totals_for_periods(
    connection: Connection[Any],
    earlier_start: datetime,
    earlier_end: datetime,
    later_start: datetime,
    later_end: datetime,
    *,
    customer_id: Any | None = None,
) -> tuple[DimensionTotals, DimensionTotals]:
    """Return channel, store and category totals, and purchases with product
    lines, for both periods at once; for every customer, or only one."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            WITH periods(period, start_at, end_at) AS (
                VALUES
                    ('earlier', %(earlier_start)s::timestamptz,
                     %(earlier_end)s::timestamptz),
                    ('later', %(later_start)s::timestamptz,
                     %(later_end)s::timestamptz)
            )
            SELECT periods.period, 'channel' AS dimension,
                   t.customer_id, ch.channel_id, ch.name,
                   count(*) AS purchases, NULL::bigint AS units,
                   sum(t.total) AS spend
            FROM periods
            JOIN transaction AS t
              ON t.occurred_at >= periods.start_at
             AND t.occurred_at < periods.end_at
             AND (%(customer_id)s::uuid IS NULL
                  OR t.customer_id = %(customer_id)s::uuid)
            JOIN channel AS ch ON ch.channel_id = t.channel_id
            GROUP BY periods.period, t.customer_id, ch.channel_id, ch.name

            UNION ALL

            SELECT periods.period, 'store' AS dimension,
                   t.customer_id, s.store_id, s.name,
                   count(*) AS purchases, NULL::bigint AS units,
                   sum(t.total) AS spend
            FROM periods
            JOIN transaction AS t
              ON t.occurred_at >= periods.start_at
             AND t.occurred_at < periods.end_at
             AND (%(customer_id)s::uuid IS NULL
                  OR t.customer_id = %(customer_id)s::uuid)
            JOIN store AS s ON s.store_id = t.store_id
            GROUP BY periods.period, t.customer_id, s.store_id, s.name

            UNION ALL

            SELECT periods.period, 'category' AS dimension,
                   t.customer_id, c.category_id, c.name,
                   count(DISTINCT t.transaction_id) AS purchases,
                   sum(tl.quantity) AS units,
                   sum(tl.quantity * tl.unit_price) AS spend
            FROM periods
            JOIN transaction AS t
              ON t.occurred_at >= periods.start_at
             AND t.occurred_at < periods.end_at
             AND (%(customer_id)s::uuid IS NULL
                  OR t.customer_id = %(customer_id)s::uuid)
            JOIN transaction_line AS tl
              ON tl.transaction_id = t.transaction_id
            JOIN product AS p ON p.product_id = tl.product_id
            JOIN category AS c ON c.category_id = p.category_id
            GROUP BY periods.period, t.customer_id, c.category_id, c.name

            UNION ALL

            SELECT periods.period, 'lined' AS dimension,
                   t.customer_id, 0, '',
                   count(*) AS purchases, NULL::bigint AS units,
                   NULL::numeric AS spend
            FROM periods
            JOIN transaction AS t
              ON t.occurred_at >= periods.start_at
             AND t.occurred_at < periods.end_at
             AND (%(customer_id)s::uuid IS NULL
                  OR t.customer_id = %(customer_id)s::uuid)
            WHERE EXISTS (
                SELECT 1 FROM transaction_line AS line
                WHERE line.transaction_id = t.transaction_id
            )
            GROUP BY periods.period, t.customer_id
            """,
            {
                "earlier_start": earlier_start,
                "earlier_end": earlier_end,
                "later_start": later_start,
                "later_end": later_end,
                "customer_id": None if customer_id is None else str(customer_id),
            },
        )
        rows = cursor.fetchall()

    totals = {
        period: {
            "channel": defaultdict(list),
            "store": defaultdict(list),
            "category": defaultdict(list),
            "lined": {},
        }
        for period in ("earlier", "later")
    }
    for period, dimension, customer, item_id, name, purchases, units, spend in rows:
        if dimension == "lined":
            totals[period]["lined"][str(customer)] = purchases
            continue
        if dimension == "category":
            total = CategoryTotal(item_id, name, purchases, units, spend)
        else:
            total = GroupTotal(item_id, name, purchases, spend)
        totals[period][dimension][str(customer)].append(total)

    return tuple(
        tuple(
            dict(totals[period][dimension])
            for dimension in ("channel", "store", "category", "lined")
        )
        for period in ("earlier", "later")
    )
