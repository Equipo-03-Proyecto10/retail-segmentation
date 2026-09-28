"""The consumption profile page (F8-04).

The profile itself is computed and tested in web/services/consumption_profile.py
(F8-03). What is covered here is the page: who may open it, that every measure
is shown with its unit and the window it covers, that a customer with no
purchase history is told so instead of being shown zeros, and that the two ways
a segment can be missing -- never assigned, and assigned to nothing -- are not
confused with each other. The database is a mock and `build_profile` is replaced,
so each test states exactly the profile the page was given.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, Mock

import pytest
from flask import Flask
from flask.testing import FlaskClient

from web.app import create_app
from web.config import Config
from web.db.categories import Category
from web.db.channels import Channel
from web.db.consumption import CategoryTotal, GroupTotal, ProductTotal
from web.db.customers import Customer
from web.services.consumption_profile import (
    ConsumptionProfile,
    RfmSnapshot,
    SegmentState,
    UnknownCustomer,
)

_ID = "00000000-0000-0000-0000-000000000001"
_URL = f"/catalog/customers/{_ID}/profile"
_START = datetime(2026, 4, 1, 9, 0, tzinfo=UTC)
_END = datetime(2026, 9, 28, 9, 0, tzinfo=UTC)
_LAST = datetime(2026, 9, 20, 10, 30, tzinfo=UTC)
_RUN_AT = datetime(2026, 9, 28, 0, 39, tzinfo=UTC)


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


def _customer(name: str = "Ada Lovelace") -> Customer:
    return Customer(
        customer_id=_ID,
        user_id=None,
        name=name,
        email="ada@example.test",
        phone="5500000001",
        registration_channel_id=2,
        registered_on=date(2026, 1, 15),
    )


def _segment(
    label: str | None,
    run_id: int = 30,
    name: str | None = None,
    *,
    closed: bool = False,
) -> SegmentState:
    """One history row. A previous segment is a closed row, so it has a
    `valid_to`; the current one is open."""
    return SegmentState(
        run_id=run_id,
        label_code=label,
        label_name=name if name is not None else (label.title() if label else None),
        valid_from=_RUN_AT,
        valid_to=_END if closed else None,
    )


def _rfm() -> RfmSnapshot:
    return RfmSnapshot(
        run_id=30,
        run_at=_RUN_AT,
        window_days=90,
        last_purchase_at=datetime(2026, 9, 1, 8, 0, tzinfo=UTC),
        frequency=7,
        monetary=Decimal("1234.50"),
        r_score=4,
        f_score=3,
        m_score=5,
    )


def _rfm_with(**overrides) -> RfmSnapshot:
    return RfmSnapshot(**{**_rfm().__dict__, **overrides})


def _profile(**overrides) -> ConsumptionProfile:
    defaults = dict(
        customer_id=_ID,
        customer_name="Ada Lovelace",
        window_days=180,
        window_start=_START,
        window_end=_END,
        has_sales=True,
        total_spend=Decimal("2750.00"),
        average_ticket=Decimal("275.00"),
        purchase_count=10,
        last_purchase_at=_LAST,
        dominant_channel=GroupTotal(4, "Marketplace", 6, Decimal("1650.00")),
        dominant_store=GroupTotal(8, "Store 8", 10, Decimal("2750.00")),
        favourite_categories=(CategoryTotal(2, "Snacks", 6, 18, Decimal("900.00")),),
        frequent_products=(ProductTotal(3, "Demo Product 3", 3, 9),),
        average_discount_pct=Decimal("64.94"),
        rfm=_rfm(),
        current_segment=_segment("LOYAL"),
        previous_segment=_segment("CHAMPION", run_id=29, closed=True),
    )
    return ConsumptionProfile(**{**defaults, **overrides})


def _open(
    app: Flask,
    monkeypatch: pytest.MonkeyPatch,
    profile: ConsumptionProfile | None = None,
    *,
    role: str = "ANALYST",
    url: str = _URL,
    customer: Customer | None = None,
) -> tuple[object, Mock]:
    """Sign in, give the page a profile, request the URL. Returns the response
    and the fake `build_profile`, so a test can inspect how it was called."""
    selected_customer = customer or _customer()
    fake = Mock(
        return_value=(
            profile
            if profile is not None
            else _profile(customer_name=selected_customer.name)
        )
    )
    monkeypatch.setattr("web.routes.catalog.build_profile", fake)
    monkeypatch.setattr(
        "web.routes.catalog.get_customer", lambda _c, _id: selected_customer
    )
    client = app.test_client()
    _sign_in(client, role)
    return client.get(url), fake


# ---------- who may open it ----------


@pytest.mark.parametrize(
    "role_code", ["STORE_MANAGER", "INVENTORY_PLANNER", "CUSTOMER"]
)
def test_a_profile_without_segment_read_is_refused(
    app: Flask, monkeypatch: pytest.MonkeyPatch, role_code: str
) -> None:
    response, fake = _open(app, monkeypatch, role=role_code)

    assert response.status_code == 403
    fake.assert_not_called()


@pytest.mark.parametrize("role_code", ["ADMIN", "ANALYST", "MARKETING", "AUDITOR"])
def test_the_roles_that_read_segments_may_open_it(
    app: Flask, monkeypatch: pytest.MonkeyPatch, role_code: str
) -> None:
    response, _ = _open(app, monkeypatch, role=role_code)

    assert response.status_code == 200


def test_signed_out_it_sends_you_to_sign_in(app: Flask) -> None:
    response = app.test_client().get(_URL)

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


# ---------- a customer with a profile ----------


def test_every_measure_is_shown_with_its_unit(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    response, _ = _open(app, monkeypatch)
    body = response.get_data(as_text=True)

    assert "Ada Lovelace" in body
    assert "2,750.00" in body and "MXN" in body  # total spend
    assert "275.00" in body  # average ticket
    assert re.search(r">\s*10\s*<", body) and "purchases" in body  # frequency
    assert "2026-09-20" in body  # last purchase, as a date
    assert "64.94" in body and "%" in body  # average discount
    assert "Marketplace" in body and "Store 8" in body
    assert "Snacks" in body and "Demo Product 3" in body


def test_the_window_the_profile_was_computed_over_is_stated(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    response, _ = _open(app, monkeypatch)
    body = response.get_data(as_text=True)

    assert "2026-04-01" in body
    assert "2026-09-28" in body
    assert "180-day" in body or "180 days" in body


def test_the_discount_says_what_it_is_measured_against(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RN-35: a comparison with today's list price, which can be negative."""
    response, _ = _open(
        app, monkeypatch, _profile(average_discount_pct=Decimal("-12.50"))
    )
    body = response.get_data(as_text=True)

    assert "-12.50" in body
    assert "list price" in body


def test_the_page_carries_r_f_m_values_scores_and_the_run_that_measured_them(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    response, _ = _open(app, monkeypatch)
    body = response.get_data(as_text=True)

    assert "1,234.50" in body  # monetary raw value
    assert "2026-09-01" in body  # recency: the last purchase that run saw
    assert "Run #30" in body or "run #30" in body
    assert "90" in body  # that run's own window, which is not the profile's
    for score in ("4", "3", "5"):
        assert score in body


def test_the_current_and_previous_segment_are_named(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    response, _ = _open(app, monkeypatch)
    body = response.get_data(as_text=True)

    assert "Loyal" in body
    assert "Champion" in body


def test_a_count_of_one_is_written_in_the_singular(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = _profile(
        purchase_count=1,
        total_spend=Decimal("50.00"),
        average_ticket=Decimal("50.00"),
        last_purchase_at=datetime(2026, 9, 27, 9, 0, tzinfo=UTC),
        dominant_channel=GroupTotal(4, "Marketplace", 1, Decimal("50.00")),
        dominant_store=GroupTotal(8, "Store 8", 1, Decimal("50.00")),
        rfm=_rfm_with(frequency=1),
    )
    response, _ = _open(app, monkeypatch, profile)
    body = response.get_data(as_text=True)

    assert "1 day before" in body
    assert "1 of 1 purchase " in body
    assert "1 purchases" not in body
    assert "1 days" not in body


# ---------- the window ----------


def test_the_default_window_is_the_segment_runs(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, fake = _open(app, monkeypatch)

    assert fake.call_args.kwargs["window_days"] == 180


def test_a_chosen_window_reaches_the_service(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, fake = _open(app, monkeypatch, url=_URL + "?window=30")

    assert fake.call_args.kwargs["window_days"] == 30


def test_the_success_path_looks_the_customer_up_once(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    customer_lookup = Mock(return_value=_customer())
    profile = _profile()

    def build_with_lookup(connection, customer_id, *, window_days):
        customer_lookup(connection, customer_id)
        return profile

    monkeypatch.setattr("web.routes.catalog.build_profile", build_with_lookup)
    monkeypatch.setattr("web.routes.catalog.get_customer", customer_lookup)
    client = app.test_client()
    _sign_in(client)

    assert client.get(_URL).status_code == 200
    customer_lookup.assert_called_once()


def test_the_window_limits_come_from_the_route_constants(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("web.routes.catalog.MAX_WINDOW_DAYS", 999)
    response, _ = _open(app, monkeypatch)

    assert "Enter a whole number between 1 and 999." in response.get_data(as_text=True)


@pytest.mark.parametrize("raw", ["abc", "0", "-5", "3651", "", "1.5"])
def test_a_window_the_segment_run_would_refuse_is_a_400_that_explains_itself(
    app: Flask, monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    response, fake = _open(app, monkeypatch, url=_URL + f"?window={raw}")
    body = response.get_data(as_text=True)

    assert response.status_code == 400
    assert "1" in body and "3650" in body
    fake.assert_not_called()
    assert "Total spend" not in body


# ---------- a customer with no accepted sales ----------


def _no_sales() -> ConsumptionProfile:
    return _profile(
        has_sales=False,
        total_spend=None,
        average_ticket=None,
        purchase_count=None,
        last_purchase_at=None,
        dominant_channel=None,
        dominant_store=None,
        favourite_categories=(),
        frequent_products=(),
        average_discount_pct=None,
    )


def test_a_customer_with_no_accepted_sales_is_told_there_is_no_purchase_history(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    response, _ = _open(app, monkeypatch, _no_sales())
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "No purchase history" in body
    assert "2026-04-01" in body and "2026-09-28" in body


def test_no_purchase_history_is_never_rendered_as_zeros(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    response, _ = _open(app, monkeypatch, _no_sales())
    body = response.get_data(as_text=True)

    for measure in ("Total spend", "Average ticket", "Average discount"):
        assert measure not in body
    assert "0.00" not in body


def test_a_customer_with_no_sales_still_shows_the_history_that_is_not_the_window(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Decided on #211: R/F/M and the segments are read from assignment history,
    so a window with no sales does not hide them."""
    response, _ = _open(app, monkeypatch, _no_sales())
    body = response.get_data(as_text=True)

    assert "Loyal" in body
    assert "1,234.50" in body


# ---------- two ways a segment can be missing ----------


def test_an_absent_previous_segment_is_not_shown_as_unassigned(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    response, _ = _open(app, monkeypatch, _profile(previous_segment=None))
    body = response.get_data(as_text=True)

    assert "No previous assignment" in body
    assert "Unassigned" not in body


def test_an_unassigned_previous_result_is_shown_as_unassigned(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RN-21: a run scored the customer and found nothing to label. That is a
    result, and it is not the same as there having been no previous run."""
    response, _ = _open(
        app,
        monkeypatch,
        _profile(previous_segment=_segment(None, run_id=29, closed=True)),
    )
    body = response.get_data(as_text=True)

    assert "Unassigned" in body
    assert "No previous assignment" not in body


def test_a_customer_no_run_has_scored_says_so(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    response, _ = _open(
        app,
        monkeypatch,
        _profile(rfm=None, current_segment=None, previous_segment=None),
    )
    body = response.get_data(as_text=True)

    assert "No segmentation run has scored this customer" in body


def test_a_customer_the_latest_run_left_unassigned_holds_no_rfm_values(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    response, _ = _open(
        app,
        monkeypatch,
        _profile(rfm=None, current_segment=_segment(None), previous_segment=None),
    )
    body = response.get_data(as_text=True)

    assert "Unassigned" in body
    assert "left this customer unassigned" in body


# ---------- a customer who does not exist ----------


def test_an_unknown_customer_is_a_404(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = Mock(side_effect=UnknownCustomer)
    monkeypatch.setattr("web.routes.catalog.build_profile", fake)
    client = app.test_client()
    _sign_in(client)

    assert client.get(_URL).status_code == 404
    fake.assert_called_once()


def test_a_customer_the_service_refuses_is_a_404(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "web.routes.catalog.build_profile", Mock(side_effect=UnknownCustomer)
    )
    client = app.test_client()
    _sign_in(client)

    assert client.get(_URL).status_code == 404


# ---------- what the page must never do ----------


def test_the_page_never_names_a_segmentation_method_or_a_cluster(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0018: a consumer of assignments reads the label and never learns how
    the run that wrote it was produced."""
    response, _ = _open(app, monkeypatch)
    body = response.get_data(as_text=True).lower()

    # (not "method": every page's sign-out form carries method="post".)
    for word in ("rfm_rules", "kmeans", "k-means", "cluster"):
        assert word not in body


def test_a_customer_name_is_escaped(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    response, _ = _open(
        app, monkeypatch, customer=_customer("<script>alert(1)</script>")
    )
    body = response.get_data(as_text=True)

    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body


def test_the_page_only_reads(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    _open(app, monkeypatch)
    client = app.test_client()
    _sign_in(client)

    assert client.post(_URL).status_code == 405


# ---------- reaching it from the customer ----------


def test_the_customer_page_links_to_the_profile(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("web.routes.catalog.get_customer", lambda _c, _id: _customer())
    monkeypatch.setattr(
        "web.routes.catalog.get_channel", lambda _c, _id: Channel(2, "Web")
    )
    monkeypatch.setattr(
        "web.routes.catalog.get_current_assignment", lambda _c, _id: None
    )
    monkeypatch.setattr(
        "web.routes.catalog.list_interest_categories",
        lambda _c, _id: [Category(1, "Running", None)],
    )
    monkeypatch.setattr(
        "web.routes.catalog.list_preferred_channels", lambda _c, _id: []
    )
    client = app.test_client()
    _sign_in(client)

    body = client.get(f"/catalog/customers/{_ID}").get_data(as_text=True)

    assert f'href="{_URL}"' in body
