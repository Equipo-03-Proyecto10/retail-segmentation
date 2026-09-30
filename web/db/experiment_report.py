"""Data access for the campaign and experiment report (F12-04).

Read-only. Every statement is parameterized. Assignment, exposure and
conversion are counted from their own relations and returned separately: the
report never derives one from another (ADR-0019).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from psycopg import Connection

from web.db.experiment_conversions import EXPOSED_CONVERTED_SQL
from web.db.experiments import Experiment


@dataclass(frozen=True)
class ReportGroup:
    """One arm's three displayed counts and conversion completeness."""

    experiment_id: int
    group_id: int
    kind: str
    assigned: int
    exposed: int
    converted: int
    pending: int
    unrecorded: int = 0
    name: str = ""
    treatment_description: str = ""
    exposed_converted: int = 0

    @property
    def not_converted(self) -> int:
        return self.assigned - self.converted - self.pending

    @property
    def conversion_rate(self) -> float | None:
        """Intent to treat: converted over every assigned customer."""
        return self.converted / self.assigned if self.assigned else None

    @property
    def exposed_conversion_rate(self) -> float | None:
        """Per exposure: bought after being exposed, over customers exposed."""
        return self.exposed_converted / self.exposed if self.exposed else None


_FILTER = """
     WHERE (%(campaign)s::int IS NULL OR e.campaign_id = %(campaign)s::int)
       AND (%(origin)s::text IS NULL OR e.data_origin = %(origin)s::text)
"""

_EXPERIMENT_SELECT = """
    SELECT e.experiment_id, e.name, e.campaign_id, c.name, e.target_metric,
           e.starts_on, e.ends_on, e.conversion_window_days, e.data_origin,
           (SELECT count(*) FROM experiment_group AS g
             WHERE g.experiment_id = e.experiment_id AND g.kind = 'CONTROL'),
           (SELECT count(*) FROM experiment_group AS g
             WHERE g.experiment_id = e.experiment_id AND g.kind = 'TREATMENT'),
           (SELECT count(*) FROM experiment_assignment AS a
             WHERE a.experiment_id = e.experiment_id)
      FROM experiment AS e
      LEFT JOIN campaign AS c ON c.campaign_id = e.campaign_id
"""

_LIST_EXPERIMENTS = (
    _EXPERIMENT_SELECT
    + _FILTER
    + " ORDER BY e.experiment_id DESC LIMIT %(limit)s OFFSET %(offset)s"
)
_COUNT_EXPERIMENTS = "SELECT count(*) FROM experiment AS e" + _FILTER

_LIST_ACTIVE_EXPERIMENTS = (
    _EXPERIMENT_SELECT
    + """
     WHERE e.starts_on <= %(active_on)s
       AND (e.ends_on IS NULL OR e.ends_on >= %(active_on)s)
       AND (c.status IS NULL OR c.status NOT IN (%(finished)s, %(cancelled)s))
       AND EXISTS (
           SELECT 1 FROM experiment_assignment AS a
            WHERE a.experiment_id = e.experiment_id
       )
     ORDER BY e.experiment_id DESC
     LIMIT %(limit)s
    """
)


def list_report_experiments(
    connection: Connection,
    *,
    campaign_id: int | None,
    data_origin: str | None,
    limit: int,
    offset: int,
) -> tuple[list[Experiment], int]:
    """The experiments matching every filter given, newest first, and how many
    match in all. The page and its total read the same filter."""
    params = {
        "campaign": campaign_id,
        "origin": data_origin,
        "limit": limit,
        "offset": offset,
    }
    with connection.cursor() as cursor:
        cursor.execute(_LIST_EXPERIMENTS, params)
        rows = cursor.fetchall()
        cursor.execute(_COUNT_EXPERIMENTS, params)
        total = cursor.fetchone()[0]
    return [Experiment(*row) for row in rows], total


def list_active_report_experiments(
    connection: Connection, *, active_on: date, limit: int
) -> list[Experiment]:
    """The newest assigned experiments active on the canonical business date.

    A finished or cancelled campaign makes its experiment inactive even while
    the experiment's own date range still covers ``active_on`` (#340). The
    database applies every predicate before ``limit``, so an excluded newer
    experiment cannot hide an older active one.
    """
    params = {
        "active_on": active_on,
        "finished": "FINISHED",
        "cancelled": "CANCELLED",
        "limit": limit,
    }
    with connection.cursor() as cursor:
        cursor.execute(_LIST_ACTIVE_EXPERIMENTS, params)
        rows = cursor.fetchall()
    return [Experiment(*row) for row in rows]


_LIST_GROUPS = (
    """
    WITH flags AS (
        SELECT a.group_id,
               EXISTS (SELECT 1 FROM experiment_exposure AS x
                        WHERE x.assignment_id = a.assignment_id) AS exposed,
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
                   AS qualifies,
    """
    + EXPOSED_CONVERTED_SQL
    + """ AS exposed_converted
          FROM experiment_assignment AS a
          JOIN experiment AS e ON e.experiment_id = a.experiment_id
         WHERE a.experiment_id = ANY(%s)
    )
    SELECT g.experiment_id, g.group_id, g.kind, count(f.group_id),
           count(*) FILTER (WHERE f.exposed),
           count(*) FILTER (WHERE f.converted),
           count(*) FILTER (WHERE NOT f.converted AND f.open),
           count(*) FILTER (WHERE NOT f.converted AND f.qualifies),
           g.name, g.treatment_description,
           count(*) FILTER (WHERE f.exposed_converted)
      FROM experiment_group AS g
      LEFT JOIN flags AS f ON f.group_id = g.group_id
     WHERE g.experiment_id = ANY(%s)
     GROUP BY g.experiment_id, g.group_id, g.kind,
              g.name, g.treatment_description
     ORDER BY g.experiment_id DESC, g.kind <> 'CONTROL', g.group_id
    """
)


def list_report_groups(
    connection: Connection, experiment_ids: list[int], now: datetime
) -> list[ReportGroup]:
    """Each group of the given experiments with its assigned, exposed, converted
    and pending counts. A customer is pending while their window is open at
    `now` and they have not converted. `unrecorded` catches qualifying sales
    that must be evaluated before uplift can be measured. `exposed_converted`
    is the per-exposure figure (#343)."""
    if not experiment_ids:
        return []
    with connection.cursor() as cursor:
        cursor.execute(_LIST_GROUPS, (now, experiment_ids, experiment_ids))
        return [ReportGroup(*row) for row in cursor.fetchall()]
