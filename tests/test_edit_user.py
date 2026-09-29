"""Editing an existing user's name, email and role (RF-09, #289)."""

from __future__ import annotations

from unittest.mock import MagicMock, Mock

import pytest
from flask import Flask
from psycopg.errors import UniqueViolation

from web.app import create_app
from web.config import Config
from web.services.users import (
    DuplicateEmailError,
    SingleAdministratorError,
    update_user,
)


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


def _sign_in(client, role_code: str) -> None:
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = "11111111-1111-1111-1111-000000000001"
        flask_session["role_code"] = role_code
        flask_session["name"] = f"{role_code.title()} user"


@pytest.mark.parametrize("role_code", ["ANALYST", "STORE_MANAGER", "CUSTOMER"])
def test_only_admin_reaches_the_edit_route(app: Flask, role_code: str) -> None:
    client = app.test_client()
    _sign_in(client, role_code)

    assert (
        client.get("/admin/users/11111111-1111-1111-1111-000000000002/edit").status_code
        == 403
    )


def test_an_unknown_user_is_a_404(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("web.routes.admin.get_user_by_id", lambda *a, **k: None)
    client = app.test_client()
    _sign_in(client, "ADMIN")

    response = client.get("/admin/users/11111111-1111-1111-1111-000000000002/edit")
    assert response.status_code == 404


def test_update_user_writes_name_email_then_moves_the_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    monkeypatch.setattr(
        "web.services.users.update_name_and_email",
        lambda _c, _uid, **kw: calls.append(("name_email", kw)),
    )
    monkeypatch.setattr(
        "web.services.users.change_role",
        lambda _c, _uid, role_code: calls.append(("role", role_code)),
    )

    update_user(
        MagicMock(),
        "u1",
        name="  Jane Doe  ",
        email="  jane@example.com  ",
        role_code="ANALYST",
    )

    assert calls == [
        ("name_email", {"name": "Jane Doe", "email": "jane@example.com"}),
        ("role", "ANALYST"),
    ]


def test_promoting_to_a_second_administrator_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "web.services.users.update_name_and_email", lambda *a, **k: None
    )

    def _refuse(*_a, **_k):
        raise SingleAdministratorError("second admin")

    monkeypatch.setattr("web.services.users.change_role", _refuse)

    with pytest.raises(SingleAdministratorError):
        update_user(
            MagicMock(), "u1", name="X", email="x@example.com", role_code="ADMIN"
        )


def test_update_user_stores_the_normalised_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    written = {}
    monkeypatch.setattr(
        "web.services.users.update_name_and_email",
        lambda _c, _uid, **kw: written.update(kw),
    )
    monkeypatch.setattr("web.services.users.change_role", lambda *a, **k: None)

    update_user(
        MagicMock(), "u1", name="Jane", email=" Jane@Example.COM ", role_code="ANALYST"
    )

    assert written["email"] == "jane@example.com"


def _clash(*_a, **_k):
    raise UniqueViolation("duplicate key value violates ux_app_user_email_lower")


def test_an_address_another_account_holds_is_a_duplicate_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("web.services.users.update_name_and_email", _clash)

    with pytest.raises(DuplicateEmailError):
        update_user(
            MagicMock(), "u1", name="X", email="taken@example.com", role_code="ANALYST"
        )


def test_the_edit_form_explains_a_duplicate_email_instead_of_failing(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("web.routes.admin.get_user_by_id", lambda *a, **k: Mock())
    monkeypatch.setattr(
        "web.routes.admin.list_role_options", lambda *a, **k: [("ANALYST", None)]
    )
    monkeypatch.setattr("web.services.users.update_name_and_email", _clash)
    client = app.test_client()
    _sign_in(client, "ADMIN")

    response = client.post(
        "/admin/users/11111111-1111-1111-1111-000000000002/edit",
        data={"name": "X", "email": "TAKEN@example.com", "role_code": "ANALYST"},
    )

    assert response.status_code == 409
    assert b"A user with that email already exists." in response.data
