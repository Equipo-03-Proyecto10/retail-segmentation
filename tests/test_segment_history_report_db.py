"""Reads behind the filtered segment history report (F12-02).

Read only, parameterized, paginated: this table already holds one row per
customer per run, so an unfiltered read across every run can be large, unlike
F12-01's per-run reads. What only the SQL can get wrong: that run, label and
period combine with AND (every applied filter narrows the result), that
"Unassigned" is not confused with "no label filter", that a period is closed
on its last day, and that no statement selects `segmentation_run.method` as a
filter.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from web.db import segment_history_report as report_db
from web.db.segment_history_report import (
    UNASSIGNED,
    count_history_entries,
    list_history_entries,
)

_START = date(2026, 4, 1)
_END = date(2026, 9, 1)


def _cursor(connection: MagicMock) -> MagicMock:
    return connection.cursor.return_value.__enter__.return_value


def _statement(connection: MagicMock, call: int = 0) -> tuple[str, dict]:
    return _cursor(connection).execute.call_args_list[call].args


def _row(**overrides) -> tuple:
    defaults = dict(
        history_id=1,
        run_id=31,
        run_at=datetime(2026, 9, 28, 9, 0, tzinfo=UTC),
        method="RFM_RULES",
        window_days=180,
        valid_from=datetime(2026, 9, 28, 9, 0, tzinfo=UTC),
        valid_to=None,
        customer_id="00000000-0000-0000-0000-000000000001",
        customer_name="Ada Lovelace",
        segment_id=1,
        label_code="LOYAL",
        label_name="Loyal",
        r_score=4,
        f_score=3,
        m_score=5,
        recency_last_purchase_at=datetime(2026, 9, 1, tzinfo=UTC),
        frequency_count=6,
        monetary_total=Decimal("400.00"),
    )
    values = {**defaults, **overrides}
    return tuple(values.values())


# ---------- every filter is optional and they combine ----------


def test_with_no_filters_the_where_clause_still_only_carries_true_conditions() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = []

    list_history_entries(connection, page=1, per_page=25)

    statement, parameters = _statement(connection)
    assert parameters["run_id"] is None
    assert parameters["label_code"] is None
    assert parameters["period_start"] is None
    assert parameters["period_end"] is None


def test_a_run_filter_is_a_parameter_never_interpolated() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = []

    list_history_entries(connection, run_id=31, page=1, per_page=25)

    statement, parameters = _statement(connection)
    assert parameters["run_id"] == 31
    assert "31" not in statement
    assert "h.run_id = %(run_id)s" in statement


def test_a_label_filter_selects_only_that_label() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = []

    list_history_entries(connection, label_code="LOYAL", page=1, per_page=25)

    statement, parameters = _statement(connection)
    assert parameters["label_code"] == "LOYAL"
    assert "h.label_code = %(label_code)s" in statement


def test_the_unassigned_filter_is_not_confused_with_no_label_filter() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = []

    list_history_entries(connection, label_code=UNASSIGNED, page=1, per_page=25)

    statement, parameters = _statement(connection)
    assert parameters["label_code"] == ""
    assert "h.label_code IS NULL" in statement


def test_all_three_filters_combine_with_and(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = []

    list_history_entries(
        connection,
        run_id=31,
        label_code="LOYAL",
        period_start=_START,
        period_end=_END,
        page=1,
        per_page=25,
    )

    statement, parameters = _statement(connection)
    where_clause = statement.upper().split("WHERE", 1)[1].split("ORDER BY")[0]
    assert where_clause.count(" AND ") >= 2
    assert parameters == {
        "run_id": 31,
        "label_code": "LOYAL",
        "period_start": _START,
        "period_end": _END,
        "limit": 25,
        "offset": 0,
    }


def test_the_period_is_closed_on_its_last_day() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = []

    list_history_entries(connection, period_end=_END, page=1, per_page=25)

    statement, _ = _statement(connection)
    assert "r.run_at < %(period_end)s::date + INTERVAL '1 day'" in statement
    assert "r.run_at <= %(period_end)s" not in statement


def test_count_and_list_use_the_same_filters() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = []
    _cursor(connection).fetchone.return_value = (0,)

    list_history_entries(connection, run_id=31, label_code="LOYAL", page=1, per_page=25)
    count_history_entries(connection, run_id=31, label_code="LOYAL")

    list_params = _statement(connection, 0)[1]
    count_params = _cursor(connection).execute.call_args_list[-1].args[1]
    assert list_params["run_id"] == count_params["run_id"] == 31
    assert list_params["label_code"] == count_params["label_code"] == "LOYAL"


# ---------- pagination and ordering ----------


def test_it_is_paged_with_limit_and_offset() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = []

    list_history_entries(connection, page=3, per_page=10)

    _, parameters = _statement(connection)
    assert parameters["limit"] == 10 and parameters["offset"] == 20


def test_rows_are_ordered_newest_run_first() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = []

    list_history_entries(connection, page=1, per_page=25)

    statement, _ = _statement(connection)
    assert re.search(r"ORDER BY r\.run_at DESC", statement)


# ---------- what a row carries ----------


def test_a_row_carries_everything_the_explanation_needs() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = [_row()]

    (entry,) = list_history_entries(connection, page=1, per_page=25)

    assert entry.history_id == 1
    assert entry.run_id == 31 and entry.method == "RFM_RULES"
    assert entry.assignment.label_code == "LOYAL"
    assert entry.assignment.r_score == 4
    assert entry.assignment.monetary_total == Decimal("400.00")


def test_a_customer_id_is_returned_as_text() -> None:
    from uuid import UUID

    connection = MagicMock()
    _cursor(connection).fetchall.return_value = [
        _row(customer_id=UUID("00000000-0000-0000-0000-000000000001"))
    ]

    assert list_history_entries(connection, page=1, per_page=25)[
        0
    ].assignment.customer_id == ("00000000-0000-0000-0000-000000000001")


def test_an_unassigned_row_carries_no_label_and_no_measurements() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = [
        _row(
            segment_id=None,
            label_code=None,
            label_name=None,
            r_score=None,
            f_score=None,
            m_score=None,
            recency_last_purchase_at=None,
            frequency_count=None,
            monetary_total=None,
        )
    ]

    (entry,) = list_history_entries(connection, page=1, per_page=25)

    assert entry.assignment.label_code is None
    assert entry.label_name is None


def test_the_two_statements_filter_identically() -> None:
    """The WHERE clause is a literal in each statement (building SQL with an
    f-string or concatenation is refused elsewhere in this codebase), so
    nothing shares it at runtime; this proves by inspection that the two
    kept in step by hand have not drifted."""
    import inspect

    def where_clause(source: str) -> str:
        start = source.index("WHERE")
        end_quote = source.index('"""', start)
        end_order = source.find("ORDER BY", start)
        end = end_order if 0 <= end_order < end_quote else end_quote
        return source[start:end]

    list_where = where_clause(inspect.getsource(list_history_entries))
    count_where = where_clause(inspect.getsource(count_history_entries))
    assert list_where.strip() == count_where.strip()


# ---------- what it may not do ----------


def test_neither_read_filters_by_method() -> None:
    """`r.method` is selected as metadata on every row, as every
    SegmentationRun-shaped reader already does; the point is that it is
    never a WHERE condition, in either statement."""
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = []
    _cursor(connection).fetchone.return_value = (0,)

    list_history_entries(connection, page=1, per_page=25)
    count_history_entries(connection)

    for call in _cursor(connection).execute.call_args_list:
        statement = call.args[0].lower()
        where_clause = statement.split("where", 1)[1]
        assert "method" not in where_clause and "cluster" not in where_clause


def test_the_module_writes_nothing() -> None:
    source = Path(report_db.__file__).read_text(encoding="utf-8")
    for pattern in (
        r"\bINSERT\s+INTO\b",
        r"\bDELETE\s+FROM\b",
        r"\bUPDATE\s+\w+\s+SET\b",
        r"\.commit\(",
    ):
        assert re.search(pattern, source, re.IGNORECASE) is None, pattern
