"""Regressions reported by the F5-03 review (#157–#165)."""

from dataclasses import replace
from decimal import Decimal
from io import BytesIO
from unittest.mock import MagicMock, Mock

import pytest
from argon2.exceptions import InvalidHashError, VerificationError
from psycopg.errors import ForeignKeyViolation, UniqueViolation

from web.app import create_app
from web.config import Config
from web.db.categories import Category
from web.routes import admin
from web.services import auth


@pytest.fixture
def connection():
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value.fetchall.return_value = []
    return connection


@pytest.fixture
def app(connection, tmp_path):
    return create_app(
        Config.from_env({"DATABASE_URL": "unused", "UPLOAD_DIR": str(tmp_path)}),
        database_connector=Mock(return_value=connection),
    )


@pytest.fixture
def client(app, monkeypatch):
    monkeypatch.setattr(
        admin, "get_category", Mock(return_value=Category(2, "Old", None))
    )
    client = app.test_client()
    with client.session_transaction() as session:
        session.update(user_id="u-1", role_code="ADMIN", name="Admin")
    return client


@pytest.mark.parametrize("path", ["new", "2/edit"])
@pytest.mark.parametrize("parent", ["x", "1.5", "-1", "32768", "9" * 100, "²"])
def test_malformed_category_parent_is_a_field_error(client, connection, path, parent):
    response = client.post(
        f"/admin/categories/{path}",
        data=dict(category_id="99", name="Shoes", parent_category_id=parent),
    )
    assert response.status_code == 400
    assert b"Parent category must be a whole number" in response.data
    connection.commit.assert_not_called()


@pytest.mark.parametrize("path", ["new", "2/edit"])
@pytest.mark.parametrize(
    "error,message",
    [(UniqueViolation, b"already exists"), (ForeignKeyViolation, b"does not exist")],
)
def test_category_constraint_refusal_is_controlled(
    client, connection, path, error, message
):
    def execute(statement, parameters=None):
        if statement.lstrip().startswith(("INSERT", "UPDATE")):
            raise error("private database detail")

    connection.cursor.return_value.__enter__.return_value.execute.side_effect = execute
    response = client.post(
        f"/admin/categories/{path}",
        data=dict(category_id="99", name="Shoes", parent_category_id="3"),
    )
    assert response.status_code == 409
    assert message in response.data
    assert b"private database detail" not in response.data
    connection.rollback.assert_called_once()
    connection.commit.assert_not_called()


@pytest.mark.parametrize("path", ["new", "1/edit"])
@pytest.mark.parametrize(
    "price", ["NaN", "sNaN", "Infinity", "-Infinity", "1e1000", "99999999.995"]
)
def test_invalid_money_never_reaches_a_write(
    client, connection, monkeypatch, path, price
):
    monkeypatch.setattr(admin, "get_product", Mock(return_value=Mock()))
    response = client.post(
        f"/admin/products/{path}",
        data=dict(
            product_id="99", sku="TEST", name="Test", category_id="1", list_price=price
        ),
    )
    assert response.status_code == 400
    assert b"List price" in response.data
    connection.commit.assert_not_called()


@pytest.mark.parametrize(
    "raw,expected", [("0.145", "0.15"), ("99999999.994", "99999999.99"), ("0", "0.00")]
)
def test_price_passes_the_validated_decimal_to_the_writer(
    client, monkeypatch, raw, expected
):
    write = Mock(return_value=None)
    monkeypatch.setattr(admin, "create_product", write)
    response = client.post(
        "/admin/products/new",
        data=dict(
            product_id="99", sku="TEST", name="Test", category_id="1", list_price=raw
        ),
    )
    assert response.status_code == 302
    assert write.call_args.kwargs["list_price"] == Decimal(expected)


@pytest.mark.parametrize("error", [InvalidHashError, VerificationError])
def test_unusable_hash_is_a_generic_login_refusal(app, monkeypatch, caplog, error):
    monkeypatch.setattr(
        auth, "get_user_by_email", Mock(return_value=Mock(is_active=True))
    )
    monkeypatch.setattr(
        auth, "_hasher", Mock(verify=Mock(side_effect=error("secret hash detail")))
    )
    response = app.test_client().post(
        "/login", data=dict(email="test@example.com", password="private password")
    )
    assert response.status_code == 401
    assert b"Invalid email or password." in response.data
    assert "unusable_hash" in caplog.text
    assert "secret hash detail" not in caplog.text
    assert "private password" not in caplog.text


@pytest.mark.parametrize("user", [None, Mock(is_active=False)])
def test_missing_or_inactive_user_still_pays_for_password_verification(
    monkeypatch, user
):
    monkeypatch.setattr(auth, "get_user_by_email", Mock(return_value=user))
    verify = Mock(return_value=True)
    monkeypatch.setattr(auth, "_hasher", Mock(verify=verify))
    assert not auth.authenticate(MagicMock(), "missing@example.com", "password").success
    verify.assert_called_once()


@pytest.mark.parametrize("raw", ["eighty", "", "0", "-1", "65536"])
def test_bad_port_uses_the_documented_default(raw):
    assert Config.from_env({"DATABASE_URL": "unused", "PORT": raw}).port == 5000


def test_oversized_request_is_refused_before_form_buffering(app, connection):
    config = replace(app.config["APP_CONFIG"], max_upload_bytes=10)
    bounded = create_app(config, database_connector=Mock(return_value=connection))
    client = bounded.test_client()
    response = client.post(
        "/login", data={"image": (BytesIO(b"x" * 100_000), "large.png")}
    )
    assert response.status_code == 413
    assert b"Reference" in response.data
    connection.commit.assert_not_called()


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
def test_missing_edit_records_use_the_shared_404(client, monkeypatch, entity, plural):
    monkeypatch.setattr(admin, f"get_{entity}", Mock(return_value=None))
    response = client.get(f"/admin/{plural}/999/edit")
    assert response.status_code == 404
    assert b"Reference" in response.data


def test_missing_user_activation_uses_the_shared_404(client, monkeypatch):
    from web.services.users import UnknownUserError

    monkeypatch.setattr(admin, "set_active", Mock(side_effect=UnknownUserError()))
    response = client.post("/admin/users/00000000-0000-0000-0000-000000000099/activate")
    assert response.status_code == 404
    assert b"Reference" in response.data


def test_real_malformed_argon2_hash_returns_the_generic_401(app, monkeypatch):
    monkeypatch.setattr(
        auth,
        "get_user_by_email",
        Mock(return_value=Mock(is_active=True, password_hash="not-an-argon2-hash")),
    )
    client = app.test_client()
    response = client.post(
        "/login", data=dict(email="user@example.com", password="password")
    )
    assert response.status_code == 401
    assert b"Invalid email or password." in response.data
    with client.session_transaction() as session:
        assert "user_id" not in session


def test_request_limit_is_checked_without_reading_the_body(app):
    class UnreadableBody(BytesIO):
        def read(self, *args):
            raise AssertionError("oversized request body was read")

        def readinto(self, *args):
            raise AssertionError("oversized request body was read")

    response = app.test_client().post(
        "/login",
        environ_overrides={
            "wsgi.input": UnreadableBody(),
            "CONTENT_LENGTH": str(app.config["MAX_CONTENT_LENGTH"] + 1),
            "CONTENT_TYPE": "application/x-www-form-urlencoded",
        },
    )
    assert response.status_code == 413


# A key the column cannot hold reaches SQL as NumericValueOutOfRange, which is
# not a constraint refusal: every catalog form must mark the field instead.
@pytest.mark.parametrize(
    "path,field,data",
    [
        ("stores/new", "store_id", dict(name="S", city="C", state="ST")),
        ("categories/new", "category_id", dict(name="C", parent_category_id="")),
        ("channels/new", "channel_id", dict(name="C")),
        ("roles/new", "role_id", dict(code="CODE", description="D")),
        (
            "products/new",
            "product_id",
            dict(sku="SKU", name="P", category_id="1", list_price="1.00"),
        ),
    ],
)
@pytest.mark.parametrize("key", ["-1", "1.5", "99999999999", "x", ""])
def test_unusable_surrogate_key_is_a_field_error(
    client, connection, path, field, data, key
):
    response = client.post(f"/admin/{path}", data={**data, field: key})
    assert response.status_code == 400
    assert b"must be a whole number between 0 and" in response.data
    connection.commit.assert_not_called()


@pytest.mark.parametrize("path", ["new", "1/edit"])
@pytest.mark.parametrize("category", ["32768", "99999999999"])
def test_out_of_range_product_category_is_a_field_error(
    client, connection, monkeypatch, path, category
):
    monkeypatch.setattr(admin, "get_product", Mock(return_value=Mock()))
    response = client.post(
        f"/admin/products/{path}",
        data=dict(
            product_id="99",
            sku="SKU",
            name="P",
            category_id=category,
            list_price="1.00",
        ),
    )
    assert response.status_code == 400
    assert b"Category must be a whole number between 0 and 32767." in response.data
    connection.commit.assert_not_called()
