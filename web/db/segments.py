"""The segment recalculation, expressed as one statement (F3-10).

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

Nothing is written for a customer whose segment does not change:
`IS DISTINCT FROM` is what keeps the audit trigger quiet on a second run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from psycopg import Connection

# Quintiles: 5 is the best score in each measure — most recent, most frequent,
# highest spend — which is the orientation segment_rule's bands are written in.
_QUINTILES = 5

_RECALCULATE = f"""
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
           {_QUINTILES} + 1 - ntile({_QUINTILES})
               OVER (ORDER BY last_purchase DESC, customer_id) AS r,
           {_QUINTILES} + 1 - ntile({_QUINTILES})
               OVER (ORDER BY frequency DESC, customer_id)     AS f,
           {_QUINTILES} + 1 - ntile({_QUINTILES})
               OVER (ORDER BY monetary DESC, customer_id)      AS m
    FROM window_sales
),
matched AS (
    SELECT s.customer_id,
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
      FROM scored AS s
),
assigned AS (
    UPDATE customer AS c
       SET current_segment_id = m.segment_id
      FROM matched AS m
     WHERE c.customer_id = m.customer_id
       AND c.current_segment_id IS DISTINCT FROM m.segment_id
    RETURNING c.customer_id
),
cleared AS (
    UPDATE customer AS c
       SET current_segment_id = NULL
     WHERE c.current_segment_id IS NOT NULL
       AND NOT EXISTS (
           SELECT 1 FROM matched AS m WHERE m.customer_id = c.customer_id
       )
    RETURNING c.customer_id
)
SELECT (SELECT count(*) FROM matched)                            AS processed,
       (SELECT count(*) FROM matched WHERE segment_id IS NOT NULL) AS assigned,
       (SELECT count(*) FROM matched WHERE segment_id IS NULL)     AS unmatched,
       (SELECT count(*) FROM assigned)                             AS reassigned,
       (SELECT count(*) FROM cleared)                              AS cleared
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
    """Score, match and write every customer's segment. One statement.

    The caller owns the transaction: nothing here commits, so a run that fails
    part way leaves the assignment exactly as it was.
    """
    with connection.cursor() as cursor:
        cursor.execute(_RECALCULATE, (window_days,))
        row = cursor.fetchone()

    return RecalculationCounts(
        processed=row[0],
        assigned=row[1],
        unmatched=row[2],
        reassigned=row[3],
        cleared=row[4],
    )
