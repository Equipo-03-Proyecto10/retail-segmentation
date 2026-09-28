"""The reads behind the consumption profile (F8-03).

The rules that turn these rows into a profile are tested in
tests/test_consumption_profile.py. What is checked here is what only the SQL
can get wrong: that every statement is parameterized, that it reads accepted
sales and nothing else, and that the segment reads take the open row and the
most recently closed one from history without ever asking which method wrote
them (ADR-0018).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from web.db import consumption
from web.db.consumption import (
    CategoryTotal,
    DiscountTotals,
    GroupTotal,
    HistoryRow,
    ProductTotal,
    SalesTotals,
    get_discount_totals,
    get_open_history_row,
    get_previous_history_row,
    get_sales_totals,
    list_category_totals,
    list_channel_totals,
    list_product_totals,
    list_store_totals,
)

_CUSTOMER = "00000000-0000-0000-0000-000000000001"
_UNTIL = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
_SINCE = _UNTIL - timedelta(days=180)


def _cursor(connection: MagicMock) -> MagicMock:
    return connection.cursor.return_value.__enter__.return_value


def _statement_and_params(connection: MagicMock) -> tuple[str, dict]:
    call = _cursor(connection).execute.call_args
    return call.args[0], call.args[1]


_WINDOWED_READS = [
    (get_sales_totals, (0, None, None), "fetchone"),
    (list_channel_totals, [], "fetchall"),
    (list_store_totals, [], "fetchall"),
    (list_category_totals, [], "fetchall"),
    (list_product_totals, [], "fetchall"),
    (get_discount_totals, (None, None), "fetchone"),
]


@pytest.mark.parametrize(("read", "empty", "fetch"), _WINDOWED_READS)
def test_every_windowed_read_is_parameterized(read, empty, fetch) -> None:
    connection = MagicMock()
    getattr(_cursor(connection), fetch).return_value = empty

    read(connection, _CUSTOMER, _SINCE, _UNTIL)

    statement, params = _statement_and_params(connection)
    assert params == {"customer_id": _CUSTOMER, "since": _SINCE, "until": _UNTIL}
    assert "%(customer_id)s" in statement
    assert "%(since)s" in statement
    assert "%(until)s" in statement
    # The value travels as a parameter; it is never written into the SQL text.
    assert _CUSTOMER not in statement


@pytest.mark.parametrize(("read", "empty", "fetch"), _WINDOWED_READS)
def test_a_windowed_read_is_closed_at_both_ends(read, empty, fetch) -> None:
    connection = MagicMock()
    getattr(_cursor(connection), fetch).return_value = empty

    read(connection, _CUSTOMER, _SINCE, _UNTIL)

    statement, _ = _statement_and_params(connection)
    assert ">= %(since)s" in statement
    assert "<= %(until)s" in statement


@pytest.mark.parametrize(("read", "empty", "fetch"), _WINDOWED_READS)
def test_a_windowed_read_starts_from_the_sales_the_ingestion_accepted(
    read, empty, fetch
) -> None:
    """Only accepted rows are ever persisted (ADR-0020), so `transaction` is the
    accepted sales. Nothing here reaches for a rejection report or a staging
    table."""
    connection = MagicMock()
    getattr(_cursor(connection), fetch).return_value = empty

    read(connection, _CUSTOMER, _SINCE, _UNTIL)

    statement, _ = _statement_and_params(connection)
    assert "FROM transaction" in statement
    assert "reject" not in statement.lower()


def test_a_customer_id_that_is_a_uuid_object_still_travels_as_text() -> None:
    from uuid import UUID

    connection = MagicMock()
    _cursor(connection).fetchone.return_value = (0, None, None)

    get_sales_totals(connection, UUID(_CUSTOMER), _SINCE, _UNTIL)

    _, params = _statement_and_params(connection)
    assert params["customer_id"] == _CUSTOMER


# ---------- what each read returns ----------


def test_sales_totals_of_no_purchases_are_none_not_zero() -> None:
    connection = MagicMock()
    _cursor(connection).fetchone.return_value = (0, None, None)

    totals = get_sales_totals(connection, _CUSTOMER, _SINCE, _UNTIL)

    assert totals == SalesTotals(purchases=0, spend=None, last_purchase_at=None)


def test_sales_totals_carry_count_spend_and_last_purchase() -> None:
    connection = MagicMock()
    last = datetime(2026, 9, 20, tzinfo=UTC)
    _cursor(connection).fetchone.return_value = (4, Decimal("400.00"), last)

    totals = get_sales_totals(connection, _CUSTOMER, _SINCE, _UNTIL)

    assert totals == SalesTotals(4, Decimal("400.00"), last)


def test_the_group_reads_map_rows_to_their_dataclasses() -> None:
    connection = MagicMock()
    cursor = _cursor(connection)

    cursor.fetchall.return_value = [(2, "app", 3, Decimal("30.00"))]
    assert list_channel_totals(connection, _CUSTOMER, _SINCE, _UNTIL) == [
        GroupTotal(2, "app", 3, Decimal("30.00"))
    ]
    assert list_store_totals(connection, _CUSTOMER, _SINCE, _UNTIL) == [
        GroupTotal(2, "app", 3, Decimal("30.00"))
    ]

    cursor.fetchall.return_value = [(1, "Dairy", 2, 5, Decimal("12.50"))]
    assert list_category_totals(connection, _CUSTOMER, _SINCE, _UNTIL) == [
        CategoryTotal(1, "Dairy", 2, 5, Decimal("12.50"))
    ]

    cursor.fetchall.return_value = [(9, "Milk", 2, 5)]
    assert list_product_totals(connection, _CUSTOMER, _SINCE, _UNTIL) == [
        ProductTotal(9, "Milk", 2, 5)
    ]


def test_a_category_counts_a_purchase_once_however_many_of_its_products_it_holds() -> (
    None
):
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = []

    list_category_totals(connection, _CUSTOMER, _SINCE, _UNTIL)

    statement, _ = _statement_and_params(connection)
    assert "count(DISTINCT t.transaction_id)" in statement


def test_a_product_listed_at_zero_is_left_out_of_the_discount_sums() -> None:
    connection = MagicMock()
    _cursor(connection).fetchone.return_value = (None, None)

    totals = get_discount_totals(connection, _CUSTOMER, _SINCE, _UNTIL)

    statement, _ = _statement_and_params(connection)
    assert "p.list_price > 0" in statement
    assert totals == DiscountTotals(paid=None, at_list=None)


# ---------- segment history: the open row and the last closed one ----------

_HISTORY_ROW = (
    30,
    "LOYAL",
    "Loyal",
    _UNTIL,
    10,
    Decimal("1234.50"),
    2,
    3,
    4,
    _UNTIL,
    None,
    _UNTIL,
    180,
)


def test_the_open_row_is_the_one_whose_validity_has_not_ended() -> None:
    connection = MagicMock()
    _cursor(connection).fetchone.return_value = _HISTORY_ROW

    row = get_open_history_row(connection, _CUSTOMER)

    statement, params = _statement_and_params(connection)
    assert "h.valid_to IS NULL" in statement
    assert params == {"customer_id": _CUSTOMER}
    assert row == HistoryRow(*_HISTORY_ROW)


def test_the_previous_row_is_the_most_recently_closed_one() -> None:
    connection = MagicMock()
    _cursor(connection).fetchone.return_value = _HISTORY_ROW

    get_previous_history_row(connection, _CUSTOMER)

    statement, _ = _statement_and_params(connection)
    assert "h.valid_to IS NOT NULL" in statement
    assert "ORDER BY h.valid_to DESC, h.history_id DESC" in statement
    assert "LIMIT 1" in statement


@pytest.mark.parametrize("read", [get_open_history_row, get_previous_history_row])
def test_no_history_row_is_none(read) -> None:
    connection = MagicMock()
    _cursor(connection).fetchone.return_value = None

    assert read(connection, _CUSTOMER) is None


@pytest.mark.parametrize("read", [get_open_history_row, get_previous_history_row])
def test_the_history_reads_never_ask_which_method_wrote_a_run(read) -> None:
    """ADR-0018: a consumer of assignments reads the label and never branches
    on segmentation_run.method, or on a raw cluster id."""
    connection = MagicMock()
    _cursor(connection).fetchone.return_value = None

    read(connection, _CUSTOMER)

    statement, _ = _statement_and_params(connection)
    assert "method" not in statement.lower()
    assert "cluster" not in statement.lower()


def test_the_module_writes_nothing() -> None:
    """Read only: no statement that changes data, and no commit. The caller
    owns any transaction, and this module never opens one."""
    source = Path(consumption.__file__).read_text(encoding="utf-8")
    for pattern in (
        r"\bINSERT\s+INTO\b",
        r"\bDELETE\s+FROM\b",
        r"\bUPDATE\s+\w+\s+SET\b",
        r"\.commit\(",
        r"\.rollback\(",
    ):
        assert re.search(pattern, source, re.IGNORECASE) is None, pattern
