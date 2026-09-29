"""Data access for experiment setup (F11-03).

Every statement is parameterized; the setup rules live in
web/services/experiments.py, which owns the transaction (ADR-0014).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from psycopg import Connection

# Takes a transaction-scoped advisory lock, so two concurrent creates cannot
# both read the same max(experiment_id) or max(group_id): neither column has an
# identity (F11-01 kept the seeded ids), the same reason campaigns lock.
_CREATE_LOCK_KEY = 222_001

CONTROL = "CONTROL"
TREATMENT = "TREATMENT"


@dataclass(frozen=True)
class Experiment:
    experiment_id: int
    name: str
    campaign_id: int | None
    campaign_name: str | None
    target_metric: str
    starts_on: date
    ends_on: date | None
    conversion_window_days: int
    data_origin: str
    control_groups: int
    treatment_groups: int
    assignments: int


@dataclass(frozen=True)
class CampaignChoice:
    campaign_id: int
    name: str
    status: str


@dataclass(frozen=True)
class GroupCounts:
    experiment_id: int
    name: str
    control_groups: int
    treatment_groups: int


# One row per experiment with its group and assignment counts; the two reads
# differ only in their last clause.
_LIST_EXPERIMENTS = """
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
     ORDER BY e.experiment_id DESC
     LIMIT %s OFFSET %s
"""

_GET_EXPERIMENT = """
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
     WHERE e.experiment_id = %s
"""


def list_experiments(
    connection: Connection, *, page: int, per_page: int
) -> tuple[list[Experiment], int]:
    """Return one page of experiments, newest first, and the total row count."""
    offset = (page - 1) * per_page
    with connection.cursor() as cursor:
        cursor.execute(_LIST_EXPERIMENTS, (per_page, offset))
        rows = cursor.fetchall()
        cursor.execute("SELECT count(*) FROM experiment")
        total = cursor.fetchone()[0]
    return [Experiment(*row) for row in rows], total


def get_experiment(connection: Connection, experiment_id: int) -> Experiment | None:
    """Return one experiment with its group and assignment counts, or None."""
    with connection.cursor() as cursor:
        cursor.execute(_GET_EXPERIMENT, (experiment_id,))
        row = cursor.fetchone()
    return None if row is None else Experiment(*row)


def lock_experiment(connection: Connection, experiment_id: int) -> bool:
    """Lock the experiment row for the rest of the transaction; False if absent.

    An edit takes this before counting assignments, so the count it acts on is
    the one it writes against. F11-04 must take at least FOR SHARE on the same
    row before recording an experiment's first assignment, or an edit and that
    assignment could each pass their check concurrently.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM experiment WHERE experiment_id = %s FOR UPDATE",
            (experiment_id,),
        )
        return cursor.fetchone() is not None


def list_campaign_choices(connection: Connection) -> list[CampaignChoice]:
    """Every campaign an experiment can be attached to, newest first."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT campaign_id, name, status FROM campaign ORDER BY campaign_id DESC"
        )
        rows = cursor.fetchall()
    return [CampaignChoice(*row) for row in rows]


def create_experiment(
    connection: Connection,
    *,
    name: str,
    campaign_id: int | None,
    target_metric: str,
    starts_on: date,
    ends_on: date | None,
    conversion_window_days: int,
    data_origin: str,
    treatment_groups: int,
) -> int | None:
    """Insert an experiment with one control and `treatment_groups` treatment
    groups under the next free ids, and return its id; or None when one with
    the same name, campaign and start date already exists (a resubmitted
    form, as #296 found for campaigns, not a second experiment)."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", (_CREATE_LOCK_KEY,))
        cursor.execute(
            """
            SELECT 1 FROM experiment
             WHERE name = %s AND campaign_id IS NOT DISTINCT FROM %s::int
               AND starts_on = %s
            """,
            (name, campaign_id, starts_on),
        )
        if cursor.fetchone() is not None:
            return None
        cursor.execute(
            """
            INSERT INTO experiment (experiment_id, name, campaign_id, target_metric,
                                    starts_on, ends_on, conversion_window_days,
                                    data_origin)
            SELECT COALESCE(max(experiment_id), 0) + 1, %s, %s, %s, %s, %s, %s, %s
            FROM experiment
            RETURNING experiment_id
            """,
            (
                name,
                campaign_id,
                target_metric,
                starts_on,
                ends_on,
                conversion_window_days,
                data_origin,
            ),
        )
        experiment_id = cursor.fetchone()[0]
        cursor.execute("SELECT COALESCE(max(group_id), 0) FROM experiment_group")
        last_group = cursor.fetchone()[0]
        kinds = [CONTROL] + [TREATMENT] * treatment_groups
        cursor.executemany(
            "INSERT INTO experiment_group (group_id, experiment_id, kind) "
            "VALUES (%s, %s, %s)",
            [
                (last_group + offset, experiment_id, kind)
                for offset, kind in enumerate(kinds, start=1)
            ],
        )
        return experiment_id


def update_experiment(
    connection: Connection,
    experiment_id: int,
    *,
    name: str,
    campaign_id: int | None,
    target_metric: str,
    starts_on: date,
    ends_on: date | None,
    conversion_window_days: int,
) -> bool:
    """Rewrite an experiment's editable fields. `data_origin` is not among
    them: it is fixed at creation (RN-26). Returns False when no row matched."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE experiment
               SET name = %s, campaign_id = %s, target_metric = %s,
                   starts_on = %s, ends_on = %s, conversion_window_days = %s
             WHERE experiment_id = %s
            """,
            (
                name,
                campaign_id,
                target_metric,
                starts_on,
                ends_on,
                conversion_window_days,
                experiment_id,
            ),
        )
        return cursor.rowcount == 1


def list_group_counts_for_campaign(
    connection: Connection, campaign_id: int
) -> list[GroupCounts]:
    """Each experiment attached to the campaign, with its control and treatment
    group counts, for the check activation makes (RN-24)."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT e.experiment_id, e.name,
                   count(g.group_id) FILTER (WHERE g.kind = 'CONTROL'),
                   count(g.group_id) FILTER (WHERE g.kind = 'TREATMENT')
              FROM experiment AS e
              LEFT JOIN experiment_group AS g ON g.experiment_id = e.experiment_id
             WHERE e.campaign_id = %s
             GROUP BY e.experiment_id, e.name
             ORDER BY e.experiment_id
            """,
            (campaign_id,),
        )
        rows = cursor.fetchall()
    return [GroupCounts(*row) for row in rows]


# ---------- assignment (F11-04) ----------


def list_groups(connection: Connection, experiment_id: int) -> list[tuple[int, str]]:
    """The experiment's groups as (group_id, kind), control first, then the
    treatments in id order: the order customers are dealt into them."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT group_id, kind FROM experiment_group
             WHERE experiment_id = %s
             ORDER BY kind <> 'CONTROL', group_id
            """,
            (experiment_id,),
        )
        return [(group_id, kind) for group_id, kind in cursor.fetchall()]


def read_target_population(connection: Connection, label_code: str) -> list[str]:
    """Every customer whose open segment assignment carries the label, in
    customer_id order: the population a campaign on that label targets now."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT customer_id FROM customer_segment_history
             WHERE valid_to IS NULL AND label_code = %s
             ORDER BY customer_id
            """,
            (label_code,),
        )
        return [str(row[0]) for row in cursor.fetchall()]


def insert_assignments(
    connection: Connection, experiment_id: int, rows: list[tuple[int, str]]
) -> None:
    """Write one assignment per (group_id, customer_id). `assigned_at` is the
    column's default, so every row of one assignment carries the same instant.

    There is deliberately no function here that updates or deletes an
    assignment: once written it is never rewritten (RN-42, ADR-0026), and the
    schema refuses retail_app both statements as well.
    """
    if not rows:
        return
    with connection.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO experiment_assignment (experiment_id, group_id, customer_id) "
            "VALUES (%s, %s, %s)",
            [(experiment_id, group_id, customer_id) for group_id, customer_id in rows],
        )


# ---------- exposure (F11-05) ----------


@dataclass(frozen=True)
class GroupExposure:
    """One arm's counts: how many customers were assigned to it, and how many
    of them have at least one exposure. An assigned customer never exposed
    stays in `assigned` and is absent from `exposed` (ADR-0019)."""

    group_id: int
    kind: str
    assigned: int
    exposed: int


def find_assignment(
    connection: Connection, experiment_id: int, customer_id: str
) -> tuple[int, str] | None:
    """The customer's assignment in the experiment as (assignment_id, kind of
    its group), or None when they were never assigned."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT a.assignment_id, g.kind
              FROM experiment_assignment AS a
              JOIN experiment_group AS g ON g.group_id = a.group_id
             WHERE a.experiment_id = %s AND a.customer_id = %s
            """,
            (experiment_id, customer_id),
        )
        row = cursor.fetchone()
    return None if row is None else (row[0], row[1])


def insert_exposure(connection: Connection, assignment_id: int) -> None:
    """Record one exposure. `exposed_at` is the column's default, its own
    timestamp, separate from `assigned_at`. Exposures are only ever added."""
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO experiment_exposure (assignment_id) VALUES (%s)",
            (assignment_id,),
        )


def list_group_exposure(
    connection: Connection, experiment_id: int
) -> list[GroupExposure]:
    """Assigned and exposed customers per group, control first."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT g.group_id, g.kind, count(a.assignment_id),
                   count(a.assignment_id) FILTER (
                       WHERE EXISTS (SELECT 1 FROM experiment_exposure AS x
                                      WHERE x.assignment_id = a.assignment_id))
              FROM experiment_group AS g
              LEFT JOIN experiment_assignment AS a ON a.group_id = g.group_id
             WHERE g.experiment_id = %s
             GROUP BY g.group_id, g.kind
             ORDER BY g.kind <> 'CONTROL', g.group_id
            """,
            (experiment_id,),
        )
        return [GroupExposure(*row) for row in cursor.fetchall()]
