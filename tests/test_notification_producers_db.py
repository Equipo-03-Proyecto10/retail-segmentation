"""The three producers, run as the application's own role against PostgreSQL
(AN-04, #419; RN-61).

Each service is called with a real connection as `retail_app`, so a missing
privilege, a wrong column or a recipient query that reads the wrong rows fails
here and not on the instance. The services commit; to leave the shared test
database as it was, the connection is wrapped so that its `commit()` does nothing
and the test rolls everything back at the end. A `rollback()` the service itself
asks for still reaches the real connection, which is what the failure tests check.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import Mock

import psycopg
import pytest

from web.db.audit import set_audit_actor
from web.services import notifications, segmentation
from web.services.notifications import (
    SALES_LOAD_FINISHED,
    SEGMENT_RUN_COMPLETED,
    USER_DEACTIVATED,
    read_inbox,
)
from web.services.sales_csv import load_and_record_sales_csv
from web.services.users import SingleAdministratorError, set_active

pytestmark = pytest.mark.postgres

ADMIN = "11111111-1111-1111-1111-000000000001"
ANALYST = "11111111-1111-1111-1111-000000000002"
STORE_MANAGER = "11111111-1111-1111-1111-000000000003"
INVENTORY_PLANNER = "11111111-1111-1111-1111-000000000005"


class _Uncommitted:
    """A real connection whose commit() is switched off."""

    def __init__(self, connection: psycopg.Connection) -> None:
        self._connection = connection

    def commit(self) -> None:
        return None

    def __getattr__(self, name: str):
        return getattr(self._connection, name)


@pytest.fixture
def app_connection(app_url: str) -> Iterator[_Uncommitted]:
    with psycopg.connect(app_url) as connection:
        try:
            yield _Uncommitted(connection)
        finally:
            connection.rollback()


def _one(connection, sql: str, params: tuple = ()):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchone()[0]


def _recipients(connection, notification_id: int) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT user_id::text FROM notification_recipient"
            " WHERE notification_id = %s",
            (notification_id,),
        )
        return {row[0] for row in cursor.fetchall()}


def _active_users_of(connection, roles: list[str]) -> set[str]:
    """The expected recipients, written independently of the service."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT u.user_id::text FROM app_user u JOIN role r USING (role_id)"
            " WHERE u.is_active AND r.code = ANY(%s)",
            (roles,),
        )
        return {row[0] for row in cursor.fetchall()}


def _count_events(connection, kind: str) -> int:
    return _one(
        connection, "SELECT count(*) FROM notification WHERE kind = %s", (kind,)
    )


# ---------- a sales load ----------

_HEADER = (
    "transaction_id,customer_id,store_id,channel_id,occurred_at,"
    "product_id,quantity,unit_price"
)


def _csv(tmp_path: Path) -> Path:
    path = tmp_path / "may.csv"
    path.write_text(
        "\n".join(
            [
                _HEADER,
                "T1,c,1,1,2026-01-15T10:00:00+00:00,1,2,9.99",
                "T2,c,1,1,2026-01-15T10:00:00+00:00,1,two,9.99",
                "T3,c,1,1,2026-01-15T10:00:00+00:00,1,1,5.00",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def test_a_load_is_announced_to_the_administrator_who_may_ingest_sales(
    app_connection, tmp_path
) -> None:
    set_audit_actor(app_connection, ADMIN)

    report, load_id = load_and_record_sales_csv(
        app_connection,
        _csv(tmp_path),
        filename="may.csv",
        contract_version=1,
        loaded_by=ADMIN,
        ingest=lambda connection, row: None,
    )

    notification_id = _one(
        app_connection,
        "SELECT notification_id FROM notification WHERE sales_load_id = %s",
        (load_id,),
    )
    assert (
        _one(
            app_connection,
            "SELECT kind FROM notification WHERE notification_id = %s",
            (notification_id,),
        )
        == SALES_LOAD_FINISHED
    )
    assert _recipients(app_connection, notification_id) == {ADMIN}
    # The words the administrator reads: how many were accepted and rejected.
    inbox = read_inbox(app_connection, user_id=ADMIN, role_code="ADMIN", page=1)
    assert inbox.items[0].message == (
        "The sales load “may.csv” finished: 2 rows accepted, 1 rejected."
    )
    assert (report.accepted, report.rejected) == (2, 1)


def test_only_the_holders_of_the_ingest_permission_are_told_of_a_load(
    app_connection, tmp_path
) -> None:
    _, load_id = load_and_record_sales_csv(
        app_connection,
        _csv(tmp_path),
        filename="may.csv",
        contract_version=1,
        loaded_by=ADMIN,
        ingest=lambda connection, row: None,
    )

    notification_id = _one(
        app_connection,
        "SELECT notification_id FROM notification WHERE sales_load_id = %s",
        (load_id,),
    )
    assert _recipients(app_connection, notification_id) == _active_users_of(
        app_connection, ["ADMIN"]
    )
    for role_user in (ANALYST, STORE_MANAGER, INVENTORY_PLANNER):
        assert role_user not in _recipients(app_connection, notification_id)


def test_a_load_whose_notification_fails_is_not_recorded(
    app_connection, tmp_path, monkeypatch
) -> None:
    loads_before = _one(app_connection, "SELECT count(*) FROM sales_load")
    events_before = _count_events(app_connection, SALES_LOAD_FINISHED)
    monkeypatch.setattr(
        notifications, "insert_recipients", Mock(side_effect=RuntimeError("boom"))
    )

    with pytest.raises(RuntimeError, match="boom"):
        load_and_record_sales_csv(
            app_connection,
            _csv(tmp_path),
            filename="may.csv",
            contract_version=1,
            loaded_by=ADMIN,
            ingest=lambda connection, row: None,
        )

    # The service's own rollback reached the database: neither row exists.
    assert _one(app_connection, "SELECT count(*) FROM sales_load") == loads_before
    assert _count_events(app_connection, SALES_LOAD_FINISHED) == events_before


# ---------- a segmentation run ----------


def test_a_run_is_announced_to_every_active_holder_of_segment_read(
    app_connection,
) -> None:
    set_audit_actor(app_connection, ADMIN)
    expected = _active_users_of(
        app_connection, ["ADMIN", "ANALYST", "STORE_MANAGER", "MARKETING", "AUDITOR"]
    )

    result = segmentation.run(app_connection, 180)

    notification_id = _one(
        app_connection,
        "SELECT notification_id FROM notification WHERE run_id = %s",
        (result.run_id,),
    )
    recipients = _recipients(app_connection, notification_id)
    assert recipients == expected and len(recipients) == 21
    assert ADMIN in recipients  # the actor, who executed the run
    assert INVENTORY_PLANNER not in recipients
    inbox = read_inbox(app_connection, user_id=ANALYST, role_code="ANALYST", page=1)
    assert inbox.items[0].message == (
        f"Segmentation run #{result.run_id} (RFM_RULES) completed for 30 customers."
    )
    assert inbox.items[0].is_unread


def test_a_deactivated_account_is_not_told_of_a_run(app_connection) -> None:
    set_active(app_connection, ANALYST, False)

    result = segmentation.run(app_connection, 180)

    notification_id = _one(
        app_connection,
        "SELECT notification_id FROM notification WHERE run_id = %s",
        (result.run_id,),
    )
    recipients = _recipients(app_connection, notification_id)
    assert ANALYST not in recipients
    assert len(recipients) == 20


def test_the_recipients_are_a_snapshot_a_later_change_does_not_move(
    app_connection,
) -> None:
    result = segmentation.run(app_connection, 180)
    notification_id = _one(
        app_connection,
        "SELECT notification_id FROM notification WHERE run_id = %s",
        (result.run_id,),
    )
    before = _recipients(app_connection, notification_id)

    # An analyst is deactivated and a store manager is moved to a role that reads
    # no segments; neither change reaches the event that already happened.
    app_connection.execute(
        "UPDATE app_user SET is_active = FALSE WHERE user_id = %s", (ANALYST,)
    )
    app_connection.execute(
        "UPDATE app_user SET role_id = 5 WHERE user_id = %s", (STORE_MANAGER,)
    )

    assert _recipients(app_connection, notification_id) == before
    assert ANALYST in before and STORE_MANAGER in before
    # What the store manager's new role may read is another matter: it is checked
    # when they read, so the event is no longer shown to them.
    assert (
        read_inbox(
            app_connection, user_id=STORE_MANAGER, role_code="INVENTORY_PLANNER", page=1
        ).total
        == 0
    )


def test_a_run_whose_notification_fails_leaves_no_run(
    app_connection, monkeypatch
) -> None:
    runs_before = _one(app_connection, "SELECT count(*) FROM segmentation_run")
    events_before = _count_events(app_connection, SEGMENT_RUN_COMPLETED)
    monkeypatch.setattr(
        notifications, "insert_recipients", Mock(side_effect=RuntimeError("boom"))
    )

    with pytest.raises(RuntimeError, match="boom"):
        segmentation.run(app_connection, 180)

    assert _one(app_connection, "SELECT count(*) FROM segmentation_run") == runs_before
    assert _count_events(app_connection, SEGMENT_RUN_COMPLETED) == events_before


# ---------- a deactivation ----------


def test_a_deactivation_is_announced_to_the_administrator_and_names_the_user(
    app_connection,
) -> None:
    set_audit_actor(app_connection, ADMIN)

    set_active(app_connection, ANALYST, False)

    notification_id = _one(
        app_connection,
        "SELECT notification_id FROM notification WHERE subject_user_id = %s",
        (ANALYST,),
    )
    assert (
        _one(
            app_connection,
            "SELECT kind FROM notification WHERE notification_id = %s",
            (notification_id,),
        )
        == USER_DEACTIVATED
    )
    assert _recipients(app_connection, notification_id) == {ADMIN}
    inbox = read_inbox(app_connection, user_id=ADMIN, role_code="ADMIN", page=1)
    assert inbox.items[0].message == "Demo User 2 was deactivated."
    # Nobody else may read it: not the user it is about, not another role.
    for user, role in ((ANALYST, "ANALYST"), (STORE_MANAGER, "STORE_MANAGER")):
        assert (
            read_inbox(app_connection, user_id=user, role_code=role, page=1)
            .items[0]
            .kind
            != USER_DEACTIVATED
        )


def test_a_user_deactivated_twice_is_two_events_but_an_inactive_one_is_not(
    app_connection,
) -> None:
    set_active(app_connection, ANALYST, False)
    # Deactivating an account that is already inactive changes nothing.
    set_active(app_connection, ANALYST, False)
    assert _count_events(app_connection, USER_DEACTIVATED) == 1

    set_active(app_connection, ANALYST, True)
    assert _count_events(app_connection, USER_DEACTIVATED) == 1

    set_active(app_connection, ANALYST, False)
    assert _count_events(app_connection, USER_DEACTIVATED) == 2


def test_the_sole_administrator_cannot_be_deactivated_and_nothing_is_announced(
    app_connection,
) -> None:
    with pytest.raises(SingleAdministratorError):
        set_active(app_connection, ADMIN, False)

    assert _count_events(app_connection, USER_DEACTIVATED) == 0
    assert _one(
        app_connection, "SELECT is_active FROM app_user WHERE user_id = %s", (ADMIN,)
    )


def test_a_deactivation_whose_notification_fails_leaves_the_account_active(
    app_connection, monkeypatch
) -> None:
    monkeypatch.setattr(
        notifications, "insert_notification", Mock(side_effect=RuntimeError("boom"))
    )

    with pytest.raises(RuntimeError, match="boom"):
        set_active(app_connection, ANALYST, False)

    assert _one(
        app_connection, "SELECT is_active FROM app_user WHERE user_id = %s", (ANALYST,)
    )
    assert _count_events(app_connection, USER_DEACTIVATED) == 0
