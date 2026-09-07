"""Functional tests over the administrator CRUD modules: the happy path.

F5-02 (tests/test_negative_flows.py) already covers the refused path — empty
or invalid fields, unauthorized roles. F3-05 (tests/test_catalog.py) already
covers read-only consultation. What was missing, and what F5-01 asks for, is
that a valid create/edit/delete actually reaches the database layer with the
right arguments and redirects to the listing — for each of the five catalogs
CATALOG_WRITE protects: stores, categories, channels, products and roles.

User management is not repeated here: its create/edit/deactivate paths are
already exercised at length by tests/test_single_administrator.py.
"""

from __future__ import annotations

from unittest.mock import ANY, MagicMock, Mock

import pytest
from flask.testing import FlaskClient

from web.app import create_app
from web.config import Config
from web.routes import admin


@pytest.fixture
def config(tmp_path) -> Config:
    return Config(
        secret_key="test",
        session_cookie_secure=False,
        environment="testing",
        port=5000,
        log_level="INFO",
        database_url="unused",
        upload_dir=str(tmp_path),
    )


@pytest.fixture
def client(config: Config) -> FlaskClient:
    app = create_app(config, database_connector=Mock(return_value=MagicMock()))
    client = app.test_client()
    with client.session_transaction() as session:
        session.update(user_id="u-1", role_code="ADMIN", name="Admin")
    return client


# ---------- stores ----------


def test_the_store_listing_is_reachable(client: FlaskClient, monkeypatch) -> None:
    monkeypatch.setattr(admin, "list_stores", Mock(return_value=([], 0)))
    response = client.get("/admin/stores")
    assert response.status_code == 200


def test_a_valid_store_is_created_and_redirects_to_the_list(
    client: FlaskClient, monkeypatch
) -> None:
    create = Mock(return_value=None)
    monkeypatch.setattr(admin, "create_store", create)
    response = client.post(
        "/admin/stores/new",
        data={
            "store_id": "31",
            "name": "New Store",
            "city": "Monterrey",
            "state": "NL",
        },
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/stores")
    create.assert_called_once()
    assert create.call_args.kwargs == {
        "store_id": 31,
        "name": "New Store",
        "city": "Monterrey",
        "state": "NL",
    }


def test_a_valid_store_edit_is_saved_and_redirects_to_the_list(
    client: FlaskClient, monkeypatch
) -> None:
    monkeypatch.setattr(admin, "get_store", Mock(return_value=Mock()))
    update = Mock(return_value=None)
    monkeypatch.setattr(admin, "update_store", update)
    response = client.post(
        "/admin/stores/1/edit",
        data={"name": "Renamed", "city": "Guadalupe", "state": "NL", "is_active": "on"},
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/stores")
    update.assert_called_once_with(
        ANY, 1, name="Renamed", city="Guadalupe", state="NL", is_active=True
    )


def test_a_store_with_nothing_referencing_it_is_deleted(
    client: FlaskClient, monkeypatch
) -> None:
    delete = Mock(return_value=True)
    monkeypatch.setattr(admin, "delete_store", delete)
    monkeypatch.setattr(admin, "get_store", Mock(return_value=Mock(image_path=None)))
    response = client.post("/admin/stores/1/delete", data={"confirm": "yes"})
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/stores")
    delete.assert_called_once_with(ANY, 1)


# ---------- categories ----------


def test_the_category_listing_is_reachable(client: FlaskClient, monkeypatch) -> None:
    monkeypatch.setattr(admin, "list_categories", Mock(return_value=([], 0)))
    response = client.get("/admin/categories")
    assert response.status_code == 200


def test_a_valid_category_is_created_and_redirects_to_the_list(
    client: FlaskClient, monkeypatch
) -> None:
    monkeypatch.setattr(admin, "list_all_categories", Mock(return_value=[]))
    create = Mock(return_value=None)
    monkeypatch.setattr(admin, "create_category", create)
    response = client.post(
        "/admin/categories/new",
        data={"category_id": "31", "name": "Bebidas", "parent_category_id": ""},
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/categories")
    create.assert_called_once()
    assert create.call_args.kwargs == {
        "category_id": 31,
        "name": "Bebidas",
        "parent_category_id": None,
    }


def test_a_valid_category_edit_is_saved_and_redirects_to_the_list(
    client: FlaskClient, monkeypatch
) -> None:
    fake_category = Mock(category_id=1)
    monkeypatch.setattr(admin, "get_category", Mock(return_value=fake_category))
    monkeypatch.setattr(admin, "list_all_categories", Mock(return_value=[]))
    update = Mock(return_value=None)
    monkeypatch.setattr(admin, "update_category", update)
    response = client.post(
        "/admin/categories/1/edit", data={"name": "Renamed", "parent_category_id": "2"}
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/categories")
    update.assert_called_once_with(ANY, 1, name="Renamed", parent_category_id=2)


def test_a_category_with_nothing_referencing_it_is_deleted(
    client: FlaskClient, monkeypatch
) -> None:
    delete = Mock(return_value=True)
    monkeypatch.setattr(admin, "delete_category", delete)
    monkeypatch.setattr(admin, "get_category", Mock(return_value=Mock(image_path=None)))
    response = client.post("/admin/categories/1/delete", data={"confirm": "yes"})
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/categories")
    delete.assert_called_once_with(ANY, 1)


# ---------- channels ----------


def test_the_channel_listing_is_reachable(client: FlaskClient, monkeypatch) -> None:
    monkeypatch.setattr(admin, "list_channels", Mock(return_value=([], 0)))
    response = client.get("/admin/channels")
    assert response.status_code == 200


def test_a_valid_channel_is_created_and_redirects_to_the_list(
    client: FlaskClient, monkeypatch
) -> None:
    create = Mock(return_value=None)
    monkeypatch.setattr(admin, "create_channel", create)
    response = client.post(
        "/admin/channels/new", data={"channel_id": "31", "name": "WhatsApp"}
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/channels")
    create.assert_called_once_with(ANY, channel_id=31, name="WhatsApp")


def test_a_valid_channel_edit_is_saved_and_redirects_to_the_list(
    client: FlaskClient, monkeypatch
) -> None:
    monkeypatch.setattr(admin, "get_channel", Mock(return_value=Mock()))
    update = Mock(return_value=None)
    monkeypatch.setattr(admin, "update_channel", update)
    response = client.post("/admin/channels/1/edit", data={"name": "Renamed"})
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/channels")
    update.assert_called_once_with(ANY, 1, name="Renamed")


def test_a_channel_with_nothing_referencing_it_is_deleted(
    client: FlaskClient, monkeypatch
) -> None:
    delete = Mock(return_value=True)
    monkeypatch.setattr(admin, "delete_channel", delete)
    monkeypatch.setattr(admin, "get_channel", Mock(return_value=Mock(image_path=None)))
    response = client.post("/admin/channels/1/delete", data={"confirm": "yes"})
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/channels")
    delete.assert_called_once_with(ANY, 1)


# ---------- products (form fields only — image upload is test_uploads.py) ----------


def test_the_product_listing_is_reachable(client: FlaskClient, monkeypatch) -> None:
    monkeypatch.setattr(admin, "list_products", Mock(return_value=([], 0)))
    response = client.get("/admin/products")
    assert response.status_code == 200


def test_a_valid_product_without_an_image_is_created_and_redirects_to_the_list(
    client: FlaskClient, monkeypatch
) -> None:
    monkeypatch.setattr(admin, "list_all_categories", Mock(return_value=[]))
    create = Mock(return_value=None)
    monkeypatch.setattr(admin, "create_product", create)
    response = client.post(
        "/admin/products/new",
        data={
            "product_id": "9001",
            "sku": "SKU-F5-01",
            "name": "Evidence product",
            "category_id": "1",
            "list_price": "99.90",
        },
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/products")
    create.assert_called_once()
    assert create.call_args.kwargs["sku"] == "SKU-F5-01"
    assert create.call_args.kwargs["image_path"] is None


def test_a_product_with_nothing_referencing_it_is_deleted(
    client: FlaskClient, monkeypatch
) -> None:
    delete = Mock(return_value=True)
    monkeypatch.setattr(admin, "delete_product", delete)
    monkeypatch.setattr(admin, "get_product", Mock(return_value=Mock(image_path=None)))
    response = client.post("/admin/products/1/delete", data={"confirm": "yes"})
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/products")
    delete.assert_called_once_with(ANY, 1)


# ---------- roles ----------


def test_the_role_listing_is_reachable(client: FlaskClient, monkeypatch) -> None:
    monkeypatch.setattr(admin, "list_roles", Mock(return_value=([], 0)))
    response = client.get("/admin/roles")
    assert response.status_code == 200


def test_a_valid_role_is_created_and_redirects_to_the_list(
    client: FlaskClient, monkeypatch
) -> None:
    create = Mock(return_value=None)
    monkeypatch.setattr(admin, "create_role", create)
    response = client.post(
        "/admin/roles/new",
        data={"role_id": "31", "code": "SUPPORT", "description": "Support desk"},
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/roles")
    create.assert_called_once_with(
        ANY, role_id=31, code="SUPPORT", description="Support desk"
    )


def test_a_valid_role_edit_is_saved_and_redirects_to_the_list(
    client: FlaskClient, monkeypatch
) -> None:
    monkeypatch.setattr(admin, "get_role", Mock(return_value=Mock()))
    update = Mock(return_value=None)
    monkeypatch.setattr(admin, "update_role", update)
    response = client.post(
        "/admin/roles/1/edit", data={"code": "SUPPORT2", "description": "Renamed"}
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/roles")
    update.assert_called_once_with(ANY, 1, code="SUPPORT2", description="Renamed")


def test_a_role_with_nothing_referencing_it_is_deleted(
    client: FlaskClient, monkeypatch
) -> None:
    delete = Mock(return_value=True)
    monkeypatch.setattr(admin, "delete_role", delete)
    monkeypatch.setattr(admin, "get_role", Mock(return_value=Mock(image_path=None)))
    response = client.post("/admin/roles/1/delete", data={"confirm": "yes"})
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/roles")
    delete.assert_called_once_with(ANY, 1)


@pytest.mark.parametrize(
    "entity,plural",
    [
        ("store", "stores"),
        ("category", "categories"),
        ("channel", "channels"),
        ("product", "products"),
        ("role", "roles"),
    ],
)
def test_delete_requires_confirmation(client, monkeypatch, entity, plural):
    delete = Mock(return_value=True)
    monkeypatch.setattr(admin, f"delete_{entity}", delete)
    monkeypatch.setattr(
        admin, f"get_{entity}", Mock(return_value=Mock(image_path=None))
    )
    response = client.post(f"/admin/{plural}/1/delete")
    assert response.status_code == 200
    assert b"Confirm deletion" in response.data
    delete.assert_not_called()


@pytest.mark.parametrize("deleted", [True, False])
def test_product_image_is_removed_only_after_successful_delete(
    client, config, monkeypatch, deleted
):
    from pathlib import Path

    image_path = Path(config.upload_dir) / "product.png"
    image_path.write_bytes(b"existing image")
    monkeypatch.setattr(
        admin, "get_product", Mock(return_value=Mock(image_path=image_path.name))
    )
    monkeypatch.setattr(admin, "list_products", Mock(return_value=([], 0)))

    def delete(connection, product_id):
        assert image_path.exists(), "keep the file until deletion succeeds"
        return deleted

    monkeypatch.setattr(admin, "delete_product", delete)
    response = client.post("/admin/products/1/delete", data={"confirm": "yes"})
    assert response.status_code == (302 if deleted else 409)
    assert image_path.exists() is not deleted


@pytest.mark.parametrize(
    "entity,plural",
    [
        ("store", "stores"),
        ("category", "categories"),
        ("channel", "channels"),
        ("product", "products"),
        ("role", "roles"),
    ],
)
@pytest.mark.parametrize(
    "role",
    ["ADMIN", "ANALYST", "AUDITOR", "MARKETING", "STORE_MANAGER", "INVENTORY_PLANNER"],
)
def test_catalog_controls_follow_write_permission(
    client, monkeypatch, entity, plural, role
):
    from types import SimpleNamespace

    record = SimpleNamespace(**{f"{entity}_id": 1}, name="Record", code="SUPPORT")
    monkeypatch.setattr(admin, f"list_{plural}", Mock(return_value=([record], 1)))
    with client.session_transaction() as session:
        session["role_code"] = role
    response = client.get(f"/admin/{plural}")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    for path in [
        f"/admin/{plural}/new",
        f"/admin/{plural}/1/edit",
        f"/admin/{plural}/1/delete",
    ]:
        assert (path in body) == (role == "ADMIN")
