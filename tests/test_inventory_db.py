"""Focused tests for the transactional inventory write used by ingestion."""

from unittest.mock import MagicMock

import pytest

from web.db.inventory import StockUnavailable, decrement_stock


def _connection(*rows: tuple | None) -> tuple[MagicMock, MagicMock]:
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.side_effect = list(rows)
    return connection, cursor


def test_decrement_stock_uses_a_conditional_parameterized_update() -> None:
    connection, cursor = _connection((3,))

    remaining = decrement_stock(connection, store_id=7, product_id=12, quantity=2)

    assert remaining == 3
    statement, parameters = cursor.execute.call_args.args
    assert "quantity_on_hand >= %s" in statement
    assert parameters == (2, 7, 12, 2)
    assert cursor.execute.call_count == 1


@pytest.mark.parametrize("available", [None, 1])
def test_decrement_stock_locks_and_describes_unavailable_stock(
    available: int | None,
) -> None:
    connection, cursor = _connection(None, None if available is None else (available,))

    with pytest.raises(StockUnavailable) as raised:
        decrement_stock(connection, store_id=7, product_id=12, quantity=2)

    error = raised.value
    assert error.store_id == 7
    assert error.product_id == 12
    assert error.available == available
    lookup_statement, lookup_parameters = cursor.execute.call_args.args
    assert "FOR UPDATE" in lookup_statement
    assert lookup_parameters == (7, 12)
    assert cursor.execute.call_count == 2
