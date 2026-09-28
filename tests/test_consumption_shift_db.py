"""The reads behind shift detection (F8-05).

What only the SQL can get wrong: that every statement is parameterized, that a
period is half-open so two adjacent periods never share an instant, that it
reads accepted sales and nothing else, and that its rows come back grouped by
customer. Ranking is not done here; it is web/services/consumption_shift.py's.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from web.db import consumption_shift
from web.db.consumption import CategoryTotal, GroupTotal
from web.db.consumption_shift import (
    list_category_totals_by_customer,
    list_channel_totals_by_customer,
    list_store_totals_by_customer,
)

_START = datetime(2026, 3, 1, tzinfo=UTC)
_END = datetime(2026, 6, 1, tzinfo=UTC)
_ADA = "00000000-0000-0000-0000-000000000001"
_BOB = "00000000-0000-0000-0000-000000000002"

_READS = [
    list_channel_totals_by_customer,
    list_store_totals_by_customer,
    list_category_totals_by_customer,
]


def _cursor(connection: MagicMock) -> MagicMock:
    return connection.cursor.return_value.__enter__.return_value


def _statement_and_params(connection: MagicMock) -> tuple[str, dict]:
    call = _cursor(connection).execute.call_args
    return call.args[0], call.args[1]


@pytest.mark.parametrize("read", _READS)
def test_every_read_is_parameterized(read) -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = []

    read(connection, _START, _END)

    statement, params = _statement_and_params(connection)
    assert params == {"start": _START, "end": _END}
    assert "%(start)s" in statement and "%(end)s" in statement


@pytest.mark.parametrize("read", _READS)
def test_a_period_is_closed_at_its_start_and_open_at_its_end(read) -> None:
    """Half-open, so two adjacent periods never both count the same instant."""
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = []

    read(connection, _START, _END)

    statement, _ = _statement_and_params(connection)
    assert ">= %(start)s" in statement
    assert "< %(end)s" in statement
    assert "<= %(end)s" not in statement


@pytest.mark.parametrize("read", _READS)
def test_every_read_starts_from_the_sales_the_ingestion_accepted(read) -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = []

    read(connection, _START, _END)

    statement, _ = _statement_and_params(connection)
    assert "FROM transaction" in statement
    assert "reject" not in statement.lower()


@pytest.mark.parametrize("read", _READS)
def test_every_read_groups_by_customer(read) -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = []

    read(connection, _START, _END)

    statement, _ = _statement_and_params(connection)
    assert "t.customer_id" in statement.split("GROUP BY")[1]


@pytest.mark.parametrize("read", _READS)
def test_no_rows_is_an_empty_mapping(read) -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = []

    assert read(connection, _START, _END) == {}


def test_channel_and_store_rows_are_grouped_under_their_customer() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = [
        (_ADA, 1, "web", 3, Decimal("30.00")),
        (_ADA, 2, "app", 1, Decimal("5.00")),
        (_BOB, 1, "web", 2, Decimal("20.00")),
    ]

    expected = {
        _ADA: [
            GroupTotal(1, "web", 3, Decimal("30.00")),
            GroupTotal(2, "app", 1, Decimal("5.00")),
        ],
        _BOB: [GroupTotal(1, "web", 2, Decimal("20.00"))],
    }
    assert list_channel_totals_by_customer(connection, _START, _END) == expected
    assert list_store_totals_by_customer(connection, _START, _END) == expected


def test_a_customer_id_that_is_a_uuid_object_is_keyed_as_text() -> None:
    from uuid import UUID

    connection = MagicMock()
    _cursor(connection).fetchall.return_value = [
        (UUID(_ADA), 1, "web", 3, Decimal("30.00"))
    ]

    assert list(list_channel_totals_by_customer(connection, _START, _END)) == [_ADA]


def test_category_rows_are_grouped_under_their_customer() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = [
        (_ADA, 7, "Dairy", 2, 5, Decimal("12.50")),
        (_BOB, 8, "Snacks", 1, 1, Decimal("3.00")),
    ]

    assert list_category_totals_by_customer(connection, _START, _END) == {
        _ADA: [CategoryTotal(7, "Dairy", 2, 5, Decimal("12.50"))],
        _BOB: [CategoryTotal(8, "Snacks", 1, 1, Decimal("3.00"))],
    }


def test_a_category_counts_a_purchase_once_however_many_of_its_products_it_holds() -> (
    None
):
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = []

    list_category_totals_by_customer(connection, _START, _END)

    statement, _ = _statement_and_params(connection)
    assert "count(DISTINCT t.transaction_id)" in statement


def test_the_module_writes_nothing() -> None:
    source = Path(consumption_shift.__file__).read_text(encoding="utf-8")
    for pattern in (
        r"\bINSERT\s+INTO\b",
        r"\bDELETE\s+FROM\b",
        r"\bUPDATE\s+\w+\s+SET\b",
        r"\.commit\(",
        r"\.rollback\(",
    ):
        assert re.search(pattern, source, re.IGNORECASE) is None, pattern
