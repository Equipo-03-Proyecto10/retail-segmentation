"""Assembling the filtered segment history report (F12-02).

`build_report` orchestrates the paginated, filtered read (F12-02's own) and,
for each row, the per-customer explanation F7-06 already built and tested
(`explain_migration`, from the customer's immediately preceding stored
assignment). Nothing here recomputes a score or reads how a run was produced;
it reuses what F7 and F9 already gave the app.
"""

from __future__ import annotations

import inspect
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, Mock

import pytest

from web.db.segment_history_report import (
    UNASSIGNED,
    HistoryEntry,
    PreviousHistoryAssignment,
)
from web.db.segments import RunAssignment
from web.services import segment_history_report as service
from web.services.segment_history_report import InvalidPeriod, build_report

_WHEN = datetime(2026, 9, 28, tzinfo=UTC)
_EARLIER = datetime(2026, 6, 1, tzinfo=UTC)


def _assignment(**overrides) -> RunAssignment:
    defaults = dict(
        customer_id="00000000-0000-0000-0000-000000000001",
        customer_name="Ada Lovelace",
        segment_id=1,
        label_code="LOYAL",
        r_score=4,
        f_score=3,
        m_score=5,
        recency_last_purchase_at=_WHEN,
        frequency_count=6,
        monetary_total=Decimal("400.00"),
    )
    return RunAssignment(**{**defaults, **overrides})


def _entry(**overrides) -> HistoryEntry:
    defaults = dict(
        history_id=2,
        run_id=31,
        run_at=_WHEN,
        method="RFM_RULES",
        window_days=180,
        valid_from=_WHEN,
        valid_to=None,
        label_name="Loyal",
        assignment=_assignment(),
        data_origin="OBSERVED",
    )
    return HistoryEntry(**{**defaults, **overrides})


_UNSET = object()


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    *,
    entries=_UNSET,
    total=1,
    previous_assignment=_UNSET,
    previous_origin="SEEDED",
) -> Mock:
    manager = Mock()
    manager.list_history_entries = Mock(
        return_value=[_entry()] if entries is _UNSET else entries
    )
    manager.count_history_entries = Mock(return_value=total)
    assignment = (
        _assignment(label_code="CHAMPION", r_score=5, f_score=4, m_score=5)
        if previous_assignment is _UNSET
        else previous_assignment
    )
    manager.get_previous_history_assignment = Mock(
        return_value=(
            PreviousHistoryAssignment(assignment, previous_origin)
            if assignment is not None
            else None
        )
    )
    for name in (
        "list_history_entries",
        "count_history_entries",
        "get_previous_history_assignment",
    ):
        monkeypatch.setattr(service, name, getattr(manager, name))
    return manager


# ---------- filters combine and are reflected ----------


def test_every_applied_filter_reaches_both_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _wire(monkeypatch)

    build_report(
        MagicMock(),
        run_id=31,
        label_code="LOYAL",
        period_start=date(2026, 4, 1),
        period_end=date(2026, 9, 1),
        page=1,
    )

    list_kwargs = manager.list_history_entries.call_args.kwargs
    count_kwargs = manager.count_history_entries.call_args.kwargs
    for kwargs in (list_kwargs, count_kwargs):
        assert kwargs["run_id"] == 31
        assert kwargs["label_code"] == "LOYAL"
        assert kwargs["period_start"] == date(2026, 4, 1)
        assert kwargs["period_end"] == date(2026, 9, 1)


def test_the_unassigned_choice_is_translated_to_the_reads_sentinel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _wire(monkeypatch)

    build_report(MagicMock(), label_code=UNASSIGNED, page=1)

    assert manager.list_history_entries.call_args.kwargs["label_code"] == UNASSIGNED


def test_a_period_with_the_start_after_the_end_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _wire(monkeypatch)

    with pytest.raises(InvalidPeriod):
        build_report(
            MagicMock(),
            period_start=date(2026, 9, 1),
            period_end=date(2026, 4, 1),
            page=1,
        )

    manager.list_history_entries.assert_not_called()


# ---------- the per-row explanation ----------


def test_a_row_carries_the_explanation_from_its_previous_assignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _wire(monkeypatch)

    report = build_report(MagicMock(), page=1)

    (row,) = report.rows
    assert row.explanation is not None
    assert row.explanation.label_before == "CHAMPION"
    assert row.explanation.label_after == "LOYAL"
    manager.get_previous_history_assignment.assert_called_once()
    assert manager.get_previous_history_assignment.call_args.args[1:] == (2,)


def test_an_observed_row_explained_against_a_seeded_run_is_synthetic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#560, ADR-0052: the explanation reads both runs, so either marks it."""
    _wire(monkeypatch, previous_origin="SEEDED")

    report = build_report(MagicMock(), page=1)

    (row,) = report.rows
    assert row.entry.data_origin == "OBSERVED"
    assert row.previous_data_origin == "SEEDED"
    assert row.synthetic and report.synthetic


def test_an_observed_row_against_an_observed_run_is_not_synthetic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(monkeypatch, previous_origin="OBSERVED")

    report = build_report(MagicMock(), page=1)

    assert not report.rows[0].synthetic and not report.synthetic


def test_a_customers_first_ever_row_has_no_explanation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first stored assignment has nothing to compare with."""
    _wire(monkeypatch, previous_assignment=None)

    report = build_report(MagicMock(), page=1)

    assert report.rows[0].explanation is None


def test_an_unassigned_previous_row_is_not_confused_with_no_previous_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(
        monkeypatch,
        previous_assignment=_assignment(
            segment_id=None,
            label_code=None,
            r_score=None,
            f_score=None,
            m_score=None,
            recency_last_purchase_at=None,
            frequency_count=None,
            monetary_total=None,
        ),
    )

    report = build_report(MagicMock(), page=1)

    assert report.rows[0].explanation is not None
    assert report.rows[0].explanation.label_before is None


def test_each_row_looks_up_that_customers_previous_assignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _wire(
        monkeypatch,
        entries=[
            _entry(history_id=1, assignment=_assignment(customer_id="a")),
            _entry(history_id=2, assignment=_assignment(customer_id="b")),
        ],
    )

    build_report(MagicMock(), page=1)

    assert manager.get_previous_history_assignment.call_count == 2
    assert [
        call.args[1] for call in manager.get_previous_history_assignment.call_args_list
    ] == [1, 2]


def test_rows_from_different_runs_still_use_each_rows_history_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _wire(
        monkeypatch,
        entries=[
            _entry(history_id=1, run_id=31, assignment=_assignment(customer_id="a")),
            _entry(history_id=2, run_id=30, assignment=_assignment(customer_id="b")),
        ],
    )

    build_report(MagicMock(), page=1)

    assert manager.get_previous_history_assignment.call_count == 2


# ---------- pagination ----------


def test_the_report_carries_the_total_and_page(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(monkeypatch, total=57)

    report = build_report(MagicMock(), page=2)

    assert report.total == 57
    assert report.page == 2
    assert report.page_count == 3  # 57 rows at 25 a page


def test_a_page_past_the_end_is_clamped_to_the_last_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(monkeypatch, total=1)

    report = build_report(MagicMock(), page=99)

    assert report.page == 1


# ---------- no rows ----------


def test_no_rows_is_an_empty_report_not_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _wire(monkeypatch, entries=[], total=0)

    report = build_report(MagicMock(), run_id=999, page=1)

    assert report.rows == () and report.total == 0
    manager.get_previous_history_assignment.assert_not_called()


# ---------- method independence ----------


def test_building_the_report_never_reads_or_is_told_a_method() -> None:
    names = " ".join(inspect.signature(build_report).parameters)
    assert "method" not in names and "cluster" not in names
