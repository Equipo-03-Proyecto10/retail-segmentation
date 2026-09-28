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
]


def list_totals_for_periods(
    connection: Connection[Any],
    earlier_start: datetime,
    earlier_end: datetime,
    later_start: datetime,
    later_end: datetime,
) -> tuple[DimensionTotals, DimensionTotals]:
    """Return channel, store and category totals for both periods at once."""
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
            JOIN transaction_line AS tl
              ON tl.transaction_id = t.transaction_id
            JOIN product AS p ON p.product_id = tl.product_id
            JOIN category AS c ON c.category_id = p.category_id
            GROUP BY periods.period, t.customer_id, c.category_id, c.name
            """,
            {
                "earlier_start": earlier_start,
                "earlier_end": earlier_end,
                "later_start": later_start,
                "later_end": later_end,
            },
        )
        rows = cursor.fetchall()

    totals = {
        period: {
            "channel": defaultdict(list),
            "store": defaultdict(list),
            "category": defaultdict(list),
        }
        for period in ("earlier", "later")
    }
    for period, dimension, customer_id, item_id, name, purchases, units, spend in rows:
        if dimension == "category":
            total = CategoryTotal(item_id, name, purchases, units, spend)
        else:
            total = GroupTotal(item_id, name, purchases, spend)
        totals[period][dimension][str(customer_id)].append(total)

    return tuple(
        tuple(
            dict(totals[period][dimension])
            for dimension in ("channel", "store", "category")
        )
        for period in ("earlier", "later")
    )
