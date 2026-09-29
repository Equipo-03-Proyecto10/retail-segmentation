"""Data access for conversion (F11-06, ADR-0019).

Conversion is a link from an assignment to a real sale, never a column on the
sale. Every statement is parameterized; the rules live in
web/services/experiment_conversions.py, which owns the transaction (ADR-0014).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from psycopg import Connection


@dataclass(frozen=True)
class GroupConversion:
    """One arm's outcome counts. Every assigned customer is in exactly one of
    `converted`, `pending` and `not_converted`, judged from the conversions
    already recorded. `unrecorded` is not a fourth outcome: it counts those
    not yet recorded as converted who nonetheless have a qualifying sale, which
    the next evaluation will attribute."""

    group_id: int
    kind: str
    assigned: int
    converted: int
    pending: int
    not_converted: int
    unrecorded: int


@dataclass(frozen=True)
class Attribution:
    """A conversion with everything needed to reproduce it: the assignment, the
    window it was judged against and the sale that qualified."""

    conversion_id: int
    assignment_id: int
    customer_id: str
    kind: str
    assigned_at: datetime
    window_ends_at: datetime
    transaction_id: int
    source_transaction_id: str
    occurred_at: datetime
    total: Decimal


def evaluate_conversions(connection: Connection, experiment_id: int) -> int:
    """Attribute every qualifying sale not yet attributed; return how many rows
    were added.

    A sale qualifies when it is the assigned customer's own and falls in
    [assigned_at, assigned_at + conversion window). The window is the
    experiment's, fixed at its first assignment, and the sale's own timestamp is
    what is compared, so the outcome is the same whenever this runs. Running it
    again adds nothing: UNIQUE (assignment_id, transaction_id) is the final word.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO experiment_conversion (assignment_id, transaction_id)
            SELECT a.assignment_id, t.transaction_id
              FROM experiment_assignment AS a
              JOIN experiment AS e ON e.experiment_id = a.experiment_id
              JOIN transaction AS t
                ON t.customer_id = a.customer_id
               AND t.occurred_at >= a.assigned_at
               AND t.occurred_at < a.assigned_at
                                   + make_interval(days => e.conversion_window_days)
             WHERE a.experiment_id = %s
            ON CONFLICT (assignment_id, transaction_id) DO NOTHING
            """,
            (experiment_id,),
        )
        return cursor.rowcount


def list_group_conversion(
    connection: Connection, experiment_id: int, now: datetime
) -> list[GroupConversion]:
    """Outcome counts per group, control first.

    A customer with no attributed sale is *pending* while their window is still
    open at `now`, and *not converted* only once it has closed: an unfinished
    window is not a failure to convert. The outcomes count recorded
    conversions only; `unrecorded` says how many of the rest already have a
    qualifying sale, so the page cannot pass off "not yet evaluated" as
    "did not convert".
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            WITH outcome AS (
                SELECT a.group_id,
                       EXISTS (SELECT 1 FROM experiment_conversion AS c
                                WHERE c.assignment_id = a.assignment_id) AS converted,
                       a.assigned_at
                       + make_interval(days => e.conversion_window_days) > %s AS open,
                       EXISTS (SELECT 1 FROM transaction AS t
                                WHERE t.customer_id = a.customer_id
                                  AND t.occurred_at >= a.assigned_at
                                  AND t.occurred_at < a.assigned_at
                                      + make_interval(
                                          days => e.conversion_window_days))
                           AS qualifies
                  FROM experiment_assignment AS a
                  JOIN experiment AS e ON e.experiment_id = a.experiment_id
                 WHERE a.experiment_id = %s
            )
            SELECT g.group_id, g.kind, count(o.group_id),
                   count(*) FILTER (WHERE o.converted),
                   count(*) FILTER (WHERE NOT o.converted AND o.open),
                   count(*) FILTER (WHERE NOT o.converted AND NOT o.open),
                   count(*) FILTER (WHERE NOT o.converted AND o.qualifies)
              FROM experiment_group AS g
              LEFT JOIN outcome AS o ON o.group_id = g.group_id
             WHERE g.experiment_id = %s
             GROUP BY g.group_id, g.kind
             ORDER BY g.kind <> 'CONTROL', g.group_id
            """,
            (now, experiment_id, experiment_id),
        )
        return [GroupConversion(*row) for row in cursor.fetchall()]


def list_attributions(
    connection: Connection, experiment_id: int, *, page: int, per_page: int
) -> tuple[list[Attribution], int]:
    """One page of conversions, newest sale first, and the total count."""
    offset = (page - 1) * per_page
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT c.conversion_id, a.assignment_id, a.customer_id, g.kind,
                   a.assigned_at,
                   a.assigned_at + make_interval(days => e.conversion_window_days),
                   t.transaction_id, t.source_transaction_id, t.occurred_at, t.total
              FROM experiment_conversion AS c
              JOIN experiment_assignment AS a ON a.assignment_id = c.assignment_id
              JOIN experiment_group AS g ON g.group_id = a.group_id
              JOIN experiment AS e ON e.experiment_id = a.experiment_id
              JOIN transaction AS t ON t.transaction_id = c.transaction_id
             WHERE a.experiment_id = %s
             ORDER BY t.occurred_at DESC, c.conversion_id DESC
             LIMIT %s OFFSET %s
            """,
            (experiment_id, per_page, offset),
        )
        rows = cursor.fetchall()
        cursor.execute(
            """
            SELECT count(*)
              FROM experiment_conversion AS c
              JOIN experiment_assignment AS a ON a.assignment_id = c.assignment_id
             WHERE a.experiment_id = %s
            """,
            (experiment_id,),
        )
        total = cursor.fetchone()[0]
    return [Attribution(row[0], row[1], str(row[2]), *row[3:]) for row in rows], total


def read_aa_population(
    connection: Connection, cutoff: datetime, window_days: int
) -> list[tuple[str, bool]]:
    """Every customer with a sale before `cutoff`, and whether they bought in the
    `window_days` from it: the population an A/A validation splits (ADR-0019).
    Nothing after the cut-off decides who is in it."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT known.customer_id,
                   EXISTS (SELECT 1 FROM transaction AS later
                            WHERE later.customer_id = known.customer_id
                              AND later.occurred_at >= %s
                              AND later.occurred_at < %s
                                                     + make_interval(days => %s))
              FROM (SELECT DISTINCT customer_id FROM transaction
                     WHERE occurred_at < %s) AS known
             ORDER BY known.customer_id
            """,
            (cutoff, cutoff, window_days, cutoff),
        )
        return [(str(row[0]), bool(row[1])) for row in cursor.fetchall()]
