"""The consumption reports page (F12-03).

`build_shift_report`/`build_recommendation_report` are built and tested in
web/services/consumption_reports.py. What is covered here is the page: who
may open it, that every applied filter reaches both reports, that a
recommendation row shows its reason and its stock, that a filter combination
with no rows says so per report rather than an empty table, and that it is
refused by the default-deny gate. The database is a mock and the two builders
are replaced, so each test states exactly what the page was given.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, Mock

import pytest
from flask import Flask
from flask.testing import FlaskClient

from web.app import create_app
from web.config import Config
from web.db.categories import Category
from web.db.channels import Channel
from web.db.stores import Store
from web.services.consumption_reports import (
    FilteredShiftReport,
    RecommendationReportPage,
    RecommendationRow,
    ShiftRow,
)
from web.services.consumption_shift import CustomerShift, DimensionShift, Leader, Period
from web.services.recommendations import Reason, Signal

_URL = "/consumption-reports/"
_WHEN = datetime(2026, 9, 28, tzinfo=UTC)


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
        database_connector=Mock(return_value=MagicMock()),
    )
    application.config["PROPAGATE_EXCEPTIONS"] = False
    return application


def _sign_in(client: FlaskClient, role_code: str = "ANALYST") -> None:
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = "11111111-1111-1111-1111-000000000001"
        flask_session["role_code"] = role_code
        flask_session["name"] = f"{role_code.title()} user"


def _shift_report(**overrides) -> FilteredShiftReport:
    defaults = dict(
        earlier=Period(
            datetime(2026, 6, 1, tzinfo=UTC), datetime(2026, 9, 1, tzinfo=UTC)
        ),
        later=Period(datetime(2026, 9, 1, tzinfo=UTC), _WHEN),
        rows=(
            ShiftRow(
                "00000000-0000-0000-0000-000000000001",
                "Ada Lovelace",
                CustomerShift(
                    customer_id="00000000-0000-0000-0000-000000000001",
                    store=DimensionShift(Leader(1, "Store 1"), Leader(2, "Store 2")),
                ),
            ),
        ),
        compared=1,
        unchanged=0,
    )
    return FilteredShiftReport(**{**defaults, **overrides})


def _recommendation_page(**overrides) -> RecommendationReportPage:
    defaults = dict(
        rows=(
            RecommendationRow(
                customer_id="00000000-0000-0000-0000-000000000001",
                customer_name="Ada Lovelace",
                store_id=8,
                store_name="Store 8",
                channel_id=4,
                channel_name="Marketplace",
                product_id=1,
                product_name="Sparkling water",
                category_id=3,
                category_name="Beverages",
                in_stock=22,
                reasons=(Reason(Signal.SEGMENT, "2 other Loyal customers bought it"),),
            ),
        ),
        total=1,
        page=1,
        page_size=25,
        page_count=1,
    )
    return RecommendationReportPage(**{**defaults, **overrides})


def _open(
    app: Flask,
    monkeypatch: pytest.MonkeyPatch,
    *,
    shifts=None,
    recommendations=None,
    role: str = "ANALYST",
    url: str = _URL,
    shift_side_effect=None,
    rec_side_effect=None,
):
    shift_fake = Mock(return_value=shifts if shifts is not None else _shift_report())
    if shift_side_effect is not None:
        shift_fake = Mock(side_effect=shift_side_effect)
    rec_fake = Mock(
        return_value=(
            recommendations if recommendations is not None else _recommendation_page()
        )
    )
    if rec_side_effect is not None:
        rec_fake = Mock(side_effect=rec_side_effect)
    monkeypatch.setattr("web.routes.consumption_reports.build_shift_report", shift_fake)
    monkeypatch.setattr(
        "web.routes.consumption_reports.build_recommendation_report", rec_fake
    )
    monkeypatch.setattr(
        "web.routes.consumption_reports.list_stores",
        Mock(return_value=([Store(8, "Store 8", "City", "ST", True)], 1)),
    )
    monkeypatch.setattr(
        "web.routes.consumption_reports.list_channels",
        Mock(return_value=([Channel(4, "Marketplace")], 1)),
    )
    monkeypatch.setattr(
        "web.routes.consumption_reports.list_all_categories",
        Mock(return_value=[Category(3, "Beverages", None)]),
    )
    client = app.test_client()
    _sign_in(client, role)
    return client.get(url), shift_fake, rec_fake


def _body(response) -> str:
    return response.get_data(as_text=True)


# ---------- who may open it ----------


@pytest.mark.parametrize("role_code", ["INVENTORY_PLANNER", "CUSTOMER"])
def test_a_profile_without_segment_read_is_refused(app, monkeypatch, role_code) -> None:
    response, shift_fake, rec_fake = _open(app, monkeypatch, role=role_code)

    assert response.status_code == 403
    shift_fake.assert_not_called()
    rec_fake.assert_not_called()


@pytest.mark.parametrize(
    "role_code", ["ADMIN", "ANALYST", "MARKETING", "AUDITOR", "STORE_MANAGER"]
)
def test_the_roles_that_read_segments_may_open_it(app, monkeypatch, role_code) -> None:
    response, _, _ = _open(app, monkeypatch, role=role_code)

    assert response.status_code == 200


def test_signed_out_it_sends_you_to_sign_in(app) -> None:
    response = app.test_client().get(_URL)

    assert response.status_code == 302 and "/login" in response.headers["Location"]


def test_the_page_only_reads(app, monkeypatch) -> None:
    _open(app, monkeypatch)
    client = app.test_client()
    _sign_in(client)

    assert client.post(_URL).status_code == 405


# ---------- every applied filter reaches both reports ----------


def test_a_store_filter_reaches_both_builders(app, monkeypatch) -> None:
    _, shift_fake, rec_fake = _open(app, monkeypatch, url=_URL + "?store=8")

    assert shift_fake.call_args.kwargs["store_id"] == 8
    assert rec_fake.call_args.kwargs["store_id"] == 8


def test_a_channel_and_category_filter_reach_both_builders(app, monkeypatch) -> None:
    _, shift_fake, rec_fake = _open(
        app, monkeypatch, url=_URL + "?channel=4&category=3"
    )

    for fake in (shift_fake, rec_fake):
        assert fake.call_args.kwargs["channel_id"] == 4
        assert fake.call_args.kwargs["category_id"] == 3


def test_the_window_reaches_both_builders(app, monkeypatch) -> None:
    _, shift_fake, rec_fake = _open(app, monkeypatch, url=_URL + "?window_days=45")

    assert shift_fake.call_args.kwargs["window_days"] == 45
    assert rec_fake.call_args.kwargs["window_days"] == 45


def test_the_chosen_filters_are_reflected_back_into_the_form(app, monkeypatch) -> None:
    body = _body(_open(app, monkeypatch, url=_URL + "?store=8&channel=4")[0])

    assert 'value="8" selected' in body.replace("  ", " ")
    assert 'value="4" selected' in body.replace("  ", " ")


@pytest.mark.parametrize("param", ["store", "channel", "category"])
def test_a_choice_that_is_not_a_whole_number_is_a_400(app, monkeypatch, param) -> None:
    response, shift_fake, rec_fake = _open(app, monkeypatch, url=_URL + f"?{param}=abc")

    assert response.status_code == 400
    shift_fake.assert_not_called()
    rec_fake.assert_not_called()


def test_an_invalid_window_is_a_400(app, monkeypatch) -> None:
    response, _, _ = _open(app, monkeypatch, url=_URL + "?window_days=abc")

    assert response.status_code == 400


def test_a_period_the_service_refuses_is_a_400(app, monkeypatch) -> None:
    from web.services.consumption_shift import InvalidPeriods

    response, _, _ = _open(
        app,
        monkeypatch,
        shift_side_effect=InvalidPeriods("A period must end after it starts."),
    )

    assert response.status_code == 400
    assert "must end after it starts" in _body(response)


# ---------- a recommendation row carries its reason and its stock ----------


def test_a_recommendation_row_shows_the_reason_and_the_stock(app, monkeypatch) -> None:
    body = _body(_open(app, monkeypatch)[0])

    assert "Sparkling water" in body
    assert "22 units" in body
    assert "2 other Loyal customers bought it" in body
    assert "Segment" in body


# ---------- no rows: each report says so ----------


def test_no_shifts_matching_says_so(app, monkeypatch) -> None:
    body = _body(_open(app, monkeypatch, shifts=_shift_report(rows=()))[0])

    assert "No shifts match those filters" in body


def test_no_recommendations_matching_says_so(app, monkeypatch) -> None:
    body = _body(
        _open(app, monkeypatch, recommendations=_recommendation_page(rows=(), total=0))[
            0
        ]
    )

    assert "No recommendations match those filters" in body


# ---------- what the page must never do ----------


def test_the_page_never_names_a_segmentation_method_or_a_cluster(
    app, monkeypatch
) -> None:
    body = _body(_open(app, monkeypatch)[0]).lower()

    for word in ("kmeans", "k-means", "cluster", "rfm_rules"):
        assert word not in body


def test_names_are_escaped(app, monkeypatch) -> None:
    hostile = _shift_report(
        rows=(
            ShiftRow(
                "00000000-0000-0000-0000-000000000001",
                "<script>alert(1)</script>",
                CustomerShift(
                    customer_id="00000000-0000-0000-0000-000000000001",
                    store=DimensionShift(Leader(1, "A"), Leader(2, "B")),
                ),
            ),
        )
    )

    body = _body(_open(app, monkeypatch, shifts=hostile)[0])

    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body
