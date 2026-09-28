"""Direct coverage for the read side of web/db/segments.py (F7-02).

The recalculation statement itself is covered in tests/test_segment_run.py;
these are about get_current_assignment/get_current_assignments, the reads
list_customers_in_segment and customer_detail sit on top of.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

from web.db.segments import (
    get_current_assignment,
    get_current_assignments,
    get_label_name,
    lock_for_run,
)

_CUSTOMER_1 = "00000000-0000-0000-0000-000000000001"
_VALID_FROM = datetime(2026, 1, 15, tzinfo=UTC)


def test_get_current_assignment_reads_label_code_alongside_segment_id() -> None:
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = (_CUSTOMER_1, 4, "CHAMPION", 5, 4, 5, _VALID_FROM)

    assignment = get_current_assignment(connection, _CUSTOMER_1)

    assert assignment is not None
    assert assignment.segment_id == 4
    assert assignment.label_code == "CHAMPION"


def test_get_current_assignments_casts_the_array_to_uuid() -> None:
    """Regression: a bare ANY(%s) sends text[] against a uuid column and
    PostgreSQL refuses it (operator does not exist: uuid = text)."""
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = []

    get_current_assignments(connection, [_CUSTOMER_1])

    statement = cursor.execute.call_args.args[0]
    assert "ANY(%s::uuid[])" in statement


def test_get_current_assignments_with_no_ids_makes_no_call() -> None:
    connection = MagicMock()

    assert get_current_assignments(connection, []) == {}
    connection.cursor.assert_not_called()


def test_lock_for_run_takes_a_transaction_scoped_advisory_lock() -> None:
    """#285: two concurrent runs must serialize on this lock rather than race
    to close and reopen the same customer_segment_history rows."""
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value

    lock_for_run(connection)

    statement, params = cursor.execute.call_args_list[0].args
    assert "pg_advisory_xact_lock" in statement
    assert params == (285_001,)


def test_the_run_instant_is_read_after_the_lock_not_from_now() -> None:
    """now() is when the transaction began, which for a run that waited is
    before the run it waited for; closing that run's rows then would put
    valid_to before valid_from (#285)."""
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = ("instant",)

    assert lock_for_run(connection) == "instant"

    statements = [call.args[0] for call in cursor.execute.call_args_list]
    assert "pg_advisory_xact_lock" in statements[0]
    assert statements[1] == "SELECT clock_timestamp()"


def test_get_label_name_reads_one_label_by_code_as_a_parameter() -> None:
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = ("Loyal",)

    assert get_label_name(connection, "LOYAL") == "Loyal"

    statement, parameters = cursor.execute.call_args.args
    assert parameters == ("LOYAL",) and "LOYAL" not in statement


def test_get_label_name_is_none_for_a_code_outside_the_vocabulary() -> None:
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value.fetchone.return_value = None

    assert get_label_name(connection, "NOT_A_LABEL") is None
