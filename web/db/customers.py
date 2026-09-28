"""Data access for customer — read only.

The consultation module (F3-05) lists and opens customers; it never writes
one. Customer creation is the loyalty sign-up flow, out of scope here. As
everywhere in web/db, every statement is parameterized and no identifier is
interpolated into SQL.

F7-02: this module no longer reads or writes a mutable segment column on
that column does not exist. A customer's current assignment is the open row in
customer_segment_history (web/db/segments.py), joined in here for the
listing view and read separately by the caller for the detail view.

What a listing shows of that row is its label (ADR-0018), which every method
writes. A segment is rule-based only: a K-means assignment carries a label and
no segment (#272).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from psycopg import Connection

from web.db.categories import Category
from web.db.channels import Channel


@dataclass(frozen=True)
class Customer:
    customer_id: str
    user_id: str | None
    name: str
    email: str | None
    phone: str | None
    registration_channel_id: int
    registered_on: date
    label_name: str | None = None


def list_customers(
    connection: Connection, *, search: str | None, page: int, per_page: int
) -> tuple[list[Customer], int]:
    """Return a page of customers, optionally filtered by name or email, and
    the total row count for building pagination controls.

    label_name is the name of the label on each customer's currently open
    history row, whichever method wrote it, and None when that row is the
    unassigned result (RN-21) or the customer has never been scored.
    """
    offset = (page - 1) * per_page

    with connection.cursor() as cursor:
        if search:
            pattern = f"%{search}%"
            cursor.execute(
                """
                SELECT c.customer_id, c.user_id, c.name, c.email, c.phone,
                       c.registration_channel_id, c.registered_on,
                       l.name
                FROM customer AS c
                LEFT JOIN customer_segment_history AS h
                       ON h.customer_id = c.customer_id AND h.valid_to IS NULL
                LEFT JOIN segment_label AS l ON l.label_code = h.label_code
                WHERE c.name ILIKE %s OR c.email ILIKE %s
                ORDER BY c.name, c.customer_id
                LIMIT %s OFFSET %s
                """,
                (pattern, pattern, per_page, offset),
            )
        else:
            cursor.execute(
                """
                SELECT c.customer_id, c.user_id, c.name, c.email, c.phone,
                       c.registration_channel_id, c.registered_on,
                       l.name
                FROM customer AS c
                LEFT JOIN customer_segment_history AS h
                       ON h.customer_id = c.customer_id AND h.valid_to IS NULL
                LEFT JOIN segment_label AS l ON l.label_code = h.label_code
                ORDER BY c.name, c.customer_id
                LIMIT %s OFFSET %s
                """,
                (per_page, offset),
            )
        rows = cursor.fetchall()

        if search:
            pattern = f"%{search}%"
            cursor.execute(
                "SELECT count(*) FROM customer WHERE name ILIKE %s OR email ILIKE %s",
                (pattern, pattern),
            )
        else:
            cursor.execute("SELECT count(*) FROM customer")
        total = cursor.fetchone()[0]

    return [Customer(*row) for row in rows], total


def get_customer(connection: Connection, customer_id: str) -> Customer | None:
    """Return one customer by id, or None if it does not exist."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT customer_id, user_id, name, email, phone,
                   registration_channel_id, registered_on
            FROM customer
            WHERE customer_id = %s
            """,
            (str(customer_id),),
        )
        row = cursor.fetchone()

    return Customer(*row) if row else None


def list_interest_categories(
    connection: Connection, customer_id: str
) -> list[Category]:
    """The categories this customer has registered an interest in (a 4NF
    multivalued fact — customer_interest_category)."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT c.category_id, c.name, c.parent_category_id
            FROM customer_interest_category AS ci
            JOIN category AS c ON c.category_id = ci.category_id
            WHERE ci.customer_id = %s
            ORDER BY c.name
            """,
            (str(customer_id),),
        )
        rows = cursor.fetchall()

    return [Category(*row) for row in rows]


def list_preferred_channels(connection: Connection, customer_id: str) -> list[Channel]:
    """The channels this customer prefers to be reached on
    (customer_preferred_channel)."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT ch.channel_id, ch.name
            FROM customer_preferred_channel AS cp
            JOIN channel AS ch ON ch.channel_id = cp.channel_id
            WHERE cp.customer_id = %s
            ORDER BY ch.name
            """,
            (str(customer_id),),
        )
        rows = cursor.fetchall()

    return [Channel(*row) for row in rows]


def list_customers_in_segment(
    connection: Connection, segment_id: int, *, page: int, per_page: int
) -> tuple[list[Customer], int]:
    """Return a page of the customers currently assigned to a segment, and
    the total (RF-13, the segment -> customers direction).

    "Currently assigned" means holding an open customer_segment_history row
    for this segment — the same meaning the old mutable column used
    to have, read from history instead of from a column. Only a rule-based
    assignment names a segment; a customer K-means labelled is not listed here.
    """
    offset = (page - 1) * per_page

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT c.customer_id, c.user_id, c.name, c.email, c.phone,
                   c.registration_channel_id, c.registered_on
            FROM customer AS c
            JOIN customer_segment_history AS h
              ON h.customer_id = c.customer_id AND h.valid_to IS NULL
            WHERE h.segment_id = %s
            ORDER BY c.name
            LIMIT %s OFFSET %s
            """,
            (segment_id, per_page, offset),
        )
        rows = cursor.fetchall()

        cursor.execute(
            """
            SELECT count(*)
            FROM customer_segment_history
            WHERE segment_id = %s AND valid_to IS NULL
            """,
            (segment_id,),
        )
        total = cursor.fetchone()[0]

    return [Customer(*row) for row in rows], total
