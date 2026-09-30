"""Experiment setup (F11-03, #222).

The database is replaced with a mock, as in the other route tests. What these
cover is the application's side: what setup requires, that the measurement
rules lock after the first assignment, that the data origin never changes,
that a campaign cannot be activated while an attached experiment lacks a
control or a treatment group, and that the default-deny gate holds.
"""

from __future__ import annotations

from datetime import date
from itertools import chain, repeat
from unittest.mock import MagicMock, Mock

import pytest
from flask import Flask
from flask.testing import FlaskClient

from web.app import create_app
from web.config import Config
from web.db import experiments as db
from web.db.campaigns import Campaign
from web.db.experiments import Experiment, GroupCounts
from web.services import campaigns as campaign_service
from web.services import experiments as service

USER_ID = "11111111-1111-1111-1111-000000000001"
FORM = dict(
    name="Win-back offer",
    campaign_id="7",
    target_metric="CONVERSION",
    starts_on="2026-10-01",
    ends_on="2026-10-31",
    conversion_window_days="14",
    data_origin="OBSERVED",
    treatment_groups="2",
    control_group="CONTROL",
    control_name="Holdout",
    control_description="No treatment delivered.",
    treatment_1_name="Offer A",
    treatment_1_description="Ten percent discount.",
    treatment_2_name="Offer B",
    treatment_2_description="Free shipping.",
)
EDIT = {
    key: FORM[key]
    for key in FORM
    if key
    not in {
        "data_origin",
        "treatment_groups",
        "control_group",
        "control_name",
        "control_description",
        "treatment_1_name",
        "treatment_1_description",
        "treatment_2_name",
        "treatment_2_description",
    }
}


def _experiment(assignments: int = 0, origin: str = "OBSERVED") -> Experiment:
    return Experiment(
        experiment_id=3,
        name="Win-back offer",
        campaign_id=7,
        campaign_name="Win-back",
        target_metric="CONVERSION",
        starts_on=date(2026, 10, 1),
        ends_on=date(2026, 10, 31),
        conversion_window_days=14,
        data_origin=origin,
        control_groups=1,
        treatment_groups=2,
        assignments=assignments,
    )


@pytest.fixture
def connection() -> MagicMock:
    connection = MagicMock()
    connection.closed = False
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = None
    cursor.rowcount = 1
    return connection


def _cursor(connection: MagicMock) -> MagicMock:
    return connection.cursor.return_value.__enter__.return_value


@pytest.fixture
def app(connection: MagicMock) -> Flask:
    application = create_app(
        Config(
            secret_key="test",
            environment="testing",
            port=5000,
            log_level="INFO",
            session_cookie_secure=False,
            database_url="unused-by-test",
        ),
        database_connector=Mock(side_effect=chain([Mock()], repeat(connection))),
    )
    application.config["PROPAGATE_EXCEPTIONS"] = False
    return application


def _sign_in(client: FlaskClient, role_code: str) -> None:
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = USER_ID
        flask_session["role_code"] = role_code
        flask_session["name"] = "Test User"


# ---------- what setup requires ----------


def test_a_complete_setup_validates() -> None:
    data, errors = service.validate_experiment(**FORM)

    assert errors == {}
    assert data.campaign_id == 7 and data.treatment_groups == 2
    assert data.conversion_window_days == 14 and data.data_origin == "OBSERVED"


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", ""),
        ("target_metric", ""),
        ("target_metric", "REVENUE"),
        ("starts_on", ""),
        ("conversion_window_days", ""),
        ("conversion_window_days", "0"),
        ("conversion_window_days", "-3"),
        ("conversion_window_days", "7.5"),
        ("data_origin", ""),
        ("data_origin", "REAL"),
        ("treatment_groups", "0"),
        ("treatment_groups", ""),
        ("treatment_groups", "two"),
        ("control_group", "SURPRISE"),
        ("campaign_id", "seven"),
    ],
)
def test_setup_refuses_a_missing_or_invalid_field(field: str, value: str) -> None:
    data, errors = service.validate_experiment(**{**FORM, field: value})

    assert data is None
    assert field in errors


def test_an_experiment_needs_no_campaign_and_no_end_date() -> None:
    data, errors = service.validate_experiment(
        **{**FORM, "campaign_id": "", "ends_on": ""}
    )

    assert errors == {}
    assert data.campaign_id is None and data.ends_on is None


def test_an_edit_cannot_choose_an_origin_or_groups() -> None:
    data, errors = service.validate_experiment(**EDIT)

    assert errors == {}
    assert data.data_origin is None and data.treatment_groups is None


def test_creation_writes_exactly_one_control_and_the_treatments(
    connection: MagicMock,
) -> None:
    cursor = _cursor(connection)
    cursor.fetchone.side_effect = [None, (31,), (60,)]  # no duplicate, id, last group

    experiment_id = db.create_experiment(
        connection,
        name="Win-back offer",
        campaign_id=7,
        target_metric="CONVERSION",
        starts_on=date(2026, 10, 1),
        ends_on=None,
        conversion_window_days=14,
        data_origin="SEEDED",
        treatment_groups=2,
        group_definitions=[
            ("Control", "No treatment delivered."),
            ("Treatment 2", "Treatment arm."),
            ("Treatment 3", "Treatment arm."),
        ],
    )

    assert experiment_id == 31
    assert "pg_advisory_xact_lock" in cursor.execute.call_args_list[0].args[0]
    statement, rows = cursor.executemany.call_args.args
    assert "INSERT INTO experiment_group" in statement
    assert rows == [
        (61, 31, "CONTROL", "Control", "No treatment delivered."),
        (62, 31, "TREATMENT", "Treatment 2", "Treatment arm."),
        (63, 31, "TREATMENT", "Treatment 3", "Treatment arm."),
    ]
    for call in cursor.execute.call_args_list:
        assert "SEEDED" not in call.args[0]  # every value is a parameter


def test_a_resubmitted_setup_is_refused_not_duplicated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(db, "create_experiment", Mock(return_value=None))
    data, _ = service.validate_experiment(**FORM)

    with pytest.raises(service.ExperimentRefused, match="already exists"):
        service.create_experiment(MagicMock(), data)


# ---------- the measurement rules lock after the first assignment ----------


def _wire_update(monkeypatch: pytest.MonkeyPatch, current: Experiment) -> Mock:
    monkeypatch.setattr(db, "lock_experiment", Mock(return_value=True))
    monkeypatch.setattr(db, "get_experiment", Mock(return_value=current))
    update = Mock(return_value=True)
    monkeypatch.setattr(db, "update_experiment", update)
    return update


def test_the_window_cannot_change_once_anyone_is_assigned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    update = _wire_update(monkeypatch, _experiment(assignments=5))
    data, _ = service.validate_experiment(**{**EDIT, "conversion_window_days": "21"})

    with pytest.raises(service.ExperimentRefused) as refusal:
        service.update_experiment(MagicMock(), 3, data)

    assert refusal.value.field == "conversion_window_days"
    assert "5 assignments" in str(refusal.value)
    update.assert_not_called()


def test_the_target_metric_cannot_change_once_anyone_is_assigned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    update = _wire_update(monkeypatch, _experiment(assignments=1))
    data, _ = service.validate_experiment(**{**EDIT, "target_metric": "AVERAGE_TICKET"})

    with pytest.raises(service.ExperimentRefused) as refusal:
        service.update_experiment(MagicMock(), 3, data)

    assert refusal.value.field == "target_metric"
    update.assert_not_called()


def test_an_assigned_experiment_can_still_be_renamed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    update = _wire_update(monkeypatch, _experiment(assignments=5))
    data, _ = service.validate_experiment(**{**EDIT, "name": "Renamed"})

    service.update_experiment(MagicMock(), 3, data)

    assert update.call_args.kwargs["name"] == "Renamed"


def test_before_any_assignment_the_window_can_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    update = _wire_update(monkeypatch, _experiment(assignments=0))
    data, _ = service.validate_experiment(**{**EDIT, "conversion_window_days": "21"})

    service.update_experiment(MagicMock(), 3, data)

    assert update.call_args.kwargs["conversion_window_days"] == 21


def test_the_edit_locks_the_row_before_it_counts_assignments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    monkeypatch.setattr(
        db, "lock_experiment", Mock(side_effect=lambda *a: order.append("lock") or True)
    )
    monkeypatch.setattr(
        db,
        "get_experiment",
        Mock(side_effect=lambda *a: order.append("read") or _experiment()),
    )
    monkeypatch.setattr(db, "update_experiment", Mock(return_value=True))
    data, _ = service.validate_experiment(**EDIT)

    service.update_experiment(MagicMock(), 3, data)

    assert order == ["lock", "read"]


def test_an_unknown_experiment_is_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "lock_experiment", Mock(return_value=False))
    data, _ = service.validate_experiment(**EDIT)

    with pytest.raises(service.ExperimentNotFound):
        service.update_experiment(MagicMock(), 99, data)


def test_the_data_origin_is_never_rewritten(connection: MagicMock) -> None:
    db.update_experiment(
        connection,
        3,
        name="X",
        campaign_id=None,
        target_metric="CONVERSION",
        starts_on=date(2026, 10, 1),
        ends_on=None,
        conversion_window_days=14,
    )

    statement = _cursor(connection).execute.call_args.args[0]
    assert "UPDATE experiment" in statement and "data_origin" not in statement


# ---------- activation needs a control and a treatment ----------


@pytest.mark.parametrize("controls,treatments,missing", [(1, 0, "no treatment group")])
def test_an_incomplete_experiment_blocks_its_campaigns_activation(
    monkeypatch: pytest.MonkeyPatch, controls: int, treatments: int, missing: str
) -> None:
    monkeypatch.setattr(
        db,
        "list_group_counts_for_campaign",
        Mock(return_value=[GroupCounts(3, "Win-back offer", controls, treatments)]),
    )
    monkeypatch.setattr(
        campaign_service.campaigns,
        "get_campaign",
        Mock(
            return_value=Campaign(
                7, "Win-back", "AT_RISK", date(2026, 10, 1), date(2999, 12, 31), "DRAFT"
            )
        ),
    )
    change_status = Mock(return_value=True)
    monkeypatch.setattr(campaign_service.campaigns, "change_status", change_status)

    with pytest.raises(campaign_service.InvalidTransition, match=missing):
        campaign_service.transition(MagicMock(), 7, "activate")

    change_status.assert_not_called()


def test_a_no_control_two_treatment_experiment_allows_campaign_activation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        db,
        "list_group_counts_for_campaign",
        Mock(return_value=[GroupCounts(3, "Two offers", 0, 2)]),
    )
    monkeypatch.setattr(
        campaign_service.campaigns,
        "get_campaign",
        Mock(
            return_value=Campaign(
                7, "Win-back", "AT_RISK", date(2026, 10, 1), date(2026, 10, 31), "DRAFT"
            )
        ),
    )
    change_status = Mock(return_value=True)
    monkeypatch.setattr(campaign_service.campaigns, "change_status", change_status)

    campaign_service.transition(MagicMock(), 7, "activate")

    change_status.assert_called_once()


def test_a_complete_experiment_lets_its_campaign_activate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        db,
        "list_group_counts_for_campaign",
        Mock(return_value=[GroupCounts(3, "Win-back offer", 1, 2)]),
    )

    assert service.activation_refusal(MagicMock(), 7) is None


def test_cancelling_does_not_check_the_experiments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counts = Mock(return_value=[GroupCounts(3, "Broken", 0, 0)])
    monkeypatch.setattr(db, "list_group_counts_for_campaign", counts)
    monkeypatch.setattr(
        campaign_service.campaigns,
        "get_campaign",
        Mock(
            return_value=Campaign(
                7, "Win-back", "AT_RISK", date(2026, 10, 1), date(2999, 12, 31), "DRAFT"
            )
        ),
    )
    monkeypatch.setattr(
        campaign_service.campaigns, "change_status", Mock(return_value=True)
    )

    assert campaign_service.transition(MagicMock(), 7, "cancel") == "CANCELLED"
    counts.assert_not_called()


# ---------- the pages ----------


def _wire_pages(monkeypatch: pytest.MonkeyPatch, experiment: Experiment) -> None:
    monkeypatch.setattr(
        "web.routes.experiments.list_experiments", Mock(return_value=([experiment], 1))
    )
    monkeypatch.setattr(
        "web.routes.experiments.get_experiment", Mock(return_value=experiment)
    )
    monkeypatch.setattr(
        "web.routes.experiments.list_campaign_choices", Mock(return_value=[])
    )


@pytest.mark.parametrize("origin", ["SEEDED", "INJECTED"])
def test_a_seeded_or_injected_experiment_is_labelled_synthetic(
    app: Flask, monkeypatch: pytest.MonkeyPatch, origin: str
) -> None:
    _wire_pages(monkeypatch, _experiment(origin=origin))
    client = app.test_client()
    _sign_in(client, "ANALYST")

    body = client.get("/experiments/").get_data(as_text=True)

    assert "Synthetic" in body and origin in body


def test_an_observed_experiment_is_not_labelled_synthetic(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire_pages(monkeypatch, _experiment(origin="OBSERVED"))
    client = app.test_client()
    _sign_in(client, "ANALYST")

    assert "Synthetic" not in client.get("/experiments/").get_data(as_text=True)


def test_a_read_only_profile_sees_the_list_but_no_setup_controls(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire_pages(monkeypatch, _experiment())
    client = app.test_client()
    _sign_in(client, "ANALYST")

    body = client.get("/experiments/").get_data(as_text=True)

    assert "Win-back offer" in body
    assert "/experiments/new" not in body and "/experiments/3/edit" not in body


def test_an_incomplete_setup_is_refused_with_field_errors(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire_pages(monkeypatch, _experiment())
    create = Mock()
    monkeypatch.setattr(service, "create_experiment", create)
    client = app.test_client()
    _sign_in(client, "MARKETING")

    response = client.post(
        "/experiments/new", data={**FORM, "data_origin": "", "treatment_groups": "0"}
    )

    body = response.get_data(as_text=True)
    assert response.status_code == 400
    assert "Choose where this experiment" in body
    assert "at least one treatment group" in body
    create.assert_not_called()


def test_a_complete_setup_is_created(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    create = Mock(return_value=31)
    monkeypatch.setattr(service, "create_experiment", create)
    client = app.test_client()
    _sign_in(client, "MARKETING")

    response = client.post("/experiments/new", data=FORM)

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/experiments/")
    assert create.call_args.args[1].treatment_groups == 2


def test_the_form_of_an_assigned_experiment_shows_the_rules_as_fixed(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire_pages(monkeypatch, _experiment(assignments=5))
    client = app.test_client()
    _sign_in(client, "MARKETING")

    body = client.get("/experiments/3/edit").get_data(as_text=True)

    assert "Measurement rules fixed" in body
    assert 'name="conversion_window_days" value="14" readonly' in body


def test_the_form_of_an_assigned_experiment_also_fixes_campaign_and_dates(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#357: the frame is locked with the measurement rules."""
    _wire_pages(monkeypatch, _experiment(assignments=5))
    client = app.test_client()
    _sign_in(client, "MARKETING")

    body = client.get("/experiments/3/edit").get_data(as_text=True)

    assert "its campaign, dates, target metric and conversion window" in body
    assert '<input type="hidden" name="campaign_id" value="7">' in body
    assert 'name="starts_on" value="2026-10-01" required readonly' in body
    assert 'name="ends_on" value="2026-10-31" readonly' in body
    assert "<select" not in body.split('id="campaign_id"')[1].split("</div>")[0]


def test_changing_an_assigned_experiments_start_date_is_a_409(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire_pages(monkeypatch, _experiment(assignments=5))
    _wire_update(monkeypatch, _experiment(assignments=5))
    client = app.test_client()
    _sign_in(client, "MARKETING")

    response = client.post(
        "/experiments/3/edit", data={**EDIT, "starts_on": "2026-09-01"}
    )

    assert response.status_code == 409
    assert "The start date is fixed" in response.get_data(as_text=True)


def test_changing_an_assigned_experiments_window_is_a_409(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire_pages(monkeypatch, _experiment(assignments=5))
    _wire_update(monkeypatch, _experiment(assignments=5))
    client = app.test_client()
    _sign_in(client, "MARKETING")

    response = client.post(
        "/experiments/3/edit", data={**EDIT, "conversion_window_days": "21"}
    )

    assert response.status_code == 409
    assert "The conversion window is fixed" in response.get_data(as_text=True)


def test_an_unknown_experiment_is_a_404(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "web.routes.experiments.get_experiment", Mock(return_value=None)
    )
    client = app.test_client()
    _sign_in(client, "MARKETING")

    assert client.get("/experiments/99/edit").status_code == 404


@pytest.mark.parametrize("role", ["ANALYST", "STORE_MANAGER", "AUDITOR"])
def test_a_profile_without_campaign_write_cannot_set_one_up(
    app: Flask, role: str
) -> None:
    client = app.test_client()
    _sign_in(client, role)

    assert client.get("/experiments/new").status_code == 403
    assert client.post("/experiments/new", data=FORM).status_code == 403
