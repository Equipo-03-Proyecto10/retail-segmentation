"""The web cannot assign an experiment the service planned (F25-08, #839).

ADR-0068 leaves the web's adoption of the new rule to this story: an experiment with a
stored plan has its arms fixed by its frame and salt, so a shuffle by the web would make
the assignment unverifiable. The database refuses the INSERT, and the web reports it as
a refusal, not a 500. Everything but the last test runs against PostgreSQL as the
application role, in a transaction that is rolled back.
"""

from __future__ import annotations

from collections.abc import Iterator

import psycopg
import pytest
from psycopg import errors

from web.db import experiments as db
from web.services import experiments as service

pytestmark = pytest.mark.postgres


@pytest.fixture
def scene(owner_url: str) -> Iterator[tuple[psycopg.Connection, int, int, str]]:
    with psycopg.connect(owner_url) as connection:
        try:
            experiment = connection.execute(
                "SELECT max(experiment_id) + 1 FROM experiment"
            ).fetchone()[0]
            group = connection.execute(
                "SELECT max(group_id) + 1 FROM experiment_group"
            ).fetchone()[0]
            customer = connection.execute(
                "SELECT customer_id::text FROM customer ORDER BY customer_id LIMIT 1"
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO experiment (experiment_id, name, campaign_id,"
                " target_metric, starts_on, conversion_window_days, data_origin)"
                " VALUES (%s, 'F25-08 web', NULL, 'CONVERSION', current_date, 7,"
                " 'OBSERVED')",
                (experiment,),
            )
            connection.execute(
                "INSERT INTO experiment_group (group_id, experiment_id, kind, name,"
                " treatment_description)"
                " VALUES (%s, %s, 'TREATMENT', 'Offer', 'Offer')",
                (group, experiment),
            )
            yield connection, experiment, group, customer
        finally:
            connection.rollback()


def _plan(connection: psycopg.Connection, experiment: int) -> None:
    connection.execute(
        "INSERT INTO experiments.experiment_plan (experiment_id, alpha,"
        " comparison_alpha, power, minimum_detectable_effect, baseline,"
        " baseline_std_dev, required_per_arm, smallest_detectable_effect,"
        " settle_days, frame_taken_at, frame_digest, label_code, salt, planned_at,"
        " planned_by) VALUES (%s, 0.05, 0.05, 0.8, 1, 2, 5, 10, 1, 7, now(),"
        " repeat('a', 64), 'CHAMPION', repeat('b', 64), now(),"
        " (SELECT user_id FROM app_user ORDER BY user_id LIMIT 1))",
        (experiment,),
    )


def _as_the_web(connection: psycopg.Connection) -> None:
    # session_user, not current_user: the guard tells the web by who signed in.
    connection.execute("SET LOCAL SESSION AUTHORIZATION retail_app")


def test_the_web_cannot_insert_an_assignment_into_a_planned_experiment(
    scene: tuple[psycopg.Connection, int, int, str],
) -> None:
    connection, experiment, group, customer = scene
    _plan(connection, experiment)
    _as_the_web(connection)
    with pytest.raises(errors.CheckViolation) as refused:
        db.insert_assignments(connection, experiment, [(group, customer)])
    assert refused.value.diag.constraint_name == service.PLANNED_BY_SERVICE


def test_the_web_still_assigns_an_experiment_it_created_itself(
    scene: tuple[psycopg.Connection, int, int, str],
) -> None:
    connection, experiment, group, customer = scene
    _as_the_web(connection)
    db.insert_assignments(connection, experiment, [(group, customer)])
    count = connection.execute(
        "SELECT count(*) FROM experiment_assignment WHERE experiment_id = %s",
        (experiment,),
    ).fetchone()[0]
    assert count == 1


def test_the_web_reports_the_refusal_and_does_not_fail_with_a_500(
    scene: tuple[psycopg.Connection, int, int, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The service's own `assign`, with the database's real refusal underneath."""
    connection, experiment, group, customer = scene
    _plan(connection, experiment)
    _as_the_web(connection)
    monkeypatch.setattr(
        service,
        "_assign",
        lambda conn, experiment_id: db.insert_assignments(
            conn, experiment_id, [(group, customer)]
        ),
    )
    with pytest.raises(
        service.AssignmentRefused, match="planned by the experimentation"
    ):
        service.assign(connection, experiment)
