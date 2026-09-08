"""The consultation module (#65, F3-05).

A signed-in staff user browses the catalog, customers, stock and segments:
list, search, open. The database is replaced with a mock; what these tests
cover is the application's side — who may reach each surface, that a search
reaches the query as a parameter, that a detail page shows what the story
asks for, and that an administrator-only route still refuses a regular user.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from unittest.mock import MagicMock, Mock

import pytest
from flask import Flask
from flask.testing import FlaskClient

from web.app import create_app
from web.config import Config
from web.db.categories import Category
from web.db.channels import Channel
from web.db.customers import Customer
from web.db.inventory import LOW_STOCK_THRESHOLD, StockRow
from web.db.products import Product
from web.db.segments import Segment, SegmentRule


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


def _sign_in(client: FlaskClient, role_code: str) -> None:
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = "11111111-1111-1111-1111-000000000001"
        flask_session["role_code"] = role_code
        flask_session["name"] = f"{role_code.title()} user"


def _product(product_id: int = 1, *, image_path: str | None = None) -> Product:
    return Product(
        product_id=product_id,
        sku=f"SKU-{product_id}",
        name=f"Product {product_id}",
        category_id=3,
        list_price="19.99",
        image_path=image_path,
        is_active=True,
    )


def _customer(name: str = "Ada Lovelace", *, segment_id: int | None = 4) -> Customer:
    return Customer(
        customer_id="00000000-0000-0000-0000-000000000001",
        user_id=None,
        name=name,
        email="ada@example.test",
        phone="5500000001",
        registration_channel_id=2,
        current_segment_id=segment_id,
        registered_on=date(2026, 1, 15),
    )


def _segment(segment_id: int = 4) -> Segment:
    return Segment(
        segment_id=segment_id,
        name=f"Segment {segment_id}",
        description="High value",
        rule_id=segment_id,
        valid_from=date(2026, 1, 1),
        valid_to=None,
    )


def _stock_row(quantity: int, *, store_id: int = 1) -> StockRow:
    return StockRow(
        store_id=store_id,
        store_name=f"Store {store_id}",
        product_id=7,
        sku="SKU-7",
        product_name="Product 7",
        quantity_on_hand=quantity,
        updated_at=datetime(2026, 9, 1, 8, 0),
    )


# ---------- who may reach each surface ----------


@pytest.mark.parametrize(
    "path",
    [
        "/catalog/",
        "/catalog/products",
        "/catalog/customers",
        "/catalog/stock",
        "/catalog/segments",
    ],
)
def test_the_customer_role_is_refused_everywhere(app: Flask, path: str) -> None:
    client = app.test_client()
    _sign_in(client, "CUSTOMER")
    assert client.get(path).status_code == 403


def test_an_analyst_reaches_every_section(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("web.routes.catalog.list_products", lambda *a, **k: ([], 0))
    monkeypatch.setattr("web.routes.catalog.list_customers", lambda *a, **k: ([], 0))
    monkeypatch.setattr("web.routes.catalog.list_stock", lambda *a, **k: ([], 0))
    monkeypatch.setattr("web.routes.catalog.list_all_stores", lambda *a, **k: [])
    monkeypatch.setattr("web.routes.catalog.list_segments", lambda *a, **k: ([], 0))
    client = app.test_client()
    _sign_in(client, "ANALYST")

    for path in (
        "/catalog/",
        "/catalog/products",
        "/catalog/customers",
        "/catalog/stock",
        "/catalog/segments",
    ):
        assert client.get(path).status_code == 200, path


def test_stock_is_catalog_read_but_customers_are_not(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An inventory planner sees stock; the customer directory is segment.read."""
    monkeypatch.setattr("web.routes.catalog.list_stock", lambda *a, **k: ([], 0))
    monkeypatch.setattr("web.routes.catalog.list_all_stores", lambda *a, **k: [])
    client = app.test_client()
    _sign_in(client, "INVENTORY_PLANNER")

    assert client.get("/catalog/stock").status_code == 200
    assert client.get("/catalog/customers").status_code == 403
    assert client.get("/catalog/segments").status_code == 403


def test_an_administrator_only_route_still_refuses_a_regular_user(app: Flask) -> None:
    """Acceptance criterion 3: the CRUD screen is catalog.write, ADMIN only."""
    client = app.test_client()
    _sign_in(client, "ANALYST")

    assert client.get("/admin/products/1/edit").status_code == 403
    assert client.post("/admin/products/1/edit").status_code == 403


# ---------- products ----------


def test_products_list_shows_names_and_a_search_reaches_the_query(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict = {}

    def capture(_connection, **kwargs):
        seen.update(kwargs)
        return [_product(1), _product(2)], 2

    monkeypatch.setattr("web.routes.catalog.list_products", capture)
    client = app.test_client()
    _sign_in(client, "ANALYST")

    body = client.get("/catalog/products?q=widget").get_data(as_text=True)

    assert "Product 1" in body and "Product 2" in body
    assert seen["search"] == "widget"
    assert 'href="/catalog/products/1"' in body


def test_products_list_tells_you_when_nothing_matches(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("web.routes.catalog.list_products", lambda *a, **k: ([], 0))
    client = app.test_client()
    _sign_in(client, "ANALYST")

    body = client.get("/catalog/products?q=nothing").get_data(as_text=True)

    assert "No products match that search." in body


def test_products_pager_keeps_the_search_applied(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "web.routes.catalog.list_products",
        lambda *a, **k: ([_product(1)], 60),
    )
    client = app.test_client()
    _sign_in(client, "ANALYST")

    body = client.get("/catalog/products?q=w&page=2").get_data(as_text=True)

    assert "q=w" in body and "page=1" in body and "page=3" in body


def test_product_detail_shows_the_category_name_and_the_image(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "web.routes.catalog.get_product",
        lambda _c, _id: _product(1, image_path="abc.png"),
    )
    monkeypatch.setattr(
        "web.routes.catalog.get_category",
        lambda _c, _id: Category(3, "Footwear", None),
    )
    client = app.test_client()
    _sign_in(client, "ANALYST")

    body = client.get("/catalog/products/1").get_data(as_text=True)

    assert "Footwear" in body
    assert 'src="/admin/products/image/abc.png"' in body


def test_product_detail_without_an_image_shows_a_placeholder(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "web.routes.catalog.get_product", lambda _c, _id: _product(1, image_path=None)
    )
    monkeypatch.setattr("web.routes.catalog.get_category", lambda _c, _id: None)
    client = app.test_client()
    _sign_in(client, "ANALYST")

    body = client.get("/catalog/products/1").get_data(as_text=True)

    assert "No image" in body
    assert "<img" not in body


def test_product_detail_is_a_404_when_the_product_is_unknown(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("web.routes.catalog.get_product", lambda _c, _id: None)
    client = app.test_client()
    _sign_in(client, "ANALYST")

    assert client.get("/catalog/products/999999").status_code == 404


def test_product_detail_offers_an_edit_link_only_to_a_writer(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("web.routes.catalog.get_product", lambda _c, _id: _product(1))
    monkeypatch.setattr("web.routes.catalog.get_category", lambda _c, _id: None)

    reader = app.test_client()
    _sign_in(reader, "ANALYST")
    assert "/admin/products/1/edit" not in reader.get("/catalog/products/1").get_data(
        as_text=True
    )

    writer = app.test_client()
    _sign_in(writer, "ADMIN")
    assert "/admin/products/1/edit" in writer.get("/catalog/products/1").get_data(
        as_text=True
    )


# ---------- customers ----------


def test_customer_detail_shows_interests_channels_and_the_segment(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("web.routes.catalog.get_customer", lambda _c, _id: _customer())
    monkeypatch.setattr(
        "web.routes.catalog.get_channel", lambda _c, _id: Channel(2, "Web")
    )
    monkeypatch.setattr("web.routes.catalog.get_segment", lambda _c, _id: _segment(4))
    monkeypatch.setattr(
        "web.routes.catalog.list_interest_categories",
        lambda _c, _id: [Category(1, "Running", None)],
    )
    monkeypatch.setattr(
        "web.routes.catalog.list_preferred_channels",
        lambda _c, _id: [Channel(5, "Email")],
    )
    client = app.test_client()
    _sign_in(client, "ANALYST")

    body = client.get(
        "/catalog/customers/00000000-0000-0000-0000-000000000001"
    ).get_data(as_text=True)

    assert "Running" in body
    assert "Email" in body
    assert "Web" in body
    assert 'href="/catalog/segments/4"' in body


def test_customer_detail_is_a_404_when_unknown(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("web.routes.catalog.get_customer", lambda _c, _id: None)
    client = app.test_client()
    _sign_in(client, "ANALYST")

    assert (
        client.get(
            "/catalog/customers/00000000-0000-0000-0000-0000000000ff"
        ).status_code
        == 404
    )


def test_customers_list_reports_an_empty_search(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("web.routes.catalog.list_customers", lambda *a, **k: ([], 0))
    client = app.test_client()
    _sign_in(client, "ANALYST")

    body = client.get("/catalog/customers?q=none").get_data(as_text=True)

    assert "No customers match that search." in body


# ---------- stock ----------


def test_stock_flags_a_row_below_the_threshold(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "web.routes.catalog.list_stock",
        lambda *a, **k: (
            [_stock_row(LOW_STOCK_THRESHOLD - 1), _stock_row(LOW_STOCK_THRESHOLD + 50)],
            2,
        ),
    )
    monkeypatch.setattr("web.routes.catalog.list_all_stores", lambda *a, **k: [])
    client = app.test_client()
    _sign_in(client, "ANALYST")

    body = client.get("/catalog/stock").get_data(as_text=True)

    assert 'class="mq-table__row--warning"' in body
    assert body.count("mq-table__row--warning") >= 1


def test_stock_store_filter_reaches_the_query(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict = {}

    def capture(_connection, **kwargs):
        seen.update(kwargs)
        return [], 0

    monkeypatch.setattr("web.routes.catalog.list_stock", capture)
    monkeypatch.setattr("web.routes.catalog.list_all_stores", lambda *a, **k: [])
    client = app.test_client()
    _sign_in(client, "ANALYST")

    client.get("/catalog/stock?store=6&q=shoe")

    assert seen["store_id"] == 6
    assert seen["search"] == "shoe"


def test_stock_reports_an_empty_result(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("web.routes.catalog.list_stock", lambda *a, **k: ([], 0))
    monkeypatch.setattr("web.routes.catalog.list_all_stores", lambda *a, **k: [])
    client = app.test_client()
    _sign_in(client, "ANALYST")

    body = client.get("/catalog/stock?store=99").get_data(as_text=True)

    assert "No stock matches those filters." in body


# ---------- segments ----------


def test_segment_detail_lists_members_and_the_rule_bands(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("web.routes.catalog.get_segment", lambda _c, _id: _segment(4))
    monkeypatch.setattr(
        "web.routes.catalog.get_segment_rule",
        lambda _c, _id: SegmentRule(4, "RULE_004", 4, 5, 3, 5, 2, 5),
    )
    monkeypatch.setattr(
        "web.routes.catalog.list_customers_in_segment",
        lambda *a, **k: ([_customer("Grace Hopper")], 1),
    )
    client = app.test_client()
    _sign_in(client, "ANALYST")

    body = client.get("/catalog/segments/4").get_data(as_text=True)

    assert "Grace Hopper" in body
    assert "RULE_004" in body
    assert re.search(r"4[–-]5", body)


def test_segment_detail_reports_an_empty_segment(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("web.routes.catalog.get_segment", lambda _c, _id: _segment(4))
    monkeypatch.setattr("web.routes.catalog.get_segment_rule", lambda _c, _id: None)
    monkeypatch.setattr(
        "web.routes.catalog.list_customers_in_segment", lambda *a, **k: ([], 0)
    )
    client = app.test_client()
    _sign_in(client, "ANALYST")

    body = client.get("/catalog/segments/4").get_data(as_text=True)

    assert "No customers are currently in this segment." in body


def test_segment_detail_is_a_404_when_unknown(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("web.routes.catalog.get_segment", lambda _c, _id: None)
    client = app.test_client()
    _sign_in(client, "ANALYST")

    assert client.get("/catalog/segments/999999").status_code == 404


# ---------- the hub and the navigation ----------


def test_the_hub_hides_customer_and_segment_links_from_a_catalog_only_role(
    app: Flask,
) -> None:
    reader = app.test_client()
    _sign_in(reader, "STORE_MANAGER")
    body = reader.get("/catalog/").get_data(as_text=True)

    assert "/catalog/products" in body
    assert "/catalog/stock" in body
    assert "/catalog/customers" not in body
    assert "/catalog/segments" not in body
    assert "/admin/catalogs" not in body


def test_the_hub_offers_management_only_to_a_writer(app: Flask) -> None:
    admin = app.test_client()
    _sign_in(admin, "ADMIN")
    body = admin.get("/catalog/").get_data(as_text=True)

    assert "/catalog/customers" in body
    assert "/admin/catalogs" in body


def test_the_catalogs_menu_entry_points_at_the_consultation_hub(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client, "ANALYST")
    body = client.get("/catalog/products").get_data(as_text=True)

    nav = body[body.index('id="primary-navigation"') : body.index("</nav>")]
    assert 'href="/catalog/"' in nav
    assert 'aria-current="page"' in nav


@pytest.mark.parametrize(
    "resource,query",
    [
        ("products", "q=milk"),
        ("customers", "q=Alice"),
        ("stock", "store=2&q=milk"),
        ("segments", "q=loyal"),
    ],
)
def test_out_of_range_pages_return_to_last_page_with_filters(
    app, monkeypatch, resource, query
):
    from urllib.parse import parse_qs, urlsplit

    monkeypatch.setattr(f"web.routes.catalog.list_{resource}", lambda *a, **k: ([], 30))
    client = app.test_client()
    _sign_in(client, "ADMIN")
    response = client.get(f"/catalog/{resource}?{query}&page=999")
    assert response.status_code == 302
    target = urlsplit(response.location)
    assert target.path == f"/catalog/{resource}"
    assert parse_qs(target.query) == parse_qs(query) | {"page": ["2"]}


# ---------- counts read as English ----------


@pytest.mark.parametrize(
    "total,expected",
    [(1, "1 product"), (2, "2 products"), (0, "0 products")],
)
def test_the_result_count_agrees_with_the_number_it_reports(
    app: Flask, monkeypatch: pytest.MonkeyPatch, total: int, expected: str
) -> None:
    """A single match reads "1 product", not "1 products"."""
    rows = [_product(number) for number in range(1, total + 1)]
    monkeypatch.setattr(
        "web.routes.catalog.list_products", lambda *a, **k: (rows, total)
    )
    client = app.test_client()
    _sign_in(client, "ANALYST")

    body = client.get("/catalog/products").get_data(as_text=True)

    if total:
        assert expected in body
        assert "1 products" not in body
