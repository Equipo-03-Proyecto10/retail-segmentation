"""The reads behind a customer's segment timeline (#337).

The rules are tested in tests/test_customer_timeline.py. What is checked here is
what only the SQL can get wrong: every statement is parameterized, the history
read takes every row for the one customer and never asks which method wrote it
(ADR-0018), and the sales read rebuilds each run's window from the run row with
the same interval arithmetic the scoring statement uses, both ends inclusive.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock

from web.db.consumption import HistoryRow
from web.db.customer_timeline import (
    SaleRow,
    list_history_rows,
    list_sales_in_window_only,
)

_CUSTOMER = "00000000-0000-0000-0000-000000000001"
_AT = datetime(2026, 9, 1, 3, 0, tzinfo=UTC)


def _cursor(connection: MagicMock) -> MagicMock:
    return connection.cursor.return_value.__enter__.return_value


def _statement_and_params(connection: MagicMock) -> tuple[str, dict]:
    call = _cursor(connection).execute.call_args
    return call.args[0], call.args[1]


def _flat(statement: str) -> str:
    return re.sub(r"\s+", " ", statement)


def test_history_reads_every_row_of_the_one_customer_newest_first() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = [
        (
            2,
            "AT_RISK",
            "At risk",
            _AT,
            3,
            Decimal("900.00"),
            2,
            3,
            3,
            _AT,
            None,
            _AT,
            180,
        )
    ]

    rows = list_history_rows(connection, _CUSTOMER)
    statement, params = _statement_and_params(connection)
    flat = _flat(statement)

    assert params == {"customer_id": _CUSTOMER}
    assert "WHERE h.customer_id = %(customer_id)s" in flat
    assert "ORDER BY h.valid_from DESC, h.history_id DESC" in flat
    assert "LIMIT" not in flat
    assert "valid_to IS NULL" not in flat
    assert "method" not in flat
    assert rows == [
        HistoryRow(
            run_id=2,
            label_code="AT_RISK",
            label_name="At risk",
            last_purchase_at=_AT,
            frequency_count=3,
            monetary_total=Decimal("900.00"),
            r_score=2,
            f_score=3,
            m_score=3,
            valid_from=_AT,
            valid_to=None,
            run_at=_AT,
            window_days=180,
        )
    ]


def test_history_reads_raw_measures_and_scores_as_stored() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = []

    list_history_rows(connection, _CUSTOMER)
    flat = _flat(_statement_and_params(connection)[0])

    for column in (
        "h.recency_last_purchase_at",
        "h.frequency_count",
        "h.monetary_total",
        "h.r_score",
        "h.f_score",
        "h.m_score",
        "h.valid_from",
        "h.valid_to",
        "r.run_at",
        "r.window_days",
    ):
        assert column in flat
    assert "LEFT JOIN segment_label" in flat


def test_sales_are_inside_one_runs_window_and_outside_the_others() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = []

    list_sales_in_window_only(connection, _CUSTOMER, run_id=3, excluding_run_id=2)
    statement, params = _statement_and_params(connection)
    flat = _flat(statement)

    assert params == {"customer_id": _CUSTOMER, "run_id": 3, "excluding_run_id": 2}
    # The run's own window, rebuilt as the scoring statement computes it.
    assert flat.count("run_at - make_interval(days => window_days) AS since") == 2
    assert "WHERE run_id = %(run_id)s" in flat
    assert "WHERE run_id = %(excluding_run_id)s" in flat
    assert "t.occurred_at >= i.since AND t.occurred_at <= i.until" in flat
    assert "AND NOT (t.occurred_at >= o.since AND t.occurred_at <= o.until)" in flat
    assert "t.customer_id = %(customer_id)s" in flat
    assert "method" not in flat


def test_sales_are_accepted_sales_with_their_lines_oldest_first() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = [
        (10, "T-10", _AT, Decimal("150.00"), "Centro", "Store", 3)
    ]

    sales = list_sales_in_window_only(
        connection, _CUSTOMER, run_id=2, excluding_run_id=3
    )
    flat = _flat(_statement_and_params(connection)[0])

    assert "FROM transaction AS t" in flat
    assert "LEFT JOIN transaction_line AS tl" in flat
    assert "COALESCE(sum(tl.quantity), 0)" in flat
    assert "ORDER BY t.occurred_at, t.transaction_id" in flat
    assert sales == [
        SaleRow(
            transaction_id=10,
            source_transaction_id="T-10",
            occurred_at=_AT,
            total=Decimal("150.00"),
            store_name="Centro",
            channel_name="Store",
            units=3,
        )
    ]
