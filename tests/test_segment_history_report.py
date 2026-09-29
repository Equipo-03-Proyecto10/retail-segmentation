"""Assembling the filtered segment history report (F12-02).

`build_report` orchestrates the paginated, filtered read (F12-02's own) and,
for each row, the per-customer explanation F7-06 already built and tested
(`explain_migration`, from the customer's previous run, found the same way
F12-01's dashboard finds a run's previous run). Nothing here recomputes a
score or reads how a run was produced; it reuses what F7 and F9 already gave
the app.
"""

from __future__ import annotations

import inspect
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, Mock

import pytest

from web.db.segment_history_report import UNASSIGNED, HistoryEntry
from web.db.segments import RunAssignment, SegmentationRun
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
    )
    return HistoryEntry(**{**defaults, **overrides})


def _run(run_id: int, **overrides) -> SegmentationRun:
    defaults = dict(
        run_id=run_id,
        method="RFM_RULES",
        window_days=180,
        parameters={},
        customer_count=3,
        executed_by=None,
        executed_by_name=None,
        run_at=_WHEN,
    )
    return SegmentationRun(**{**defaults, **overrides})


_UNSET = object()


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    *,
    entries=_UNSET,
    total=1,
    previous=_UNSET,
    previous_assignment=_UNSET,
) -> Mock:
    manager = Mock()
    manager.list_history_entries = Mock(
        return_value=[_entry()] if entries is _UNSET else entries
    )
    manager.count_history_entries = Mock(return_value=total)
    manager.get_previous_run = Mock(
        return_value=_run(30) if previous is _UNSET else previous
    )
    manager.get_customer_assignment_for_run = Mock(
        return_value=(
            _assignment(label_code="CHAMPION", r_score=5, f_score=4, m_score=5)
            if previous_assignment is _UNSET
            else previous_assignment
        )
    )
    for name in (
        "list_history_entries",
        "count_history_entries",
        "get_previous_run",
        "get_customer_assignment_for_run",
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


def test_a_row_carries_the_explanation_from_its_customers_previous_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _wire(monkeypatch)

    report = build_report(MagicMock(), page=1)

    (row,) = report.rows
    assert row.explanation is not None
    assert row.explanation.label_before == "CHAMPION"
    assert row.explanation.label_after == "LOYAL"
    manager.get_customer_assignment_for_run.assert_called_once()
    assert manager.get_customer_assignment_for_run.call_args.args[1:] == (
        30,
        "00000000-0000-0000-0000-000000000001",
    )


def test_a_customers_first_ever_row_has_no_explanation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The row's own run has no previous run, so there is nothing to compare
    it with -- not an error, and not a customer who is absent."""
    manager = _wire(monkeypatch, previous=None)

    report = build_report(MagicMock(), page=1)

    assert report.rows[0].explanation is None
    manager.get_customer_assignment_for_run.assert_not_called()


def test_a_customer_the_previous_run_did_not_score_still_has_no_explanation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every run scores every customer (RN-21), so in practice this should
    not happen -- but the explanation machinery already handles a missing
    side (F7-06), and the report must not crash if it ever does."""
    _wire(monkeypatch, previous_assignment=None)

    report = build_report(MagicMock(), page=1)

    assert report.rows[0].explanation is not None
    assert report.rows[0].explanation.label_before is None


def test_two_rows_from_the_same_run_share_one_previous_run_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """25 rows on a page are typically only a handful of distinct runs; the
    previous-run lookup is not repeated once per row."""
    manager = _wire(
        monkeypatch,
        entries=[
            _entry(history_id=1, assignment=_assignment(customer_id="a")),
            _entry(history_id=2, assignment=_assignment(customer_id="b")),
        ],
    )

    build_report(MagicMock(), page=1)

    manager.get_previous_run.assert_called_once()


def test_rows_from_different_runs_each_look_up_their_own_previous_run(
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

    assert manager.get_previous_run.call_count == 2


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
    manager.get_previous_run.assert_not_called()


# ---------- method independence ----------


def test_building_the_report_never_reads_or_is_told_a_method() -> None:
    names = " ".join(inspect.signature(build_report).parameters)
    assert "method" not in names and "cluster" not in names
