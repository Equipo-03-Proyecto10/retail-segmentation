"""Exposure as its own event (F11-05, ADR-0019).

The database is mocked, as in the other route tests. What is proved here is the
application's rule; the tables themselves come from sql/01_schema.sql.
"""

from __future__ import annotations

from itertools import chain, repeat
from unittest.mock import MagicMock, Mock

import pytest
from flask import Flask

from tests.test_experiment_assignment import _experiment
from web.app import create_app
from web.config import Config
from web.db import experiments as db
from web.db.experiments import GroupExposure
from web.services import experiments as service

USER_ID = "11111111-1111-1111-1111-000000000001"
CUSTOMER = "00000000-0000-0000-0000-000000000005"
GROUPS = [
    GroupExposure(61, "CONTROL", 5, 0),
    GroupExposure(62, "TREATMENT", 4, 3),
    GroupExposure(63, "TREATMENT", 4, 1),
]


def _wire(
    monkeypatch: pytest.MonkeyPatch, *, found: tuple[int, str] | None
) -> dict[str, Mock]:
    mocks = {
        "lock_experiment": Mock(return_value=True),
        "find_assignment": Mock(return_value=found),
        "insert_exposure": Mock(),
        "get_experiment": Mock(return_value=_experiment(assignments=13)),
        "list_group_exposure": Mock(return_value=GROUPS),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(db, name, mock)
    return mocks


# ---------- the rule ----------


def test_a_treatment_customer_is_exposed(monkeypatch: pytest.MonkeyPatch) -> None:
    mocks = _wire(monkeypatch, found=(900, "TREATMENT"))

    service.record_exposure(MagicMock(), 31, CUSTOMER)

    mocks["insert_exposure"].assert_called_once()
    assert mocks["insert_exposure"].call_args.args[1] == 900


def test_the_control_group_is_refused_and_nothing_is_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mocks = _wire(monkeypatch, found=(901, "CONTROL"))

    with pytest.raises(service.ExposureRefused, match="control group"):
        service.record_exposure(MagicMock(), 31, CUSTOMER)

    mocks["insert_exposure"].assert_not_called()


def test_an_unassigned_customer_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    mocks = _wire(monkeypatch, found=None)

    with pytest.raises(service.ExposureRefused, match="not assigned"):
        service.record_exposure(MagicMock(), 31, CUSTOMER)

    mocks["insert_exposure"].assert_not_called()


def test_an_unknown_experiment_is_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    mocks = _wire(monkeypatch, found=(900, "TREATMENT"))
    mocks["lock_experiment"].return_value = False

    with pytest.raises(service.ExperimentNotFound):
        service.record_exposure(MagicMock(), 99, CUSTOMER)


def test_the_assignment_is_looked_up_only_after_the_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    mocks = _wire(monkeypatch, found=(900, "TREATMENT"))
    mocks["lock_experiment"].side_effect = lambda *a: order.append("lock") or True
    mocks["find_assignment"].side_effect = lambda *a: order.append("find") or (
        900,
        "TREATMENT",
    )

    service.record_exposure(MagicMock(), 31, CUSTOMER)

    assert order == ["lock", "find"]


@pytest.mark.parametrize("raw", ["", "abc", "1234", CUSTOMER[:-1]])
def test_a_malformed_customer_id_is_not_a_uuid(raw: str) -> None:
    assert service.parse_customer_id(raw) is None


def test_a_customer_id_is_normalised() -> None:
    assert service.parse_customer_id(f"  {CUSTOMER.upper()} ") == CUSTOMER


# ---------- the counts ----------


def test_assigned_but_never_exposed_stay_in_assigned_and_out_of_exposed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(monkeypatch, found=None)

    summary = service.exposure_summary(MagicMock(), 31)

    assert summary.treatment_assigned == 8
    assert summary.treatment_exposed == 4
    assert summary.not_exposed == 4
    assert summary.exposure_rate == 0.5


def test_the_rate_is_undefined_before_anyone_is_assigned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mocks = _wire(monkeypatch, found=None)
    mocks["list_group_exposure"].return_value = []

    assert service.exposure_summary(MagicMock(), 31).exposure_rate is None


def test_the_exposure_insert_is_parameterized_and_carries_no_timestamp() -> None:
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value

    db.insert_exposure(connection, 900)

    statement, params = cursor.execute.call_args.args
    assert statement.startswith("INSERT INTO experiment_exposure")
    assert "exposed_at" not in statement  # the column default is its own instant
    assert params == (900,)


def test_no_module_rewrites_an_exposure() -> None:
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    rewrite = re.compile(
        r"(UPDATE\s+experiment_exposure|DELETE\s+FROM\s+experiment_exposure)",
        re.IGNORECASE,
    )
    assert [
        p.name for p in (root / "web").rglob("*.py") if rewrite.search(p.read_text())
    ] == []


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


def _as(app: Flask, role: str):
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session.update(user_id=USER_ID, role_code=role, name="Test User")
    return client


def test_the_page_presents_the_rate_as_a_delivery_diagnostic(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire(monkeypatch, found=None)

    response = _as(app, "MARKETING").get("/experiments/31/exposure")

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Delivery diagnostic" in body
    assert "4 of 8 assigned treatment customers were exposed (50.0%)" in body
    assert "not whether it worked" in body


def test_recording_an_exposure_redirects_with_a_confirmation(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    mocks = _wire(monkeypatch, found=(900, "TREATMENT"))

    response = _as(app, "MARKETING").post(
        "/experiments/31/exposure", data={"customer_id": CUSTOMER}
    )

    assert response.status_code == 302
    mocks["insert_exposure"].assert_called_once()


def test_exposing_the_control_group_is_a_409(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    mocks = _wire(monkeypatch, found=(901, "CONTROL"))

    response = _as(app, "MARKETING").post(
        "/experiments/31/exposure", data={"customer_id": CUSTOMER}
    )

    assert response.status_code == 409
    assert "control group" in response.get_data(as_text=True)
    mocks["insert_exposure"].assert_not_called()


def test_a_malformed_id_is_a_400_and_reaches_no_query(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    mocks = _wire(monkeypatch, found=None)

    response = _as(app, "MARKETING").post(
        "/experiments/31/exposure", data={"customer_id": "nope"}
    )

    assert response.status_code == 400
    mocks["find_assignment"].assert_not_called()
