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
customer's result lands in a new `customer_segment_history` row. The statement
closes the previously open row even when the segment is unchanged, so every
run records one result row per customer. Its `changed_flag` distinguishes
changed assignments only for the result counts; it does not decide which rows
are written.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg import Connection

# Quintiles: 5 is the best score in each measure — most recent, most frequent,
# highest spend — which is the orientation segment_rule's bands are written in.
_QUINTILES = 5

_RECALCULATE = """
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
),
matched AS (
    -- Every customer, not only those with sales: a customer absent from
    -- window_sales has no r/f/m and no matching segment, so they land here
    -- with all columns but customer_id NULL — RN-21's unassigned result,
    -- recorded rather than skipped. This is the only case ADR-0018 allows a
    -- null label for.
    --
    -- The LATERAL join picks the same "lowest segment_id" winner the old
    -- MIN(seg.segment_id) subquery did (ORDER BY segment_id LIMIT 1 is
    -- equivalent), but as a join it can also carry that winning row's
    -- label_code -- ADR-0018's stable vocabulary -- without a second,
    -- possibly inconsistent, correlated subquery.
    --
    -- A customer who *does* have sales but matches no rule band still gets a
    -- label — ADR-0018 forbids a null label for anyone actually scored. The
    -- second LATERAL falls back to the worst-ranked segment (highest
    -- ordinal_position) only when the first found no exact band match and
    -- the customer has an r/f/m triple to fall back from at all.
    SELECT c.customer_id,
           s.r, s.f, s.m,
           w.last_purchase, w.frequency, w.monetary,
           COALESCE(bm.segment_id, fb.segment_id)     AS segment_id,
           COALESCE(bm.label_code, fb.label_code)     AS label_code
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
),
new_run AS (
    -- Placed after `matched`, not before: `matched` doesn't depend on the
    -- run row at all, and a data-modifying CTE cannot see another
    -- statement's effect on its own target table within the same query
    -- (only RETURNING crosses that boundary) -- an earlier version tried to
    -- backfill customer_count with a second UPDATE CTE keyed on new_run's
    -- RETURNING id, and PostgreSQL silently matched zero rows because
    -- new_run's insert isn't visible to a plain WHERE-clause scan of
    -- segmentation_run from a sibling CTE. Computing the count here, before
    -- the row is even inserted, sidesteps that rule entirely.
    --
    -- parameters and executed_by are ADR-0017's parameter snapshot and
    -- executing user. executed_by reuses the same connection GUC fn_audit()
    -- reads (web/middleware/authz.py sets it once per request), not a new
    -- Python-side actor parameter.
    INSERT INTO segmentation_run
        (method, window_days, parameters, customer_count, executed_by)
    VALUES (
        'RFM_RULES', %s, jsonb_build_object('window_days', %s),
        (SELECT count(*) FROM matched),
        NULLIF(current_setting('mosaiq.user_id', true), '')::uuid
    )
    RETURNING run_id
),
current_open AS (
    SELECT h.customer_id, h.segment_id AS open_segment_id
    FROM customer_segment_history AS h
    WHERE h.valid_to IS NULL
),
changed AS (
    -- ADR-0017: every customer in the run writes a history row, whether or
    -- not their result changed -- the ADR's own compliance query checks
    -- count(h.run_id) = r.customer_count for every run. changed_flag keeps
    -- the distinction the old IS DISTINCT FROM guard used to make, now for
    -- the result counts below rather than for deciding who gets written.
    SELECT m.customer_id, m.r, m.f, m.m,
           m.last_purchase, m.frequency, m.monetary,
           m.segment_id, m.label_code,
           (co.customer_id IS NULL
              OR co.open_segment_id IS DISTINCT FROM m.segment_id) AS changed_flag
    FROM matched AS m
    LEFT JOIN current_open AS co ON co.customer_id = m.customer_id
),
closed AS (
    -- Closes the prior open row for every customer, "including when labels
    -- do not change" (ADR-0017) -- there is no WHERE on changed_flag here.
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
        (customer_id, run_id, segment_id, label_code,
         recency_last_purchase_at, frequency_count, monetary_total,
         r_score, f_score, m_score, valid_from)
    SELECT ch.customer_id,
           (SELECT run_id FROM new_run),
           ch.segment_id, ch.label_code,
           ch.last_purchase, ch.frequency, ch.monetary,
           ch.r, ch.f, ch.m,
           now()
    FROM changed AS ch
    LEFT JOIN closed AS cl ON cl.customer_id = ch.customer_id
    RETURNING customer_id, segment_id
)
SELECT (SELECT count(*) FROM matched)                              AS processed,
       (SELECT count(*) FROM matched WHERE segment_id IS NOT NULL) AS assigned,
       (SELECT count(*) FROM matched WHERE segment_id IS NULL)     AS unmatched,
       (SELECT count(*) FROM changed
         WHERE changed_flag AND segment_id IS NOT NULL)            AS reassigned,
        (SELECT count(*) FROM changed
         WHERE changed_flag AND segment_id IS NULL)                AS cleared
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
        cursor.execute(
            _RECALCULATE,
            (window_days,) + (_QUINTILES,) * 6 + (window_days, window_days),
        )
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
