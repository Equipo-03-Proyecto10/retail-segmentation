"""What a user may read of their notifications, and the words they read it in
(AN-04, #419; ADR-0047).

The data-access functions are replaced, so each test states what the service was
given. The queries themselves are exercised against PostgreSQL in
tests/test_notification_reads_db.py.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from unittest.mock import MagicMock, Mock
from uuid import UUID

import pytest

from web.db.notifications import NotificationRow
from web.middleware.authz import (
    ALL_PERMISSIONS,
    CAMPAIGN_READ,
    PERMISSIONS,
    SALES_INGEST_EXECUTE,
    SEGMENT_READ,
    USER_WRITE,
    permissions_for,
    roles_holding,
)
from web.services import notifications
from web.services.notifications import (
    BADGE_CAP,
    DELIVERY_PERMISSION,
    EXPERIMENT_ENDED,
    PAGE_SIZE,
    SALES_LOAD_FINISHED,
    SEGMENT_RUN_COMPLETED,
    USER_DEACTIVATED,
    badge_text,
    describe,
    read_inbox,
    unread_count,
    visible_kinds,
)

USER_ID = "11111111-1111-1111-1111-000000000001"
WHEN = datetime(2026, 10, 15, 9, 30, tzinfo=UTC)


def row(kind: str, notification_id: int = 1, **overrides) -> NotificationRow:
    """One stored event as the query returns it: its own subject filled in."""
    subject: dict = {
        "sales_load_id": None,
        "sales_load_filename": None,
        "sales_load_accepted": None,
        "sales_load_rejected": None,
        "run_id": None,
        "run_method": None,
        "run_customer_count": None,
        "experiment_id": None,
        "experiment_name": None,
        "experiment_ends_on": None,
        "subject_user_id": None,
        "subject_user_name": None,
    }
    subject.update(
        {
            SALES_LOAD_FINISHED: {
                "sales_load_id": 7,
                "sales_load_filename": "september.csv",
                "sales_load_accepted": 1240,
                "sales_load_rejected": 3,
            },
            SEGMENT_RUN_COMPLETED: {
                "run_id": 12,
                "run_method": "RFM_RULES",
                "run_customer_count": 30,
            },
            EXPERIMENT_ENDED: {
                "experiment_id": 5,
                "experiment_name": "Free shipping",
                "experiment_ends_on": date(2026, 10, 14),
            },
            USER_DEACTIVATED: {
                "subject_user_id": UUID("11111111-1111-1111-1111-000000000009"),
                "subject_user_name": "Demo User 9",
            },
        }[kind]
    )
    subject.update(overrides)
    return NotificationRow(
        notification_id=notification_id,
        kind=kind,
        created_at=WHEN,
        read_at=None,
        **subject,
    )


# ---------- who may read what ----------


def test_every_kind_has_exactly_one_delivery_permission_and_it_exists() -> None:
    assert set(DELIVERY_PERMISSION) == {
        SALES_LOAD_FINISHED,
        SEGMENT_RUN_COMPLETED,
        EXPERIMENT_ENDED,
        USER_DEACTIVATED,
    }
    assert set(DELIVERY_PERMISSION.values()) <= ALL_PERMISSIONS


def test_the_delivery_permissions_are_the_ones_the_decision_names() -> None:
    assert DELIVERY_PERMISSION == {
        SALES_LOAD_FINISHED: SALES_INGEST_EXECUTE,
        SEGMENT_RUN_COMPLETED: SEGMENT_READ,
        EXPERIMENT_ENDED: CAMPAIGN_READ,
        USER_DEACTIVATED: USER_WRITE,
    }


@pytest.mark.parametrize(
    ("role", "kinds"),
    [
        (
            "ADMIN",
            [
                EXPERIMENT_ENDED,
                SALES_LOAD_FINISHED,
                SEGMENT_RUN_COMPLETED,
                USER_DEACTIVATED,
            ],
        ),
        ("ANALYST", [EXPERIMENT_ENDED, SEGMENT_RUN_COMPLETED]),
        ("MARKETING", [EXPERIMENT_ENDED, SEGMENT_RUN_COMPLETED]),
        ("AUDITOR", [EXPERIMENT_ENDED, SEGMENT_RUN_COMPLETED]),
        ("STORE_MANAGER", [SEGMENT_RUN_COMPLETED]),
        ("INVENTORY_PLANNER", []),
        ("CUSTOMER", []),
        ("NO_SUCH_ROLE", []),
        (None, []),
    ],
)
def test_a_role_reads_the_kinds_whose_permission_it_holds(
    role: str | None, kinds: list[str]
) -> None:
    assert visible_kinds(role) == kinds


def test_only_the_administrator_reads_a_deactivation() -> None:
    assert [
        role for role in PERMISSIONS if USER_DEACTIVATED in visible_kinds(role)
    ] == ["ADMIN"]


def test_visible_kinds_is_derived_from_the_matrix_not_copied_from_it() -> None:
    for role in PERMISSIONS:
        held = permissions_for(role)
        assert set(visible_kinds(role)) == {
            kind
            for kind, permission in DELIVERY_PERMISSION.items()
            if permission in held
        }


@pytest.mark.parametrize("permission", sorted(ALL_PERMISSIONS))
def test_roles_holding_lists_exactly_the_roles_with_the_permission(
    permission: str,
) -> None:
    holders = roles_holding(permission)

    assert holders == sorted(holders)
    assert set(holders) == {
        role for role, held in PERMISSIONS.items() if permission in held
    }


def test_the_recipients_of_each_event_are_the_roles_that_may_read_it() -> None:
    """The write side addresses `roles_holding(permission)` and the read side
    filters by `visible_kinds(role)`: they must be the same set of roles."""
    for kind, permission in DELIVERY_PERMISSION.items():
        assert set(roles_holding(permission)) == {
            role for role in PERMISSIONS if kind in visible_kinds(role)
        }


# ---------- the badge ----------


@pytest.mark.parametrize(
    ("count", "text"),
    [
        (0, None),
        (-3, None),
        (1, "1"),
        (7, "7"),
        (BADGE_CAP, "99"),
        (BADGE_CAP + 1, "99+"),
        (5000, "99+"),
    ],
)
def test_the_badge_says_nothing_for_none_and_stops_counting_at_99(
    count: int, text: str | None
) -> None:
    assert badge_text(count) == text


def test_the_unread_count_is_asked_only_for_the_kinds_the_role_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    count = Mock(return_value=4)
    monkeypatch.setattr(notifications, "count_unread", count)
    connection = MagicMock()

    assert unread_count(connection, user_id=USER_ID, role_code="STORE_MANAGER") == 4

    count.assert_called_once_with(
        connection, user_id=USER_ID, kinds=[SEGMENT_RUN_COMPLETED]
    )


# ---------- the words ----------


def test_a_finished_sales_load_says_how_many_rows_were_accepted_and_rejected() -> None:
    item = describe(row(SALES_LOAD_FINISHED))

    assert item.label == "Sales load finished"
    assert item.message == (
        "The sales load “september.csv” finished: 1240 rows accepted, 3 rejected."
    )
    assert (item.link_endpoint, item.link_arguments) == (
        "sales_import.detail",
        {"load_id": 7},
    )


def test_one_accepted_row_is_not_plural() -> None:
    item = describe(
        row(SALES_LOAD_FINISHED, sales_load_accepted=1, sales_load_rejected=0)
    )

    assert "1 row accepted, 0 rejected." in item.message


def test_a_completed_run_names_its_method_and_how_many_customers() -> None:
    item = describe(row(SEGMENT_RUN_COMPLETED))

    assert item.label == "Segmentation run completed"
    assert (
        item.message == "Segmentation run #12 (RFM_RULES) completed for 30 customers."
    )
    assert (item.link_endpoint, item.link_arguments) == (
        "run_history.detail",
        {"run_id": 12},
    )


def test_an_ended_experiment_names_it_and_its_end_date() -> None:
    item = describe(row(EXPERIMENT_ENDED))

    assert item.label == "Experiment ended"
    assert item.message == (
        "The experiment “Free shipping” reached its end date, 2026-10-14."
    )
    assert (item.link_endpoint, item.link_arguments) == (
        "experiments.uplift",
        {"experiment_id": 5},
    )


def test_a_deactivation_names_the_user() -> None:
    item = describe(row(USER_DEACTIVATED))

    assert item.label == "User deactivated"
    assert item.message == "Demo User 9 was deactivated."
    assert item.link_endpoint == "admin.edit_user_view"
    assert item.link_arguments == {
        "user_id": UUID("11111111-1111-1111-1111-000000000009")
    }


def test_the_sentence_follows_the_subject_and_is_never_stored() -> None:
    """A corrected count reads correctly the next time: nothing was kept."""
    before = describe(row(SALES_LOAD_FINISHED, sales_load_accepted=10))
    after = describe(row(SALES_LOAD_FINISHED, sales_load_accepted=12))

    assert "10 rows accepted" in before.message
    assert "12 rows accepted" in after.message


def test_a_kind_the_service_does_not_know_is_refused_loudly() -> None:
    with pytest.raises(ValueError, match="Unknown notification kind"):
        describe(replace(row(SALES_LOAD_FINISHED), kind="SOMETHING_ELSE"))


def test_an_unread_notification_says_so() -> None:
    unread = row(SEGMENT_RUN_COMPLETED)

    assert describe(unread).is_unread is True
    assert describe(replace(unread, read_at=WHEN)).is_unread is False


# ---------- the inbox ----------


@pytest.fixture
def reads(monkeypatch: pytest.MonkeyPatch) -> dict[str, Mock]:
    fakes = {
        "count_notifications": Mock(return_value=45),
        "count_unread": Mock(return_value=3),
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


def test_the_inbox_is_twenty_to_a_page(reads: dict[str, Mock]) -> None:
    assert PAGE_SIZE == 20

    inbox = read_inbox(MagicMock(), user_id=USER_ID, role_code="ANALYST", page=3)

    kwargs = reads["list_notifications"].call_args.kwargs
    assert (kwargs["limit"], kwargs["offset"]) == (20, 40)
    assert (inbox.page, inbox.page_count, inbox.total) == (3, 3, 45)
    assert inbox.has_previous and not inbox.has_next


def test_the_inbox_reads_only_what_the_role_may_and_only_the_users_own(
    reads: dict[str, Mock],
) -> None:
    read_inbox(MagicMock(), user_id=USER_ID, role_code="STORE_MANAGER", page=1)

    for name in ("count_notifications", "count_unread", "list_notifications"):
        kwargs = reads[name].call_args.kwargs
        assert kwargs["user_id"] == USER_ID
        assert kwargs["kinds"] == [SEGMENT_RUN_COMPLETED]


def test_the_inbox_arrives_in_words_in_the_order_it_was_read(
    reads: dict[str, Mock],
) -> None:
    inbox = read_inbox(MagicMock(), user_id=USER_ID, role_code="ANALYST", page=1)

    assert [item.notification_id for item in inbox.items] == [9, 8]
    assert [item.kind for item in inbox.items] == [
        SEGMENT_RUN_COMPLETED,
        EXPERIMENT_ENDED,
    ]
    assert inbox.unread == 3


def test_a_page_below_one_is_the_first(reads: dict[str, Mock]) -> None:
    inbox = read_inbox(MagicMock(), user_id=USER_ID, role_code="ANALYST", page=0)

    assert inbox.page == 1
    assert reads["list_notifications"].call_args.kwargs["offset"] == 0


def test_an_empty_inbox_has_one_page(reads: dict[str, Mock]) -> None:
    reads["count_notifications"].return_value = 0
    reads["list_notifications"].return_value = []

    inbox = read_inbox(MagicMock(), user_id=USER_ID, role_code="ANALYST", page=1)

    assert (inbox.items, inbox.total, inbox.page_count) == ((), 0, 1)


@pytest.mark.parametrize(
    ("role", "receives"),
    [("ADMIN", True), ("STORE_MANAGER", True), ("INVENTORY_PLANNER", False)],
)
def test_a_role_that_is_sent_nothing_is_told_so(
    reads: dict[str, Mock], role: str, receives: bool
) -> None:
    inbox = read_inbox(MagicMock(), user_id=USER_ID, role_code=role, page=1)

    assert inbox.receives_any is receives
