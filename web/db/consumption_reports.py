"""The one new read behind the filtered consumption-shift and recommendation
reports (F12-03): customer names in bulk, for whichever customers a filter
leaves in a shift report. Everything else these reports need already exists:
F8-05's `detect_shifts`, F10-01's `recommend`, and `list_stores` /
`list_channels` / `list_all_categories` for the filter pickers.
"""

from __future__ import annotations

from typing import Any

from psycopg import Connection


def list_customer_names(
    connection: Connection[Any], customer_ids: list[Any]
) -> dict[str, str]:
    """The name of each given customer, keyed by id as text. Empty in, empty
    out, with no statement run."""
    if not customer_ids:
        return {}
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT customer_id, name
            FROM customer
            WHERE customer_id = ANY(%s::uuid[])
            """,
            ([str(customer_id) for customer_id in customer_ids],),
        )
        rows = cursor.fetchall()

    return {str(customer_id): name for customer_id, name in rows}
