"""The announcement of an experiment's end, against PostgreSQL (AN-04, #419;
ADR-0047, RN-64).

Three things the mocked tests cannot show. The statement's rules (ended means
before today, a 14-day lookback, a NULL end never announced, once only) run on
real dates. Two sessions that both see an experiment as unannounced leave one
event and one set of recipients: the race is made deterministic by holding one
session's insert uncommitted until the other is blocked behind it. And the real
application, signed in through the real login, announces on a `GET` and on
nothing else.

The first group runs in a transaction that is rolled back. The other two need
committed rows, so they create experiments with their own ids and remove them,
with everything that cascades from them, when they finish.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Iterator

import psycopg
import pytest

from web.app import create_app
from web.config import Config
from web.db.notifications import announce_ended_experiments
from web.services.notifications import (
    EXPERIMENT_END_LOOKBACK_DAYS,
    read_inbox,
)

pytestmark = pytest.mark.postgres

ROLES = ["ADMIN", "ANALYST", "AUDITOR", "MARKETING"]
ADMIN = "11111111-1111-1111-1111-000000000001"
ANALYST = "11111111-1111-1111-1111-000000000002"
PASSWORD = "Password123!"


@pytest.fixture
def app_connection(app_url: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(app_url) as connection:
        try:
            yield connection
        finally:
            connection.rollback()


def _one(connection, sql: str, params: tuple = ()):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchone()[0]


def _experiment(connection, experiment_id: int, days_ago: int | None, name="Scratch"):
    """An experiment that ended `days_ago` days ago (negative: in the future),
    or has no end date."""
    connection.execute(
        "INSERT INTO experiment (experiment_id, name, target_metric, starts_on,"
        " ends_on, conversion_window_days, data_origin)"
        " VALUES (%s, %s, 'CONVERSION', CURRENT_DATE - 400,"
        "         CASE WHEN %s::int IS NULL THEN NULL"
        "              ELSE CURRENT_DATE - %s::int END, 7, 'SEEDED')",
        (experiment_id, name, days_ago, days_ago),
    )


def _announce(connection):
    return announce_ended_experiments(
        connection, role_codes=ROLES, lookback_days=EXPERIMENT_END_LOOKBACK_DAYS
    )


def _event_count(connection, experiment_id: int) -> int:
    return _one(
        connection,
        "SELECT count(*) FROM notification WHERE experiment_id = %s",
        (experiment_id,),
    )


def _recipient_count(connection, experiment_id: int) -> int:
    return _one(
        connection,
        "SELECT count(*) FROM notification_recipient r"
        " JOIN notification n USING (notification_id) WHERE n.experiment_id = %s",
        (experiment_id,),
    )


# ---------- the rules, on real dates ----------


@pytest.mark.parametrize(
    ("days_ago", "announced"),
    [
        (-30, False),  # ends in the future
        (-1, False),  # ends tomorrow
        (0, False),  # ends today: still running on its end date
        (1, True),  # ended yesterday
        (7, True),
        (14, True),  # the oldest the lookback reaches
        (15, False),  # one day past it
        (400, False),  # a restored database's old experiment
        (None, False),  # no end date is never announced
    ],
)
def test_an_experiment_is_announced_only_once_it_has_ended_and_only_within_the_lookback(
    app_connection, days_ago: int | None, announced: bool
) -> None:
    _experiment(app_connection, 9101, days_ago)

    events, _ = _announce(app_connection)

    assert (events, _event_count(app_connection, 9101)) == (
        (1, 1) if announced else (0, 0)
    )


def test_an_ended_experiment_is_announced_to_every_active_holder_of_campaign_read(
    app_connection,
) -> None:
    _experiment(app_connection, 9101, 1, name="Free shipping")
    expected = _one(
        app_connection,
        "SELECT count(*) FROM app_user u JOIN role r USING (role_id)"
        " WHERE u.is_active AND r.code = ANY(%s)",
        (ROLES,),
    )

    events, recipients = _announce(app_connection)

    assert (events, recipients) == (1, expected) and expected == 16
    inbox = read_inbox(app_connection, user_id=ANALYST, role_code="ANALYST", page=1)
    assert inbox.items[0].kind == "EXPERIMENT_ENDED"
    assert inbox.items[0].message.startswith(
        "The experiment “Free shipping” reached its end date, "
    )
    # A role that does not hold campaign.read is not written to, and could not
    # read it if it were.
    for user in (
        "11111111-1111-1111-1111-000000000003",  # STORE_MANAGER
        "11111111-1111-1111-1111-000000000005",  # INVENTORY_PLANNER
        "11111111-1111-1111-1111-000000000007",  # CUSTOMER
    ):
        assert (
            _one(
                app_connection,
                "SELECT count(*) FROM notification_recipient r"
                " JOIN notification n USING (notification_id)"
                " WHERE n.experiment_id = 9101 AND r.user_id = %s",
                (user,),
            )
            == 0
        )


def test_an_inactive_account_is_not_written_to(app_connection) -> None:
    app_connection.execute(
        "UPDATE app_user SET is_active = FALSE WHERE user_id = %s", (ANALYST,)
    )
    _experiment(app_connection, 9101, 1)

    _announce(app_connection)

    assert (
        _one(
            app_connection,
            "SELECT count(*) FROM notification_recipient r"
            " JOIN notification n USING (notification_id)"
            " WHERE n.experiment_id = 9101 AND r.user_id = %s",
            (ANALYST,),
        )
        == 0
    )
    assert _recipient_count(app_connection, 9101) == 15


def test_announcing_again_writes_nothing(app_connection) -> None:
    _experiment(app_connection, 9101, 1)

    first = _announce(app_connection)
    second = _announce(app_connection)

    assert first == (1, 16)
    assert second == (0, 0)
    assert (
        _event_count(app_connection, 9101),
        _recipient_count(app_connection, 9101),
    ) == (
        1,
        16,
    )


def test_several_ended_experiments_are_announced_in_one_statement(
    app_connection,
) -> None:
    for experiment_id, days in ((9101, 1), (9102, 3), (9103, 14)):
        _experiment(app_connection, experiment_id, days)

    assert _announce(app_connection) == (3, 48)


def test_experiments_announced_together_are_listed_most_recently_ended_first(
    app_connection,
) -> None:
    # Inserted in an order that is neither the end order nor the id order.
    _experiment(app_connection, 9102, 3, name="Ended three days ago")
    _experiment(app_connection, 9103, 10, name="Ended ten days ago")
    _experiment(app_connection, 9101, 1, name="Ended yesterday")

    _announce(app_connection)

    inbox = read_inbox(app_connection, user_id=ANALYST, role_code="ANALYST", page=1)
    assert [item.message.split("“")[1].split("”")[0] for item in inbox.items[:3]] == [
        "Ended yesterday",
        "Ended three days ago",
        "Ended ten days ago",
    ]


def test_the_seeded_experiments_are_not_announced_again(app_connection) -> None:
    """The relative-date seed already announced even the recent experiment ends."""
    assert (
        _one(
            app_connection,
            "SELECT count(*) FROM experiment WHERE ends_on < CURRENT_DATE"
            " AND ends_on >= CURRENT_DATE - %s",
            (EXPERIMENT_END_LOOKBACK_DAYS,),
        )
        > 0
    )
    before = _one(app_connection, "SELECT count(*) FROM notification")
    recipients = _one(app_connection, "SELECT count(*) FROM notification_recipient")

    assert _announce(app_connection) == (0, 0)
    assert _announce(app_connection) == (0, 0)
    assert _one(app_connection, "SELECT count(*) FROM notification") == before
    assert (
        _one(app_connection, "SELECT count(*) FROM notification_recipient")
        == recipients
    )


def test_an_experiment_whose_end_date_is_edited_after_it_was_announced_keeps_one_event(
    app_connection,
) -> None:
    _experiment(app_connection, 9101, 2)
    _announce(app_connection)

    app_connection.execute(
        "UPDATE experiment SET ends_on = CURRENT_DATE - 1 WHERE experiment_id = 9101"
    )

    assert _announce(app_connection) == (0, 0)
    assert _event_count(app_connection, 9101) == 1


# ---------- two sessions at once ----------


@pytest.fixture
def owner_autocommit(owner_url: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(owner_url, autocommit=True) as connection:
        yield connection


def _wait_until_blocked(owner: psycopg.Connection, seconds: float = 10.0) -> bool:
    """True once some session is waiting on another's transaction."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        waiting = _one(
            owner,
            "SELECT count(*) FROM pg_stat_activity"
            " WHERE wait_event_type = 'Lock' AND wait_event = 'transactionid'"
            "   AND datname = current_database()",
        )
        if waiting:
            return True
        time.sleep(0.05)
    return False


def test_two_sessions_that_both_see_the_experiment_unannounced_leave_one_event(
    app_url: str, owner_autocommit: psycopg.Connection
) -> None:
    """A holds its announcement uncommitted. B, started while it is, finds the
    experiment unannounced too, tries to insert, and waits on A's unique index
    entry. When A commits, B's `ON CONFLICT DO NOTHING` inserts nothing, and
    writes no recipients, since it writes them from what it inserted."""
    owner_autocommit.execute("DELETE FROM experiment WHERE experiment_id = 9201")
    _experiment(owner_autocommit, 9201, 2)
    outcome: dict[str, object] = {}
    session_a = psycopg.connect(app_url)
    session_b = psycopg.connect(app_url)
    try:
        assert _announce(session_a) == (1, 16)  # uncommitted

        def announce_in_b() -> None:
            try:
                outcome["b"] = _announce(session_b)
                session_b.commit()
            except Exception as error:  # surfaced by the assertions below
                outcome["error"] = error

        thread = threading.Thread(target=announce_in_b)
        thread.start()
        blocked = _wait_until_blocked(owner_autocommit)
        session_a.commit()
        thread.join(timeout=15)

        assert blocked, "B never waited on A: the race was not reproduced"
        assert "error" not in outcome, outcome.get("error")
        assert not thread.is_alive()
        assert outcome["b"] == (0, 0)
        assert _event_count(owner_autocommit, 9201) == 1
        assert _recipient_count(owner_autocommit, 9201) == 16
    finally:
        session_a.close()
        session_b.close()
        owner_autocommit.execute("DELETE FROM experiment WHERE experiment_id = 9201")


def test_if_the_first_session_rolls_back_the_second_announces_it(
    app_url: str, owner_autocommit: psycopg.Connection
) -> None:
    owner_autocommit.execute("DELETE FROM experiment WHERE experiment_id = 9202")
    _experiment(owner_autocommit, 9202, 2)
    outcome: dict[str, object] = {}
    session_a = psycopg.connect(app_url)
    session_b = psycopg.connect(app_url)
    try:
        assert _announce(session_a) == (1, 16)

        def announce_in_b() -> None:
            try:
                outcome["b"] = _announce(session_b)
                session_b.commit()
            except Exception as error:
                outcome["error"] = error

        thread = threading.Thread(target=announce_in_b)
        thread.start()
        blocked = _wait_until_blocked(owner_autocommit)
        session_a.rollback()
        thread.join(timeout=15)

        assert blocked and "error" not in outcome, outcome.get("error")
        assert outcome["b"] == (1, 16)
        assert _event_count(owner_autocommit, 9202) == 1
    finally:
        session_a.close()
        session_b.close()
        owner_autocommit.execute("DELETE FROM experiment WHERE experiment_id = 9202")


# ---------- the real application ----------


@pytest.mark.real_sessions
@pytest.mark.real_csrf
@pytest.mark.real_notification_count
@pytest.mark.real_experiment_announcer
def test_a_signed_in_get_announces_and_a_head_a_post_or_a_visitor_does_not(
    app_url: str, owner_autocommit: psycopg.Connection
) -> None:
    owner_autocommit.execute("DELETE FROM experiment WHERE experiment_id = 9203")
    _experiment(owner_autocommit, 9203, 1, name="Win-back")
    application = create_app(
        Config(
            secret_key="announcer-test",
            environment="testing",
            port=5000,
            log_level="INFO",
            session_cookie_secure=False,
            database_url=app_url,
        )
    )
    application.config["PROPAGATE_EXCEPTIONS"] = False
    try:
        visitor = application.test_client()
        visitor.get("/login")
        visitor.get("/notifications/")  # redirected to sign in
        assert _event_count(owner_autocommit, 9203) == 0

        client = application.test_client()
        page = client.get("/login").get_data(as_text=True)
        token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        signed_in = client.post(
            "/login",
            data={
                "email": "admin@mosaiq-demo.com",
                "password": PASSWORD,
                "csrf_token": token,
            },
        )
        assert signed_in.status_code == 302
        assert _event_count(owner_autocommit, 9203) == 0  # signing in announces nothing

        assert client.head("/notifications/").status_code == 200
        with client.session_transaction() as flask_session:
            csrf = flask_session["csrf_token"]
        missing = client.post(
            "/notifications/999999999/read", data={"csrf_token": csrf}
        )
        assert missing.status_code == 404
        assert _event_count(owner_autocommit, 9203) == 0

        response = client.get("/notifications/")
        body = response.get_data(as_text=True)
        assert response.status_code == 200
        assert _event_count(owner_autocommit, 9203) == 1
        # The page the same request rendered already shows it.
        assert "The experiment “Win-back” reached its end date" in body
        assert "mq-nav__badge" in body

        client.get("/notifications/")
        assert _event_count(owner_autocommit, 9203) == 1
        assert _recipient_count(owner_autocommit, 9203) == 16
    finally:
        owner_autocommit.execute("DELETE FROM experiment WHERE experiment_id = 9203")
        owner_autocommit.execute("DELETE FROM app_session WHERE user_id = %s", (ADMIN,))
