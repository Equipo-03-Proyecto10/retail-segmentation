"""Reads behind the segmentation dashboard (F12-01).

Two reads are new. The rest of the dashboard's data comes from readers F7 and F9
already built and already tested (`list_runs`, `get_run`, `get_label_ordinals`,
`compute_migration`, `build_migration_matrix`); this file does not retest them.

What only the SQL can get wrong here: that the R/F/M read is unpaged (the dashboard
aggregates over every customer a run scored, not one page of them), that revenue is
summed over the run's own window and not some other one, and that neither statement
selects `segmentation_run.method` or a raw cluster id (ADR-0018).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

from web.db import segmentation_dashboard as dashboard_db
from web.db.segmentation_dashboard import (
    RunRfmRow,
    get_previous_run,
    list_run_revenue_by_label,
    list_run_rfm_rows,
)

_SINCE = datetime(2026, 3, 1, tzinfo=UTC)
_UNTIL = datetime(2026, 9, 1, tzinfo=UTC)
_ADA = "00000000-0000-0000-0000-000000000001"
_BOB = "00000000-0000-0000-0000-000000000002"


def _cursor(connection: MagicMock) -> MagicMock:
    return connection.cursor.return_value.__enter__.return_value


def _statement(connection: MagicMock) -> tuple[str, tuple]:
    call = _cursor(connection).execute.call_args
    return call.args[0], call.args[1]


# ---------- the R/F/M read ----------


def test_the_rfm_read_is_not_paged() -> None:
    """The dashboard aggregates over the whole run, so there is no LIMIT/OFFSET
    to get wrong by forgetting a page."""
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = []

    list_run_rfm_rows(connection, 31)

    statement, parameters = _statement(connection)
    assert parameters == (31,)
    assert "LIMIT" not in statement.upper()
    assert "OFFSET" not in statement.upper()


def test_every_customer_the_run_scored_is_returned_including_the_unassigned() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = [
        (_ADA, "LOYAL", _SINCE, 6, Decimal("400.00")),
        (_BOB, None, None, None, None),
    ]

    rows = list_run_rfm_rows(connection, 31)

    assert rows == [
        RunRfmRow(_ADA, "LOYAL", _SINCE, 6, Decimal("400.00")),
        RunRfmRow(_BOB, None, None, None, None),
    ]


def test_a_customer_id_is_returned_as_text() -> None:
    from uuid import UUID

    connection = MagicMock()
    _cursor(connection).fetchall.return_value = [(UUID(_ADA), None, None, None, None)]

    assert list_run_rfm_rows(connection, 31)[0].customer_id == _ADA


def test_the_rfm_read_selects_no_method_and_no_cluster() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = []

    list_run_rfm_rows(connection, 31)

    statement, _ = _statement(connection)
    assert "method" not in statement.lower() and "cluster" not in statement.lower()


# ---------- revenue by label ----------


def test_revenue_is_summed_over_the_given_window_as_parameters() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = []

    list_run_revenue_by_label(connection, 31, _SINCE, _UNTIL)

    statement, parameters = _statement(connection)
    assert parameters == (31, _SINCE, _UNTIL)
    assert "t.occurred_at >= %s" in statement
    assert "t.occurred_at <= %s" in statement


def test_revenue_is_grouped_by_the_runs_own_label() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = []

    list_run_revenue_by_label(connection, 31, _SINCE, _UNTIL)

    statement, _ = _statement(connection)
    assert "h.run_id = %s" in statement
    assert "GROUP BY h.label_code" in statement


def test_revenue_comes_back_as_a_mapping_from_label_to_total() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = [
        ("LOYAL", Decimal("900.00")),
        ("LOST", Decimal("140.00")),
    ]

    assert list_run_revenue_by_label(connection, 31, _SINCE, _UNTIL) == {
        "LOYAL": Decimal("900.00"),
        "LOST": Decimal("140.00"),
    }


def test_no_revenue_in_the_window_is_an_empty_mapping() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = []

    assert list_run_revenue_by_label(connection, 31, _SINCE, _UNTIL) == {}


def test_the_revenue_read_selects_no_method_and_no_cluster() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = []

    list_run_revenue_by_label(connection, 31, _SINCE, _UNTIL)

    statement, _ = _statement(connection)
    assert "method" not in statement.lower() and "cluster" not in statement.lower()


# ---------- the previous run ----------


def test_the_previous_run_is_ordered_by_run_at_then_id_strictly_before_this_one() -> (
    None
):
    connection = MagicMock()
    _cursor(connection).fetchone.return_value = None

    get_previous_run(connection, 31)

    statement, parameters = _statement(connection)
    assert parameters == (31,)
    assert "ORDER BY r.run_at DESC, r.run_id DESC" in statement
    assert "LIMIT 1" in statement


def test_no_earlier_run_is_none_not_an_error() -> None:
    connection = MagicMock()
    _cursor(connection).fetchone.return_value = None

    assert get_previous_run(connection, 31) is None


def test_the_previous_run_is_not_restricted_to_the_same_method() -> None:
    """ADR-0018: migration compares two runs by label whatever method
    produced each of them, so nothing here filters by method. `r.method` is
    still selected, as every SegmentationRun reader selects it, to describe
    the run; the point is that it is never a WHERE condition."""
    connection = MagicMock()
    _cursor(connection).fetchone.return_value = None

    get_previous_run(connection, 31)

    statement, _ = _statement(connection)
    where_clause = statement.lower().split("where", 1)[1].split("order by")[0]
    assert "method" not in where_clause


# ---------- writes ----------


def test_the_module_writes_nothing() -> None:
    source = Path(dashboard_db.__file__).read_text(encoding="utf-8")
    for pattern in (
        r"\bINSERT\s+INTO\b",
        r"\bDELETE\s+FROM\b",
        r"\bUPDATE\s+\w+\s+SET\b",
        r"\.commit\(",
    ):
        assert re.search(pattern, source, re.IGNORECASE) is None, pattern
