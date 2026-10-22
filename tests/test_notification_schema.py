"""The notification tables hold their shape in the database itself (#419).

ADR-0047 stores an event once (`notification`, with one subject out of four) and
each recipient's read state beside it (`notification_recipient`). What these
tests state is what the schema refuses and what the seed contains, against a
database loaded with the three scripts. Every statement runs in a transaction
that is rolled back, so the shared test database keeps its rows.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.errors import CheckViolation, NotNullViolation, UniqueViolation

from web.middleware.authz import (
    CAMPAIGN_READ,
    PERMISSIONS,
    SEGMENT_READ,
)

pytestmark = pytest.mark.postgres

ROOT = Path(__file__).resolve().parents[1]

# The delivery permission of each event the seed contains (ADR-0047). The seed
# writes the roles out; this is where it is compared with the matrix.
SEEDED_DELIVERY = {
    "EXPERIMENT_ENDED": CAMPAIGN_READ,
    "SEGMENT_RUN_COMPLETED": SEGMENT_READ,
}


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


def _new_user(connection: psycopg.Connection) -> str:
    """A throwaway account in a role that is not the administrator's."""
    user_id = str(uuid4())
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO app_user (user_id, role_id, name, email, password_hash)"
            " VALUES (%s, 2, 'Scratch', %s, 'x')",
            (user_id, f"{user_id}@scratch.example"),
        )
    return user_id


def _new_sales_load(connection: psycopg.Connection) -> int:
    return _one(
        connection,
        "INSERT INTO sales_load (filename, contract_version, received_count,"
        " accepted_count, rejected_count) VALUES ('scratch.csv', 1, 0, 0, 0)"
        " RETURNING load_id",
    )


# One literal statement per subject: the SQL is never built from a name.
_INSERT_EVENT = {
    "sales_load_id": (
        "INSERT INTO notification (sales_load_id) VALUES (%s)"
        " RETURNING notification_id"
    ),
    "run_id": "INSERT INTO notification (run_id) VALUES (%s) RETURNING notification_id",
    "experiment_id": (
        "INSERT INTO notification (experiment_id) VALUES (%s)"
        " RETURNING notification_id"
    ),
    "subject_user_id": (
        "INSERT INTO notification (subject_user_id) VALUES (%s)"
        " RETURNING notification_id"
    ),
}


def _notify(connection: psycopg.Connection, column: str, value) -> int:
    return _one(connection, _INSERT_EVENT[column], (value,))


# ---------- the subject is an exclusive arc ----------


def test_a_notification_with_no_subject_is_refused(owner: psycopg.Connection) -> None:
    """With nothing to derive `kind` from, its NOT NULL fires before the arc
    check does: the row cannot exist either way."""
    with pytest.raises(NotNullViolation) as refusal:
        with owner.transaction():
            owner.execute("INSERT INTO notification DEFAULT VALUES")
    assert refusal.value.diag.column_name == "kind"


def test_a_notification_with_two_subjects_is_refused_by_the_arc_check(
    owner: psycopg.Connection,
) -> None:
    with pytest.raises(CheckViolation) as refusal:
        with owner.transaction():
            owner.execute(
                "INSERT INTO notification (run_id, experiment_id)"
                " SELECT (SELECT min(run_id) FROM segmentation_run),"
                "        (SELECT max(experiment_id) FROM experiment)"
            )
    assert refusal.value.diag.constraint_name == "notification_one_subject"


def _new_subject(connection: psycopg.Connection, column: str):
    """A subject that has no notification yet: the seed announced every run and
    every ended experiment it holds, so those two are made."""
    if column == "sales_load_id":
        return _new_sales_load(connection)
    if column == "run_id":
        return _one(
            connection,
            "INSERT INTO segmentation_run (method, window_days, customer_count,"
            " data_origin) VALUES ('RFM_RULES', 30, 0, 'SEEDED') RETURNING run_id",
        )
    if column == "experiment_id":
        return _one(
            connection,
            "INSERT INTO experiment (experiment_id, name, target_metric, starts_on,"
            " conversion_window_days, data_origin)"
            " VALUES (9001, 'Scratch', 'CONVERSION', CURRENT_DATE, 7, 'SEEDED')"
            " RETURNING experiment_id",
        )
    return _new_user(connection)


@pytest.mark.parametrize(
    ("column", "kind"),
    [
        ("sales_load_id", "SALES_LOAD_FINISHED"),
        ("run_id", "SEGMENT_RUN_COMPLETED"),
        ("experiment_id", "EXPERIMENT_ENDED"),
        ("subject_user_id", "USER_DEACTIVATED"),
    ],
)
def test_kind_follows_whichever_subject_is_set(
    owner: psycopg.Connection, column: str, kind: str
) -> None:
    notification_id = _notify(owner, column, _new_subject(owner, column))

    assert (
        _one(
            owner,
            "SELECT kind FROM notification WHERE notification_id = %s",
            (notification_id,),
        )
        == kind
    )


# ---------- an event is announced once ----------


def test_a_sales_load_is_announced_once(owner: psycopg.Connection) -> None:
    load_id = _new_sales_load(owner)
    _notify(owner, "sales_load_id", load_id)

    with pytest.raises(UniqueViolation) as refusal:
        with owner.transaction():
            _notify(owner, "sales_load_id", load_id)
    assert refusal.value.diag.constraint_name == "notification_sales_load_id_key"


def test_a_run_is_announced_once(owner: psycopg.Connection) -> None:
    with pytest.raises(UniqueViolation) as refusal:
        with owner.transaction():
            _notify(
                owner,
                "run_id",
                _one(owner, "SELECT min(run_id) FROM segmentation_run"),
            )
    assert refusal.value.diag.constraint_name == "notification_run_id_key"


def test_an_experiment_is_announced_once(owner: psycopg.Connection) -> None:
    with pytest.raises(UniqueViolation) as refusal:
        with owner.transaction():
            _notify(
                owner,
                "experiment_id",
                _one(owner, "SELECT min(experiment_id) FROM notification"),
            )
    assert refusal.value.diag.constraint_name == "notification_experiment_id_key"


def test_a_user_can_be_deactivated_more_than_once(owner: psycopg.Connection) -> None:
    user_id = _new_user(owner)

    first = _notify(owner, "subject_user_id", user_id)
    second = _notify(owner, "subject_user_id", user_id)

    assert first != second


# ---------- a recipient hears of an event once ----------


def test_a_user_is_a_recipient_of_an_event_once(owner: psycopg.Connection) -> None:
    user_id = _new_user(owner)
    notification_id = _notify(owner, "subject_user_id", user_id)
    owner.execute(
        "INSERT INTO notification_recipient (notification_id, user_id)"
        " VALUES (%s, %s)",
        (notification_id, user_id),
    )

    with pytest.raises(UniqueViolation):
        with owner.transaction():
            owner.execute(
                "INSERT INTO notification_recipient (notification_id, user_id)"
                " VALUES (%s, %s)",
                (notification_id, user_id),
            )


def test_a_new_recipient_row_is_unread(owner: psycopg.Connection) -> None:
    user_id = _new_user(owner)
    notification_id = _notify(owner, "subject_user_id", user_id)
    owner.execute(
        "INSERT INTO notification_recipient (notification_id, user_id)"
        " VALUES (%s, %s)",
        (notification_id, user_id),
    )

    assert (
        _one(
            owner,
            "SELECT read_at IS NULL FROM notification_recipient"
            " WHERE notification_id = %s AND user_id = %s",
            (notification_id, user_id),
        )
        is True
    )


# ---------- what goes when the thing it names goes ----------


def test_deleting_an_event_deletes_its_recipients(owner: psycopg.Connection) -> None:
    notification_id = _one(
        owner,
        "SELECT min(notification_id) FROM notification"
        " WHERE kind = 'SEGMENT_RUN_COMPLETED'",
    )
    assert (
        _one(
            owner,
            "SELECT count(*) FROM notification_recipient WHERE notification_id = %s",
            (notification_id,),
        )
        > 0
    )

    owner.execute(
        "DELETE FROM notification WHERE notification_id = %s", (notification_id,)
    )

    assert (
        _one(
            owner,
            "SELECT count(*) FROM notification_recipient WHERE notification_id = %s",
            (notification_id,),
        )
        == 0
    )


def test_deleting_a_run_deletes_the_event_that_announced_it(
    owner: psycopg.Connection,
) -> None:
    run_id = _one(owner, "SELECT min(run_id) FROM segmentation_run")

    owner.execute("DELETE FROM segmentation_run WHERE run_id = %s", (run_id,))

    assert (
        _one(owner, "SELECT count(*) FROM notification WHERE run_id = %s", (run_id,))
        == 0
    )


def test_deleting_a_user_deletes_what_they_were_told_and_what_was_about_them(
    owner: psycopg.Connection,
) -> None:
    user_id = _new_user(owner)
    about_them = _notify(owner, "subject_user_id", user_id)
    owner.execute(
        "INSERT INTO notification_recipient (notification_id, user_id)"
        " SELECT notification_id, %s FROM notification"
        " WHERE kind = 'EXPERIMENT_ENDED' LIMIT 3",
        (user_id,),
    )

    owner.execute("DELETE FROM app_user WHERE user_id = %s", (user_id,))

    assert (
        _one(
            owner,
            "SELECT count(*) FROM notification_recipient WHERE user_id = %s",
            (user_id,),
        )
        == 0
    )
    assert (
        _one(
            owner,
            "SELECT count(*) FROM notification WHERE notification_id = %s",
            (about_them,),
        )
        == 0
    )


# ---------- the application role ----------


@pytest.mark.parametrize("table", ["notification", "notification_recipient"])
def test_the_application_role_has_default_dml_and_nothing_more(
    owner: psycopg.Connection, table: str
) -> None:
    privileges = {
        privilege: _one(
            owner,
            "SELECT has_table_privilege('retail_app', %s, %s)",
            (f"public.{table}", privilege),
        )
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")
    }

    assert privileges == {
        "SELECT": True,
        "INSERT": True,
        "UPDATE": True,
        "DELETE": True,
        "TRUNCATE": False,
    }


@pytest.mark.parametrize("table", ["notification", "notification_recipient"])
def test_no_service_role_reaches_a_notification_table(
    owner: psycopg.Connection, table: str
) -> None:
    reachable = _one(
        owner,
        "SELECT count(*) FROM pg_roles r"
        " WHERE r.rolname LIKE 'svc\\_%%'"
        "   AND has_any_column_privilege(r.oid, %s::regclass,"
        "       'SELECT, INSERT, UPDATE, REFERENCES')",
        (f"public.{table}",),
    )

    assert reachable == 0


def test_notifications_are_runtime_state_and_not_audited(
    owner: psycopg.Connection,
) -> None:
    audited = _one(
        owner,
        "SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid"
        " WHERE c.relname IN ('notification', 'notification_recipient')"
        "   AND NOT t.tgisinternal",
    )

    assert audited == 0


# ---------- the seed ----------


def test_both_tables_are_seeded_above_the_minimum_and_neither_is_exempt(
    owner: psycopg.Connection,
) -> None:
    exempt = {
        line.strip()
        for line in (ROOT / "sql/seed-exempt.txt").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    counts = {
        "notification": _one(owner, "SELECT count(*) FROM notification"),
        "notification_recipient": _one(
            owner, "SELECT count(*) FROM notification_recipient"
        ),
    }
    assert counts == {"notification": 54, "notification_recipient": 1019}
    for table, rows in counts.items():
        assert table not in exempt
        assert rows >= 30


def test_every_seeded_run_and_every_ended_seeded_experiment_has_one_event(
    owner: psycopg.Connection,
) -> None:
    unannounced_runs = _one(
        owner,
        "SELECT count(*) FROM segmentation_run r"
        " WHERE NOT EXISTS (SELECT 1 FROM notification n WHERE n.run_id = r.run_id)",
    )
    unannounced_experiments = _one(
        owner,
        "SELECT count(*) FROM experiment e WHERE e.ends_on < CURRENT_DATE"
        " AND NOT EXISTS (SELECT 1 FROM notification n"
        "                 WHERE n.experiment_id = e.experiment_id)",
    )

    assert (unannounced_runs, unannounced_experiments) == (0, 0)


def test_running_experiments_have_no_notification_and_no_event_is_in_the_future(
    owner: psycopg.Connection,
) -> None:
    assert (
        _one(
            owner,
            "SELECT count(*) FROM notification n"
            " JOIN experiment e USING (experiment_id)"
            " WHERE e.ends_on IS NULL OR e.ends_on >= CURRENT_DATE",
        )
        == 0
    )
    assert (
        _one(owner, "SELECT count(*) FROM notification WHERE created_at > now()") == 0
    )
    assert (
        _one(
            owner,
            "SELECT count(*) FROM notification_recipient nr"
            " JOIN notification n USING (notification_id)"
            " WHERE nr.read_at < n.created_at OR nr.read_at > now()",
        )
        == 0
    )


@pytest.mark.parametrize(("kind", "permission"), sorted(SEEDED_DELIVERY.items()))
def test_the_seeded_recipients_are_the_active_holders_of_the_delivery_permission(
    owner: psycopg.Connection, kind: str, permission: str
) -> None:
    holders = {role for role, held in PERMISSIONS.items() if permission in held}

    with owner.cursor() as cursor:
        cursor.execute(
            "SELECT DISTINCT r.code FROM notification_recipient nr"
            " JOIN notification n ON n.notification_id = nr.notification_id"
            " JOIN app_user u ON u.user_id = nr.user_id"
            " JOIN role r ON r.role_id = u.role_id WHERE n.kind = %s",
            (kind,),
        )
        recipient_roles = {row[0] for row in cursor.fetchall()}
        cursor.execute(
            "SELECT count(*) FROM notification n"
            " CROSS JOIN app_user u JOIN role r ON r.role_id = u.role_id"
            " WHERE n.kind = %s AND u.is_active AND r.code = ANY(%s)"
            "   AND NOT EXISTS (SELECT 1 FROM notification_recipient nr"
            "                   WHERE nr.notification_id = n.notification_id"
            "                     AND nr.user_id = u.user_id)",
            (kind, sorted(holders)),
        )
        missing = cursor.fetchone()[0]

    assert recipient_roles == holders
    assert missing == 0


def test_the_seed_reads_what_is_older_than_a_week_and_leaves_the_rest_unread(
    owner: psycopg.Connection,
) -> None:
    """A week before the seed ran, which is when its newest run was recorded:
    the test runs some seconds later, so it cannot use its own now()."""
    with owner.cursor() as cursor:
        cursor.execute(
            "WITH seeded AS (SELECT max(created_at) AS at FROM notification)"
            " SELECT bool_and(nr.read_at IS NOT NULL) FILTER"
            "          (WHERE n.created_at < seeded.at - interval '7 days'),"
            "        bool_and(nr.read_at IS NULL) FILTER"
            "          (WHERE n.created_at >= seeded.at - interval '7 days')"
            " FROM notification_recipient nr"
            " JOIN notification n ON n.notification_id = nr.notification_id"
            " CROSS JOIN seeded"
        )
        old_all_read, recent_all_unread = cursor.fetchone()

    assert old_all_read is True
    assert recent_all_unread is True


def test_the_identity_follows_creation_order_so_the_inbox_can_order_by_it(
    owner: psycopg.Connection,
) -> None:
    out_of_order = _one(
        owner,
        "SELECT count(*) FROM notification a JOIN notification b"
        " ON b.notification_id > a.notification_id AND b.created_at < a.created_at",
    )

    assert out_of_order == 0
