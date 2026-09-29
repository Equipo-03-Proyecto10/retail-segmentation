"""Conversion as an attribution from an assignment to a sale (F11-06, ADR-0019).

The database is mocked, as in the other route tests, so what is proved here is
the service's rules and the shape of the SQL it sends. The window arithmetic
itself is PostgreSQL's and was not exercised: there is no local database.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal
from itertools import chain, repeat
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest
from flask import Flask

from tests.test_experiment_assignment import _experiment
from web.app import create_app
from web.config import Config
from web.db import experiment_conversions as db
from web.db import experiments
from web.db.experiment_conversions import Attribution, GroupConversion
from web.services import experiment_conversions as service
from web.services.experiments import ExperimentNotFound

ROOT = Path(__file__).resolve().parents[1]
USER_ID = "11111111-1111-1111-1111-000000000001"
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
GROUPS = [
    GroupConversion(61, "CONTROL", 5, 1, 2, 2),
    GroupConversion(62, "TREATMENT", 5, 2, 1, 2),
]


def _wire(
    monkeypatch: pytest.MonkeyPatch, *, assignments: int = 10, added: int = 3
) -> dict[str, Mock]:
    mocks = {
        "lock_experiment": Mock(return_value=True),
        "get_experiment": Mock(return_value=_experiment(assignments=assignments)),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(experiments, name, mock)
    conversion_mocks = {
        "evaluate_conversions": Mock(return_value=added),
        "list_group_conversion": Mock(return_value=GROUPS),
        "list_attributions": Mock(
            return_value=(
                [
                    Attribution(
                        1,
                        900,
                        "00000000-0000-0000-0000-000000000005",
                        "TREATMENT",
                        datetime(2026, 10, 1, 9, 0, tzinfo=UTC),
                        datetime(2026, 10, 15, 9, 0, tzinfo=UTC),
                        77,
                        "TX-77",
                        datetime(2026, 10, 3, 10, 30, tzinfo=UTC),
                        Decimal("42.50"),
                    )
                ],
                1,
            )
        ),
    }
    for name, mock in conversion_mocks.items():
        monkeypatch.setattr(db, name, mock)
    monkeypatch.setattr(
        "web.routes.experiments.list_attributions",
        conversion_mocks["list_attributions"],
    )
    return {**mocks, **conversion_mocks}


# ---------- evaluating ----------


def test_evaluation_attributes_and_reports_how_many_were_added(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mocks = _wire(monkeypatch, added=3)

    assert service.evaluate(MagicMock(), 31) == 3
    assert mocks["evaluate_conversions"].call_args.args[1] == 31


def test_an_experiment_without_assignments_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mocks = _wire(monkeypatch, assignments=0)

    with pytest.raises(service.ConversionRefused, match="no assignments"):
        service.evaluate(MagicMock(), 31)

    mocks["evaluate_conversions"].assert_not_called()


def test_an_unknown_experiment_is_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    mocks = _wire(monkeypatch)
    mocks["lock_experiment"].return_value = False

    with pytest.raises(ExperimentNotFound):
        service.evaluate(MagicMock(), 99)


def test_the_experiment_is_locked_before_it_is_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    mocks = _wire(monkeypatch)
    mocks["lock_experiment"].side_effect = lambda *a: order.append("lock") or True
    mocks["get_experiment"].side_effect = lambda *a: order.append("read") or (
        _experiment(assignments=10)
    )

    service.evaluate(MagicMock(), 31)

    assert order == ["lock", "read"]


def test_a_failure_rolls_the_evaluation_back(monkeypatch: pytest.MonkeyPatch) -> None:
    mocks = _wire(monkeypatch)
    mocks["evaluate_conversions"].side_effect = RuntimeError("connection lost")
    connection = MagicMock()

    with pytest.raises(RuntimeError):
        service.evaluate(connection, 31)

    connection.rollback.assert_called_once()
    connection.commit.assert_not_called()


# ---------- the SQL ----------


def _sql_of(call) -> str:
    return re.sub(r"\s+", " ", call.args[0])


def test_the_window_is_the_experiments_own_and_starts_at_the_assignment() -> None:
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value

    db.evaluate_conversions(connection, 31)

    sql = _sql_of(cursor.execute.call_args)
    assert "t.customer_id = a.customer_id" in sql
    assert "t.occurred_at >= a.assigned_at" in sql
    assert (
        "t.occurred_at < a.assigned_at "
        "+ make_interval(days => e.conversion_window_days)" in sql
    )
    assert cursor.execute.call_args.args[1] == (31,)


def test_re_evaluating_cannot_record_a_sale_twice() -> None:
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value

    db.evaluate_conversions(connection, 31)

    assert "ON CONFLICT (assignment_id, transaction_id) DO NOTHING" in _sql_of(
        cursor.execute.call_args
    )


def test_pending_is_an_open_window_and_not_converted_a_closed_one() -> None:
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = [(61, "CONTROL", 5, 1, 2, 2)]

    counts = db.list_group_conversion(connection, 31, NOW)

    sql = _sql_of(cursor.execute.call_args)
    assert "FILTER (WHERE NOT o.converted AND o.open)" in sql
    assert "FILTER (WHERE NOT o.converted AND NOT o.open)" in sql
    assert cursor.execute.call_args.args[1] == (NOW, 31, 31)
    assert counts == [GroupConversion(61, "CONTROL", 5, 1, 2, 2)]


def test_a_sale_carries_no_experiment_column() -> None:
    schema = (ROOT / "sql/01_schema.sql").read_text()
    table = re.search(r"CREATE TABLE transaction \((.*?)\n\);", schema, re.S).group(1)

    assert "experiment" not in table.lower()


def test_no_module_rewrites_a_conversion() -> None:
    forbidden = re.compile(
        r"(UPDATE\s+experiment_conversion|DELETE\s+FROM\s+experiment_conversion)",
        re.IGNORECASE,
    )
    assert [
        p.name for p in (ROOT / "web").rglob("*.py") if forbidden.search(p.read_text())
    ] == []


# ---------- the summary ----------


def test_every_assigned_customer_is_in_exactly_one_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(monkeypatch)

    summary = service.conversion_summary(MagicMock(), 31, NOW)

    assert summary.evaluated_at == NOW
    assert summary.pending == 3
    for group in summary.groups:
        assert group.converted + group.pending + group.not_converted == group.assigned


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


def test_the_page_separates_pending_from_not_converted_and_traces_a_sale(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire(monkeypatch)

    response = _as(app, "MARKETING").get("/experiments/31/conversion")

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Pending" in body and "Not converted" in body
    assert "pending, not counted as not converted" in body
    assert "TX-77" in body and "#77" in body  # traceable to the sale
    assert "900" in body  # and to the assignment


def test_evaluating_redirects_with_the_count(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire(monkeypatch, added=3)
    client = _as(app, "MARKETING")

    response = client.post("/experiments/31/conversion")

    assert response.status_code == 302
    with client.session_transaction() as flask_session:
        (_, message) = flask_session["_flashes"][0]
    assert "3 new conversions recorded" in message


def test_a_refused_evaluation_is_a_409(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire(monkeypatch, assignments=0)

    response = _as(app, "MARKETING").post("/experiments/31/conversion")

    assert response.status_code == 409
    assert "no assignments" in response.get_data(as_text=True)
