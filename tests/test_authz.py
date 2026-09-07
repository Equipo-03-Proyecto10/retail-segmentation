"""Authorization is enforced in one place, before the view runs (#69, F4-01).

The test that matters most is `test_every_registered_endpoint_declares_what_it
_requires`: it is the executable form of the reason this story exists — a new
route cannot be left unprotected by accident, because forgetting the
declaration fails the build.
"""

from __future__ import annotations

import logging
import re
from unittest.mock import MagicMock, Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from flask import Flask
from flask.testing import FlaskClient

from web.app import create_app
from web.config import Config
from web.db import get_connection
from web.middleware.authz import (
    ALL_PERMISSIONS,
    NAVIGATION,
    PERMISSIONS,
    permissions_for,
    public,
    requirement_of,
    requires,
    safe_next,
)

_SEED_ROLE_CODES = re.compile(r"^\((\d+),'([A-Z_]+)'", re.MULTILINE)


def _app() -> Flask:
    app = create_app(
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
    app.config["PROPAGATE_EXCEPTIONS"] = False
    return app


def _sign_in(client: FlaskClient, role_code: str, user_id: str = "u-1") -> None:
    """Put a signed-in user in the session without going through login.

    The login route has its own tests; what these assert is the gate, and
    driving it through a real sign-in would need a database.
    """
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = user_id
        flask_session["role_code"] = role_code
        flask_session["name"] = f"{role_code.title()} user"


# ---------- the declaration sweep ----------


def test_every_registered_endpoint_declares_what_it_requires() -> None:
    """A route with no declaration is the accident this story prevents."""
    app = _app()

    undeclared = [
        endpoint
        for endpoint, view in app.view_functions.items()
        if endpoint != "static" and requirement_of(view) is None
    ]

    assert undeclared == [], (
        "These endpoints carry neither @public nor @requires(...): "
        f"{', '.join(undeclared)}. The gate refuses them, which is safe but "
        "not what the author intended."
    )


def test_an_undeclared_endpoint_is_refused_and_reported(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = _app()

    @app.get("/forgot-to-declare")
    def forgot_to_declare() -> str:  # pragma: no cover - must never run
        raise AssertionError("the view ran despite carrying no declaration")

    with caplog.at_level(logging.ERROR):
        response = app.test_client().get("/forgot-to-declare")

    assert response.status_code == 403
    assert "forgot_to_declare" in caplog.text


# ---------- signed out (RF-03) ----------


def test_an_anonymous_get_is_sent_to_sign_in_and_remembers_the_page() -> None:
    app = _app()

    @app.get("/reports")
    @requires("report.read")
    def reports() -> str:  # pragma: no cover - must never run
        raise AssertionError("the view ran for an anonymous visitor")

    response = app.test_client().get("/reports?page=2")
    location = urlsplit(response.headers["Location"])

    assert response.status_code == 302
    assert location.path == "/login"
    # Asserted after decoding rather than as a literal: what matters is the
    # value the sign-in page reads back, not how Werkzeug chose to escape it.
    assert parse_qs(location.query)["next"] == ["/reports?page=2"]


def test_an_anonymous_post_is_refused_rather_than_redirected() -> None:
    """A POST body cannot survive the round trip through the sign-in page."""
    app = _app()

    @app.post("/reports")
    @requires("report.read")
    def create_report() -> str:  # pragma: no cover - must never run
        raise AssertionError("the view ran for an anonymous visitor")

    assert app.test_client().post("/reports").status_code == 403


def test_a_public_route_is_reachable_signed_out() -> None:
    assert _app().test_client().get("/").status_code == 200


# ---------- signed in (RF-04, HU-04) ----------


def test_a_role_without_the_permission_is_refused_before_the_view_runs() -> None:
    app = _app()
    reached: list[str] = []

    @app.get("/audit")
    @requires("audit.read")
    def audit() -> str:
        reached.append("audit")
        return "the audit log"

    client = app.test_client()
    _sign_in(client, "ANALYST")
    response = client.get("/audit")

    assert response.status_code == 403
    assert reached == [], "the view body ran before the gate refused it"
    assert "audit log" not in response.get_data(as_text=True)


def test_a_role_with_the_permission_is_served() -> None:
    app = _app()

    @app.get("/audit")
    @requires("audit.read")
    def audit() -> str:
        return "the audit log"

    client = app.test_client()
    _sign_in(client, "AUDITOR")
    response = client.get("/audit")

    assert response.status_code == 200
    assert "the audit log" in response.get_data(as_text=True)


def test_a_signed_in_route_needs_no_particular_permission() -> None:
    app = _app()

    @app.get("/any-signed-in-user")
    @requires()
    def anybody() -> str:
        return "signed in"

    client = app.test_client()
    assert client.get("/any-signed-in-user").status_code == 302

    _sign_in(client, "CUSTOMER")
    assert client.get("/any-signed-in-user").status_code == 200


def test_the_refusal_page_explains_itself_and_carries_a_reference() -> None:
    app = _app()

    @app.get("/audit")
    @requires("audit.read")
    def audit() -> str:  # pragma: no cover - must never run
        raise AssertionError("the view ran for a role without the permission")

    client = app.test_client()
    _sign_in(client, "CUSTOMER")
    body = client.get("/audit").get_data(as_text=True)

    assert "403" in body
    assert "role may reach" in body
    assert "Traceback" not in body


def test_a_refusal_is_logged_with_the_user_the_route_and_the_path(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = _app()

    @app.get("/audit")
    @requires("audit.read")
    def audit() -> str:  # pragma: no cover - must never run
        raise AssertionError("the view ran for a role without the permission")

    client = app.test_client()
    _sign_in(client, "MARKETING", user_id="u-42")

    with caplog.at_level(logging.WARNING):
        client.get("/audit")

    assert "u-42" in caplog.text
    assert "MARKETING" in caplog.text
    assert "audit" in caplog.text
    assert "/audit" in caplog.text


def test_a_session_carrying_an_unknown_role_holds_nothing() -> None:
    app = _app()

    @app.get("/catalogs")
    @requires("catalog.read")
    def catalogs() -> str:  # pragma: no cover - must never run
        raise AssertionError("the view ran for a role that does not exist")

    client = app.test_client()
    _sign_in(client, "SUPERUSER")

    assert client.get("/catalogs").status_code == 403


# ---------- who the audit triggers are told about ----------


def _actor_named_on_the_connection(signed_in_as: str | None) -> tuple[str, tuple]:
    """Open a connection inside a request and report what the actor was set to."""
    connection = MagicMock()
    connection.closed = False
    app = create_app(
        Config(
            secret_key="test",
            environment="testing",
            port=5000,
            log_level="INFO",
            session_cookie_secure=False,
            database_url="unused-by-test",
        ),
        database_connector=Mock(side_effect=[MagicMock(), connection]),
    )

    @app.get("/touches-the-database")
    @public
    def touches_the_database() -> str:
        get_connection()
        return "ok"

    client = app.test_client()
    if signed_in_as is not None:
        _sign_in(client, "ADMIN", user_id=signed_in_as)
    client.get("/touches-the-database")

    cursor = connection.cursor.return_value.__enter__.return_value
    return cursor.execute.call_args.args


def test_the_signed_in_user_is_named_on_the_connection() -> None:
    """RF-14: a change the application makes is attributable to whoever made it."""
    statement, parameters = _actor_named_on_the_connection("u-42")

    assert "set_config" in statement
    assert parameters == ("mosaiq.user_id", "u-42")


def test_an_anonymous_request_leaves_the_actor_unattributed() -> None:
    """Better a NULL actor than a fiction: the audit column is nullable for this."""
    _statement, parameters = _actor_named_on_the_connection(None)

    assert parameters == ("mosaiq.user_id", "")


# ---------- the matrix itself ----------


def test_the_matrix_covers_exactly_the_seeded_roles() -> None:
    """A role added to the seed without a permission row is a hole."""
    seed = open("sql/02_seed_30_per_table.sql", encoding="utf-8").read()
    roles_block = seed[seed.index("INSERT INTO role") :]
    roles_block = roles_block[: roles_block.index(";")]
    seeded = {code for _, code in _SEED_ROLE_CODES.findall(roles_block)}

    assert seeded == set(PERMISSIONS), (
        "sql/02_seed_30_per_table.sql and the matrix in web/middleware/authz.py "
        "disagree about which roles exist."
    )


def test_the_regular_user_and_the_administrator_both_exist() -> None:
    """The issue's third acceptance criterion, stated as the matrix states it.

    `ADMIN` is not a superset of `CUSTOMER`: `user.self` is the customer's
    own-row access to the Users section, which the matrix does not grant the
    administrator because the administrator already holds the section outright.
    What must be true is that the administrator is the stronger of the two and
    that the regular user writes nothing.
    """
    assert "ADMIN" in PERMISSIONS
    assert "CUSTOMER" in PERMISSIONS
    assert len(permissions_for("ADMIN")) > len(permissions_for("CUSTOMER"))
    assert {"catalog.write", "user.write", "audit.read"} <= permissions_for("ADMIN")
    assert not any(
        permission.endswith(".write") for permission in permissions_for("CUSTOMER")
    )


def test_every_permission_named_anywhere_is_part_of_the_vocabulary() -> None:
    granted = {permission for role in PERMISSIONS.values() for permission in role}
    navigated = {entry.permission for entry in NAVIGATION if entry.permission}
    declared = {
        permission
        for view in _app().view_functions.values()
        for requirement in [requirement_of(view)]
        if requirement is not None
        for permission in requirement.permissions
    }

    assert granted <= ALL_PERMISSIONS
    assert navigated <= ALL_PERMISSIONS
    assert declared <= ALL_PERMISSIONS


def test_requires_rejects_a_permission_that_does_not_exist() -> None:
    with pytest.raises(ValueError, match="Unknown permission"):

        @requires("catalog.destroy")
        def view() -> str:  # pragma: no cover - never defined
            return ""


# ---------- the menu ----------


def _menu_labels(client: FlaskClient) -> list[str]:
    response = client.get("/")
    # Guard: the shell renders on the error page too, so without this a broken
    # landing page would still yield a menu and the assertion would pass.
    assert response.status_code == 200, "the landing page did not render"
    body = response.get_data(as_text=True)
    if 'id="primary-navigation"' not in body:
        return ["Home"] if 'class="mq-public-nav"' in body else []
    nav = body[body.index('id="primary-navigation"') : body.index("</nav>")]
    return re.findall(r'mq-nav__label">([^<]+)</span>', nav)


def test_the_menu_is_driven_by_permissions_rather_than_by_role() -> None:
    app = _app()

    administrator = app.test_client()
    _sign_in(administrator, "ADMIN")
    analyst = app.test_client()
    _sign_in(analyst, "ANALYST")

    # The real registered destinations follow each visitor's permissions.
    assert _menu_labels(administrator) == [
        "Home",
        "Catalogs",
        "Users",
        "Segments",
        "Campaigns",
        "Segment run",
        "Reports",
        "Audit log",
    ]
    assert _menu_labels(analyst) == [
        "Home",
        "Catalogs",
        "Segments",
        "Campaigns",
        "Reports",
    ]
    assert _menu_labels(app.test_client()) == ["Home"]


def test_planned_menu_entries_reach_a_status_page() -> None:
    """Visible destinations without a workflow explain their delivery status."""
    client = _app().test_client()
    _sign_in(client, "ADMIN")

    for path in ("/campaigns/", "/reports/"):
        response = client.get(path)
        assert response.status_code == 200
        assert "Still building" in response.get_data(as_text=True)


def test_the_signed_in_name_and_sign_out_replace_the_sign_in_link() -> None:
    app = _app()
    client = app.test_client()

    assert "Sign in" in client.get("/").get_data(as_text=True)

    _sign_in(client, "ADMIN")
    body = client.get("/").get_data(as_text=True)

    assert "Sign out" in body
    assert "Admin user" in body


# ---------- returning where the visitor was going ----------


@pytest.mark.parametrize(
    "target",
    ["/reports", "/reports?page=2", "/"],
)
def test_safe_next_accepts_a_path_on_this_site(target: str) -> None:
    assert safe_next(target) == target


@pytest.mark.parametrize(
    "target",
    [
        None,
        "",
        "//evil.example",
        "https://evil.example/steal",
        "http://evil.example",
        "\\\\evil.example",
        "/reports\nSet-Cookie: a=b",
        "javascript:alert(1)",
    ],
)
def test_safe_next_refuses_anything_that_could_leave_the_site(
    target: str | None,
) -> None:
    assert safe_next(target) is None


def test_public_and_requires_stamp_a_declaration() -> None:
    @public
    def open_view() -> str:
        return ""

    @requires("catalog.read")
    def closed_view() -> str:
        return ""

    open_requirement = requirement_of(open_view)
    closed_requirement = requirement_of(closed_view)

    assert open_requirement is not None and open_requirement.anonymous_allowed
    assert closed_requirement is not None
    assert closed_requirement.anonymous_allowed is False
    assert closed_requirement.permissions == frozenset({"catalog.read"})


@pytest.mark.parametrize("role", ["ADMIN", "ANALYST", "AUDITOR"])
def test_catalog_menu_reaches_all_five_real_listings(role: str) -> None:
    app = _app()
    client = app.test_client()
    _sign_in(client, role)
    response = client.get("/admin/catalogs")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    for catalog in ("stores", "categories", "channels", "products", "roles"):
        assert f'href="/admin/{catalog}"' in body
    assert re.search(
        r'mq-nav__item--active(?:(?!</a>)[\s\S])*mq-nav__label">Catalogs</span>',
        body,
    )
    assert not re.search(
        r'mq-nav__item--active(?:(?!</a>)[\s\S])*mq-nav__label">Users</span>',
        body,
    )


def test_users_section_does_not_mark_catalogs_current() -> None:
    app = _app()
    client = app.test_client()
    _sign_in(client, "ADMIN")
    # The user creation form reads role options but does not mutate data.
    response = client.get("/admin/users/new")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert re.search(
        r'mq-nav__item--active(?:(?!</a>)[\s\S])*mq-nav__label">Users</span>',
        body,
    )
    assert not re.search(
        r'mq-nav__item--active(?:(?!</a>)[\s\S])*mq-nav__label">Catalogs</span>',
        body,
    )
