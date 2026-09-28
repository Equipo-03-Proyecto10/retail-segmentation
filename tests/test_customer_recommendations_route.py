"""The customer recommendations page (F10-02).

The recommendations are computed and tested in web/services/recommendations.py
(F10-01). What is covered here is the page: who may open it, that each
recommendation shows the product, the store, the stock available and the reason it
was chosen, that it is computed afresh on every request so a product whose stock
reaches zero is gone on the next reload, and that a customer with nothing to
recommend is told why instead of being shown an empty table. The database is a mock
and `recommend` is replaced, so each test states exactly the result the page was
given.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import MagicMock, Mock

import pytest
from flask import Flask
from flask.testing import FlaskClient

from web.app import create_app
from web.config import Config
from web.db.customers import Customer
from web.services.consumption_profile import UnknownCustomer
from web.services.recommendations import (
    Reason,
    Recommendation,
    RecommendationResult,
    Signal,
    Status,
)

_ID = "00000000-0000-0000-0000-000000000001"
_URL = f"/catalog/customers/{_ID}/recommendations"
_START = datetime(2026, 4, 1, 9, 0, tzinfo=UTC)
_END = datetime(2026, 9, 28, 9, 0, tzinfo=UTC)


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


def _recommendation(
    product_id: int, name: str, stock: int = 22, reasons=None
) -> Recommendation:
    return Recommendation(
        product_id=product_id,
        name=name,
        category_id=3,
        category_name="Beverages",
        in_stock=stock,
        reasons=tuple(
            reasons
            or [
                Reason(
                    Signal.SEGMENT,
                    "2 other Lost customers bought it in the last 180 days",
                ),
                Reason(
                    Signal.PREFERRED_CATEGORY,
                    "In Beverages, a category the customer said they like",
                ),
            ]
        ),
    )


def _result(**overrides) -> RecommendationResult:
    defaults = dict(
        customer_id=_ID,
        customer_name="Ada Lovelace",
        status=Status.RECOMMENDED,
        message="2 recommendations from what Store 8 has in stock.",
        window_days=180,
        window_start=_START,
        window_end=_END,
        label_code="LOST",
        label_name="Lost",
        store_id=8,
        store_name="Store 8",
        recommendations=(
            _recommendation(4, "Sparkling water"),
            _recommendation(
                1,
                "Whole milk",
                stock=19,
                reasons=[Reason(Signal.PURCHASE_HISTORY, "Bought from Dairy 6 times")],
            ),
        ),
    )
    return RecommendationResult(**{**defaults, **overrides})


def _open(
    app: Flask,
    monkeypatch: pytest.MonkeyPatch,
    result: RecommendationResult | None = None,
    *,
    role: str = "ANALYST",
    url: str = _URL,
    side_effect=None,
) -> tuple[object, Mock]:
    fake = Mock(return_value=result or _result())
    if side_effect is not None:
        fake = Mock(side_effect=side_effect)
    monkeypatch.setattr("web.routes.catalog.recommend", fake)
    client = app.test_client()
    _sign_in(client, role)
    return client.get(url), fake


def _body(response) -> str:
    return response.get_data(as_text=True)


# ---------- who may open it ----------


@pytest.mark.parametrize("role_code", ["INVENTORY_PLANNER", "CUSTOMER"])
def test_a_profile_without_segment_read_is_refused(app, monkeypatch, role_code) -> None:
    response, fake = _open(app, monkeypatch, role=role_code)

    assert response.status_code == 403
    fake.assert_not_called()


@pytest.mark.parametrize(
    "role_code", ["ADMIN", "ANALYST", "STORE_MANAGER", "MARKETING", "AUDITOR"]
)
def test_the_roles_that_read_segments_may_open_it(app, monkeypatch, role_code) -> None:
    response, _ = _open(app, monkeypatch, role=role_code)

    assert response.status_code == 200


def test_signed_out_it_sends_you_to_sign_in(app) -> None:
    response = app.test_client().get(_URL)

    assert response.status_code == 302 and "/login" in response.headers["Location"]


def test_the_page_only_reads(app, monkeypatch) -> None:
    _open(app, monkeypatch)
    client = app.test_client()
    _sign_in(client)

    assert client.post(_URL).status_code == 405


# ---------- a customer with recommendations ----------


def _cards(body: str) -> dict[str, str]:
    """Each recommendation's own markup, by product id: from its marker to the next."""
    parts = body.split('data-recommendation="')[1:]
    return {part.split('"', 1)[0]: part for part in parts}


def test_each_recommendation_shows_the_product_the_store_the_stock_and_the_reason(
    app, monkeypatch
) -> None:
    """Checked inside each recommendation's own card, so a store or a stock figure
    stated once at the top of the page cannot stand in for it."""
    body = _body(_open(app, monkeypatch)[0])
    cards = _cards(body)

    water, milk = cards["4"], cards["1"]
    assert "Sparkling water" in water and "Whole milk" in milk
    for card in (water, milk):
        assert "Store 8" in card
    assert "22 units" in water and "19 units" in milk
    assert "2 other Lost customers bought it in the last 180 days" in water
    assert "In Beverages, a category the customer said they like" in water
    assert "Bought from Dairy 6 times" in milk
    assert "Bought from Dairy 6 times" not in water


def test_there_is_one_entry_per_recommendation_in_the_order_given(
    app, monkeypatch
) -> None:
    body = _body(_open(app, monkeypatch)[0])

    assert list(_cards(body)) == ["4", "1"]


def test_a_product_links_to_its_page(app, monkeypatch) -> None:
    body = _body(_open(app, monkeypatch)[0])

    assert 'href="/catalog/products/4"' in body


def test_a_reason_says_which_kind_of_signal_it_is(app, monkeypatch) -> None:
    body = _body(_open(app, monkeypatch)[0])

    assert (
        "Segment" in body
        and "Preferred category" in body
        and "Purchase history" in body
    )


def test_the_segment_the_store_and_the_window_the_list_is_based_on_are_stated(
    app, monkeypatch
) -> None:
    body = _body(_open(app, monkeypatch)[0])

    assert "Lost" in body and "Store 8" in body
    assert "2026-04-01" in body and "2026-09-28" in body
    assert "Ada Lovelace" in body


def test_the_stock_is_in_units_and_a_single_unit_is_singular(app, monkeypatch) -> None:
    one = _result(recommendations=(_recommendation(4, "Sparkling water", stock=1),))

    body = _body(_open(app, monkeypatch, one)[0])

    assert "1 unit" in body and "1 units" not in body


# ---------- stock that reaches zero ----------


def test_the_list_is_computed_afresh_on_every_request(app, monkeypatch) -> None:
    """Nothing is kept between requests, so a product whose stock reaches zero is
    gone the next time the page is loaded."""
    before = _result()
    after = _result(recommendations=(_recommendation(1, "Whole milk", stock=19),))
    fake = Mock(side_effect=[before, after])
    monkeypatch.setattr("web.routes.catalog.recommend", fake)
    client = app.test_client()
    _sign_in(client)

    first = _body(client.get(_URL))
    second = _body(client.get(_URL))

    assert "Sparkling water" in first
    assert "Sparkling water" not in second and "Whole milk" in second
    assert fake.call_count == 2


def test_the_response_is_not_cacheable_by_a_shared_cache(app, monkeypatch) -> None:
    response, _ = _open(app, monkeypatch)

    assert "no-store" in response.headers.get("Cache-Control", "") or "private" in (
        response.headers.get("Cache-Control", "")
    )


# ---------- nothing to recommend: it says so ----------


def test_a_customer_with_no_eligible_products_is_told_so(app, monkeypatch) -> None:
    none = _result(
        status=Status.NONE_MATCH,
        message=(
            "Nothing in stock at Store 8 matches this customer: no product there..."
        ),
        recommendations=(),
    )

    response, _ = _open(app, monkeypatch, none)
    body = _body(response)

    assert response.status_code == 200
    assert "No eligible products" in body
    assert "Nothing in stock at Store 8 matches this customer" in body
    assert 'data-recommendation="' not in body and "<table" not in body


def test_a_customer_with_no_segment_is_told_so(app, monkeypatch) -> None:
    none = _result(
        status=Status.NO_SEGMENT,
        message=(
            "No segment is available for this customer: no segmentation run has "
            "assigned them one, so no recommendations are given."
        ),
        label_code=None,
        label_name=None,
        store_id=None,
        store_name=None,
        recommendations=(),
    )

    body = _body(_open(app, monkeypatch, none)[0])

    assert "No segment available" in body
    assert "no segmentation run has assigned them one" in body
    assert 'data-recommendation="' not in body


def test_a_customer_with_no_usual_store_is_told_so(app, monkeypatch) -> None:
    none = _result(
        status=Status.NO_USUAL_STORE,
        message=(
            "No usual store is available: the customer has no accepted sale in the "
            "last 180 days, so stock cannot be checked and no recommendations "
            "are given."
        ),
        store_id=None,
        store_name=None,
        recommendations=(),
    )

    body = _body(_open(app, monkeypatch, none)[0])

    assert "No usual store" in body
    assert "stock cannot be checked" in body
    assert 'data-recommendation="' not in body


# ---------- the window ----------


def test_the_default_window_is_the_profiles(app, monkeypatch) -> None:
    _, fake = _open(app, monkeypatch)

    assert fake.call_args.kwargs["window_days"] == 180


def test_a_chosen_window_reaches_the_computation(app, monkeypatch) -> None:
    _, fake = _open(app, monkeypatch, url=_URL + "?window=30")

    assert fake.call_args.kwargs["window_days"] == 30


@pytest.mark.parametrize("raw", ["abc", "0", "-5", "3651", "", "1.5"])
def test_a_window_the_segment_run_would_refuse_is_a_400_that_explains_itself(
    app, monkeypatch, raw
) -> None:
    monkeypatch.setattr(
        "web.routes.catalog.get_customer",
        lambda _c, _id: Customer(
            _ID, None, "Ada Lovelace", None, None, 1, date(2026, 1, 15)
        ),
    )

    response, fake = _open(app, monkeypatch, url=_URL + f"?window={raw}")

    assert response.status_code == 400
    assert "3650" in _body(response)
    fake.assert_not_called()


# ---------- a customer who does not exist ----------


def test_an_unknown_customer_is_a_404(app, monkeypatch) -> None:
    response, _ = _open(app, monkeypatch, side_effect=UnknownCustomer)

    assert response.status_code == 404


def test_a_bad_window_for_an_unknown_customer_is_a_404_not_a_400(
    app, monkeypatch
) -> None:
    monkeypatch.setattr("web.routes.catalog.get_customer", lambda _c, _id: None)

    response, _ = _open(app, monkeypatch, url=_URL + "?window=abc")

    assert response.status_code == 404


# ---------- what the page must never do ----------


def test_the_page_never_names_a_segmentation_method_or_a_cluster(
    app, monkeypatch
) -> None:
    body = _body(_open(app, monkeypatch)[0]).lower()

    for word in ("rfm_rules", "kmeans", "k-means", "cluster"):
        assert word not in body


def test_names_are_escaped(app, monkeypatch) -> None:
    hostile = _result(
        customer_name="<script>alert(1)</script>",
        recommendations=(_recommendation(4, "<b>Bold</b> product"),),
    )

    body = _body(_open(app, monkeypatch, hostile)[0])

    assert "<script>alert(1)</script>" not in body and "&lt;script&gt;" in body
    assert "<b>Bold</b>" not in body


# ---------- reaching it ----------


def test_the_customer_page_links_to_the_recommendations(app, monkeypatch) -> None:
    monkeypatch.setattr(
        "web.routes.catalog.get_customer",
        lambda _c, _id: Customer(
            _ID, None, "Ada Lovelace", None, None, 1, date(2026, 1, 15)
        ),
    )
    monkeypatch.setattr("web.routes.catalog.get_channel", lambda _c, _id: None)
    monkeypatch.setattr(
        "web.routes.catalog.get_current_assignment", lambda _c, _id: None
    )
    monkeypatch.setattr(
        "web.routes.catalog.list_interest_categories", lambda _c, _id: []
    )
    monkeypatch.setattr(
        "web.routes.catalog.list_preferred_channels", lambda _c, _id: []
    )
    client = app.test_client()
    _sign_in(client)

    body = _body(client.get(f"/catalog/customers/{_ID}"))

    assert f'href="{_URL}"' in body
