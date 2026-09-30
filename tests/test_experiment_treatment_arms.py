"""F11-08 / #342 coverage for named, optional-control experiment arms."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest
from flask import Flask

from tests.test_experiment_assignment import _experiment
from web.app import create_app
from web.config import Config
from web.db import experiments as db
from web.db.experiments import ExperimentGroup
from web.routes import experiments as routes
from web.services import experiments as service

ROOT = Path(__file__).resolve().parents[1]
CUSTOMERS = [f"00000000-0000-0000-0000-{n:012d}" for n in range(1, 11)]
TODAY = date(2026, 10, 15)


@pytest.fixture
def connection() -> MagicMock:
    connection = MagicMock()
    connection.closed = False
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = None
    return connection


@pytest.fixture
def route_app() -> Flask:
    application = create_app(
        Config(
            secret_key="test",
            environment="testing",
            port=5000,
            log_level="INFO",
            session_cookie_secure=False,
            database_url="unused-by-test",
        ),
        database_connector=Mock(return_value=MagicMock()),
    )
    application.config["PROPAGATE_EXCEPTIONS"] = False
    return application


def _signed_in(client, role: str) -> None:
    with client.session_transaction() as session:
        session.update(
            user_id="11111111-1111-1111-1111-000000000001",
            role_code=role,
            name="Test User",
        )


def _form(**changes: str) -> dict[str, str]:
    values = {
        "name": "Two offers",
        "campaign_id": "7",
        "target_metric": "CONVERSION",
        "starts_on": "2026-10-01",
        "ends_on": "2026-10-31",
        "conversion_window_days": "14",
        "data_origin": "OBSERVED",
        "treatment_groups": "2",
    }
    values.update(changes)
    return values


def test_no_control_requires_exactly_two_named_treatment_arms() -> None:
    names = {"treatment_1": "Offer A", "treatment_2": "Offer B"}
    descriptions = {
        "treatment_1": "Ten percent discount.",
        "treatment_2": "Free shipping.",
    }

    data, errors = service.validate_experiment(
        **_form(control_group="NONE"),
        group_names=names,
        group_descriptions=descriptions,
    )

    assert errors == {}
    assert data is not None and not data.has_control
    assert data.group_definitions == (
        ("Offer A", "Ten percent discount."),
        ("Offer B", "Free shipping."),
    )


@pytest.mark.parametrize("count", ["", "1", "3", "999"])
def test_no_control_rejects_any_treatment_count_other_than_two(count: str) -> None:
    data, errors = service.validate_experiment(
        **_form(control_group="NONE", treatment_groups=count),
        group_names={"treatment_1": "A", "treatment_2": "B"},
        group_descriptions={"treatment_1": "A.", "treatment_2": "B."},
    )

    assert data is None
    assert "exactly two" in errors["treatment_groups"]


def test_supplied_arm_name_and_description_are_required_and_bounded() -> None:
    data, errors = service.validate_experiment(
        **_form(),
        group_names={"control": "", "treatment_1": "A", "treatment_2": "B"},
        group_descriptions={"control": "", "treatment_1": "A.", "treatment_2": "B."},
    )

    assert data is None
    assert errors["control_name"] == "Arm name is required."
    assert errors["control_description"] == "Treatment description is required."


def test_group_insert_has_metadata_and_parameter_values(connection: MagicMock) -> None:
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.side_effect = [None, (31,), (60,)]

    db.create_experiment(
        connection,
        name="Two offers",
        campaign_id=7,
        target_metric="CONVERSION",
        starts_on=date(2026, 10, 1),
        ends_on=None,
        conversion_window_days=14,
        data_origin="OBSERVED",
        treatment_groups=2,
        control_group=False,
        group_definitions=[
            ("Offer A", "Ten percent discount."),
            ("Offer B", "Free shipping."),
        ],
    )

    statement, rows = cursor.executemany.call_args.args
    assert "name" in statement and "treatment_description" in statement
    assert rows == [
        (61, 31, "TREATMENT", "Offer A", "Ten percent discount."),
        (62, 31, "TREATMENT", "Offer B", "Free shipping."),
    ]
    assert "Offer A" not in statement and "Free shipping." not in statement


def test_two_treatment_split_is_even_random_and_reproducible() -> None:
    groups = [(1, "TREATMENT"), (2, "TREATMENT")]

    first = service.split(7, CUSTOMERS, groups)
    second = service.split(7, list(reversed(CUSTOMERS)), groups)

    assert first == second
    assert [len(arm.customers) for arm in first] == [5, 5]
    assert set(first[0].customers).isdisjoint(first[1].customers)
    assert first != service.split(8, CUSTOMERS, groups)


def test_assignment_refuses_small_population_before_batch_insert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    experiment = Mock(
        experiment_id=7,
        assignments=0,
        campaign_id=1,
        control_groups=0,
        treatment_groups=2,
    )
    monkeypatch.setattr(db, "lock_experiment", Mock(return_value=True))
    monkeypatch.setattr(db, "get_experiment", Mock(return_value=experiment))
    monkeypatch.setattr(
        service,
        "get_campaign",
        Mock(return_value=Mock(status="ACTIVE", label_code="AT_RISK")),
    )
    monkeypatch.setattr(db, "read_target_population", Mock(return_value=[CUSTOMERS[0]]))
    monkeypatch.setattr(
        db,
        "list_groups",
        Mock(return_value=[(1, "TREATMENT"), (2, "TREATMENT")]),
    )
    insert = Mock()
    monkeypatch.setattr(db, "insert_assignments", insert)

    with pytest.raises(service.AssignmentRefused, match="at least 2 customers"):
        service.assign(MagicMock(), 7)

    insert.assert_not_called()


def _group() -> ExperimentGroup:
    return ExperimentGroup(2, 7, "TREATMENT", "Offer A", "Discount.", 2)


def _wire_bulk_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        db,
        "get_experiment",
        Mock(
            return_value=_experiment(
                experiment_id=7,
                campaign_id=1,
                starts_on=date(2026, 10, 1),
                ends_on=date(2026, 10, 31),
            )
        ),
    )
    monkeypatch.setattr(db, "get_campaign_status", Mock(return_value="ACTIVE"))


def test_bulk_exposure_selected_subset_is_append_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire_bulk_frame(monkeypatch)
    monkeypatch.setattr(db, "lock_experiment", Mock(return_value=True))
    monkeypatch.setattr(db, "list_group_definitions", Mock(return_value=[_group()]))
    ids = Mock(return_value=[10, 11])
    monkeypatch.setattr(db, "list_assignment_ids", ids)
    insert = Mock(return_value=True)
    monkeypatch.setattr(db, "insert_exposure", insert)

    count = service.record_group_exposures(
        MagicMock(), 7, 2, CUSTOMERS[:2], today=TODAY
    )

    assert count == 2
    assert [call.args[1] for call in insert.call_args_list] == [10, 11]


def test_bulk_exposure_all_uses_no_customer_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire_bulk_frame(monkeypatch)
    monkeypatch.setattr(db, "lock_experiment", Mock(return_value=True))
    monkeypatch.setattr(db, "list_group_definitions", Mock(return_value=[_group()]))
    ids = Mock(return_value=[10, 11])
    monkeypatch.setattr(db, "list_assignment_ids", ids)
    monkeypatch.setattr(db, "insert_exposure", Mock(return_value=True))

    connection = MagicMock()
    assert service.record_group_exposures(connection, 7, 2, today=TODAY) == 2
    assert ids.call_args.args == (connection, 7, 2, None)


@pytest.mark.parametrize(
    "selected,message",
    [([], "at least one"), (["not-a-uuid"], "UUID"), (CUSTOMERS[:1], "assigned")],
)
def test_bulk_exposure_rejects_empty_invalid_or_cross_arm_selection(
    monkeypatch: pytest.MonkeyPatch, selected: list[str], message: str
) -> None:
    _wire_bulk_frame(monkeypatch)
    monkeypatch.setattr(db, "lock_experiment", Mock(return_value=True))
    monkeypatch.setattr(db, "list_group_definitions", Mock(return_value=[_group()]))
    monkeypatch.setattr(db, "list_assignment_ids", Mock(return_value=[]))
    insert = Mock()
    monkeypatch.setattr(db, "insert_exposure", insert)

    with pytest.raises(service.ExposureRefused, match=message):
        service.record_group_exposures(MagicMock(), 7, 2, selected, today=TODAY)

    insert.assert_not_called()


def test_bulk_exposure_rejects_control(monkeypatch: pytest.MonkeyPatch) -> None:
    control = ExperimentGroup(1, 7, "CONTROL", "Holdout", "No treatment.", 2)
    monkeypatch.setattr(db, "lock_experiment", Mock(return_value=True))
    monkeypatch.setattr(db, "list_group_definitions", Mock(return_value=[control]))
    insert = Mock()
    monkeypatch.setattr(db, "insert_exposure", insert)

    with pytest.raises(service.ExposureRefused, match="never exposed"):
        service.record_group_exposures(MagicMock(), 7, 1, CUSTOMERS[:1], today=TODAY)

    insert.assert_not_called()


def test_bulk_exposure_rejects_duplicate_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire_bulk_frame(monkeypatch)
    monkeypatch.setattr(db, "lock_experiment", Mock(return_value=True))
    monkeypatch.setattr(db, "list_group_definitions", Mock(return_value=[_group()]))
    monkeypatch.setattr(db, "list_assignment_ids", Mock(return_value=[10]))
    insert = Mock()
    monkeypatch.setattr(db, "insert_exposure", insert)

    with pytest.raises(service.ExposureRefused, match="assigned"):
        service.record_group_exposures(
            MagicMock(), 7, 2, [CUSTOMERS[0], CUSTOMERS[0]], today=TODAY
        )

    insert.assert_not_called()


def test_bulk_exposure_rolls_back_when_one_insert_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire_bulk_frame(monkeypatch)
    monkeypatch.setattr(db, "lock_experiment", Mock(return_value=True))
    monkeypatch.setattr(db, "list_group_definitions", Mock(return_value=[_group()]))
    monkeypatch.setattr(db, "list_assignment_ids", Mock(return_value=[10, 11]))
    monkeypatch.setattr(
        db, "insert_exposure", Mock(side_effect=[True, RuntimeError("lost")])
    )
    connection = MagicMock()

    with pytest.raises(RuntimeError):
        service.record_group_exposures(connection, 7, 2, today=TODAY)

    connection.rollback.assert_called_once()
    connection.commit.assert_not_called()


def test_schema_has_required_arm_constraints_and_kind_immutability() -> None:
    schema = (ROOT / "sql/01_schema.sql").read_text()
    verify = (ROOT / "sql/verify_integrity.sql").read_text()

    assert "name          VARCHAR(120) NOT NULL CHECK (btrim(name) <> '')" in schema
    assert "treatment_description VARCHAR(500) NOT NULL CHECK" in schema
    assert "BEFORE UPDATE OF kind, name, treatment_description" in schema
    assert "EXISTS (\n           SELECT 1 FROM experiment_assignment" in schema
    assert "ON DELETE CASCADE" in schema
    assert schema.count("REVOKE UPDATE, DELETE ON experiment_assignment") == 1
    assert "UPDATE experiment_group SET kind" in verify
    assert "UPDATE experiment_group SET name" in verify
    assert "treatment_description = 'Edited after assignment.'" in verify


def _wire_group_route(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        routes,
        "get_experiment",
        Mock(return_value=Mock(experiment_id=7, name="Two offers")),
    )
    monkeypatch.setattr(routes, "list_group_definitions", Mock(return_value=[_group()]))
    monkeypatch.setattr(
        routes,
        "list_assigned_customers",
        Mock(return_value=[(101, CUSTOMERS[0], date(2026, 10, 1), False)]),
    )


def test_group_page_lists_assigned_customers_and_metadata(
    route_app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire_group_route(monkeypatch)
    client = route_app.test_client()
    _signed_in(client, "ANALYST")

    response = client.get("/experiments/7/groups/2")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Offer A" in body and "Discount." in body and CUSTOMERS[0] in body
    assert "all_customers" not in body and "customer_id" not in body


def test_creation_without_arm_metadata_is_rejected_without_writing(
    route_app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    create = Mock()
    monkeypatch.setattr(routes.service, "create_experiment", create)
    client = route_app.test_client()
    _signed_in(client, "MARKETING")

    response = client.post("/experiments/new", data=_form(control_group="CONTROL"))

    assert response.status_code == 400
    assert "Arm name is required" in response.get_data(as_text=True)
    create.assert_not_called()


def test_group_post_records_all_or_selected_and_requires_write_permission(
    route_app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire_group_route(monkeypatch)
    record = Mock(return_value=1)
    monkeypatch.setattr(routes.service, "record_group_exposures", record)
    client = route_app.test_client()

    _signed_in(client, "ANALYST")
    assert (
        client.post("/experiments/7/groups/2", data={"all_customers": "on"}).status_code
        == 403
    )
    record.assert_not_called()

    _signed_in(client, "MARKETING")
    body = client.get("/experiments/7/groups/2").get_data(as_text=True)
    assert 'name="all_customers"' in body and 'name="customer_id"' in body

    response = client.post("/experiments/7/groups/2", data={"all_customers": "on"})
    assert response.status_code == 302
    assert record.call_args.args[-1] is None

    response = client.post(
        "/experiments/7/groups/2", data={"customer_id": [CUSTOMERS[0]]}
    )
    assert response.status_code == 302
    assert record.call_args.args[-1] == [CUSTOMERS[0]]


def test_group_post_renders_readable_refusal_without_writing(
    route_app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire_group_route(monkeypatch)
    record = Mock(
        side_effect=service.ExposureRefused("", "selected customer is not assigned")
    )
    monkeypatch.setattr(routes.service, "record_group_exposures", record)
    client = route_app.test_client()
    _signed_in(client, "MARKETING")

    response = client.post(
        "/experiments/7/groups/2", data={"customer_id": [CUSTOMERS[0]]}
    )

    assert response.status_code == 409
    assert "selected customer is not assigned" in response.get_data(as_text=True)


@pytest.mark.real_csrf
def test_group_post_is_refused_without_csrf_before_service(
    route_app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire_group_route(monkeypatch)
    record = Mock(return_value=1)
    monkeypatch.setattr(routes.service, "record_group_exposures", record)
    client = route_app.test_client()
    _signed_in(client, "MARKETING")

    response = client.post("/experiments/7/groups/2", data={"all_customers": "on"})

    assert response.status_code == 403
    record.assert_not_called()


def test_seed_names_and_descriptions_are_parameter_safe_source_values() -> None:
    seed = (ROOT / "sql/02_seed_30_per_table.sql").read_text()

    assert (
        "INSERT INTO experiment_group (group_id, experiment_id, kind, "
        "name, treatment_description)" in seed
    )
    assert "Holdout ' || n" in seed and "Offer ' || n" in seed
    assert not re.search(r"INSERT INTO experiment_group \([^\n]*kind\)\s*VALUES", seed)


def test_arm_metadata_is_carried_by_every_experiment_template_and_export() -> None:
    templates = [
        ROOT / "web/templates/experiments/index.html",
        ROOT / "web/templates/experiments/assign.html",
        ROOT / "web/templates/experiments/group.html",
        ROOT / "web/templates/experiments/exposure.html",
        ROOT / "web/templates/experiments/conversion.html",
        ROOT / "web/templates/experiments/uplift.html",
        ROOT / "web/templates/experiment_report/index.html",
    ]
    for template in templates:
        text = template.read_text()
        assert "treatment_description" in text
    from web.services.experiment_report import EXPORT_COLUMNS

    assert "arm_name" in EXPORT_COLUMNS
    assert "treatment_description" in EXPORT_COLUMNS
