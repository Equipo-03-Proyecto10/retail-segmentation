"""Data access for sales CSV upload attempts (#334): one sales_load row per
upload and one sales_load_rejection row per rejected line, committed together
and read back in the file's own line order."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import UUID

from web.db.sales_loads import (
    get_sales_load,
    insert_sales_load,
    list_sales_load_rejections,
    list_sales_loads,
)

_ADMIN_ID = "11111111-1111-1111-1111-000000000001"
_WHEN = datetime(2026, 9, 29, 15, 30, tzinfo=UTC)
_ROW = (7, "september.csv", 1, 3, 1, 2, UUID(_ADMIN_ID), "Ada Admin", _WHEN)


def _cursor(connection: MagicMock) -> MagicMock:
    return connection.cursor.return_value.__enter__.return_value


def _insert(connection: MagicMock, rejections: list[tuple[int, str]]) -> int:
    _cursor(connection).fetchone.return_value = (7,)
    return insert_sales_load(
        connection,
        filename="september.csv",
        contract_version=1,
        received_count=3,
        accepted_count=3 - len(rejections),
        rejected_count=len(rejections),
        loaded_by=_ADMIN_ID,
        rejections=rejections,
    )


def test_a_load_and_every_rejected_line_are_written_and_committed_together() -> None:
    connection = MagicMock()

    load_id = _insert(connection, [(3, "unknown product"), (4, "quantity bad")])

    assert load_id == 7
    _, parameters = _cursor(connection).execute.call_args.args
    assert parameters == ("september.csv", 1, 3, 1, 2, _ADMIN_ID)
    statement, rows = _cursor(connection).executemany.call_args.args
    assert "INSERT INTO sales_load_rejection" in statement
    assert rows == [(7, 3, "unknown product"), (7, 4, "quantity bad")]
    connection.commit.assert_called_once()
    connection.rollback.assert_not_called()


def test_a_clean_load_writes_no_rejection_rows() -> None:
    connection = MagicMock()

    _insert(connection, [])

    _cursor(connection).executemany.assert_not_called()
    connection.commit.assert_called_once()


def test_a_load_with_no_signed_in_user_is_credited_to_nobody() -> None:
    connection = MagicMock()
    _cursor(connection).fetchone.return_value = (7,)

    insert_sales_load(
        connection,
        filename="september.csv",
        contract_version=1,
        received_count=0,
        accepted_count=0,
        rejected_count=0,
        loaded_by=None,
        rejections=[],
    )

    _, parameters = _cursor(connection).execute.call_args.args
    assert parameters[-1] is None


def test_a_page_of_loads_is_read_newest_first_with_its_total() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = [_ROW]
    _cursor(connection).fetchone.return_value = (41,)

    loads, total = list_sales_loads(connection, page=3, per_page=20)

    statement, parameters = _cursor(connection).execute.call_args_list[0].args
    assert "ORDER BY l.loaded_at DESC, l.load_id DESC" in statement
    assert parameters == (20, 40)
    assert total == 41
    assert loads[0].filename == "september.csv"
    assert loads[0].loaded_by_name == "Ada Admin"


def test_one_load_is_read_by_id_or_is_none() -> None:
    connection = MagicMock()
    _cursor(connection).fetchone.return_value = _ROW

    assert get_sales_load(connection, 7).rejected_count == 2
    assert _cursor(connection).execute.call_args.args[1] == (7,)

    _cursor(connection).fetchone.return_value = None
    assert get_sales_load(connection, 8) is None


def test_rejections_are_read_in_the_file_s_own_line_order() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = [(3, "unknown product")]

    rejections = list_sales_load_rejections(connection, 7)

    statement, parameters = _cursor(connection).execute.call_args.args
    assert "ORDER BY line_number" in statement
    assert parameters == (7,)
    assert rejections[0].line_number == 3
