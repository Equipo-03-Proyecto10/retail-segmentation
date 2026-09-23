"""The campaign workflow (F11-02, #221).

The database is replaced with a mock, as in the other route tests. What these
cover is the application's side: which transitions the service allows, that an
illegal one is refused with a message, that the default-deny gate holds, and that
every statement reaches the driver parameterized. That the audit trigger records
each UPDATE is a schema fact, not something a mocked cursor can show.
"""

from __future__ import annotations

import re
from datetime import date
from itertools import chain, repeat
from unittest.mock import MagicMock, Mock

import pytest
from flask import Flask
from flask.testing import FlaskClient

from web.app import create_app
from web.config import Config
from web.db.campaigns import Campaign
from web.services import campaigns as service

USER_ID = "11111111-1111-1111-1111-000000000001"
FORM = dict(
    name="Win-back",
    label_code="AT_RISK",
    starts_on="2026-10-01",
    ends_on="2026-10-31",
)


def _campaign(status: str, campaign_id: int = 7) -> Campaign:
    return Campaign(
        campaign_id,
        "Win-back",
        "AT_RISK",
        date(2026, 10, 1),
        date(2026, 10, 31),
        status,
    )


@pytest.fixture
def connection() -> MagicMock:
    connection = MagicMock()
    connection.closed = False
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = None
    cursor.rowcount = 1
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
        # The first call is the startup probe; every request after it is served
        # the same mock connection.
        database_connector=Mock(side_effect=chain([Mock()], repeat(connection))),
    )
    application.config["PROPAGATE_EXCEPTIONS"] = False
    return application


def _sign_in(client: FlaskClient, role_code: str) -> None:
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = USER_ID
        flask_session["role_code"] = role_code
        flask_session["name"] = "Test User"


def _cursor(connection: MagicMock) -> MagicMock:
    return connection.cursor.return_value.__enter__.return_value


def _existing(connection: MagicMock, status: str | None) -> None:
    """Make the next SELECT of one campaign return it (or nothing)."""
    _cursor(connection).fetchone.return_value = (
        None if status is None else tuple(_campaign(status).__dict__.values())
    )


def _updates(connection: MagicMock) -> list[tuple]:
    return [
        call.args
        for call in _cursor(connection).execute.call_args_list
        if call.args[0].lstrip().upper().startswith("UPDATE")
    ]


# ---------- the transition rules ----------


def test_the_lifecycle_has_two_terminal_states() -> None:
    assert service.TRANSITIONS[service.DRAFT] == {service.ACTIVE, service.CANCELLED}
    assert service.TRANSITIONS[service.ACTIVE] == {service.FINISHED, service.CANCELLED}
    assert service.TRANSITIONS[service.FINISHED] == frozenset()
    assert service.TRANSITIONS[service.CANCELLED] == frozenset()


@pytest.mark.parametrize(
    ("status", "action", "new"),
    [
        ("DRAFT", "activate", "ACTIVE"),
        ("DRAFT", "cancel", "CANCELLED"),
        ("ACTIVE", "complete", "FINISHED"),
        ("ACTIVE", "cancel", "CANCELLED"),
    ],
)
def test_a_permitted_transition_is_one_guarded_update(
    app: Flask, connection: MagicMock, status: str, action: str, new: str
) -> None:
    client = app.test_client()
    _sign_in(client, "MARKETING")
    _existing(connection, status)

    response = client.post(f"/campaigns/7/{action}")

    assert response.status_code == 302
    # Conditional on the status that was read, so a concurrent change loses.
    assert _updates(connection) == [
        (
            "UPDATE campaign SET status = %s WHERE campaign_id = %s AND status = %s",
            (new, 7, status),
        )
    ]
    connection.commit.assert_called_once()


@pytest.mark.parametrize(
    ("status", "action"),
    [
        ("DRAFT", "complete"),
        ("ACTIVE", "activate"),
        ("FINISHED", "activate"),
        ("FINISHED", "cancel"),
        ("CANCELLED", "activate"),
        ("CANCELLED", "complete"),
    ],
)
def test_an_illegal_transition_is_refused_with_a_message(
    app: Flask, connection: MagicMock, status: str, action: str
) -> None:
    client = app.test_client()
    _sign_in(client, "MARKETING")
    _existing(connection, status)

    response = client.post(f"/campaigns/7/{action}")

    assert response.status_code == 409
    assert "Campaign 7 is" in response.get_data(as_text=True)
    assert _updates(connection) == []
    connection.rollback.assert_called_once()


def test_a_closed_campaign_says_no_further_transition_is_permitted(
    app: Flask, connection: MagicMock
) -> None:
    client = app.test_client()
    _sign_in(client, "MARKETING")
    _existing(connection, "FINISHED")

    body = client.post("/campaigns/7/cancel").get_data(as_text=True)

    assert "no further transition is permitted" in body


def test_a_lost_race_is_refused_not_ignored(app: Flask, connection: MagicMock) -> None:
    client = app.test_client()
    _sign_in(client, "MARKETING")
    _existing(connection, "DRAFT")
    _cursor(connection).rowcount = 0  # someone else moved it first

    response = client.post("/campaigns/7/activate")

    assert response.status_code == 409
    assert "changed while you were working" in response.get_data(as_text=True)


def test_an_unknown_campaign_or_action_is_a_404(
    app: Flask, connection: MagicMock
) -> None:
    client = app.test_client()
    _sign_in(client, "MARKETING")
    _existing(connection, None)

    assert client.post("/campaigns/7/activate").status_code == 404
    assert client.post("/campaigns/7/delete").status_code == 404
    assert client.get("/campaigns/7/edit").status_code == 404


# ---------- creating and editing a draft ----------


def test_a_campaign_is_created_as_a_draft_naming_a_label_code(
    app: Flask, connection: MagicMock
) -> None:
    client = app.test_client()
    _sign_in(client, "MARKETING")
    _cursor(connection).fetchone.return_value = (8,)

    response = client.post("/campaigns/new", data=FORM)

    assert response.status_code == 302
    statements = [call.args for call in _cursor(connection).execute.call_args_list]
    insert = next(args for args in statements if "INSERT INTO campaign" in args[0])
    assert "'DRAFT'" in insert[0]
    assert insert[1] == ("Win-back", "AT_RISK", date(2026, 10, 1), date(2026, 10, 31))
    connection.commit.assert_called_once()


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"name": " "}, "name"),
        ({"name": "x" * 121}, "name"),
        ({"label_code": ""}, "label_code"),
        ({"starts_on": ""}, "starts_on"),
        ({"ends_on": "31/10/2026"}, "ends_on"),
        ({"starts_on": "2026-11-01"}, "ends_on"),
    ],
)
def test_an_invalid_draft_is_refused_before_any_write(
    app: Flask, connection: MagicMock, change: dict[str, str], field: str
) -> None:
    client = app.test_client()
    _sign_in(client, "MARKETING")

    response = client.post("/campaigns/new", data=FORM | change)

    assert response.status_code == 400
    assert f'for="{field}"' in response.get_data(as_text=True)
    assert "mq-field__error" in response.get_data(as_text=True)
    connection.commit.assert_not_called()


def test_a_label_outside_the_vocabulary_is_a_field_error(
    app: Flask, connection: MagicMock
) -> None:
    from psycopg.errors import ForeignKeyViolation

    client = app.test_client()
    _sign_in(client, "MARKETING")

    def refuse_the_insert(statement: str, *_: object) -> None:
        if "INSERT INTO campaign" in statement:
            raise ForeignKeyViolation()

    _cursor(connection).execute.side_effect = refuse_the_insert

    response = client.post("/campaigns/new", data=FORM | {"label_code": "NOPE"})

    assert response.status_code == 409
    assert "not in the vocabulary" in response.get_data(as_text=True)


def test_only_a_draft_can_be_edited(app: Flask, connection: MagicMock) -> None:
    client = app.test_client()
    _sign_in(client, "MARKETING")

    _existing(connection, "DRAFT")
    assert client.get("/campaigns/7/edit").status_code == 200

    # A read has no conflict to report: back to the list with the reason.
    _existing(connection, "ACTIVE")
    response = client.get("/campaigns/7/edit?page=2")
    assert response.status_code == 302
    assert response.location.endswith("/campaigns/?page=2")
    with client.session_transaction() as flask_session:
        assert flask_session["_flashes"] == [
            ("danger", "Campaign 7 is active; only a draft can be edited.")
        ]

    response = client.post("/campaigns/7/edit", data=FORM)
    assert response.status_code == 409
    assert "only a draft can be edited" in response.get_data(as_text=True)
    assert _updates(connection) == []


def test_a_non_draft_is_refused_before_the_form_is_validated(
    app: Flask, connection: MagicMock
) -> None:
    """Invalid data on a campaign that can never be saved must not re-invite edits."""
    client = app.test_client()
    _sign_in(client, "MARKETING")
    _existing(connection, "ACTIVE")

    response = client.post("/campaigns/7/edit", data=FORM | {"name": ""})
    body = response.get_data(as_text=True)

    assert response.status_code == 409
    assert "only a draft can be edited" in body
    assert 'name="label_code"' not in body  # the edit form is not shown again


def test_a_draft_that_moved_on_mid_edit_is_refused_not_overwritten(
    app: Flask, connection: MagicMock
) -> None:
    client = app.test_client()
    _sign_in(client, "MARKETING")
    _existing(connection, "DRAFT")
    _cursor(connection).rowcount = 0  # activated by someone else after we read it

    response = client.post("/campaigns/7/edit", data=FORM)

    assert response.status_code == 409
    assert "left draft while you were editing it" in response.get_data(as_text=True)
    connection.commit.assert_not_called()
    connection.rollback.assert_called_once()


def test_editing_a_draft_rewrites_only_while_it_is_still_a_draft(
    app: Flask, connection: MagicMock
) -> None:
    client = app.test_client()
    _sign_in(client, "MARKETING")
    _existing(connection, "DRAFT")

    response = client.post("/campaigns/7/edit", data=FORM | {"label_code": "LOYAL"})

    assert response.status_code == 302
    ((statement, parameters),) = _updates(connection)
    assert "status = 'DRAFT'" in statement
    assert parameters[1] == "LOYAL"


# ---------- the list, as the people who use it ----------


def test_marketing_sees_the_right_actions_for_every_status(
    app: Flask, connection: MagicMock
) -> None:
    """The one place a write permission and a non-empty list meet."""
    client = app.test_client()
    _sign_in(client, "MARKETING")
    statuses = ["DRAFT", "ACTIVE", "FINISHED", "CANCELLED"]
    _cursor(connection).fetchall.return_value = [
        tuple(_campaign(status, campaign_id).__dict__.values())
        for campaign_id, status in enumerate(statuses, start=1)
    ]
    _cursor(connection).fetchone.return_value = (4,)

    response = client.get("/campaigns/")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    rows = body.split("<tbody>")[1].split("</tr>")[:4]
    controls = [
        re.findall(r">(Edit|Activate|Complete|Cancel|Closed)<", row) for row in rows
    ]
    assert controls == [
        ["Edit", "Activate", "Cancel"],
        ["Complete", "Cancel"],
        ["Closed"],
        ["Closed"],
    ]
    # Every control points at a real endpoint and carries the page it came from.
    assert 'action="/campaigns/1/activate?page=1"' in body
    assert 'action="/campaigns/2/complete?page=1"' in body
    assert 'action="/campaigns/2/cancel?page=1"' in body
    assert 'href="/campaigns/1/edit?page=1"' in body
    assert 'href="/campaigns/new"' in body


def test_a_write_returns_to_the_page_it_started_from(
    app: Flask, connection: MagicMock
) -> None:
    client = app.test_client()
    _sign_in(client, "MARKETING")
    _existing(connection, "DRAFT")

    response = client.post("/campaigns/7/activate?page=3")

    assert response.status_code == 302
    assert response.location.endswith("/campaigns/?page=3")


def test_a_refusal_on_a_later_page_keeps_its_message(
    app: Flask, connection: MagicMock
) -> None:
    """redirect_last_page would answer a POST with a GET redirect to a POST rule."""
    client = app.test_client()
    _sign_in(client, "MARKETING")
    _existing(connection, "FINISHED")

    response = client.post("/campaigns/7/activate?page=9")

    assert response.status_code == 409
    assert "no further transition is permitted" in response.get_data(as_text=True)


# ---------- who may reach it ----------


@pytest.mark.parametrize("role", ["ANALYST", "AUDITOR"])
def test_read_only_profiles_see_the_list_but_no_controls(
    app: Flask, connection: MagicMock, role: str
) -> None:
    client = app.test_client()
    _sign_in(client, role)
    _cursor(connection).fetchall.return_value = [
        tuple(_campaign("DRAFT").__dict__.values())
    ]
    _cursor(connection).fetchone.return_value = (1,)

    response = client.get("/campaigns/")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Win-back" in body
    assert "New campaign" not in body and "Activate" not in body


@pytest.mark.parametrize("role", ["ANALYST", "AUDITOR", "STORE_MANAGER", "CUSTOMER"])
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/campaigns/new"),
        ("POST", "/campaigns/new"),
        ("GET", "/campaigns/7/edit"),
        ("POST", "/campaigns/7/edit"),
        ("POST", "/campaigns/7/activate"),
        ("POST", "/campaigns/7/complete"),
        ("POST", "/campaigns/7/cancel"),
    ],
)
def test_a_profile_without_campaign_write_is_refused(
    app: Flask, connection: MagicMock, role: str, method: str, path: str
) -> None:
    client = app.test_client()
    _sign_in(client, role)

    response = client.open(path, method=method, data=FORM)

    assert response.status_code == 403
    assert _updates(connection) == []
    connection.commit.assert_not_called()


def test_a_visitor_who_is_not_signed_in_is_refused(
    app: Flask, connection: MagicMock
) -> None:
    response = app.test_client().post("/campaigns/7/activate")

    assert response.status_code == 403
    assert _updates(connection) == []
