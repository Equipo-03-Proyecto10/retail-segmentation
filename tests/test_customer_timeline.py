"""The rules behind a customer's segment timeline (#337).

`web/services/customer_timeline.py` is pure over `HistoryRow` values
(ADR-0003), so every edge here is stated with plain rows: recency in days, the
date a customer entered a label across unchanged reruns, which assignment was
open at the end of a given day, and which two runs a change compares. The reads
are replaced, so each test says exactly which rows the service was given; what
the SQL itself selects is in tests/test_customer_timeline_db.py.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from web.db.consumption import HistoryRow
from web.db.customer_timeline import SaleRow
from web.services import customer_timeline
from web.services.customer_timeline import (
    UnknownChange,
    build_change,
    build_entries,
    build_timeline,
    end_of_day,
    entry_as_of,
    recency_days,
    run_window,
)
from web.services.segment_migration import explain_migration

_CUSTOMER = "00000000-0000-0000-0000-000000000001"
_JULY = datetime(2026, 7, 1, 3, 0, tzinfo=UTC)
_AUGUST = datetime(2026, 8, 1, 3, 0, tzinfo=UTC)
_SEPTEMBER = datetime(2026, 9, 1, 3, 0, tzinfo=UTC)


def _row(
    run_id: int,
    label: str | None,
    valid_from: datetime,
    valid_to: datetime | None,
    *,
    last_purchase_at: datetime | None = None,
    frequency: int | None = 4,
    monetary: str | None = "1200.00",
    scores: tuple[int | None, int | None, int | None] = (3, 3, 3),
    window_days: int = 180,
) -> HistoryRow:
    unassigned = label is None
    return HistoryRow(
        run_id=run_id,
        label_code=label,
        label_name=None if unassigned else label.replace("_", " ").title(),
        last_purchase_at=(
            None if unassigned else last_purchase_at or valid_from - timedelta(days=12)
        ),
        frequency_count=None if unassigned else frequency,
        monetary_total=None if unassigned else Decimal(monetary),
        r_score=None if unassigned else scores[0],
        f_score=None if unassigned else scores[1],
        m_score=None if unassigned else scores[2],
        valid_from=valid_from,
        valid_to=valid_to,
        run_at=valid_from,
        window_days=window_days,
    )


def _three_runs(first: str | None, second: str | None, third: str | None):
    """Three contiguous assignments, newest first, as the read returns them."""
    return [
        _row(3, third, _SEPTEMBER, None),
        _row(2, second, _AUGUST, _SEPTEMBER),
        _row(1, first, _JULY, _AUGUST),
    ]


# ---------- recency in days (AC 3) ----------


def test_recency_is_the_whole_days_from_last_purchase_to_the_run() -> None:
    run_at = datetime(2026, 9, 28, 0, 39, tzinfo=UTC)
    assert recency_days(run_at - timedelta(days=12, hours=5), run_at) == 12


def test_recency_is_none_when_the_run_found_no_purchase() -> None:
    assert recency_days(None, _SEPTEMBER) is None


def test_every_entry_carries_its_recency_in_days() -> None:
    entries = build_entries(
        [_row(1, "LOYAL", _JULY, None, last_purchase_at=_JULY - timedelta(days=1))]
    )
    assert entries[0].recency_days == 1


# ---------- since: the date the customer entered the label (AC 5) ----------


def test_since_on_an_unchanged_rerun_is_when_the_label_was_entered() -> None:
    entries = build_entries(_three_runs("LOYAL", "LOYAL", "LOYAL"))

    assert [entry.label_since for entry in entries] == [_JULY, _JULY, _JULY]
    # Not the last run's date, which is what valid_from alone would say.
    assert entries[0].row.valid_from == _SEPTEMBER


def test_since_restarts_when_the_label_changes() -> None:
    entries = build_entries(_three_runs("LOYAL", "AT_RISK", "AT_RISK"))
    assert [entry.label_since for entry in entries] == [_AUGUST, _AUGUST, _JULY]


def test_an_unassigned_run_ends_a_stretch_of_one_label() -> None:
    entries = build_entries(_three_runs("LOYAL", None, "LOYAL"))
    assert entries[0].label_since == _SEPTEMBER


def test_consecutive_unassigned_runs_are_one_stretch() -> None:
    entries = build_entries(_three_runs("LOYAL", None, None))
    assert entries[0].label_since == _AUGUST


def test_a_gap_between_two_rows_is_not_bridged() -> None:
    rows = [
        _row(2, "LOYAL", _SEPTEMBER, None),
        _row(1, "LOYAL", _JULY, _AUGUST),
    ]
    assert build_entries(rows)[0].label_since == _SEPTEMBER


def test_changed_marks_only_an_assignment_whose_label_differs_from_the_one_before() -> (
    None
):
    entries = build_entries(_three_runs("LOYAL", "LOYAL", "AT_RISK"))

    assert [entry.changed for entry in entries] == [True, False, False]
    assert [entry.is_first for entry in entries] == [False, False, True]


# ---------- as of a date (AC 2) ----------


def test_end_of_day_is_the_first_instant_of_the_next_day_in_the_zone() -> None:
    zone = timezone(timedelta(hours=-6))
    assert end_of_day(date(2026, 8, 15), zone) == datetime(2026, 8, 16, tzinfo=zone)


def test_as_of_a_day_inside_an_assignment_finds_it() -> None:
    entries = build_entries(_three_runs("LOYAL", "AT_RISK", "CHAMPION"))
    found = entry_as_of(entries, end_of_day(date(2026, 8, 15), UTC))
    assert found is not None and found.row.run_id == 2


def test_as_of_the_day_a_run_closed_an_assignment_finds_the_one_open_at_its_end() -> (
    None
):
    entries = build_entries(_three_runs("LOYAL", "AT_RISK", "CHAMPION"))
    # Run 3 opened at 03:00 on 1 September, so by the end of that day it is open.
    found = entry_as_of(entries, end_of_day(date(2026, 9, 1), UTC))
    assert found is not None and found.row.run_id == 3


def test_an_assignment_closed_exactly_at_the_boundary_was_open_all_that_day() -> None:
    boundary = datetime(2026, 8, 16, tzinfo=UTC)
    rows = [
        _row(2, "AT_RISK", boundary, None),
        _row(1, "LOYAL", _JULY, boundary),
    ]
    found = entry_as_of(build_entries(rows), end_of_day(date(2026, 8, 15), UTC))
    assert found is not None and found.row.run_id == 1


def test_as_of_a_day_before_the_first_run_finds_nothing() -> None:
    entries = build_entries(_three_runs("LOYAL", "AT_RISK", "CHAMPION"))
    assert entry_as_of(entries, end_of_day(date(2026, 6, 1), UTC)) is None


def test_as_of_a_day_after_the_last_run_finds_the_open_assignment() -> None:
    entries = build_entries(_three_runs("LOYAL", "AT_RISK", "CHAMPION"))
    found = entry_as_of(entries, end_of_day(date(2027, 1, 1), UTC))
    assert found is not None and found.is_open


# ---------- assembling the timeline (AC 1, 2, 3) ----------


def _connection(zone=UTC) -> MagicMock:
    connection = MagicMock()
    connection.info.timezone = zone
    return connection


def test_the_timeline_lists_every_assignment_with_current_and_previous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = _three_runs("LOYAL", "LOYAL", "AT_RISK")
    monkeypatch.setattr(customer_timeline, "list_history_rows", lambda *_: rows)

    timeline = build_timeline(_connection(), _CUSTOMER)

    assert [entry.row.run_id for entry in timeline.entries] == [3, 2, 1]
    assert timeline.current.row.run_id == 3
    assert timeline.previous.row.run_id == 2
    assert timeline.comparison.label_before == "LOYAL"
    assert timeline.comparison.label_after == "AT_RISK"
    assert timeline.as_of is None and timeline.as_of_entry is None


def test_the_comparison_carries_both_runs_raw_recency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = _three_runs("LOYAL", "LOYAL", "AT_RISK")
    monkeypatch.setattr(customer_timeline, "list_history_rows", lambda *_: rows)

    comparison = build_timeline(_connection(), _CUSTOMER).comparison

    assert comparison.recency.raw_before == rows[1].last_purchase_at
    assert comparison.recency.raw_after == rows[0].last_purchase_at


def test_a_history_row_stands_in_for_explain_migration() -> None:
    before = _row(1, "LOYAL", _JULY, _AUGUST, scores=(5, 3, 3))
    after = _row(2, "AT_RISK", _AUGUST, None, scores=(2, 3, 3))

    explanation = explain_migration(before, after)

    assert before.recency_last_purchase_at == before.last_purchase_at
    assert explanation.recency.raw_before == before.last_purchase_at
    assert explanation.recency.score_delta == -3


def test_a_customer_never_scored_has_an_empty_timeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(customer_timeline, "list_history_rows", lambda *_: [])

    timeline = build_timeline(_connection(), _CUSTOMER, as_of=date(2026, 8, 1))

    assert timeline.entries == ()
    assert timeline.current is None and timeline.previous is None
    assert timeline.comparison is None and timeline.as_of_entry is None


def test_a_single_assignment_has_no_previous_and_no_comparison(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        customer_timeline,
        "list_history_rows",
        lambda *_: [_row(1, "LOYAL", _JULY, None)],
    )

    timeline = build_timeline(_connection(), _CUSTOMER)

    assert timeline.current.row.run_id == 1
    assert timeline.previous is None and timeline.comparison is None


def test_as_of_is_read_at_the_end_of_the_day_in_the_session_time_zone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Run 3 opens at 03:00 UTC on 1 September: 21:00 on 31 August in UTC-6.
    rows = _three_runs("LOYAL", "AT_RISK", "CHAMPION")
    monkeypatch.setattr(customer_timeline, "list_history_rows", lambda *_: rows)
    mexico_city = timezone(timedelta(hours=-6))

    in_utc = build_timeline(_connection(UTC), _CUSTOMER, as_of=date(2026, 8, 31))
    in_mexico = build_timeline(
        _connection(mexico_city), _CUSTOMER, as_of=date(2026, 8, 31)
    )

    assert in_utc.as_of_entry.row.run_id == 2
    assert in_mexico.as_of_entry.row.run_id == 3


# ---------- a change and the sales behind it (AC 4) ----------


def _sale(transaction_id: int, total: str, occurred_at: datetime) -> SaleRow:
    return SaleRow(
        transaction_id=transaction_id,
        source_transaction_id=f"T-{transaction_id}",
        occurred_at=occurred_at,
        total=Decimal(total),
        store_name="Centro",
        channel_name="Store",
        units=2,
    )


def test_a_change_lists_sales_that_entered_and_left_between_the_two_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = _three_runs("LOYAL", "LOYAL", "AT_RISK")
    monkeypatch.setattr(customer_timeline, "list_history_rows", lambda *_: rows)
    calls = []

    def sales(_connection, _customer, *, run_id, excluding_run_id):
        calls.append((run_id, excluding_run_id))
        if run_id == 3:
            return [_sale(10, "150.00", _AUGUST + timedelta(days=3))]
        return [_sale(7, "80.50", _JULY), _sale(8, "19.50", _JULY)]

    monkeypatch.setattr(customer_timeline, "list_sales_in_window_only", sales)

    change = build_change(_connection(), _CUSTOMER, 3)

    assert calls == [(3, 2), (2, 3)]
    assert change.entry.row.run_id == 3
    assert change.previous.row.run_id == 2
    assert change.explanation.label_changed
    assert [sale.transaction_id for sale in change.entered] == [10]
    assert [sale.transaction_id for sale in change.left] == [7, 8]
    assert change.entered_total == Decimal("150.00")
    assert change.left_total == Decimal("100.00")


def test_a_change_states_both_runs_windows() -> None:
    row = _row(3, "AT_RISK", _SEPTEMBER, None, window_days=90)
    window = run_window(row)
    assert window.end == _SEPTEMBER
    assert window.start == _SEPTEMBER - timedelta(days=90)


def test_the_first_assignment_has_nothing_to_compare_and_reads_no_sales(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = _three_runs("LOYAL", "LOYAL", "AT_RISK")
    monkeypatch.setattr(customer_timeline, "list_history_rows", lambda *_: rows)

    def refuse(*_args, **_kwargs):
        raise AssertionError("a first assignment has no previous window")

    monkeypatch.setattr(customer_timeline, "list_sales_in_window_only", refuse)

    change = build_change(_connection(), _CUSTOMER, 1)

    assert change.previous is None and change.explanation is None
    assert change.entered == () and change.left == ()


def test_a_run_that_did_not_assign_the_customer_is_an_unknown_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = _three_runs("LOYAL", "LOYAL", "AT_RISK")
    monkeypatch.setattr(customer_timeline, "list_history_rows", lambda *_: rows)

    with pytest.raises(UnknownChange):
        build_change(_connection(), _CUSTOMER, 99)
