"""Filters on the campaign, experiment and run-history lists (#538, AN-17b).

Each list takes a text filter and one closed-vocabulary filter, follows the
catalog lists' pattern (the filters survive paging, an empty result says so),
and keeps its permission gate. The database is a mock here, so these cover
what reaches the driver; tests/test_analytics_db_postgres.py runs the same
queries on PostgreSQL and shows `%` and `_` matching literally there.
"""

from __future__ import annotations

from datetime import date, datetime
from itertools import chain, repeat
from unittest.mock import MagicMock, Mock

import pytest
from flask import Flask
from flask.testing import FlaskClient

from web.app import create_app
from web.config import Config
from web.db import campaigns as campaign_db
from web.db import experiments as experiment_db
from web.db import segments as segment_db
from web.db.campaigns import Campaign
from web.db.experiments import Experiment
from web.db.segments import SegmentationRun

USER_ID = "11111111-1111-1111-1111-000000000001"


@pytest.fixture
def connection() -> MagicMock:
    connection = MagicMock()
    connection.closed = False
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = (0,)
    return connection


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


def _as(app: Flask, role_code: str) -> FlaskClient:
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = USER_ID
        flask_session["role_code"] = role_code
        flask_session["name"] = f"{role_code.title()} user"
    return client


def _campaign(campaign_id: int = 7, status: str = "DRAFT") -> Campaign:
    return Campaign(
        campaign_id,
        "Win-back",
        "AT_RISK",
        date(2026, 10, 1),
        date(2026, 10, 31),
        status,
    )


def _experiment(experiment_id: int = 31) -> Experiment:
    return Experiment(
        experiment_id,
        "Spring offer",
        7,
        "Win-back",
        "CONVERSION",
        date(2026, 10, 1),
        None,
        7,
        "SEEDED",
        1,
        1,
        0,
    )


def _run(run_id: int = 40) -> SegmentationRun:
    return SegmentationRun(
        run_id,
        "KMEANS",
        180,
        {"k": 6, "seed": 17},
        30,
        USER_ID,
        "MOSAIQ Administrator",
        datetime(2026, 10, 1, 9, 0),
        "OBSERVED",
    )


def _executed(connection: MagicMock) -> list[tuple[str, dict]]:
    cursor = connection.cursor.return_value.__enter__.return_value
    return [(call.args[0], call.args[1]) for call in cursor.execute.call_args_list]


# ---------- the queries: literal wildcards, parameters only ----------


@pytest.mark.parametrize(
    ("search", "pattern"),
    [("50%", "%50\\%%"), ("spring_sale", "%spring\\_sale%"), ("a\\b", "%a\\\\b%")],
)
def test_each_list_matches_wildcards_literally(
    connection: MagicMock, search: str, pattern: str
) -> None:
    campaign_db.list_campaigns(connection, page=1, per_page=20, search=search)
    experiment_db.list_experiments(connection, page=1, per_page=20, search=search)
    segment_db.list_runs(connection, page=1, per_page=20, search=search)

    statements = _executed(connection)
    assert len(statements) == 6
    for sql, parameters in statements:
        assert parameters["search"] == pattern
        assert search not in sql


def test_an_unset_filter_is_sent_as_null(connection: MagicMock) -> None:
    campaign_db.list_campaigns(connection, page=2, per_page=20)
    experiment_db.list_experiments(connection, page=2, per_page=20)
    segment_db.list_runs(connection, page=2, per_page=20)

    for sql, parameters in _executed(connection):
        assert parameters["search"] is None
        assert "IS NULL" in sql
        if "LIMIT" in sql:
            assert (parameters["limit"], parameters["offset"]) == (20, 20)


def test_the_choice_filters_reach_the_query_as_parameters(
    connection: MagicMock,
) -> None:
    campaign_db.list_campaigns(connection, page=1, per_page=20, status="ACTIVE")
    experiment_db.list_experiments(
        connection, page=1, per_page=20, target_metric="CONVERSION"
    )
    segment_db.list_runs(connection, page=1, per_page=20, method="KMEANS")

    sent = [parameters for _sql, parameters in _executed(connection)]
    assert [p["status"] for p in sent[0:2]] == ["ACTIVE", "ACTIVE"]
    assert [p["target_metric"] for p in sent[2:4]] == ["CONVERSION", "CONVERSION"]
    assert [p["method"] for p in sent[4:6]] == ["KMEANS", "KMEANS"]


# ---------- the routes: filters read, carried and refused ----------

LISTS = [
    # (path, role, the patched reader, its filter args, a valid choice)
    (
        "/campaigns/",
        "MARKETING",
        "web.routes.campaigns.list_campaigns",
        "status",
        "ACTIVE",
    ),
    (
        "/experiments/",
        "MARKETING",
        "web.routes.experiments.list_experiments",
        "metric",
        "CONVERSION",
    ),
    (
        "/run-history/",
        "ANALYST",
        "web.routes.run_history.list_runs",
        "method",
        "KMEANS",
    ),
]


@pytest.mark.parametrize(("path", "role", "reader", "choice", "value"), LISTS)
def test_the_filters_reach_the_reader(
    app: Flask,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    role: str,
    reader: str,
    choice: str,
    value: str,
) -> None:
    read = Mock(return_value=([], 0))
    monkeypatch.setattr(reader, read)

    response = _as(app, role).get(path, query_string={"q": "  50%  ", choice: value})

    assert response.status_code == 200
    kwargs = read.call_args.kwargs
    assert kwargs["search"] == "50%"
    assert value in kwargs.values()


@pytest.mark.parametrize(("path", "role", "reader", "choice", "value"), LISTS)
def test_an_unknown_choice_is_a_400(
    app: Flask,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    role: str,
    reader: str,
    choice: str,
    value: str,
) -> None:
    read = Mock(return_value=([], 0))
    monkeypatch.setattr(reader, read)

    response = _as(app, role).get(path, query_string={choice: "NOT_A_CODE"})

    assert response.status_code == 400
    read.assert_not_called()


@pytest.mark.parametrize(("path", "role", "reader", "choice", "value"), LISTS)
def test_an_empty_filtered_result_says_so(
    app: Flask,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    role: str,
    reader: str,
    choice: str,
    value: str,
) -> None:
    monkeypatch.setattr(reader, Mock(return_value=([], 0)))

    filtered = _as(app, role).get(path, query_string={"q": "nothing"})
    unfiltered = _as(app, role).get(path)

    assert "match these filters" in filtered.get_data(as_text=True)
    assert 'value="nothing"' in filtered.get_data(as_text=True)
    assert "match these filters" not in unfiltered.get_data(as_text=True)


@pytest.mark.parametrize(
    ("path", "role", "reader", "row"),
    [
        (
            "/campaigns/",
            "MARKETING",
            "web.routes.campaigns.list_campaigns",
            _campaign(),
        ),
        (
            "/experiments/",
            "MARKETING",
            "web.routes.experiments.list_experiments",
            _experiment(),
        ),
        ("/run-history/", "ANALYST", "web.routes.run_history.list_runs", _run()),
    ],
)
def test_the_filters_survive_paging(
    app: Flask,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    role: str,
    reader: str,
    row: object,
) -> None:
    monkeypatch.setattr(reader, Mock(return_value=([row], 45)))
    monkeypatch.setattr(
        "web.routes.experiments.list_group_definitions", Mock(return_value=[])
    )

    body = (
        _as(app, role)
        .get(path, query_string={"q": "a b", "page": 2})
        .get_data(as_text=True)
    )

    assert f'href="{path}?page=1&amp;q=a+b"' in body
    assert f'href="{path}?page=3&amp;q=a+b"' in body


def test_a_campaign_status_change_returns_to_the_filtered_list(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "web.routes.campaigns.list_campaigns", Mock(return_value=([_campaign()], 1))
    )
    monkeypatch.setattr(
        "web.routes.campaigns.service.transition", Mock(return_value="CANCELLED")
    )
    client = _as(app, "MARKETING")

    body = client.get(
        "/campaigns/", query_string={"status": "DRAFT", "q": "win"}
    ).get_data(as_text=True)
    action = "/campaigns/7/cancel?page=1&amp;q=win&amp;status=DRAFT"
    edit = "/campaigns/7/edit?page=1&amp;q=win&amp;status=DRAFT"
    assert action in body
    assert edit in body

    response = client.post(action.replace("&amp;", "&"))

    assert response.status_code == 302
    assert response.headers["Location"].endswith(
        "/campaigns/?page=1&q=win&status=DRAFT"
    )


@pytest.mark.parametrize(
    ("path", "role"),
    [
        ("/campaigns/?q=a&status=ACTIVE", "STORE_MANAGER"),
        ("/experiments/?q=a&metric=CONVERSION", "INVENTORY_PLANNER"),
        ("/run-history/?q=a&method=KMEANS", "CUSTOMER"),
    ],
)
def test_a_filter_does_not_open_the_gate(app: Flask, path: str, role: str) -> None:
    assert _as(app, role).get(path).status_code == 403
