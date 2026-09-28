"""The reads behind product recommendations (F10-01).

What only the SQL can get wrong: that stock means a *positive* quantity in the
*named store* of an *active* product, that "customers in the same segment" means the
customers whose *open* assignment carries the label and no one else's history, that
the customer is left out of their own segment's popularity, and that every statement
is parameterized and reads nothing about how a run was produced (ADR-0018).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from web.db import recommendations
from web.db.recommendations import (
    StockedProduct,
    list_segment_buyers,
    list_stocked_products,
)

_SINCE = datetime(2026, 3, 1, tzinfo=UTC)
_UNTIL = datetime(2026, 9, 1, tzinfo=UTC)
_ID = "00000000-0000-0000-0000-000000000001"


def _cursor(connection: MagicMock) -> MagicMock:
    return connection.cursor.return_value.__enter__.return_value


def _statement(connection: MagicMock) -> tuple[str, dict]:
    call = _cursor(connection).execute.call_args
    return call.args[0], call.args[1]


# ---------- stock ----------


def test_stock_is_read_for_the_named_store_as_a_parameter() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = []

    list_stocked_products(connection, 8)

    statement, parameters = _statement(connection)
    assert parameters == {"store_id": 8}
    assert "%(store_id)s" in statement and " 8" not in statement


def test_the_store_is_a_filter_on_the_inventory_row_and_not_merely_a_parameter() -> (
    None
):
    """Stock in another store is not stock here. The clause that says so is pinned,
    because a statement can carry the parameter and still ignore it."""
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = []

    list_stocked_products(connection, 8)

    statement, _ = _statement(connection)
    assert "i.store_id = %(store_id)s" in statement
    assert "FROM inventory AS i" in statement


def test_only_a_positive_quantity_counts_as_stock() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = []

    list_stocked_products(connection, 8)

    statement, _ = _statement(connection)
    assert "quantity_on_hand > 0" in statement
    assert ">= 0" not in statement


def test_only_active_products_are_offered() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = []

    list_stocked_products(connection, 8)

    statement, _ = _statement(connection)
    assert "is_active" in statement


def test_stock_rows_carry_the_product_its_category_and_the_quantity() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = [(7, "Whole milk", 3, "Dairy", 12)]

    assert list_stocked_products(connection, 8) == [
        StockedProduct(7, "Whole milk", 3, "Dairy", 12)
    ]


def test_stock_is_returned_in_product_order() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = []

    list_stocked_products(connection, 8)

    statement, _ = _statement(connection)
    assert re.search(r"ORDER BY p\.product_id", statement)


# ---------- what other customers in the segment bought ----------


def test_segment_buyers_are_read_by_label_customer_and_window_as_parameters() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = []

    list_segment_buyers(connection, "LOYAL", _ID, _SINCE, _UNTIL)

    statement, parameters = _statement(connection)
    assert parameters == {
        "label_code": "LOYAL",
        "customer_id": _ID,
        "since": _SINCE,
        "until": _UNTIL,
    }
    assert "LOYAL" not in statement and _ID not in statement


def test_a_segment_is_the_customers_whose_open_assignment_carries_the_label() -> None:
    """Not everyone who ever held it: a customer who moved on is no longer in the
    segment, so what they bought does not speak for it."""
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = []

    list_segment_buyers(connection, "LOYAL", _ID, _SINCE, _UNTIL)

    statement, _ = _statement(connection)
    assert "h.valid_to IS NULL" in statement
    assert "h.label_code = %(label_code)s" in statement


def test_the_customer_is_left_out_of_their_own_segments_popularity() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = []

    list_segment_buyers(connection, "LOYAL", _ID, _SINCE, _UNTIL)

    statement, _ = _statement(connection)
    assert "h.customer_id <> %(customer_id)s" in statement


def test_a_buyer_counts_once_however_many_times_they_bought() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = []

    list_segment_buyers(connection, "LOYAL", _ID, _SINCE, _UNTIL)

    statement, _ = _statement(connection)
    assert "count(DISTINCT t.customer_id)" in statement


def test_the_window_matches_the_consumption_profiles_closed_window() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = []

    list_segment_buyers(connection, "LOYAL", _ID, _SINCE, _UNTIL)

    statement, _ = _statement(connection)
    assert "t.occurred_at >= %(since)s" in statement
    assert "t.occurred_at <= %(until)s" in statement


def test_segment_buyers_come_back_as_a_count_per_product() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = [(7, 4), (9, 1)]

    assert list_segment_buyers(connection, "LOYAL", _ID, _SINCE, _UNTIL) == {7: 4, 9: 1}


def test_no_buyers_is_an_empty_mapping() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = []

    assert list_segment_buyers(connection, "LOYAL", _ID, _SINCE, _UNTIL) == {}


# ---------- what the reads may not do ----------


@pytest.mark.parametrize("read", [list_stocked_products, list_segment_buyers])
def test_no_read_selects_how_a_run_was_produced(read) -> None:
    import inspect

    source = inspect.getsource(read).lower()

    for word in ("method", "cluster", "segmentation_run"):
        assert word not in source, word


def test_the_module_writes_nothing() -> None:
    source = Path(recommendations.__file__).read_text(encoding="utf-8")
    for pattern in (
        r"\bINSERT\s+INTO\b",
        r"\bDELETE\s+FROM\b",
        r"\bUPDATE\s+\w+\s+SET\b",
        r"\.commit\(",
    ):
        assert re.search(pattern, source, re.IGNORECASE) is None, pattern
