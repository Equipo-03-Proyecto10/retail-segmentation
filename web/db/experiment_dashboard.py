"""Data access for the experiment dashboard (AN-03, ADR-0019).

Read-only. Every statement is parameterized. The arms' counts and the
per-exposure figures come from web/db/experiment_conversions.py, the same reads
the uplift page uses; this module adds the one thing they do not give, when
within the window each customer first converted.
"""

from __future__ import annotations

from dataclasses import dataclass

from psycopg import Connection

SECONDS_PER_DAY = 86_400


@dataclass(frozen=True)
class ConversionDay:
    """How many of an arm's assigned customers first converted `day` days
    after their own assignment (day 0 is the first 24 hours)."""

    group_id: int
    day: int
    customers: int


# A customer's first recorded conversion is the earliest of their attributed
# sales, so a customer who bought three times in the window is one conversion
# on one day, not three. The window test repeats the one the conversion was
# recorded under, so a row outside it can never be charted.
_CONVERSION_DAYS = """
    SELECT a.group_id,
           floor(extract(epoch FROM (first_sale.first_at - a.assigned_at))
                 / %(seconds_per_day)s)::int AS day,
           count(*)
      FROM experiment_assignment AS a
      JOIN experiment AS e ON e.experiment_id = a.experiment_id
      JOIN (SELECT x.assignment_id, min(t.occurred_at) AS first_at
              FROM experiment_assignment AS x
              JOIN experiment_conversion AS c ON c.assignment_id = x.assignment_id
              JOIN transaction AS t ON t.transaction_id = c.transaction_id
             WHERE x.experiment_id = %(experiment)s
             GROUP BY x.assignment_id) AS first_sale
        ON first_sale.assignment_id = a.assignment_id
     WHERE a.experiment_id = %(experiment)s
       AND first_sale.first_at >= a.assigned_at
       AND first_sale.first_at < a.assigned_at
                                 + make_interval(days => e.conversion_window_days)
     GROUP BY a.group_id, day
     ORDER BY a.group_id, day
"""


def list_conversion_days(
    connection: Connection, experiment_id: int
) -> list[ConversionDay]:
    """Per arm, the customers who first converted on each day since their
    assignment. Days with nobody are absent; the service fills them."""
    with connection.cursor() as cursor:
        cursor.execute(
            _CONVERSION_DAYS,
            {"experiment": experiment_id, "seconds_per_day": SECONDS_PER_DAY},
        )
        return [ConversionDay(*row) for row in cursor.fetchall()]
