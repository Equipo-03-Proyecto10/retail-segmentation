"""Group assignment as a durable event (F11-04, #223, ADR-0019, ADR-0026).

The database is replaced with a mock, as in the other route tests; the schema's
own refusals (UNIQUE per experiment and customer, and retail_app having no
UPDATE or DELETE on experiment_assignment) are checked against PostgreSQL by
the self-test in sql/01_schema.sql, which CI runs as retail_app.
"""

from __future__ import annotations

import re
from datetime import date
from itertools import chain, repeat
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest
from flask import Flask
from psycopg.errors import UniqueViolation

from web.app import create_app
from web.config import Config
from web.db import experiments as db
from web.db.campaigns import Campaign
from web.db.experiments import Experiment
from web.services import experiments as service

ROOT = Path(__file__).resolve().parents[1]
USER_ID = "11111111-1111-1111-1111-000000000001"
GROUPS = [(61, "CONTROL"), (62, "TREATMENT"), (63, "TREATMENT")]
CUSTOMERS = [f"00000000-0000-0000-0000-{n:012d}" for n in range(1, 11)]


def _experiment(**changes) -> Experiment:
    fields = dict(
        experiment_id=31,
        name="Win-back offer",
        campaign_id=7,
        campaign_name="Win-back",
        target_metric="CONVERSION",
        starts_on=date(2026, 10, 1),
        ends_on=None,
        conversion_window_days=14,
        data_origin="OBSERVED",
        control_groups=1,
        treatment_groups=2,
        assignments=0,
    )
    return Experiment(**{**fields, **changes})


def _campaign(status: str = "ACTIVE") -> Campaign:
    return Campaign(
        7, "Win-back", "AT_RISK", date(2026, 10, 1), date(2026, 10, 31), status
    )


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    *,
    experiment: Experiment | None = None,
    campaign: Campaign | None = None,
    population: list[str] | None = None,
) -> dict[str, Mock]:
    mocks = {
        "lock_experiment": Mock(return_value=True),
        "get_experiment": Mock(return_value=experiment or _experiment()),
        "read_target_population": Mock(
            return_value=CUSTOMERS if population is None else population
        ),
        "list_groups": Mock(return_value=GROUPS),
        "insert_assignments": Mock(),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(db, name, mock)
    monkeypatch.setattr(
        service, "get_campaign", Mock(return_value=campaign or _campaign())
    )
    return mocks


# ---------- the split ----------


def test_every_customer_lands_in_exactly_one_arm_and_arms_differ_by_one_at_most() -> (
    None
):
    arms = service.split(31, CUSTOMERS, GROUPS)

    assert [arm.kind for arm in arms] == ["CONTROL", "TREATMENT", "TREATMENT"]
    assert sorted(len(arm.customers) for arm in arms) == [3, 3, 4]
    assigned = [customer for arm in arms for customer in arm.customers]
    assert sorted(assigned) == sorted(CUSTOMERS)


def test_the_split_is_reproducible_whatever_order_the_population_arrives_in() -> None:
    first = service.split(31, CUSTOMERS, GROUPS)

    assert service.split(31, list(reversed(CUSTOMERS)), GROUPS) == first


def test_a_different_experiment_gets_a_different_split() -> None:
    assert service.split(31, CUSTOMERS, GROUPS) != service.split(32, CUSTOMERS, GROUPS)


# ---------- when assignment is refused ----------


@pytest.mark.parametrize(
    "setup,message",
    [
        (dict(experiment=_experiment(assignments=4)), "already has 4 assignments"),
        (dict(experiment=_experiment(campaign_id=None)), "has no campaign"),
        (dict(campaign=_campaign("DRAFT")), "is draft"),
        (dict(campaign=_campaign("FINISHED")), "is finished"),
        (dict(experiment=_experiment(treatment_groups=0)), "no treatment group"),
        (dict(population=[]), "nobody to assign"),
    ],
)
def test_assignment_is_refused_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, setup: dict, message: str
) -> None:
    mocks = _wire(monkeypatch, **setup)

    with pytest.raises(service.AssignmentRefused, match=message):
        service.assign(MagicMock(), 31)

    mocks["insert_assignments"].assert_not_called()


def test_a_no_control_experiment_can_be_assigned_to_two_treatment_arms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    no_control = _experiment(control_groups=0, treatment_groups=2)
    mocks = _wire(monkeypatch, experiment=no_control, population=CUSTOMERS)
    mocks["list_groups"].return_value = [(62, "TREATMENT"), (63, "TREATMENT")]

    plan = service.assign(MagicMock(), 31)

    assert [len(arm.customers) for arm in plan.arms] == [5, 5]
    mocks["insert_assignments"].assert_called_once()


def test_an_unknown_experiment_is_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    mocks = _wire(monkeypatch)
    mocks["lock_experiment"].return_value = False

    with pytest.raises(service.ExperimentNotFound):
        service.assign(MagicMock(), 99)


# ---------- the write ----------


def test_every_customer_of_the_population_is_written_to_its_arm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mocks = _wire(monkeypatch)

    plan = service.assign(MagicMock(), 31)

    ((_, experiment_id, rows),) = [
        call.args for call in mocks["insert_assignments"].call_args_list
    ]
    assert experiment_id == 31
    assert sorted(customer for _, customer in rows) == sorted(CUSTOMERS)
    assert rows == [
        (arm.group_id, customer) for arm in plan.arms for customer in arm.customers
    ]
    assert plan.population == 10


def test_the_experiment_is_locked_before_anything_is_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    mocks = _wire(monkeypatch)
    mocks["lock_experiment"].side_effect = lambda *a: order.append("lock") or True
    mocks["get_experiment"].side_effect = lambda *a: order.append("read") or (
        _experiment()
    )

    service.assign(MagicMock(), 31)

    assert order[:2] == ["lock", "read"]


def test_the_databases_refusal_of_a_second_assignment_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mocks = _wire(monkeypatch)
    mocks["insert_assignments"].side_effect = UniqueViolation("duplicate key")

    with pytest.raises(service.AssignmentRefused, match="already assigned"):
        service.assign(MagicMock(), 31)


def test_a_failure_part_way_rolls_the_whole_assignment_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mocks = _wire(monkeypatch)
    mocks["insert_assignments"].side_effect = RuntimeError("connection lost")
    connection = MagicMock()

    with pytest.raises(RuntimeError):
        service.assign(connection, 31)

    connection.rollback.assert_called_once()
    connection.commit.assert_not_called()


def test_the_insert_is_one_parameterized_batch() -> None:
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value

    db.insert_assignments(connection, 31, [(61, CUSTOMERS[0]), (62, CUSTOMERS[1])])

    statement, rows = cursor.executemany.call_args.args
    assert statement.startswith("INSERT INTO experiment_assignment")
    assert rows == [(31, 61, CUSTOMERS[0]), (31, 62, CUSTOMERS[1])]


# ---------- never rewritten (RN-42, ADR-0026) ----------


def test_no_module_updates_or_deletes_an_assignment() -> None:
    """The application half: there is no path that rewrites an assignment."""
    rewrite = re.compile(
        r"(UPDATE\s+experiment_assignment|DELETE\s+FROM\s+experiment_assignment)",
        re.IGNORECASE,
    )
    offenders = [
        str(path.relative_to(ROOT))
        for path in (ROOT / "web").rglob("*.py")
        if rewrite.search(path.read_text())
    ]

    assert offenders == []


SCHEMA = (ROOT / "sql/01_schema.sql").read_text()
REVOKE = "REVOKE UPDATE, DELETE ON experiment_assignment FROM retail_app;"


def test_the_schema_revokes_update_and_delete_after_creating_the_table() -> None:
    assert SCHEMA.index("CREATE TABLE experiment_assignment") < SCHEMA.index(REVOKE)


def test_the_self_test_proves_the_application_role_cannot_rewrite_one() -> None:
    assert re.search(r"c\.relname NOT IN \([^)]*'experiment_assignment'", SCHEMA)
    assert "PASS: experiment_assignment is append-only" in SCHEMA
    assert "PASS: UPDATE experiment_assignment refused" in SCHEMA
    assert "PASS: DELETE experiment_assignment refused" in SCHEMA


# ---------- the page ----------


@pytest.fixture
def app() -> Flask:
    application = create_app(
        Config(
            secret_key="test",
            environment="testing",
            port=5000,
            log_level="INFO",
            session_cookie_secure=False,
            database_url="unused-by-test",
        ),
        database_connector=Mock(side_effect=chain([Mock()], repeat(MagicMock()))),
    )
    application.config["PROPAGATE_EXCEPTIONS"] = False
    return application


def _marketing(app: Flask):
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session.update(user_id=USER_ID, role_code="MARKETING", name="Test User")
    return client


def test_the_preview_shows_the_arms_and_writes_nothing(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    mocks = _wire(monkeypatch)

    response = _marketing(app).get("/experiments/31/assign")

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Assign 10 customers" in body and "This cannot be undone" in body
    mocks["insert_assignments"].assert_not_called()
    mocks["lock_experiment"].assert_not_called()


def test_confirming_assigns_and_reports_the_arm_sizes(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    mocks = _wire(monkeypatch)
    client = _marketing(app)

    response = client.post("/experiments/31/assign")

    assert response.status_code == 302
    mocks["insert_assignments"].assert_called_once()
    with client.session_transaction() as flask_session:
        _, message = flask_session["_flashes"][0]
    assert "10 customers assigned" in message


def test_a_refused_assignment_is_a_409_with_the_reason(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire(monkeypatch, campaign=_campaign("DRAFT"))
    monkeypatch.setattr(
        "web.routes.experiments.get_experiment", Mock(return_value=_experiment())
    )

    response = _marketing(app).post("/experiments/31/assign")

    assert response.status_code == 409
    assert "is draft" in response.get_data(as_text=True)
