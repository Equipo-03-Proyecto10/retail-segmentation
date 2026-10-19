"""One notification, and its read state, against PostgreSQL (AN-04, #419).

The privacy rule lives in these statements: a notification is found, and marked,
only for the user it was written for and only while their role still holds the
delivery permission. Every statement runs in a transaction that is rolled back.
"""

from __future__ import annotations

from collections.abc import Iterator

import psycopg
import pytest

from web.db.notifications import (
    count_unread,
    get_notification,
    list_notifications,
    mark_all_read,
    mark_read,
)
from web.services.notifications import (
    EXPERIMENT_ENDED,
    SEGMENT_RUN_COMPLETED,
    visible_kinds,
)

pytestmark = pytest.mark.postgres

ADMIN = "11111111-1111-1111-1111-000000000001"
ANALYST = "11111111-1111-1111-1111-000000000002"
STORE_MANAGER = "11111111-1111-1111-1111-000000000003"
INVENTORY_PLANNER = "11111111-1111-1111-1111-000000000005"
BOTH = [EXPERIMENT_ENDED, SEGMENT_RUN_COMPLETED]


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


def _unread_run(connection: psycopg.Connection, user: str) -> int:
    """The newest run event, which the seed leaves unread for everyone."""
    return _one(
        connection,
        "SELECT max(n.notification_id) FROM notification n"
        " JOIN notification_recipient r USING (notification_id)"
        " WHERE r.user_id = %s AND n.kind = 'SEGMENT_RUN_COMPLETED'"
        "   AND r.read_at IS NULL",
        (user,),
    )


def _read_at(connection: psycopg.Connection, user: str, notification_id: int):
    return _one(
        connection,
        "SELECT read_at FROM notification_recipient"
        " WHERE user_id = %s AND notification_id = %s",
        (user, notification_id),
    )


def test_a_user_finds_their_own_notification_with_its_facts(owner) -> None:
    notification_id = _unread_run(owner, ANALYST)

    found = get_notification(
        owner, user_id=ANALYST, notification_id=notification_id, kinds=BOTH
    )

    assert found is not None
    assert (found.notification_id, found.kind) == (
        notification_id,
        SEGMENT_RUN_COMPLETED,
    )
    assert found.run_method == "RFM_RULES" and found.run_customer_count == 30
    assert found.read_at is None


def test_the_same_id_is_not_found_for_a_user_it_was_not_written_for(owner) -> None:
    notification_id = _unread_run(owner, ANALYST)

    assert (
        get_notification(
            owner,
            user_id=INVENTORY_PLANNER,
            notification_id=notification_id,
            kinds=visible_kinds("INVENTORY_PLANNER"),
        )
        is None
    )
    # Even asking with every kind: the row is somebody else's.
    assert (
        get_notification(
            owner,
            user_id=INVENTORY_PLANNER,
            notification_id=notification_id,
            kinds=BOTH,
        )
        is None
    )


def test_an_id_that_does_not_exist_is_not_found(owner) -> None:
    assert (
        get_notification(
            owner, user_id=ADMIN, notification_id=2**62, kinds=visible_kinds("ADMIN")
        )
        is None
    )


def test_a_kind_the_role_no_longer_holds_is_not_found_though_it_was_written_for_them(
    owner,
) -> None:
    experiment = _one(
        owner,
        "SELECT min(n.notification_id) FROM notification n"
        " JOIN notification_recipient r USING (notification_id)"
        " WHERE r.user_id = %s AND n.kind = 'EXPERIMENT_ENDED'",
        (ANALYST,),
    )

    assert (
        get_notification(owner, user_id=ANALYST, notification_id=experiment, kinds=BOTH)
        is not None
    )
    assert (
        get_notification(
            owner,
            user_id=ANALYST,
            notification_id=experiment,
            kinds=[SEGMENT_RUN_COMPLETED],
        )
        is None
    )


def test_marking_one_read_sets_the_time_and_changes_only_that_row(owner) -> None:
    notification_id = _unread_run(owner, ANALYST)
    before = count_unread(owner, user_id=ANALYST, kinds=BOTH)
    others_before = count_unread(owner, user_id=ADMIN, kinds=BOTH)

    assert mark_read(
        owner, user_id=ANALYST, notification_id=notification_id, kinds=BOTH
    )

    assert _read_at(owner, ANALYST, notification_id) is not None
    assert count_unread(owner, user_id=ANALYST, kinds=BOTH) == before - 1
    # The administrator was written for the same event and still has it unread.
    assert count_unread(owner, user_id=ADMIN, kinds=BOTH) == others_before
    assert _read_at(owner, ADMIN, notification_id) is None


def test_marking_one_that_is_already_read_keeps_the_first_time_and_still_succeeds(
    owner,
) -> None:
    notification_id = _unread_run(owner, ANALYST)
    mark_read(owner, user_id=ANALYST, notification_id=notification_id, kinds=BOTH)
    first = _read_at(owner, ANALYST, notification_id)
    owner.execute(
        "UPDATE notification_recipient SET read_at = read_at - interval '1 day'"
        " WHERE user_id = %s AND notification_id = %s",
        (ANALYST, notification_id),
    )
    earlier = _read_at(owner, ANALYST, notification_id)

    assert mark_read(
        owner, user_id=ANALYST, notification_id=notification_id, kinds=BOTH
    )

    assert _read_at(owner, ANALYST, notification_id) == earlier != first


def test_marking_somebody_elses_notification_changes_nothing_and_says_so(
    owner,
) -> None:
    notification_id = _unread_run(owner, ANALYST)

    assert not mark_read(
        owner, user_id=INVENTORY_PLANNER, notification_id=notification_id, kinds=BOTH
    )
    assert _read_at(owner, ANALYST, notification_id) is None


def test_marking_a_kind_the_role_no_longer_holds_changes_nothing(owner) -> None:
    experiment = _one(
        owner,
        "SELECT min(n.notification_id) FROM notification n"
        " JOIN notification_recipient r USING (notification_id)"
        " WHERE r.user_id = %s AND n.kind = 'EXPERIMENT_ENDED'",
        (STORE_MANAGER,),
    )
    # The store manager was never written for an experiment: no row to mark.
    assert experiment is None

    analyst_experiment = _one(
        owner,
        "SELECT min(n.notification_id) FROM notification n"
        " WHERE n.kind = 'EXPERIMENT_ENDED'",
    )
    owner.execute(
        "UPDATE notification_recipient SET read_at = NULL"
        " WHERE user_id = %s AND notification_id = %s",
        (ANALYST, analyst_experiment),
    )

    assert not mark_read(
        owner,
        user_id=ANALYST,
        notification_id=analyst_experiment,
        kinds=[SEGMENT_RUN_COMPLETED],
    )
    assert _read_at(owner, ANALYST, analyst_experiment) is None


def test_mark_all_read_marks_what_the_role_reads_and_counts_it(owner) -> None:
    expected = count_unread(owner, user_id=ANALYST, kinds=BOTH)
    assert expected > 0

    marked = mark_all_read(owner, user_id=ANALYST, kinds=BOTH)

    assert marked == expected
    assert count_unread(owner, user_id=ANALYST, kinds=BOTH) == 0
    assert mark_all_read(owner, user_id=ANALYST, kinds=BOTH) == 0
    # Other users' rows are untouched.
    assert count_unread(owner, user_id=ADMIN, kinds=BOTH) == expected


def test_mark_all_read_leaves_unread_what_the_role_can_no_longer_see(owner) -> None:
    owner.execute(
        "UPDATE notification_recipient SET read_at = NULL"
        " WHERE user_id = %s AND notification_id IN ("
        "   SELECT notification_id FROM notification WHERE kind = 'EXPERIMENT_ENDED'"
        "   ORDER BY notification_id LIMIT 4)",
        (ANALYST,),
    )
    runs_unread = count_unread(owner, user_id=ANALYST, kinds=[SEGMENT_RUN_COMPLETED])

    marked = mark_all_read(owner, user_id=ANALYST, kinds=[SEGMENT_RUN_COMPLETED])

    assert marked == runs_unread
    # The four experiment events stay unread: hidden, so not "all", and back the
    # day the permission returns.
    assert count_unread(owner, user_id=ANALYST, kinds=BOTH) == 4


def test_a_read_notification_leaves_the_unread_count_but_not_the_inbox(owner) -> None:
    notification_id = _unread_run(owner, ANALYST)
    total_before = len(
        list_notifications(owner, user_id=ANALYST, kinds=BOTH, limit=100, offset=0)
    )

    mark_read(owner, user_id=ANALYST, notification_id=notification_id, kinds=BOTH)

    listed = list_notifications(owner, user_id=ANALYST, kinds=BOTH, limit=100, offset=0)
    assert len(listed) == total_before
    assert [r for r in listed if r.notification_id == notification_id][0].read_at
