"""The segment recalculation, expressed as one statement (F3-10, F7-02).

Scoring, matching and writing happen in a single round trip. That is not an
optimisation: the run has to be atomic, and it has to be *repeatable* — the
same sales must produce the same assignment, or RN-20's audit trail fills with
entries recording a segment flapping between two equally valid answers.

Two things make it repeatable, and both are deliberate:

* every `ntile` window is ordered by `customer_id` after its measure, so
  customers who tie on recency, frequency or spend are always cut into the same
  quintile rather than into whichever the planner happened to return first;
* a triple that satisfies several rules takes the lowest `segment_id`. The
  seeded rule bands overlap heavily, so this is not a corner case; a real rule
  set would be disjoint, and until it is, the tie is broken the same way every
  time.

F7-02 replaces the mutable segment column customer used to carry with durable
history (ADR-0017): every run inserts one `segmentation_run` row, and every
customer's result lands in `customer_segment_history` — closing the
previously open row and opening a new one only when the result actually
changed. Nothing is written for a customer whose segment does not change: the
same `IS DISTINCT FROM` guard that used to keep the audit trigger quiet on a
second run now keeps history from growing on one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from psycopg import Connection

# Quintiles: 5 is the best score in each measure — most recent, most frequent,
# highest spend — which is the orientation segment_rule's bands are written in.
_QUINTILES = 5

_RECALCULATE = """
WITH new_run AS (
    INSERT INTO segmentation_run (method, window_days)
    VALUES ('RFM_RULES', %s)
    RETURNING run_id
),
window_sales AS (
    SELECT customer_id,
           max(occurred_at) AS last_purchase,
           count(*)         AS frequency,
           sum(total)       AS monetary
    FROM transaction
    WHERE occurred_at >= now() - make_interval(days => %s)
    GROUP BY customer_id
),
scored AS (
    SELECT customer_id,
           %s + 1 - ntile(%s)
               OVER (ORDER BY last_purchase DESC, customer_id) AS r,
           %s + 1 - ntile(%s)
               OVER (ORDER BY frequency DESC, customer_id)     AS f,
           %s + 1 - ntile(%s)
               OVER (ORDER BY monetary DESC, customer_id)      AS m
    FROM window_sales
),
matched AS (
    -- Every customer, not only those with sales: a customer absent from
    -- window_sales has no r/f/m and no matching segment, so they land here
    -- with all four columns NULL — RN-21's unassigned result, recorded
    -- rather than skipped.
    SELECT c.customer_id,
           s.r, s.f, s.m,
           (SELECT min(seg.segment_id)
              FROM segment AS seg
              JOIN segment_rule AS sr ON sr.rule_id = seg.rule_id
             WHERE s.r BETWEEN sr.r_min AND sr.r_max
               AND s.f BETWEEN sr.f_min AND sr.f_max
               AND s.m BETWEEN sr.m_min AND sr.m_max
               AND seg.valid_from <= CURRENT_DATE
               AND (seg.valid_to IS NULL OR seg.valid_to >= CURRENT_DATE)
           ) AS segment_id
      FROM customer AS c
      LEFT JOIN scored AS s ON s.customer_id = c.customer_id
),
current_open AS (
    SELECT h.customer_id, h.segment_id AS open_segment_id
    FROM customer_segment_history AS h
    WHERE h.valid_to IS NULL
),
changed AS (
    -- Only customers whose result differs from their currently open row —
    -- same IS DISTINCT FROM guard the old UPDATE used, so a second run over
    -- unchanged sales still writes and audits nothing.
    SELECT m.customer_id, m.r, m.f, m.m, m.segment_id
    FROM matched AS m
    LEFT JOIN current_open AS co ON co.customer_id = m.customer_id
    WHERE co.customer_id IS NULL
       OR co.open_segment_id IS DISTINCT FROM m.segment_id
),
closed AS (
    UPDATE customer_segment_history AS h
       SET valid_to = now()
      FROM changed AS ch
     WHERE h.customer_id = ch.customer_id
       AND h.valid_to IS NULL
    RETURNING h.customer_id
),
opened AS (
    -- The LEFT JOIN to `closed` is not filtering anything (cl is unused) —
    -- it exists purely to create a data dependency. Sibling data-modifying
    -- CTEs in PostgreSQL have no guaranteed execution order unless one
    -- references the other; without this, `opened`'s INSERT could run
    -- before `closed`'s UPDATE finished clearing the old open row, and
    -- collide with ux_customer_segment_history_open.
    INSERT INTO customer_segment_history
        (customer_id, run_id, segment_id, r_score, f_score, m_score, valid_from)
    SELECT ch.customer_id,
           (SELECT run_id FROM new_run),
           ch.segment_id, ch.r, ch.f, ch.m,
           now()
    FROM changed AS ch
    LEFT JOIN closed AS cl ON cl.customer_id = ch.customer_id
    RETURNING customer_id, segment_id
)
SELECT (SELECT count(*) FROM matched)                              AS processed,
       (SELECT count(*) FROM matched WHERE segment_id IS NOT NULL) AS assigned,
       (SELECT count(*) FROM matched WHERE segment_id IS NULL)     AS unmatched,
       (SELECT count(*) FROM opened WHERE segment_id IS NOT NULL)  AS reassigned,
       (SELECT count(*) FROM opened WHERE segment_id IS NULL)      AS cleared
"""


@dataclass(frozen=True)
class RecalculationCounts:
    """What one run did, straight from the statement that did it."""

    processed: int
    assigned: int
    unmatched: int
    reassigned: int
    cleared: int


def recalculate_segments(
    connection: Connection[Any], window_days: int
) -> RecalculationCounts:
    """Score, match and write every customer's segment as a new run. One
    statement.

    The caller owns the transaction: nothing here commits, so a run that
    fails part way leaves neither the run row nor any assignment behind.
    """
    with connection.cursor() as cursor:
        cursor.execute(_RECALCULATE, (window_days, window_days) + (_QUINTILES,) * 6)
        row = cursor.fetchone()

    return RecalculationCounts(
        processed=row[0],
        assigned=row[1],
        unmatched=row[2],
        reassigned=row[3],
        cleared=row[4],
    )


# ---------- reading segments and their rules (F3-05 / RF-13) ----------


@dataclass(frozen=True)
class Segment:
    segment_id: int
    name: str
    description: str | None
    rule_id: int
    valid_from: date
    valid_to: date | None


@dataclass(frozen=True)
class SegmentRule:
    rule_id: int
    rule_code: str
    r_min: int
    r_max: int
    f_min: int
    f_max: int
    m_min: int
    m_max: int


def list_segments(
    connection: Connection[Any], *, search: str | None, page: int, per_page: int
) -> tuple[list[Segment], int]:
    """Return a page of segments, optionally filtered by name, and the total."""
    offset = (page - 1) * per_page

    with connection.cursor() as cursor:
        if search:
            pattern = f"%{search}%"
            cursor.execute(
                """
                SELECT segment_id, name, description, rule_id, valid_from, valid_to
                FROM segment
                WHERE name ILIKE %s
                ORDER BY segment_id
                LIMIT %s OFFSET %s
                """,
                (pattern, per_page, offset),
            )
        else:
            cursor.execute(
                """
                SELECT segment_id, name, description, rule_id, valid_from, valid_to
                FROM segment
                ORDER BY segment_id
                LIMIT %s OFFSET %s
                """,
                (per_page, offset),
            )
        rows = cursor.fetchall()

        if search:
            pattern = f"%{search}%"
            cursor.execute(
                "SELECT count(*) FROM segment WHERE name ILIKE %s", (pattern,)
            )
        else:
            cursor.execute("SELECT count(*) FROM segment")
        total = cursor.fetchone()[0]

    return [Segment(*row) for row in rows], total


def get_segment(connection: Connection[Any], segment_id: int) -> Segment | None:
    """Return one segment by id, or None if it does not exist."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT segment_id, name, description, rule_id, valid_from, valid_to
            FROM segment
            WHERE segment_id = %s
            """,
            (segment_id,),
        )
        row = cursor.fetchone()

    return Segment(*row) if row else None


def get_segment_rule(connection: Connection[Any], rule_id: int) -> SegmentRule | None:
    """Return the RFM bands a segment is defined by, or None."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT rule_id, rule_code, r_min, r_max, f_min, f_max, m_min, m_max
            FROM segment_rule
            WHERE rule_id = %s
            """,
            (rule_id,),
        )
        row = cursor.fetchone()

    return SegmentRule(*row) if row else None


# ---------- reading current assignments from history (F7-02) ----------


@dataclass(frozen=True)
class CustomerSegmentAssignment:
    """A customer's currently open history row — what the old mutable column
    used to be, read from durable history instead."""

    customer_id: str
    segment_id: int | None
    r_score: int | None
    f_score: int | None
    m_score: int | None
    valid_from: datetime


def get_current_assignment(
    connection: Connection[Any], customer_id: str
) -> CustomerSegmentAssignment | None:
    """Return one customer's open history row, or None if they have never
    been scored by a run."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT customer_id, segment_id, r_score, f_score, m_score, valid_from
            FROM customer_segment_history
            WHERE customer_id = %s AND valid_to IS NULL
            """,
            (str(customer_id),),
        )
        row = cursor.fetchone()

    return CustomerSegmentAssignment(*row) if row else None


def get_current_assignments(
    connection: Connection[Any], customer_ids: list[str]
) -> dict[str, CustomerSegmentAssignment]:
    """Return the open history row for each of several customers, keyed by
    id. Customers with no open row (never scored) are simply absent — the
    caller reads that as unassigned, the same as a NULL segment_id used to
    mean."""
    if not customer_ids:
        return {}

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT customer_id, segment_id, r_score, f_score, m_score, valid_from
            FROM customer_segment_history
            WHERE customer_id = ANY(%s) AND valid_to IS NULL
            """,
            ([str(cid) for cid in customer_ids],),
        )
        rows = cursor.fetchall()

    return {str(row[0]): CustomerSegmentAssignment(*row) for row in rows}
