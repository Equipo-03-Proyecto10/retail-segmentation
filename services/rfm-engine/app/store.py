"""rfm-engine's reads and its one write: a customer's profile row."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from psycopg import Connection

from .profile import Band, Measures, Profile, RuleSet

# Two keys: this one and the hash of the customer. Registered in
# contracts/db/advisory-locks.md. It serializes the recomputations of one customer
# so that a recomputation never reads before an earlier one has written.
LOCK_KEY = 295001


def rule_set(connection: Connection, rule_set_id: int) -> RuleSet | None:
    row = connection.execute(
        "SELECT rule_set_id, window_days, recency_boundaries, frequency_boundaries,"
        " monetary_boundaries, fallback_label_code, returned_sales"
        " FROM rfm.rule_set WHERE rule_set_id = %s",
        (rule_set_id,),
    ).fetchone()
    if row is None:
        return None
    bands = connection.execute(
        "SELECT label_code, r_min, r_max, f_min, f_max, m_min, m_max"
        " FROM rfm.rule_set_band WHERE rule_set_id = %s ORDER BY band_no",
        (rule_set_id,),
    ).fetchall()
    return RuleSet(
        rule_set_id=row[0],
        window_days=row[1],
        recency=tuple(row[2]),  # type: ignore[arg-type]
        frequency=tuple(row[3]),  # type: ignore[arg-type]
        monetary=tuple(Decimal(value) for value in row[4]),  # type: ignore[arg-type]
        bands=tuple(
            Band(b[0], (b[1], b[2]), (b[3], b[4]), (b[5], b[6])) for b in bands
        ),
        fallback_label=row[5],
        keep_reversed=row[6] == "KEEP",
    )


def customer_of_sale(connection: Connection, transaction_id: int) -> UUID | None:
    """The customer a sale belongs to, read from the sale's lines. `None` when the
    sale has no lines (yet): it has no purchase to count."""
    row = connection.execute(
        "SELECT customer_id FROM api_sales.purchase_line_v1"
        " WHERE transaction_id = %s LIMIT 1",
        (transaction_id,),
    ).fetchone()
    return row[0] if row else None


def measures(
    connection: Connection,
    customer_id: UUID,
    window_days: int,
    as_of: datetime,
    keep_reversed: bool = False,
) -> Measures:
    """Net purchases of this one customer in the window, closed at both ends.

    With `keep_reversed` (a rule set whose cancelled and returned sales are `KEEP`) a
    sale counts as sold at its full lines, whatever was reversed afterwards.

    A purchase is one sale with a net line (ADR-0062): a sale whose lines were all
    returned is not one. The statement names the customer, so its cost follows that
    customer's sales and not the customer count (idx_transaction_customer_date).
    """
    if keep_reversed:
        kept = connection.execute(
            "WITH sale AS ("
            " SELECT transaction_id, min(occurred_at) AS occurred_at,"
            "        sum(quantity * unit_price) AS net_total"
            "   FROM api_sales.purchase_line_v1"
            "  WHERE customer_id = %(customer)s"
            "    AND occurred_at >= %(as_of)s - make_interval(days => %(days)s)"
            "    AND occurred_at <= %(as_of)s"
            "  GROUP BY transaction_id)"
            " SELECT count(*), COALESCE(sum(net_total), 0)::numeric(12,2),"
            "        min(occurred_at), max(occurred_at) FROM sale",
            {"customer": customer_id, "as_of": as_of, "days": window_days},
        ).fetchone()
        assert kept is not None
        return Measures(kept[0], kept[1], kept[2], kept[3])
    row = connection.execute(
        "WITH sale AS ("
        " SELECT transaction_id, min(occurred_at) AS occurred_at,"
        "        sum(net_amount) AS net_total"
        "   FROM api_sales.net_purchase_line_v1"
        "  WHERE customer_id = %(customer)s"
        "    AND occurred_at >= %(as_of)s - make_interval(days => %(days)s)"
        "    AND occurred_at <= %(as_of)s"
        "  GROUP BY transaction_id)"
        " SELECT count(*), COALESCE(sum(net_total), 0)::numeric(12,2),"
        "        min(occurred_at), max(occurred_at) FROM sale",
        {"customer": customer_id, "as_of": as_of, "days": window_days},
    ).fetchone()
    assert row is not None
    return Measures(row[0], row[1], row[2], row[3])


def now(connection: Connection) -> datetime:
    row = connection.execute("SELECT clock_timestamp()").fetchone()
    assert row is not None
    return row[0]


def lock(connection: Connection, customer_id: UUID) -> None:
    connection.execute(
        "SELECT pg_advisory_xact_lock(%s, hashtext(%s))",
        (LOCK_KEY, str(customer_id)),
    )


def save(connection: Connection, customer_id: UUID, profile: Profile) -> None:
    """Insert the customer's row or update it, and nobody else's."""
    connection.execute(
        "INSERT INTO rfm.customer_profile (customer_id, state, rule_set_id, r_days,"
        " f_count, m_total, r_score, f_score, m_score, label_code, via_fallback,"
        " computed_at, next_change_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        " ON CONFLICT (customer_id) DO UPDATE SET state = EXCLUDED.state,"
        " rule_set_id = EXCLUDED.rule_set_id, r_days = EXCLUDED.r_days,"
        " f_count = EXCLUDED.f_count, m_total = EXCLUDED.m_total,"
        " r_score = EXCLUDED.r_score, f_score = EXCLUDED.f_score,"
        " m_score = EXCLUDED.m_score, label_code = EXCLUDED.label_code,"
        " via_fallback = EXCLUDED.via_fallback, computed_at = EXCLUDED.computed_at,"
        " next_change_at = EXCLUDED.next_change_at",
        (
            customer_id,
            profile.state,
            profile.rule_set_id,
            profile.r_days,
            profile.f_count,
            profile.m_total,
            profile.r_score,
            profile.f_score,
            profile.m_score,
            profile.label_code,
            profile.via_fallback,
            profile.computed_at,
            profile.next_change_at,
        ),
    )


def current(connection: Connection, customer_id: UUID) -> tuple[object, ...] | None:
    return connection.execute(
        "SELECT state, rule_set_id, r_days, f_count, m_total, r_score, f_score,"
        " m_score, label_code, via_fallback FROM rfm.customer_profile"
        " WHERE customer_id = %s",
        (customer_id,),
    ).fetchone()
