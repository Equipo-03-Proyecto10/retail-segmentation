"""The one new read behind the filtered consumption-shift and recommendation
reports (F12-03): customer names in bulk, for the shift rows a filter leaves.

Everything else the reports need already exists: F8-05's `detect_shifts`,
F10-01's `recommend`, and `list_stores`/`list_channels`/`list_all_categories`
for the filter pickers.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

from web.db import consumption_reports as reports_db
from web.db.consumption_reports import list_customer_names

_ADA = "00000000-0000-0000-0000-000000000001"
_BOB = "00000000-0000-0000-0000-000000000002"


def _cursor(connection: MagicMock) -> MagicMock:
    return connection.cursor.return_value.__enter__.return_value


def test_names_are_read_for_exactly_the_given_customers() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = [(_ADA, "Ada Lovelace")]

    names = list_customer_names(connection, [_ADA])

    statement, parameters = _cursor(connection).execute.call_args.args
    assert parameters == ([_ADA],)
    assert "= ANY(%s::uuid[])" in statement
    assert names == {_ADA: "Ada Lovelace"}


def test_no_customer_ids_is_an_empty_mapping_and_no_statement_runs() -> None:
    connection = MagicMock()

    assert list_customer_names(connection, []) == {}
    _cursor(connection).execute.assert_not_called()


def test_several_names_come_back_keyed_by_id() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = [
        (_ADA, "Ada Lovelace"),
        (_BOB, "Bob Marley"),
    ]

    assert list_customer_names(connection, [_ADA, _BOB]) == {
        _ADA: "Ada Lovelace",
        _BOB: "Bob Marley",
    }


def test_a_customer_id_is_matched_as_text() -> None:
    from uuid import UUID

    connection = MagicMock()
    _cursor(connection).fetchall.return_value = [(_ADA, "Ada Lovelace")]

    list_customer_names(connection, [UUID(_ADA)])

    statement, parameters = _cursor(connection).execute.call_args.args
    assert parameters == ([_ADA],)


def test_the_read_selects_no_method_and_no_cluster() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = []

    list_customer_names(connection, [_ADA])

    statement, _ = _cursor(connection).execute.call_args.args
    assert "method" not in statement.lower() and "cluster" not in statement.lower()


def test_the_module_writes_nothing() -> None:
    source = Path(reports_db.__file__).read_text(encoding="utf-8")
    for pattern in (
        r"\bINSERT\s+INTO\b",
        r"\bDELETE\s+FROM\b",
        r"\bUPDATE\s+\w+\s+SET\b",
        r"\.commit\(",
    ):
        assert re.search(pattern, source, re.IGNORECASE) is None, pattern
