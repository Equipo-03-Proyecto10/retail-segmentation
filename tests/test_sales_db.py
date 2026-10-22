"""SQL-level guards for concurrent sales header updates."""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import MagicMock

from web.db.sales import (
    SaleReferences,
    get_sale_references,
    get_transaction_by_source_id,
    insert_transaction,
)


def test_transaction_header_lookup_locks_before_a_line_is_added() -> None:
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = None

    assert get_transaction_by_source_id(connection, "TXN-1") is None

    statement, parameters = cursor.execute.call_args.args
    assert "FOR UPDATE" in statement
    assert parameters == ("TXN-1",)


def test_header_lookup_preserves_the_existing_header_shape() -> None:
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = (
        7,
        "TXN-1",
        "00000000-0000-0000-0000-000000000001",
        1,
        1,
        datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
        "SEEDED",
    )

    header = get_transaction_by_source_id(connection, "TXN-1")

    assert header is not None
    assert header.transaction_id == 7
    assert header.source_transaction_id == "TXN-1"
    assert header.data_origin == "SEEDED"
    statement = cursor.execute.call_args.args[0]
    assert "data_origin" in statement


def test_a_new_header_binds_its_data_origin_as_a_parameter() -> None:
    """ADR-0052: the origin is a bound value, never text in the statement."""
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = (11,)

    transaction_id = insert_transaction(
        connection,
        source_transaction_id="TXN-1",
        customer_id="00000000-0000-0000-0000-000000000001",
        store_id=1,
        channel_id=2,
        occurred_at=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
        data_origin="OBSERVED",
    )

    statement, parameters = cursor.execute.call_args.args
    assert transaction_id == 11
    assert "data_origin" in statement
    assert "OBSERVED" not in statement
    assert statement.count("%s") == len(parameters) == 6
    assert parameters[-1] == "OBSERVED"


def test_sale_references_are_parameterized_and_map_customer_and_product_facts() -> None:
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = (date(2026, 1, 1), False)

    references = get_sale_references(
        connection,
        customer_id="00000000-0000-0000-0000-000000000001",
        product_id=7,
    )

    statement, parameters = cursor.execute.call_args.args
    assert parameters == (7, "00000000-0000-0000-0000-000000000001")
    assert statement.count("%s") == 2
    assert references == SaleReferences(date(2026, 1, 1), False)
