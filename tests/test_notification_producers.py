"""The three events a request causes are written in that request's own
transaction (AN-04, #419; ADR-0047, ADR-0014, RN-61).

The data-access calls are replaced, so each test states what the producer did
and in what order. Against PostgreSQL, with the application's own role, the same
producers are run in tests/test_notification_producers_db.py.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

from tests.test_single_administrator import _ADMIN_ID, _ANALYST_ID, _Connection
from web.db import notifications as notifications_db
from web.db.transactions import atomic
from web.services import notifications, segmentation, users
from web.services.notifications import (
    notify_sales_load,
    notify_segment_run,
    notify_user_deactivated,
)
from web.services.sales_csv import UnsupportedContractVersion, load_and_record_sales_csv
from web.services.segmentation import (
    Assignment,
    MethodAdapter,
    MethodOutput,
    run_method,
)
from web.services.users import SingleAdministratorError, set_active

ROOT = Path(__file__).resolve().parents[1]


def _cursor(connection: MagicMock) -> MagicMock:
    return connection.cursor.return_value.__enter__.return_value


# ---------- the statements ----------


def test_an_event_is_written_with_exactly_one_subject_and_returns_its_id() -> None:
    connection = MagicMock()
    _cursor(connection).fetchone.return_value = (41,)

    notification_id = notifications_db.insert_notification(connection, run_id=12)

    statement, parameters = _cursor(connection).execute.call_args.args
    assert "INSERT INTO notification" in statement
    assert "RETURNING notification_id" in statement
    assert parameters == (None, 12, None, None)
    assert notification_id == 41
    connection.commit.assert_not_called()


def test_a_user_subject_is_passed_as_text() -> None:
    connection = MagicMock()
    _cursor(connection).fetchone.return_value = (1,)

    notifications_db.insert_notification(connection, subject_user_id=_ANALYST_ID)

    assert _cursor(connection).execute.call_args.args[1] == (
        None,
        None,
        None,
        str(_ANALYST_ID),
    )


def test_recipients_are_the_active_users_of_the_given_roles_and_nobody_else() -> None:
    connection = MagicMock()
    _cursor(connection).rowcount = 5

    written = notifications_db.insert_recipients(
        connection, notification_id=41, role_codes=["ADMIN", "ANALYST"]
    )

    statement, parameters = _cursor(connection).execute.call_args.args
    assert "INSERT INTO notification_recipient" in statement
    assert "u.is_active" in statement
    assert "r.code = ANY(%s::text[])" in statement
    assert parameters == (41, ["ADMIN", "ANALYST"])
    assert written == 5
    connection.commit.assert_not_called()


def test_no_statement_here_is_built_from_a_value() -> None:
    source = (ROOT / "web/db/notifications.py").read_text()

    assert not re.search(r"execute\(\s*f[\"']", source)
    assert "%s" in source and ".format(" not in source


# ---------- who is told of what ----------


@pytest.fixture
def writes(monkeypatch: pytest.MonkeyPatch) -> dict[str, Mock]:
    fakes = {
        "insert_notification": Mock(return_value=41),
        "insert_recipients": Mock(return_value=4),
    }
    for name, fake in fakes.items():
        monkeypatch.setattr(notifications, name, fake)
    return fakes


@pytest.mark.parametrize(
    ("notify", "subject", "subject_arguments", "roles"),
    [
        (notify_sales_load, 7, {"sales_load_id": 7}, ["ADMIN"]),
        (
            notify_segment_run,
            12,
            {"run_id": 12},
            ["ADMIN", "ANALYST", "AUDITOR", "MARKETING", "STORE_MANAGER"],
        ),
        (
            notify_user_deactivated,
            _ANALYST_ID,
            {"subject_user_id": _ANALYST_ID},
            ["ADMIN"],
        ),
    ],
    ids=["sales load", "segmentation run", "user deactivated"],
)
def test_each_event_is_written_to_the_roles_that_hold_its_permission(
    writes: dict[str, Mock], notify, subject, subject_arguments, roles
) -> None:
    connection = MagicMock()

    recipients = notify(connection, subject)

    assert recipients == 4
    kwargs = writes["insert_notification"].call_args.kwargs
    assert {k: v for k, v in kwargs.items() if v is not None} == subject_arguments
    assert writes["insert_recipients"].call_args.kwargs == {
        "notification_id": 41,
        "role_codes": roles,
    }


def test_the_actor_is_among_the_recipients_because_they_hold_the_permission() -> None:
    """The three events are caused by the administrator, who holds all three
    delivery permissions, so being the actor never takes them off the list."""
    for permission_roles in (
        notifications.roles_holding(notifications.SALES_INGEST_EXECUTE),
        notifications.roles_holding(notifications.SEGMENT_READ),
        notifications.roles_holding(notifications.USER_WRITE),
    ):
        assert "ADMIN" in permission_roles


def test_the_event_is_written_before_its_recipients(
    writes: dict[str, Mock],
) -> None:
    order: list[str] = []
    writes["insert_notification"].side_effect = (
        lambda *a, **k: order.append("event") or 41
    )
    writes["insert_recipients"].side_effect = (
        lambda *a, **k: order.append("recipients") or 1
    )

    notify_segment_run(MagicMock(), 12)

    assert order == ["event", "recipients"]


def test_an_event_nobody_can_be_told_of_is_still_an_event(
    writes: dict[str, Mock],
) -> None:
    writes["insert_recipients"].return_value = 0

    assert notify_sales_load(MagicMock(), 7) == 0
    writes["insert_notification"].assert_called_once()


def test_the_write_is_logged_without_naming_anyone(
    writes: dict[str, Mock], caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    with caplog.at_level(logging.INFO):
        notify_segment_run(MagicMock(), 12)

    assert (
        "notification_written kind=SEGMENT_RUN_COMPLETED notification=41 recipients=4"
        in (caplog.text)
    )


# ---------- inside the caller's transaction (RN-61) ----------


def test_a_notification_joins_its_callers_transaction_and_never_commits_alone(
    writes: dict[str, Mock],
) -> None:
    connection = MagicMock()

    @atomic
    def operation(connection: MagicMock) -> None:
        notify_segment_run(connection, 12)
        connection.commit.assert_not_called()

    operation(connection)

    connection.commit.assert_called_once_with()


def test_a_notification_that_cannot_be_written_rolls_its_operation_back(
    writes: dict[str, Mock],
) -> None:
    writes["insert_recipients"].side_effect = RuntimeError("disk full")
    connection = MagicMock()
    undone: list[str] = []

    @atomic
    def operation(connection: MagicMock) -> None:
        undone.append("the business write")
        notify_segment_run(connection, 12)

    with pytest.raises(RuntimeError, match="disk full"):
        operation(connection)

    connection.rollback.assert_called_once_with()
    connection.commit.assert_not_called()


# ---------- a sales load ----------


def _csv(tmp_path: Path, rows: list[str]) -> Path:
    path = tmp_path / "sales.csv"
    header = (
        "transaction_id,customer_id,store_id,channel_id,occurred_at,"
        "product_id,quantity,unit_price"
    )
    path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
    return path


_ROWS = [
    "T1,c,1,1,2026-01-15T10:00:00+00:00,1,2,9.99",
    "T2,c,1,1,2026-01-15T10:00:00+00:00,1,two,9.99",
]


@pytest.fixture
def sales(monkeypatch: pytest.MonkeyPatch) -> dict[str, Mock]:
    fakes = {
        "insert_sales_load": Mock(return_value=7),
        "notify_sales_load": Mock(return_value=1),
    }
    for name, fake in fakes.items():
        monkeypatch.setattr(f"web.services.sales_csv.{name}", fake)
    return fakes


def test_a_recorded_load_is_announced_in_the_transaction_that_records_it(
    tmp_path: Path, sales: dict[str, Mock]
) -> None:
    connection = MagicMock()
    order: list[str] = []
    sales["insert_sales_load"].side_effect = lambda *a, **k: order.append("load") or 7
    sales["notify_sales_load"].side_effect = lambda *a, **k: order.append("notify") or 1
    connection.commit.side_effect = lambda: order.append("commit")

    _, load_id = load_and_record_sales_csv(
        connection,
        _csv(tmp_path, _ROWS),
        filename="sales.csv",
        contract_version=1,
        loaded_by="11111111-1111-1111-1111-000000000001",
        ingest=lambda connection, row: None,
    )

    assert load_id == 7
    sales["notify_sales_load"].assert_called_once_with(connection, 7)
    assert order == ["load", "notify", "commit"]


def test_a_load_that_cannot_be_announced_is_not_recorded(
    tmp_path: Path, sales: dict[str, Mock]
) -> None:
    sales["notify_sales_load"].side_effect = RuntimeError("disk full")
    connection = MagicMock()

    with pytest.raises(RuntimeError, match="disk full"):
        load_and_record_sales_csv(
            connection,
            _csv(tmp_path, _ROWS),
            filename="sales.csv",
            contract_version=1,
            loaded_by=None,
            ingest=lambda connection, row: None,
        )

    connection.rollback.assert_called_once_with()
    connection.commit.assert_not_called()


@pytest.mark.parametrize(
    "bad_file",
    ["wrong header", "not utf-8", "wrong contract version"],
)
def test_a_refused_file_announces_nothing(
    tmp_path: Path, sales: dict[str, Mock], bad_file: str
) -> None:
    path = tmp_path / "bad.csv"
    version = 1
    if bad_file == "wrong header":
        path.write_text("a,b,c\n1,2,3\n", encoding="utf-8")
    elif bad_file == "not utf-8":
        path.write_bytes(b"transaction_id\n\xff\xfe\n")
    else:
        path.write_text("", encoding="utf-8")
        version = 2

    with pytest.raises(UnsupportedContractVersion):
        load_and_record_sales_csv(
            MagicMock(),
            path,
            filename="bad.csv",
            contract_version=version,
            loaded_by=None,
            ingest=lambda connection, row: None,
        )

    sales["insert_sales_load"].assert_not_called()
    sales["notify_sales_load"].assert_not_called()


# ---------- a segmentation run ----------


def _adapter(*customers: str) -> MethodAdapter:
    return MethodAdapter(
        "RFM_RULES",
        lambda connection, window: MethodOutput(
            assignments=tuple(Assignment(c, None) for c in customers)
        ),
    )


@pytest.fixture
def run_writes(monkeypatch: pytest.MonkeyPatch) -> Mock:
    manager = Mock()
    manager.read_open_assignments.return_value = {}
    manager.create_run.return_value = 12
    for name in (
        "read_open_assignments",
        "create_run",
        "close_open_assignments",
        "insert_assignments",
        "notify_segment_run",
    ):
        monkeypatch.setattr(segmentation, name, getattr(manager, name))
    monkeypatch.setattr(segmentation, "lock_for_run", Mock())
    return manager


def test_a_completed_run_is_announced_by_its_own_id_before_it_commits(
    run_writes: Mock,
) -> None:
    connection = MagicMock()
    order: list[str] = []
    run_writes.notify_segment_run.side_effect = lambda *a, **k: order.append("notify")
    connection.commit.side_effect = lambda: order.append("commit")

    result = run_method(connection, "RFM_RULES", 180, adapter=_adapter("a", "b"))

    run_writes.notify_segment_run.assert_called_once_with(connection, 12)
    assert result.run_id == 12
    assert order == ["notify", "commit"]


def test_a_run_that_cannot_be_announced_is_rolled_back(run_writes: Mock) -> None:
    run_writes.notify_segment_run.side_effect = RuntimeError("disk full")
    connection = MagicMock()

    with pytest.raises(RuntimeError, match="disk full"):
        run_method(connection, "RFM_RULES", 180, adapter=_adapter("a"))

    connection.rollback.assert_called_once_with()
    connection.commit.assert_not_called()


def test_a_failed_run_announces_nothing(run_writes: Mock) -> None:
    def refuse(connection, window):
        raise RuntimeError("the adapter failed")

    with pytest.raises(RuntimeError):
        run_method(
            MagicMock(), "RFM_RULES", 180, adapter=MethodAdapter("RFM_RULES", refuse)
        )

    run_writes.create_run.assert_not_called()
    run_writes.notify_segment_run.assert_not_called()


def test_a_refused_method_announces_nothing(run_writes: Mock) -> None:
    with pytest.raises(segmentation.UnknownMethod):
        run_method(MagicMock(), "NO_SUCH_METHOD", 180)

    run_writes.notify_segment_run.assert_not_called()


# ---------- a deactivation made in the web ----------


@pytest.fixture
def deactivation(monkeypatch: pytest.MonkeyPatch) -> Mock:
    notify = Mock(return_value=1)
    monkeypatch.setattr(users, "notify_user_deactivated", notify)
    return notify


def test_deactivating_a_user_announces_it_once_with_the_users_id(
    deactivation: Mock,
) -> None:
    connection = _Connection(administrators=1)

    set_active(connection, _ANALYST_ID, False)

    deactivation.assert_called_once_with(connection, _ANALYST_ID)
    assert connection.commits == 1


def test_activating_a_user_announces_nothing(deactivation: Mock) -> None:
    set_active(_Connection(administrators=1), _ANALYST_ID, True)

    deactivation.assert_not_called()


def test_deactivating_an_account_that_is_already_inactive_announces_nothing(
    deactivation: Mock,
) -> None:
    from tests.test_single_administrator import _user

    inactive = replace(_user(_ANALYST_ID, "ANALYST"), is_active=False)
    connection = _Connection(
        administrators=1,
        users={_ADMIN_ID: _user(_ADMIN_ID, "ADMIN"), _ANALYST_ID: inactive},
    )

    set_active(connection, _ANALYST_ID, False)

    deactivation.assert_not_called()


def test_a_refused_deactivation_announces_nothing(deactivation: Mock) -> None:
    connection = _Connection(administrators=1)

    with pytest.raises(SingleAdministratorError):
        set_active(connection, _ADMIN_ID, False)

    deactivation.assert_not_called()
    assert connection.rollbacks == 1


def test_a_deactivation_that_cannot_be_announced_is_rolled_back(
    deactivation: Mock,
) -> None:
    deactivation.side_effect = RuntimeError("disk full")
    connection = _Connection(administrators=1)

    with pytest.raises(RuntimeError):
        set_active(connection, _ANALYST_ID, False)

    assert (connection.commits, connection.rollbacks) == (0, 1)


def test_the_bulk_deactivation_of_demonstration_accounts_announces_nothing(
    deactivation: Mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The command line's `--deactivate-demo-accounts` is not a web action
    (Q-14): it changes many accounts at once and tells nobody."""
    monkeypatch.setattr(users, "install_administrator", Mock())
    monkeypatch.setattr(
        users, "deactivate_demonstration_accounts", Mock(return_value=["a@x", "b@x"])
    )

    users.provision_administrator(
        MagicMock(),
        name="Real Admin",
        email="admin@example.test",
        password="a-long-enough-password",
        deactivate_demo_accounts=True,
    )

    deactivation.assert_not_called()


def test_only_the_web_services_deactivation_writes_a_notification() -> None:
    """Q-14: no other path, and in particular not the command line and not a
    database trigger, creates this story's notification."""
    callers = sorted(
        str(path.relative_to(ROOT))
        for path in (ROOT / "web").rglob("*.py")
        if "notify_user_deactivated" in path.read_text()
    )

    assert callers == ["web/services/notifications.py", "web/services/users.py"]
    assert "notif" not in (ROOT / "web/cli.py").read_text().lower()
    schema = (ROOT / "sql/01_schema.sql").read_text()
    triggers_on_app_user = re.findall(
        r"CREATE (?:OR REPLACE )?TRIGGER (\w+)\s+(?:AFTER|BEFORE)"
        r"[^;]*?ON (?:public\.)?app_user\b",
        schema,
    )
    # users-service's capture (#421) records access changes in its outbox for
    # auth-service; it writes no notification.
    assert triggers_on_app_user == [
        "trg_audit_app_user",
        "trg_users_capture_access_changed",
    ]
    capture = schema.split(
        "CREATE OR REPLACE FUNCTION users_events.capture_access_changed()", 1
    )[1].split("$capture$;", 1)[0]
    assert "notification" not in capture.lower()
