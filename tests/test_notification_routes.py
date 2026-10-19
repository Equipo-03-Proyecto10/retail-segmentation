"""The notification inbox and the navigation badge (AN-04, #419; ADR-0047).

The database is a mock and the data-access functions are replaced, so each test
states what the page was given. What the queries return for a real database is
tests/test_notification_reads_db.py's.
"""

from __future__ import annotations

import logging
import re
from unittest.mock import MagicMock, Mock
from urllib.parse import parse_qs, urlsplit

import psycopg
import pytest
from flask import Flask
from flask.testing import FlaskClient

from tests.test_notification_service import USER_ID, row
from web.app import create_app
from web.config import Config
from web.middleware.authz import PERMISSIONS
from web.services import notifications
from web.services.notifications import (
    EXPERIMENT_ENDED,
    SALES_LOAD_FINISHED,
    SEGMENT_RUN_COMPLETED,
    USER_DEACTIVATED,
)

_URL = "/notifications/"


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


@pytest.fixture
def reads(monkeypatch: pytest.MonkeyPatch) -> dict[str, Mock]:
    fakes = {
        "count_notifications": Mock(return_value=2),
        "count_unread": Mock(return_value=1),
        "list_notifications": Mock(
            return_value=[
                row(SEGMENT_RUN_COMPLETED, notification_id=9),
                row(EXPERIMENT_ENDED, notification_id=8),
            ]
        ),
    }
    for name, fake in fakes.items():
        monkeypatch.setattr(notifications, name, fake)
    return fakes


def _sign_in(app: Flask, role: str = "ANALYST") -> FlaskClient:
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = USER_ID
        flask_session["role_code"] = role
        flask_session["name"] = "Test User"
    return client


def _body(response) -> str:
    return response.get_data(as_text=True)


def _nav_labels(body: str) -> list[str]:
    nav = body[body.index('id="primary-navigation"') : body.index("</nav>")]
    return re.findall(r'mq-nav__label">([^<]+)</span>', nav)


def _badge(body: str) -> str | None:
    found = re.search(r'mq-nav__badge">([^<]+)<span', body)
    return found.group(1) if found else None


# ---------- the inbox ----------


def test_the_inbox_lists_each_notification_in_words_with_its_state(
    app: Flask, reads: dict[str, Mock]
) -> None:
    response = _sign_in(app).get(_URL)
    body = _body(response)

    assert response.status_code == 200
    assert "Segmentation run #12 (RFM_RULES) completed for 30 customers." in body
    assert "The experiment “Free shipping” reached its end date, 2026-10-14." in body
    assert "Segmentation run completed" in body and "Experiment ended" in body
    assert 'datetime="2026-10-15T09:30:00+00:00"' in body
    assert "2 notifications" in body and "1 unread" in body
    assert body.count("mq-notice--unread") == 2  # both rows are unread


def test_an_unread_row_is_marked_in_words_not_only_in_colour(
    app: Flask, reads: dict[str, Mock]
) -> None:
    body = _body(_sign_in(app).get(_URL))

    assert body.count('class="mq-notice__state">Unread</span>') == 2


def test_each_row_links_to_what_it_is_about(app: Flask, reads: dict[str, Mock]) -> None:
    body = _body(_sign_in(app).get(_URL))

    assert 'href="/run-history/12"' in body
    assert 'href="/experiments/5/uplift"' in body


def test_the_administrator_reads_every_kind_and_is_linked_to_each_subject(
    app: Flask, reads: dict[str, Mock]
) -> None:
    reads["list_notifications"].return_value = [
        row(SALES_LOAD_FINISHED, notification_id=4),
        row(USER_DEACTIVATED, notification_id=3),
    ]
    body = _body(_sign_in(app, "ADMIN").get(_URL))

    assert (
        "The sales load “september.csv” finished: 1240 rows accepted, 3 rejected."
        in body
    )
    assert 'href="/admin/sales-import/7"' in body
    assert "Demo User 9 was deactivated." in body
    assert 'href="/admin/users/11111111-1111-1111-1111-000000000009/edit"' in body


def test_a_subject_s_name_is_escaped_not_trusted(
    app: Flask, reads: dict[str, Mock]
) -> None:
    reads["list_notifications"].return_value = [
        row(
            SALES_LOAD_FINISHED,
            sales_load_filename="<script>alert(1)</script>.csv",
        )
    ]
    body = _body(_sign_in(app, "ADMIN").get(_URL))

    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;.csv" in body


def test_the_inbox_asks_only_for_the_kinds_the_role_reads(
    app: Flask, reads: dict[str, Mock]
) -> None:
    _sign_in(app, "STORE_MANAGER").get(_URL)

    kwargs = reads["list_notifications"].call_args.kwargs
    assert kwargs["kinds"] == [SEGMENT_RUN_COMPLETED]
    assert kwargs["user_id"] == USER_ID
    assert (kwargs["limit"], kwargs["offset"]) == (20, 0)


def test_the_second_page_reads_the_next_twenty(
    app: Flask, reads: dict[str, Mock]
) -> None:
    reads["count_notifications"].return_value = 45

    response = _sign_in(app).get(_URL + "?page=2")

    assert response.status_code == 200
    assert reads["list_notifications"].call_args.kwargs["offset"] == 20
    body = _body(response)
    assert "Page 2 of 3" in body
    assert 'href="/notifications/?page=1"' in body
    assert 'href="/notifications/?page=3"' in body


def test_a_page_past_the_last_goes_to_the_last_page(
    app: Flask, reads: dict[str, Mock]
) -> None:
    reads["count_notifications"].return_value = 45

    response = _sign_in(app).get(_URL + "?page=9")

    assert response.status_code == 302
    assert response.headers["Location"] == "/notifications/?page=3"


@pytest.mark.parametrize("page", ["0", "-1", "abc", "٢", "99999999999999999999", ""])
def test_a_page_that_is_not_a_page_number_is_the_first(
    app: Flask, reads: dict[str, Mock], page: str
) -> None:
    response = _sign_in(app).get(_URL + "?page=" + page)

    assert response.status_code == 200
    assert reads["list_notifications"].call_args.kwargs["offset"] == 0


def test_an_empty_inbox_says_what_will_appear_here(
    app: Flask, reads: dict[str, Mock]
) -> None:
    reads["count_notifications"].return_value = 0
    reads["count_unread"].return_value = 0
    reads["list_notifications"].return_value = []

    body = _body(_sign_in(app).get(_URL))

    assert "No notifications yet" in body
    assert "mq-notice-list" not in body


def test_a_role_that_is_sent_nothing_is_told_so_instead_of_promised_something(
    app: Flask, reads: dict[str, Mock]
) -> None:
    reads["count_notifications"].return_value = 0
    reads["count_unread"].return_value = 0
    reads["list_notifications"].return_value = []

    body = _body(_sign_in(app, "INVENTORY_PLANNER").get(_URL))

    assert "Nothing is sent to your role" in body
    assert "No notifications yet" not in body


def test_an_anonymous_visitor_is_sent_to_sign_in_and_nothing_is_read(
    app: Flask, reads: dict[str, Mock]
) -> None:
    response = app.test_client().get(_URL)

    assert response.status_code == 302
    target = urlsplit(response.location)
    assert target.path == "/login"
    assert parse_qs(target.query) == {"next": [_URL]}
    reads["list_notifications"].assert_not_called()


def test_a_head_request_reads_the_inbox_like_a_get(
    app: Flask, reads: dict[str, Mock]
) -> None:
    assert _sign_in(app).head(_URL).status_code == 200


# ---------- the navigation ----------


@pytest.mark.parametrize("role", sorted(PERMISSIONS))
def test_every_signed_in_role_has_the_notifications_entry(
    app: Flask, reads: dict[str, Mock], role: str
) -> None:
    body = _body(_sign_in(app, role).get("/"))

    assert "Notifications" in _nav_labels(body)


def test_the_entry_is_in_the_workspace_section_after_home(
    app: Flask, reads: dict[str, Mock]
) -> None:
    labels = _nav_labels(_body(_sign_in(app, "ADMIN").get("/")))

    assert labels[:2] == ["Home", "Notifications"]


def test_the_notifications_entry_is_current_on_the_inbox(
    app: Flask, reads: dict[str, Mock]
) -> None:
    body = _body(_sign_in(app).get(_URL))

    assert re.search(
        r'mq-nav__item--active"\s+href="/notifications/"\s+aria-current="page"',
        body,
    )


def test_a_visitor_who_is_not_signed_in_has_no_notifications_entry(app: Flask) -> None:
    body = _body(app.test_client().get("/"))

    assert "Notifications" not in body


# ---------- the badge (the real count, not the stub) ----------


@pytest.mark.real_notification_count
@pytest.mark.parametrize(
    ("unread", "shown"),
    [(1, "1"), (42, "42"), (99, "99"), (100, "99+"), (12345, "99+")],
)
def test_the_navigation_shows_the_unread_count_capped_at_99(
    app: Flask, reads: dict[str, Mock], unread: int, shown: str
) -> None:
    reads["count_unread"].return_value = unread

    body = _body(_sign_in(app).get("/"))

    assert _badge(body) == shown
    assert (
        f'mq-nav__badge">{shown}<span class="mq-visually-hidden"> unread</span>' in body
    )


@pytest.mark.real_notification_count
def test_no_badge_is_drawn_when_nothing_is_unread(
    app: Flask, reads: dict[str, Mock]
) -> None:
    reads["count_unread"].return_value = 0

    body = _body(_sign_in(app).get("/"))

    assert "mq-nav__badge" not in body


@pytest.mark.real_notification_count
def test_the_badge_counts_only_what_the_role_may_read(
    app: Flask, reads: dict[str, Mock]
) -> None:
    _sign_in(app, "STORE_MANAGER").get("/")

    assert reads["count_unread"].call_args.kwargs["kinds"] == [SEGMENT_RUN_COMPLETED]


@pytest.mark.real_notification_count
def test_a_count_that_cannot_be_read_hides_the_badge_and_the_page_is_still_served(
    app: Flask, reads: dict[str, Mock], caplog: pytest.LogCaptureFixture
) -> None:
    reads["count_unread"].side_effect = psycopg.OperationalError("connection lost")

    with caplog.at_level(logging.WARNING):
        response = _sign_in(app).get("/")

    assert response.status_code == 200
    assert "mq-nav__badge" not in _body(response)
    assert "notification_count_failed reason=OperationalError" in caplog.text
    assert "connection lost" not in caplog.text


@pytest.mark.real_notification_count
def test_a_failing_connection_also_hides_the_badge(
    app: Flask, caplog: pytest.LogCaptureFixture
) -> None:
    """A page that needs no data of its own (a 404) shows what the failure does
    to the navigation alone."""
    app.extensions["database_connector"] = Mock(
        side_effect=psycopg.OperationalError("down")
    )

    with caplog.at_level(logging.WARNING):
        response = _sign_in(app).get("/no-such-page")

    assert response.status_code == 404
    assert "mq-nav__badge" not in _body(response)
    assert "notification_count_failed" in caplog.text


@pytest.mark.real_notification_count
def test_the_badge_is_on_error_pages_too(app: Flask, reads: dict[str, Mock]) -> None:
    reads["count_unread"].return_value = 3

    response = _sign_in(app).get("/no-such-page")

    assert response.status_code == 404
    assert _badge(_body(response)) == "3"


@pytest.mark.real_notification_count
def test_a_signed_out_page_never_asks_for_a_count(
    app: Flask, reads: dict[str, Mock]
) -> None:
    app.test_client().get("/login")

    reads["count_unread"].assert_not_called()


def test_by_default_the_count_is_stubbed_so_a_page_opens_no_connection(
    app: Flask,
) -> None:
    app.extensions["database_connector"].reset_mock()

    _sign_in(app).get("/no-such-page")

    app.extensions["database_connector"].assert_not_called()
