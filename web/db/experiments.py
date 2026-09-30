"""Data access for experiment setup (F11-03).

Every statement is parameterized; the setup rules live in
web/services/experiments.py, which owns the transaction (ADR-0014).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

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


@dataclass(frozen=True)
class ExperimentGroup:
    """A fixed, user-facing experiment arm definition."""

    group_id: int
    experiment_id: int
    kind: str
    name: str
    treatment_description: str
    assigned: int = 0


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
    group_definitions: list[tuple[str, str]],
    control_group: bool = True,
) -> int | None:
    """Insert an experiment with optional control and treatment groups.

    ``group_definitions`` is ordered control first (when present), followed by
    treatment arms, and must contain a non-empty name and description for each
    arm.
    Return None when one with
    the same name, campaign and start date already exists (a resubmitted
    form, as #296 found for campaigns, not a second experiment)."""
    if not control_group and treatment_groups != 2:
        raise ValueError("a no-control experiment requires exactly two treatments")
    if treatment_groups < 1:
        raise ValueError("at least one treatment group is required")
    if not group_definitions:
        raise ValueError("one name and treatment description are required per arm")
    kinds = ([CONTROL] if control_group else []) + [TREATMENT] * treatment_groups
    definitions = group_definitions
    if len(definitions) != len(kinds):
        raise ValueError("one name and treatment description are required per arm")
    if any(
        not arm_name.strip() or not description.strip()
        for arm_name, description in definitions
    ):
        raise ValueError("arm names and treatment descriptions cannot be blank")
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
        rows = [
            (last_group + offset, experiment_id, kind, *definition)
            for offset, (kind, definition) in enumerate(
                zip(kinds, definitions, strict=True), start=1
            )
        ]
        cursor.executemany(
            "INSERT INTO experiment_group "
            "(group_id, experiment_id, kind, name, treatment_description) "
            "VALUES (%s, %s, %s, %s, %s)",
            rows,
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


def list_group_definitions(
    connection: Connection, experiment_id: int
) -> list[ExperimentGroup]:
    """Read arm labels and descriptions with assigned counts."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT g.group_id, g.experiment_id, g.kind, g.name,
                   g.treatment_description, count(a.assignment_id)
              FROM experiment_group AS g
              LEFT JOIN experiment_assignment AS a ON a.group_id = g.group_id
             WHERE g.experiment_id = %s
             GROUP BY g.group_id, g.experiment_id, g.kind, g.name,
                      g.treatment_description
             ORDER BY g.kind <> 'CONTROL', g.group_id
            """,
            (experiment_id,),
        )
        return [ExperimentGroup(*row) for row in cursor.fetchall()]


def list_assigned_customers(
    connection: Connection, experiment_id: int, group_id: int
) -> list[tuple[int, str, datetime, bool]]:
    """Assigned customers for an arm and whether each has an exposure."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT a.assignment_id, a.customer_id::text, a.assigned_at,
                   EXISTS (SELECT 1 FROM experiment_exposure AS x
                            WHERE x.assignment_id = a.assignment_id)
              FROM experiment_assignment AS a
             WHERE a.experiment_id = %s AND a.group_id = %s
             ORDER BY a.customer_id
            """,
            (experiment_id, group_id),
        )
        return [
            (row[0], str(row[1]), row[2], bool(row[3])) for row in cursor.fetchall()
        ]


def list_assignment_ids(
    connection: Connection,
    experiment_id: int,
    group_id: int,
    customer_ids: list[str] | None = None,
) -> list[int]:
    """Return treatment assignment ids, optionally narrowed to selected IDs."""
    with connection.cursor() as cursor:
        if customer_ids:
            cursor.execute(
                """
                SELECT a.assignment_id
                  FROM experiment_assignment AS a
                  JOIN experiment_group AS g ON g.group_id = a.group_id
                 WHERE a.experiment_id = %s AND a.group_id = %s
                   AND g.kind = 'TREATMENT' AND a.customer_id = ANY(%s::uuid[])
                 ORDER BY a.assignment_id
                """,
                (experiment_id, group_id, customer_ids),
            )
        else:
            cursor.execute(
                """
                SELECT a.assignment_id
                  FROM experiment_assignment AS a
                  JOIN experiment_group AS g ON g.group_id = a.group_id
                 WHERE a.experiment_id = %s AND a.group_id = %s
                   AND g.kind = 'TREATMENT'
                 ORDER BY a.assignment_id
                """,
                (experiment_id, group_id),
            )
        return [row[0] for row in cursor.fetchall()]


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
    name: str = ""
    treatment_description: str = ""


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
                       WHERE x.assignment_id = a.assignment_id)),
                   g.name, g.treatment_description
              FROM experiment_group AS g
              LEFT JOIN experiment_assignment AS a ON a.group_id = g.group_id
             WHERE g.experiment_id = %s
             GROUP BY g.group_id, g.kind, g.name, g.treatment_description
             ORDER BY g.kind <> 'CONTROL', g.group_id
            """,
            (experiment_id,),
        )
        return [GroupExposure(*row) for row in cursor.fetchall()]
