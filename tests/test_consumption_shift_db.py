"""The single-snapshot read behind shift detection (F8-05).

The SQL returns both periods and all dimensions in one parameterized statement.
These tests cover its bounds, sources, aggregation definitions and mapping;
ranking remains the service's responsibility.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock
from uuid import UUID

from web.db import consumption_shift
from web.db.consumption import CategoryTotal, GroupTotal
from web.db.consumption_shift import list_totals_for_periods

_T0 = datetime(2026, 3, 1, tzinfo=UTC)
_T1 = datetime(2026, 6, 1, tzinfo=UTC)
_T2 = datetime(2026, 9, 1, tzinfo=UTC)
_ADA = "00000000-0000-0000-0000-000000000001"
_BOB = "00000000-0000-0000-0000-000000000002"


def _cursor(connection: MagicMock) -> MagicMock:
    return connection.cursor.return_value.__enter__.return_value


def _read(connection: MagicMock):
    return list_totals_for_periods(connection, _T0, _T1, _T1, _T2)


def _statement_and_params(connection: MagicMock) -> tuple[str, dict]:
    call = _cursor(connection).execute.call_args
    return call.args[0], call.args[1]


def test_both_periods_and_all_dimensions_are_read_by_one_statement() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = []

    _read(connection)

    _cursor(connection).execute.assert_called_once()
    statement, _ = _statement_and_params(connection)
    assert "'earlier'" in statement and "'later'" in statement
    assert "'channel' AS dimension" in statement
    assert "'store' AS dimension" in statement
    assert "'category' AS dimension" in statement
    assert "'lined' AS dimension" in statement
    assert statement.count("UNION ALL") == 3


def test_the_single_read_binds_both_periods_as_parameters() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = []

    _read(connection)

    statement, params = _statement_and_params(connection)
    assert params == {
        "earlier_start": _T0,
        "earlier_end": _T1,
        "later_start": _T1,
        "later_end": _T2,
        "customer_id": None,
    }
    assert "%(earlier_start)s" in statement
    assert "%(earlier_end)s" in statement
    assert "%(later_start)s" in statement
    assert "%(later_end)s" in statement


def test_each_period_is_closed_at_its_start_and_open_at_its_end() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = []

    _read(connection)

    statement, _ = _statement_and_params(connection)
    assert statement.count("t.occurred_at >= periods.start_at") == 4
    assert statement.count("t.occurred_at < periods.end_at") == 4
    assert "t.occurred_at <= periods.end_at" not in statement


def test_header_and_category_sources_match_their_definitions() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = []

    _read(connection)

    statement, _ = _statement_and_params(connection)
    assert statement.count("JOIN transaction AS t") == 4
    assert statement.count("JOIN transaction_line AS tl") == 1
    assert statement.count("JOIN product AS p") == 1
    assert statement.count("JOIN category AS c") == 1
    assert "reject" not in statement.lower()


def test_category_counts_each_purchase_once_and_preserves_units_and_spend() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = []

    _read(connection)

    statement, _ = _statement_and_params(connection)
    assert "count(DISTINCT t.transaction_id) AS purchases" in statement
    assert "sum(tl.quantity) AS units" in statement
    assert "sum(tl.quantity * tl.unit_price) AS spend" in statement


def test_tagged_rows_are_mapped_to_their_period_and_dimension() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = [
        ("earlier", "channel", _ADA, 1, "web", 3, None, Decimal("30.00")),
        ("earlier", "store", _ADA, 4, "North", 3, None, Decimal("30.00")),
        ("earlier", "category", _ADA, 7, "Dairy", 2, 5, Decimal("12.50")),
        ("later", "channel", _BOB, 2, "app", 1, None, Decimal("8.00")),
        ("later", "store", _BOB, 5, "South", 1, None, Decimal("8.00")),
        ("later", "category", _BOB, 8, "Snacks", 1, 2, Decimal("8.00")),
        ("earlier", "lined", _ADA, 0, "", 2, None, None),
    ]

    earlier, later = _read(connection)

    assert earlier == (
        {_ADA: [GroupTotal(1, "web", 3, Decimal("30.00"))]},
        {_ADA: [GroupTotal(4, "North", 3, Decimal("30.00"))]},
        {_ADA: [CategoryTotal(7, "Dairy", 2, 5, Decimal("12.50"))]},
        {_ADA: 2},
    )
    assert later == (
        {_BOB: [GroupTotal(2, "app", 1, Decimal("8.00"))]},
        {_BOB: [GroupTotal(5, "South", 1, Decimal("8.00"))]},
        {_BOB: [CategoryTotal(8, "Snacks", 1, 2, Decimal("8.00"))]},
        {},
    )


def test_no_rows_returns_empty_inputs_for_both_periods() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = []

    assert _read(connection) == (({}, {}, {}, {}), ({}, {}, {}, {}))


def test_a_customer_id_that_is_a_uuid_object_is_keyed_as_text() -> None:
    from uuid import UUID

    connection = MagicMock()
    _cursor(connection).fetchall.return_value = [
        ("earlier", "channel", UUID(_ADA), 1, "web", 3, None, Decimal("30.00"))
    ]

    earlier, _ = _read(connection)

    assert list(earlier[0]) == [_ADA]


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


# ---------- #341: one customer, and the purchases with product lines ----------


def test_every_grouping_can_be_narrowed_to_one_customer() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = []

    list_totals_for_periods(connection, _T0, _T1, _T1, _T2, customer_id=UUID(_ADA))
    statement, params = _statement_and_params(connection)

    assert params["customer_id"] == _ADA
    assert (
        " ".join(statement.split()).count(
            "AND (%(customer_id)s::uuid IS NULL "
            "OR t.customer_id = %(customer_id)s::uuid)"
        )
        == 4
    )


def test_purchases_with_product_lines_are_counted_once_each() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = []

    _read(connection)
    statement = " ".join(_statement_and_params(connection)[0].split())

    assert (
        "WHERE EXISTS ( SELECT 1 FROM transaction_line AS line "
        "WHERE line.transaction_id = t.transaction_id )" in statement
    )
    assert "GROUP BY periods.period, t.customer_id" in statement
