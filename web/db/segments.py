"""SQL under the segmentation pipeline (F3-10, F7-02, F9-01).

F3-10 scored, matched and wrote in one statement, which made the run atomic and
repeatable but meant no second method could reuse the writing. The pipeline
(ADR-0018) needs the two halves apart: what a *method* decides, and what the
pipeline *records* whichever method decided it.

**The decision.** `score_rfm_rules` is the RFM_RULES adapter's read: quintile
scores, matched against the segment bands, one row per customer. `read_rfm_inputs`
is what a second method reads: the raw recency, frequency and monetary values per
customer, with no scoring.

**The record.** `create_run`, `close_open_assignments` and `insert_assignments`
write a run and its assignments and know nothing about which method produced
them. None of them commits; the service owns the transaction (ADR-0014), so a
run that fails part way leaves neither the run row nor any assignment behind.

Two things make a run repeatable, and both are in the adapter's read:

* every `ntile` window is ordered by `customer_id` after its measure, so
  customers who tie on recency, frequency or spend are always cut into the same
  quintile rather than into whichever the planner happened to return first;
* a triple that satisfies several rules takes the lowest `segment_id`. The
  seeded rule bands overlap heavily, so this is not a corner case; a real rule
  set would be disjoint, and until it is, the tie is broken the same way every
  time.

ADR-0017: every run writes one `customer_segment_history` row per customer, and
closes the previously open row even when the result is unchanged. Closing and
opening share one instant, taken by `lock_for_run`, so a customer's rows are
contiguous.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg import Connection

from web.db.search import ilike_pattern

# Quintiles: 5 is the best score in each measure — most recent, most frequent,
# highest spend — which is the orientation segment_rule's bands are written in.
QUINTILES = 5

# Takes a transaction-scoped advisory lock, so two concurrent runs cannot both
# close the same open history rows and race to reopen them: the loser would
# hit ux_customer_segment_history_open as an unhandled 500 instead of simply
# running after the winner (#285). Advisory rather than LOCK TABLE: it
# serializes runs without blocking reads of the segmentation history.
_RUN_LOCK_KEY = 285_001

_SCORE_AND_MATCH = """
WITH window_sales AS (
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
)
-- Every customer, not only those with sales: a customer absent from
-- window_sales has no r/f/m and no matching segment, so every column but
-- customer_id comes back NULL. That is RN-21's unassigned result, recorded
-- rather than skipped, and the only case ADR-0018 allows a null label for.
--
-- The first LATERAL join takes the segment whose band holds the triple, the
-- lowest segment_id when several do, and carries that row's label_code, the
-- stable vocabulary of ADR-0018, in the same join.
--
-- A customer who does have sales but matches no band still gets a label,
-- because ADR-0018 forbids a null label for anyone actually scored. The second
-- LATERAL join falls back to the worst-ranked segment, the highest
-- ordinal_position, only when the first found nothing and the customer has a
-- triple to fall back from at all.
SELECT c.customer_id,
       w.last_purchase, w.frequency, w.monetary,
       s.r, s.f, s.m,
       COALESCE(bm.segment_id, fb.segment_id) AS segment_id,
       COALESCE(bm.label_code, fb.label_code) AS label_code,
       (bm.segment_id IS NULL AND fb.segment_id IS NOT NULL) AS via_fallback
  FROM customer AS c
  LEFT JOIN scored AS s ON s.customer_id = c.customer_id
  LEFT JOIN window_sales AS w ON w.customer_id = c.customer_id
  LEFT JOIN LATERAL (
      SELECT seg.segment_id, seg.label_code
        FROM segment AS seg
        JOIN segment_rule AS sr ON sr.rule_id = seg.rule_id
       WHERE s.r BETWEEN sr.r_min AND sr.r_max
         AND s.f BETWEEN sr.f_min AND sr.f_max
         AND s.m BETWEEN sr.m_min AND sr.m_max
         AND seg.valid_from <= CURRENT_DATE
         AND (seg.valid_to IS NULL OR seg.valid_to >= CURRENT_DATE)
       ORDER BY seg.segment_id
       LIMIT 1
  ) AS bm ON true
  LEFT JOIN LATERAL (
      SELECT seg.segment_id, seg.label_code
        FROM segment AS seg
        JOIN segment_label AS sl ON sl.label_code = seg.label_code
       WHERE w.customer_id IS NOT NULL AND bm.segment_id IS NULL
         AND seg.valid_from <= CURRENT_DATE
         AND (seg.valid_to IS NULL OR seg.valid_to >= CURRENT_DATE)
       ORDER BY sl.ordinal_position DESC, seg.segment_id
       LIMIT 1
  ) AS fb ON true
 ORDER BY c.customer_id
"""

_RFM_INPUTS = """
SELECT c.customer_id, w.last_purchase, w.frequency, w.monetary
  FROM customer AS c
  LEFT JOIN (
      SELECT customer_id,
             max(occurred_at) AS last_purchase,
             count(*)         AS frequency,
             sum(total)       AS monetary
      FROM transaction
      WHERE occurred_at >= now() - make_interval(days => %s)
      GROUP BY customer_id
  ) AS w ON w.customer_id = c.customer_id
 ORDER BY c.customer_id
"""


@dataclass(frozen=True)
class ScoredCustomer:
    """One customer as the RFM_RULES adapter's read returns them.

    Every field but `customer_id` is None for a customer with no sales in the
    window, which is the unassigned result and not an omission.
    """

    customer_id: str
    last_purchase_at: datetime | None
    frequency: int | None
    monetary: Decimal | None
    r_score: int | None
    f_score: int | None
    m_score: int | None
    segment_id: int | None
    label_code: str | None
    # True when no band held the customer's triple and the worst label was
    # used instead (#345); the run reports how many, never hides them.
    via_fallback: bool = False


@dataclass(frozen=True)
class CustomerSales:
    """A customer's raw recency, frequency and monetary values over a window,
    unscored. All three are None when the window holds no sale."""

    customer_id: str
    last_purchase_at: datetime | None
    frequency: int | None
    monetary: Decimal | None


def score_rfm_rules(
    connection: Connection[Any], window_days: int
) -> list[ScoredCustomer]:
    """Score every customer's recency, frequency and spend in quintiles and match
    each against the segment bands. Reads only; the caller records the result."""
    with connection.cursor() as cursor:
        cursor.execute(_SCORE_AND_MATCH, (window_days,) + (QUINTILES,) * 6)
        rows = cursor.fetchall()

    return [ScoredCustomer(str(row[0]), *row[1:]) for row in rows]


def read_rfm_inputs(
    connection: Connection[Any], window_days: int
) -> list[CustomerSales]:
    """Every customer's raw recency, frequency and monetary values over the
    window, ordered by customer id, for a method that scores them itself."""
    with connection.cursor() as cursor:
        cursor.execute(_RFM_INPUTS, (window_days,))
        rows = cursor.fetchall()

    return [CustomerSales(str(row[0]), *row[1:]) for row in rows]


def lock_for_run(connection: Connection[Any]) -> datetime:
    """Serialize runs, and return the instant this run is recorded at.

    Blocks until any other run in flight has committed or rolled back. Held for
    the rest of the transaction (#285), so the read of the prior open
    assignments, the close, and the insert all see one consistent, uncontested
    state.

    The instant is read after the lock is granted, not taken from `now()`:
    `now()` is when the transaction began, which for a run that waited is
    before the run it waited for. Closing that run's rows at such an instant
    would put `valid_to` before their `valid_from`, and its run_at would sort
    it ahead of a run whose assignments it replaced.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", (_RUN_LOCK_KEY,))
        cursor.execute("SELECT clock_timestamp()")
        return cursor.fetchone()[0]


def create_run(
    connection: Connection[Any],
    method: str,
    window_days: int,
    parameters: dict[str, Any],
    customer_count: int,
    *,
    run_at: datetime,
) -> int:
    """Insert the run row, recorded at `run_at`, and return its id.

    `parameters` is ADR-0017's snapshot of what produced the run. It is stored
    with its keys sorted so the same parameters are always the same text, and
    NaN and Infinity are refused: jsonb has neither, and a metric that came out
    as one is a defect in the run that must not be written as a plausible number.
    `executed_by` is the connection setting fn_audit() reads (web/middleware/
    authz.py sets it once per request), not a Python-side actor argument.
    """
    snapshot = json.dumps(parameters, sort_keys=True, allow_nan=False)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO segmentation_run
                (method, window_days, parameters, customer_count, executed_by,
                 run_at)
            VALUES (
                %s, %s, %s::jsonb, %s,
                NULLIF(current_setting('mosaiq.user_id', true), '')::uuid, %s
            )
            RETURNING run_id
            """,
            (method, window_days, snapshot, customer_count, run_at),
        )
        return cursor.fetchone()[0]


def read_open_assignments(
    connection: Connection[Any],
) -> dict[str, tuple[int | None, str | None]]:
    """Each customer's open assignment, as (segment_id, label_code), before a
    run replaces it. A customer with no history is simply absent."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT customer_id, segment_id, label_code
            FROM customer_segment_history
            WHERE valid_to IS NULL
            """
        )
        rows = cursor.fetchall()

    return {
        str(customer_id): (segment_id, label) for customer_id, segment_id, label in rows
    }


def close_open_assignments(
    connection: Connection[Any], customer_ids: list[str], *, at: datetime
) -> None:
    """Close, at `at`, the open assignment of every listed customer, whether or
    not the run is about to write the same result again (ADR-0017)."""
    if not customer_ids:
        return
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE customer_segment_history
               SET valid_to = %s
             WHERE valid_to IS NULL
               AND customer_id = ANY(%s::uuid[])
            """,
            (at, customer_ids),
        )


def insert_assignments(
    connection: Connection[Any],
    run_id: int,
    rows: list[tuple[Any, ...]],
    *,
    at: datetime,
) -> None:
    """Open one history row per customer for this run.

    Each row is (customer, segment, label, last purchase, frequency, monetary,
    r, f, m). The pipeline has already closed the previous open rows, and `at`
    is the same instant they were closed at.
    """
    if not rows:
        return
    with connection.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO customer_segment_history
                (customer_id, run_id, segment_id, label_code,
                 recency_last_purchase_at, frequency_count, monetary_total,
                 r_score, f_score, m_score, valid_from)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [(row[0], run_id, *row[1:], at) for row in rows],
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
            pattern = ilike_pattern(search)
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
            pattern = ilike_pattern(search)
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
    label_code: str | None
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
            SELECT customer_id, segment_id, label_code, r_score, f_score,
                   m_score, valid_from
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
            SELECT customer_id, segment_id, label_code, r_score, f_score,
                   m_score, valid_from
            FROM customer_segment_history
            WHERE customer_id = ANY(%s::uuid[]) AND valid_to IS NULL
            """,
            ([str(cid) for cid in customer_ids],),
        )
        rows = cursor.fetchall()

    return {str(row[0]): CustomerSegmentAssignment(*row) for row in rows}


# ---------- reading for migration (F7-04) ----------


def get_run_at(connection: Connection[Any], run_id: int) -> datetime | None:
    """Return one run's timestamp, or None if the run does not exist.

    Only run_at — never method — so a caller comparing two runs can validate
    and order them without being tempted to branch on method (ADR-0018).
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT run_at FROM segmentation_run WHERE run_id = %s", (run_id,)
        )
        row = cursor.fetchone()

    return row[0] if row else None


def list_run_labels(connection: Connection[Any], run_id: int) -> dict[Any, str | None]:
    """Return every customer this run scored, mapped to the label_code it
    assigned — None where the customer was scored but left unassigned
    (RN-21). SQL only: the migration classification itself lives in
    web/services/segment_migration.py (ADR-0003)."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT customer_id, label_code FROM customer_segment_history"
            " WHERE run_id = %s",
            (run_id,),
        )
        return dict(cursor.fetchall())


def get_label_ordinals(connection: Connection[Any]) -> dict[str, int]:
    """Return every label_code mapped to its ordinal_position (ADR-0018's
    declared best-to-worst business order) — the vocabulary migration
    direction is computed against."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT label_code, ordinal_position FROM segment_label")
        return dict(cursor.fetchall())


def get_label_name(connection: Connection[Any], label_code: str) -> str | None:
    """Return a label's display name, or None if the code is not in the
    vocabulary."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT name FROM segment_label WHERE label_code = %s", (label_code,)
        )
        row = cursor.fetchone()

    return row[0] if row else None


# ---------- run history (F7-03) ----------


@dataclass(frozen=True)
class SegmentationRun:
    run_id: int
    method: str
    window_days: int
    parameters: dict
    customer_count: int
    executed_by: UUID | None
    executed_by_name: str | None
    run_at: datetime


def list_runs(
    connection: Connection[Any], *, page: int, per_page: int
) -> tuple[list[SegmentationRun], int]:
    """Return a page of completed runs, most recent first, and the total."""
    offset = (page - 1) * per_page

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT r.run_id, r.method, r.window_days, r.parameters,
                   r.customer_count, r.executed_by, u.name, r.run_at
            FROM segmentation_run AS r
            LEFT JOIN app_user AS u ON u.user_id = r.executed_by
            ORDER BY r.run_at DESC, r.run_id DESC
            LIMIT %s OFFSET %s
            """,
            (per_page, offset),
        )
        rows = cursor.fetchall()

        cursor.execute("SELECT count(*) FROM segmentation_run")
        total = cursor.fetchone()[0]

    return [SegmentationRun(*row) for row in rows], total


def get_run(connection: Connection[Any], run_id: int) -> SegmentationRun | None:
    """Return one run by id, or None if it does not exist."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT r.run_id, r.method, r.window_days, r.parameters,
                   r.customer_count, r.executed_by, u.name, r.run_at
            FROM segmentation_run AS r
            LEFT JOIN app_user AS u ON u.user_id = r.executed_by
            WHERE r.run_id = %s
            """,
            (run_id,),
        )
        row = cursor.fetchone()

    return SegmentationRun(*row) if row else None


@dataclass(frozen=True)
class RunAssignment:
    """One customer's result within a specific run — every customer the run
    scored, including those left unassigned (RN-21): segment_id and
    label_code are simply None for them, never omitted."""

    customer_id: UUID
    customer_name: str
    segment_id: int | None
    label_code: str | None
    r_score: int | None
    f_score: int | None
    m_score: int | None
    recency_last_purchase_at: datetime | None
    frequency_count: int | None
    monetary_total: Decimal | None


def list_run_assignments(
    connection: Connection[Any], run_id: int, *, page: int, per_page: int
) -> tuple[list[RunAssignment], int]:
    """Return a page of this run's customer assignments, and the total.

    Every customer the run scored appears here, whether or not they matched
    a segment — unassigned is a result, not an omission (RN-21).
    """
    offset = (page - 1) * per_page

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT h.customer_id, c.name, h.segment_id, h.label_code,
                   h.r_score, h.f_score, h.m_score,
                   h.recency_last_purchase_at, h.frequency_count,
                   h.monetary_total
            FROM customer_segment_history AS h
            JOIN customer AS c ON c.customer_id = h.customer_id
            WHERE h.run_id = %s
            ORDER BY c.name, h.customer_id
            LIMIT %s OFFSET %s
            """,
            (run_id, per_page, offset),
        )
        rows = cursor.fetchall()

        cursor.execute(
            "SELECT count(*) FROM customer_segment_history WHERE run_id = %s",
            (run_id,),
        )
        total = cursor.fetchone()[0]

    return [RunAssignment(*row) for row in rows], total


def get_customer_assignment_for_run(
    connection: Connection[Any], run_id: int, customer_id: Any
) -> RunAssignment | None:
    """Return one customer's result within one specific run, or None if
    that customer was not part of the run (F7-06). Raw R/F/M values and
    scores come straight from the stored history row -- no recomputation
    (ADR-0017)."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT h.customer_id, c.name, h.segment_id, h.label_code,
                   h.r_score, h.f_score, h.m_score,
                   h.recency_last_purchase_at, h.frequency_count,
                   h.monetary_total
            FROM customer_segment_history AS h
            JOIN customer AS c ON c.customer_id = h.customer_id
            WHERE h.run_id = %s AND h.customer_id = %s
            """,
            (run_id, str(customer_id)),
        )
        row = cursor.fetchone()

    return RunAssignment(*row) if row else None
