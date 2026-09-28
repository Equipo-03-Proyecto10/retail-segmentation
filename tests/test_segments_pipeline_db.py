"""The SQL under the segmentation pipeline (F9-01).

F3-10 did the scoring, the matching and the writing in one statement, so no
second method could reuse the writing. It is two halves now: the RFM_RULES
adapter's read, which decides, and three writes that record a run and its
assignments whichever method decided them. What only the SQL can get wrong is
checked here; what it does over real sales is in
docs/evidence/f9-01-rfm-rules-adapter.md, because a mocked cursor cannot show it.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from web.db import segments
from web.db.segments import (
    CustomerSales,
    ScoredCustomer,
    close_open_assignments,
    create_run,
    insert_assignments,
    read_open_assignments,
    read_rfm_inputs,
    score_rfm_rules,
)

_SALE = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
_ADA = "00000000-0000-0000-0000-000000000001"
_BOB = "00000000-0000-0000-0000-000000000002"


def _cursor(connection: MagicMock) -> MagicMock:
    return connection.cursor.return_value.__enter__.return_value


# ---------- the RFM_RULES adapter's read ----------


def test_the_window_is_a_parameter_and_never_interpolated() -> None:
    connection = MagicMock()
    cursor = _cursor(connection)
    cursor.fetchall.return_value = []

    score_rfm_rules(connection, 90)

    statement, parameters = cursor.execute.call_args.args
    assert parameters == (90, 5, 5, 5, 5, 5, 5)
    assert "90" not in statement


def test_every_quintile_is_ordered_deterministically() -> None:
    """Ties broken by customer_id, or two runs could disagree and both be right."""
    connection = MagicMock()
    cursor = _cursor(connection)
    cursor.fetchall.return_value = []

    score_rfm_rules(connection, 180)

    statement, _ = cursor.execute.call_args.args
    windows = [line for line in statement.splitlines() if "OVER (ORDER BY" in line]
    assert len(windows) == 3
    assert all(", customer_id)" in line for line in windows)


def test_the_adapter_read_returns_every_customer_including_those_without_sales() -> (
    None
):
    """A customer absent from the window's sales is the unassigned result
    (RN-21), recorded and not skipped, so the read starts from `customer`."""
    connection = MagicMock()
    cursor = _cursor(connection)
    cursor.fetchall.return_value = []

    score_rfm_rules(connection, 180)

    statement, _ = cursor.execute.call_args.args
    assert "FROM customer AS c" in statement


def test_the_adapter_read_writes_nothing_and_never_asks_for_a_method() -> None:
    connection = MagicMock()
    cursor = _cursor(connection)
    cursor.fetchall.return_value = []

    score_rfm_rules(connection, 180)

    statement, _ = cursor.execute.call_args.args
    upper = statement.upper()
    for verb in ("INSERT ", "UPDATE ", "DELETE "):
        assert verb not in upper, verb
    assert "method" not in statement.lower()


def test_the_adapter_read_maps_rows_in_the_order_it_selects_them() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = [
        (_ADA, _SALE, 4, Decimal("400.00"), 5, 4, 3, 1, "LOYAL"),
        (_BOB, None, None, None, None, None, None, None, None),
    ]

    rows = score_rfm_rules(connection, 180)

    assert rows == [
        ScoredCustomer(_ADA, _SALE, 4, Decimal("400.00"), 5, 4, 3, 1, "LOYAL"),
        ScoredCustomer(_BOB, None, None, None, None, None, None, None, None),
    ]


def test_a_customer_id_is_returned_as_text() -> None:
    from uuid import UUID

    connection = MagicMock()
    _cursor(connection).fetchall.return_value = [
        (UUID(_ADA), None, None, None, None, None, None, None, None)
    ]

    assert score_rfm_rules(connection, 180)[0].customer_id == _ADA


# ---------- the features a second method reads ----------


def test_a_second_methods_inputs_are_parameterized_and_cover_every_customer() -> None:
    connection = MagicMock()
    cursor = _cursor(connection)
    cursor.fetchall.return_value = [
        (_ADA, _SALE, 4, Decimal("400.00")),
        (_BOB, None, None, None),
    ]

    rows = read_rfm_inputs(connection, 60)

    statement, parameters = cursor.execute.call_args.args
    assert parameters == (60,)
    assert "60" not in statement
    assert "FROM customer AS c" in statement
    assert "ORDER BY c.customer_id" in statement
    assert rows == [
        CustomerSales(_ADA, _SALE, 4, Decimal("400.00")),
        CustomerSales(_BOB, None, None, None),
    ]


# ---------- recording the run ----------


def test_the_run_row_carries_method_window_parameters_count_and_the_actor() -> None:
    connection = MagicMock()
    cursor = _cursor(connection)
    cursor.fetchone.return_value = (11,)

    run_id = create_run(connection, "KMEANS", 45, {"k": 5, "seed": 7}, 30)

    statement, parameters = cursor.execute.call_args.args
    assert run_id == 11
    assert parameters[0] == "KMEANS" and parameters[1] == 45 and parameters[3] == 30
    assert json.loads(parameters[2]) == {"k": 5, "seed": 7}
    # The actor is the connection setting the audit trigger reads, not a Python
    # argument that a caller could forget or forge.
    assert "current_setting('mosaiq.user_id', true)" in statement
    assert "%s::jsonb" in statement


def test_the_parameters_are_stored_in_a_stable_key_order() -> None:
    connection = MagicMock()
    _cursor(connection).fetchone.return_value = (1,)

    create_run(connection, "RFM_RULES", 180, {"window_days": 180, "quintiles": 5}, 1)

    (_, parameters) = _cursor(connection).execute.call_args.args
    assert parameters[2] == '{"quintiles": 5, "window_days": 180}'


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_a_parameter_that_cannot_be_stored_as_json_is_refused_before_the_insert(
    bad,
) -> None:
    """PostgreSQL's jsonb has no NaN or Infinity. A metric that came out as one
    is a defect in the run, and it must not be written as a plausible number."""
    connection = MagicMock()

    with pytest.raises(ValueError):
        create_run(connection, "KMEANS", 180, {"inertia": bad}, 1)

    _cursor(connection).execute.assert_not_called()


def test_the_open_assignments_are_read_as_segment_and_label_by_customer() -> None:
    connection = MagicMock()
    _cursor(connection).fetchall.return_value = [
        (_ADA, 1, "LOYAL"),
        (_BOB, None, None),
    ]

    assert read_open_assignments(connection) == {
        _ADA: (1, "LOYAL"),
        _BOB: (None, None),
    }
    statement = _cursor(connection).execute.call_args.args[0]
    assert "valid_to IS NULL" in statement


def test_closing_takes_the_customers_as_a_typed_array_parameter() -> None:
    """Cast to uuid[]: a bare ANY(%s) sends text[] and PostgreSQL refuses to
    compare it with a uuid column, which a mocked cursor cannot catch."""
    connection = MagicMock()
    cursor = _cursor(connection)

    close_open_assignments(connection, [_ADA, _BOB])

    statement, parameters = cursor.execute.call_args.args
    assert "ANY(%s::uuid[])" in statement
    assert "valid_to IS NULL" in statement
    assert parameters == ([_ADA, _BOB],)
    assert _ADA not in statement


def test_closing_nobody_runs_no_statement() -> None:
    connection = MagicMock()

    close_open_assignments(connection, [])

    _cursor(connection).execute.assert_not_called()


def test_the_assignments_are_inserted_with_one_parameterized_statement() -> None:
    connection = MagicMock()
    cursor = _cursor(connection)
    rows = [
        (_ADA, 1, "LOYAL", _SALE, 4, Decimal("400.00"), 5, 4, 3),
        (_BOB, None, None, None, None, None, None, None, None),
    ]

    insert_assignments(connection, 11, rows)

    statement, batch = cursor.executemany.call_args.args
    assert "INSERT INTO customer_segment_history" in statement
    assert statement.count("%s") == 10  # customer, run, and the eight fields
    assert "now()" in statement  # closing and opening share one instant
    assert batch == [(_ADA, 11, *rows[0][1:]), (_BOB, 11, *rows[1][1:])]


def test_inserting_nothing_runs_no_statement() -> None:
    connection = MagicMock()

    insert_assignments(connection, 11, [])

    _cursor(connection).executemany.assert_not_called()


def test_the_old_single_statement_is_gone() -> None:
    """Scoring, matching and writing in one statement is what kept a second
    method from reusing the writing. Keeping it beside the pipeline would leave
    two ways to run the same method, and only one of them tested."""
    source = Path(segments.__file__).read_text(encoding="utf-8")

    assert "_RECALCULATE" not in source
    assert not re.search(r"def recalculate_segments", source)
