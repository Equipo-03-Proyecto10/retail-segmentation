"""Direct coverage for the read side of web/db/segments.py (F7-02).

The recalculation statement itself is covered in tests/test_segment_run.py;
these are about get_current_assignment/get_current_assignments, the reads
list_customers_in_segment and customer_detail sit on top of.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

from web.db.segments import get_current_assignment, get_current_assignments

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
