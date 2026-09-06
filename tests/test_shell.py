"""The signed-in shell: who I am, what I may reach, and real figures (#106).

The navigation's permission filtering is F4-01's and is tested in
tests/test_authz.py; what these assert is the shell built on top of it — the
landing page a signed-in visitor gets, the figures it may show them, and the
marking of the section they are in.
"""

from __future__ import annotations

import re
from datetime import datetime
from unittest.mock import MagicMock, Mock
from uuid import UUID

import pytest
from flask import Flask, render_template_string
from flask.testing import FlaskClient

from web.app import create_app
from web.config import Config
from web.db.audit import AuditEntry
from web.db.users import AppUser
from web.middleware.authz import requires

_USER_ID = UUID("11111111-1111-1111-1111-000000000001")


def _user(role_code: str, description: str) -> AppUser:
    return AppUser(
        user_id=_USER_ID,
        role_id=1,
        role_code=role_code,
        role_description=description,
        name="MOSAIQ Administrator",
        email="admin@mosaiq-demo.com",
        password_hash="argon2id-hash",
        is_active=True,
    )


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Flask:
    """An application whose dashboard reads come from the test, not PostgreSQL."""
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

    monkeypatch.setattr(
        "web.services.dashboard.get_user_by_id",
        lambda _connection, _user_id: _user("ADMIN", "System administrator"),
    )
    monkeypatch.setattr(
        "web.services.dashboard.count_rows",
        lambda _connection, entity: {
            "customer": 30,
            "product": 30,
            "store": 30,
            "app_user": 30,
        }[entity],
    )
    monkeypatch.setattr(
        "web.services.dashboard.recent_entries",
        lambda _connection, limit: [
            AuditEntry(
                audit_id=2,
                entity="store",
                entity_pk="1",
                action="UPDATE",
                actor_name="MOSAIQ Administrator",
                executed_at=datetime(2026, 9, 6, 10, 30),
            ),
            AuditEntry(
                audit_id=1,
                entity="product",
                entity_pk="7",
                action="INSERT",
                actor_name=None,
                executed_at=datetime(2026, 9, 5, 9, 0),
            ),
        ][:limit],
    )
    return application


def _sign_in(client: FlaskClient, role_code: str = "ADMIN") -> None:
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = str(_USER_ID)
        flask_session["role_code"] = role_code
        flask_session["name"] = "MOSAIQ Administrator"


def test_signed_out_the_front_door_is_the_public_landing_page(app: Flask) -> None:
    body = app.test_client().get("/").get_data(as_text=True)

    assert "Retail segmentation platform" in body
    assert "Sign in" in body


def test_signing_in_lands_on_a_page_that_names_me_and_my_role(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)

    body = client.get("/").get_data(as_text=True)

    assert "MOSAIQ Administrator" in body
    assert "System administrator" in body


def test_the_landing_page_shows_real_figures(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)

    body = client.get("/").get_data(as_text=True)

    for label in ("Customers", "Products", "Stores", "Active users"):
        assert label in body
    assert "30" in body


def test_the_landing_page_shows_the_most_recent_audit_entries(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)

    body = client.get("/").get_data(as_text=True)

    assert "Recent changes" in body
    assert "2026-09-06 10:30" in body
    assert "UPDATE" in body


def test_an_entry_with_no_actor_is_shown_as_unattributed(app: Flask) -> None:
    """`audit_log.user_id` is NULL for the seed; that is a fact, not a gap."""
    client = app.test_client()
    _sign_in(client)

    assert "unattributed" in client.get("/").get_data(as_text=True)


def test_a_role_without_the_permissions_gets_no_figures(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dashboard that counted products for a loyalty customer would leak them."""
    monkeypatch.setattr(
        "web.services.dashboard.get_user_by_id",
        lambda _connection, _user_id: _user("CUSTOMER", "Loyalty programme customer"),
    )
    client = app.test_client()
    _sign_in(client, "CUSTOMER")

    body = client.get("/").get_data(as_text=True)

    assert "Customers" not in body
    assert "Recent changes" not in body
    assert "Loyalty programme customer" in body


def test_an_analyst_sees_the_catalog_figures_but_not_the_audit_log(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "web.services.dashboard.get_user_by_id",
        lambda _connection, _user_id: _user("ANALYST", "Commercial analyst"),
    )
    client = app.test_client()
    _sign_in(client, "ANALYST")

    body = client.get("/").get_data(as_text=True)

    assert "Customers" in body
    assert "Active users" not in body
    assert "Recent changes" not in body


def test_a_session_whose_account_is_gone_is_signed_out(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "web.services.dashboard.get_user_by_id",
        lambda _connection, _user_id: None,
    )
    client = app.test_client()
    _sign_in(client)

    body = client.get("/").get_data(as_text=True)

    assert "Sign in" in body
    with client.session_transaction() as flask_session:
        assert "user_id" not in flask_session


def test_the_shell_marks_the_section_the_visitor_is_in(app: Flask) -> None:
    @requires("audit.read")
    def audit_index() -> str:
        # Rendered through the shell, which is the thing being asserted.
        return render_template_string(
            '{% extends "base.html" %}{% block content %}audit{% endblock %}'
        )

    app.add_url_rule("/audit", endpoint="audit.index", view_func=audit_index)

    client = app.test_client()
    _sign_in(client)

    on_audit = client.get("/audit").get_data(as_text=True)
    on_home = client.get("/").get_data(as_text=True)

    assert _current_entry(on_audit) == "Audit log"
    assert _current_entry(on_home) == "Home"


def _current_entry(body: str) -> str | None:
    marked = re.search(
        r"masthead__link--current[^>]*>\s*([^<]+?)\s*</a>", body, re.DOTALL
    )
    return None if marked is None else marked.group(1)


def test_every_page_carries_the_same_shell(app: Flask) -> None:
    """Including the error pages: one masthead, one way to sign out."""
    client = app.test_client()
    _sign_in(client)

    for path, expected in (("/", 200), ("/no-such-page", 404)):
        response = client.get(path)
        body = response.get_data(as_text=True)
        assert response.status_code == expected
        assert 'class="masthead"' in body
        assert "Sign out" in body
