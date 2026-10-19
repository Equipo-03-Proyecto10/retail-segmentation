"""The notification read queries, against PostgreSQL (AN-04, #419).

The service tests replace these functions; this runs them, so a join that names
a column wrongly or a filter that leaks another user's row fails here. The
seeded events (one per seeded run and experiment, written for the holders of the
delivery permission) are what is read, and anything a test adds is rolled back.
"""

from __future__ import annotations

from collections.abc import Iterator

import psycopg
import pytest

from web.db.notifications import count_notifications, count_unread, list_notifications
from web.services.notifications import (
    EXPERIMENT_ENDED,
    SALES_LOAD_FINISHED,
    SEGMENT_RUN_COMPLETED,
    USER_DEACTIVATED,
    describe,
    visible_kinds,
)

pytestmark = pytest.mark.postgres

ADMIN = "11111111-1111-1111-1111-000000000001"
ANALYST = "11111111-1111-1111-1111-000000000002"
STORE_MANAGER = "11111111-1111-1111-1111-000000000003"
INVENTORY_PLANNER = "11111111-1111-1111-1111-000000000005"
CUSTOMER = "11111111-1111-1111-1111-000000000007"


@pytest.fixture
def owner(owner_url: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(owner_url) as connection:
        try:
            yield connection
        finally:
            connection.rollback()


def _one(connection: psycopg.Connection, sql: str, params: tuple = ()):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchone()[0]


def _page(connection, user: str, kinds: list[str], limit=20, offset=0):
    return list_notifications(
        connection, user_id=user, kinds=kinds, limit=limit, offset=offset
    )


def test_a_user_reads_every_kind_their_role_holds_and_nothing_else(owner) -> None:
    kinds = visible_kinds("STORE_MANAGER")

    assert kinds == [SEGMENT_RUN_COMPLETED]
    assert count_notifications(owner, user_id=STORE_MANAGER, kinds=kinds) == 30
    assert {r.kind for r in _page(owner, STORE_MANAGER, kinds, limit=100)} == {
        SEGMENT_RUN_COMPLETED
    }


def test_the_administrator_reads_runs_and_experiments(owner) -> None:
    kinds = visible_kinds("ADMIN")

    assert count_notifications(owner, user_id=ADMIN, kinds=kinds) == 60
    assert {r.kind for r in _page(owner, ADMIN, kinds, limit=100)} == {
        SEGMENT_RUN_COMPLETED,
        EXPERIMENT_ENDED,
    }


def test_a_role_with_no_kinds_reads_nothing_even_when_rows_exist(owner) -> None:
    # The seed has no row for this user, and an empty list must not be read as
    # "all": it matches nothing.
    assert count_notifications(owner, user_id=ADMIN, kinds=[]) == 0
    assert count_unread(owner, user_id=ADMIN, kinds=[]) == 0
    assert _page(owner, ADMIN, []) == []
    assert count_notifications(owner, user_id=INVENTORY_PLANNER, kinds=[]) == 0
    assert count_notifications(owner, user_id=CUSTOMER, kinds=["EXPERIMENT_ENDED"]) == 0


def test_a_user_never_reads_another_users_row(owner) -> None:
    kinds = visible_kinds("ANALYST")
    mine = {r.notification_id for r in _page(owner, ANALYST, kinds, limit=100)}
    # The administrator is a recipient of the same events, with their own state.
    owner.execute(
        "UPDATE notification_recipient SET read_at = now() WHERE user_id = %s",
        (ADMIN,),
    )

    assert count_unread(owner, user_id=ANALYST, kinds=kinds) == _one(
        owner,
        "SELECT count(*) FROM notification_recipient nr"
        " JOIN notification n USING (notification_id)"
        " WHERE nr.user_id = %s AND nr.read_at IS NULL AND n.kind = ANY(%s)",
        (ANALYST, kinds),
    )
    assert mine == {r.notification_id for r in _page(owner, ANALYST, kinds, limit=100)}


def test_the_inbox_is_newest_first_and_pages_do_not_overlap(owner) -> None:
    kinds = visible_kinds("ADMIN")

    first = _page(owner, ADMIN, kinds, limit=20, offset=0)
    second = _page(owner, ADMIN, kinds, limit=20, offset=20)

    ids = [r.notification_id for r in first + second]
    assert len(ids) == 40 and ids == sorted(set(ids), reverse=True)
    created = [r.created_at for r in first + second]
    assert created == sorted(created, reverse=True)


def test_the_unread_count_is_the_unread_rows_the_role_may_read(owner) -> None:
    kinds = visible_kinds("ANALYST")
    expected = _one(
        owner,
        "SELECT count(*) FROM notification_recipient nr"
        " JOIN notification n USING (notification_id)"
        " WHERE nr.user_id = %s AND nr.read_at IS NULL AND n.kind = ANY(%s)",
        (ANALYST, kinds),
    )

    assert expected > 0
    assert count_unread(owner, user_id=ANALYST, kinds=kinds) == expected


def test_a_demoted_role_stops_counting_what_it_no_longer_holds(owner) -> None:
    both = visible_kinds("ANALYST")
    only_runs = [SEGMENT_RUN_COMPLETED]
    # The seed reads every experiment event, so leave one unread.
    owner.execute(
        "UPDATE notification_recipient SET read_at = NULL"
        " WHERE user_id = %s AND notification_id = ("
        "   SELECT min(notification_id) FROM notification"
        "   WHERE kind = 'EXPERIMENT_ENDED')",
        (ANALYST,),
    )

    assert count_notifications(owner, user_id=ANALYST, kinds=both) == 60
    assert count_notifications(owner, user_id=ANALYST, kinds=only_runs) == 30
    assert (
        count_unread(owner, user_id=ANALYST, kinds=both)
        == count_unread(owner, user_id=ANALYST, kinds=only_runs) + 1
    )


def test_each_kind_comes_back_with_the_facts_its_sentence_needs(owner) -> None:
    load_id = _one(
        owner,
        "INSERT INTO sales_load (filename, contract_version, received_count,"
        " accepted_count, rejected_count) VALUES ('may.csv', 1, 10, 7, 3)"
        " RETURNING load_id",
    )
    subject_user = _one(
        owner,
        "SELECT user_id::text FROM app_user WHERE email = 'user9@mosaiq-demo.com'",
    )
    owner.execute("INSERT INTO notification (sales_load_id) VALUES (%s)", (load_id,))
    owner.execute(
        "INSERT INTO notification (subject_user_id) VALUES (%s)", (subject_user,)
    )
    owner.execute(
        "INSERT INTO notification_recipient (notification_id, user_id)"
        " SELECT notification_id, %s FROM notification"
        " WHERE kind IN ('SALES_LOAD_FINISHED', 'USER_DEACTIVATED')",
        (ADMIN,),
    )

    rows = _page(owner, ADMIN, visible_kinds("ADMIN"), limit=100)
    by_kind = {r.kind: describe(r) for r in rows}

    assert by_kind[SALES_LOAD_FINISHED].message == (
        "The sales load “may.csv” finished: 7 rows accepted, 3 rejected."
    )
    assert by_kind[USER_DEACTIVATED].message == "Demo User 9 was deactivated."
    assert by_kind[SEGMENT_RUN_COMPLETED].message.endswith("for 30 customers.")
    assert by_kind[EXPERIMENT_ENDED].message.startswith("The experiment “Experiment ")
