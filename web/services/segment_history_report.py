"""Assembling the filtered segment history report (F12-02).

Every applied filter -- run, label, period -- reaches both the count and the
list read, so the page and its total always agree (RN-43). Each row carries a
per-customer explanation, built the same way F7-06 already builds one: from
the row's own values and the customer's assignment on the run immediately
before this row's run (F12-01's `get_previous_run`, reused rather than
duplicated). A customer's first-ever row has no previous run and so no
explanation; that is not an error and not a missing customer, since RN-21
guarantees every run scores every customer, so a "previous run exists but did
not score this customer" case cannot occur in practice -- the machinery
tolerates it anyway, because it costs nothing to.

Nothing here reads or is told which method produced a run.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from web.db.segment_history_report import (
    HistoryEntry,
    count_history_entries,
    list_history_entries,
)
from web.db.segmentation_dashboard import get_previous_run
from web.db.segments import get_customer_assignment_for_run
from web.services.pagination import page_count
from web.services.segment_migration import MigrationExplanation, explain_migration

PAGE_SIZE = 25


class InvalidPeriod(ValueError):
    """A period whose start is after its end."""


@dataclass(frozen=True)
class ReportRow:
    entry: HistoryEntry
    explanation: MigrationExplanation | None


@dataclass(frozen=True)
class ReportPage:
    rows: tuple[ReportRow, ...]
    total: int
    page: int
    page_count: int

    @property
    def has_previous(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.page_count


def build_report(
    connection: Any,
    *,
    run_id: int | None = None,
    label_code: str | None = None,
    period_start: date | None = None,
    period_end: date | None = None,
    page: int = 1,
) -> ReportPage:
    """One page of segment history, filtered, each row with its explanation."""
    if (
        period_start is not None
        and period_end is not None
        and period_start > period_end
    ):
        raise InvalidPeriod("The period's start must be on or before its end.")

    total = count_history_entries(
        connection,
        run_id=run_id,
        label_code=label_code,
        period_start=period_start,
        period_end=period_end,
    )
    total_pages = page_count(total, PAGE_SIZE)
    page = min(max(page, 1), total_pages)

    entries = list_history_entries(
        connection,
        run_id=run_id,
        label_code=label_code,
        period_start=period_start,
        period_end=period_end,
        page=page,
        per_page=PAGE_SIZE,
    )

    previous_runs: dict[int, Any] = {}
    rows = []
    for entry in entries:
        if entry.run_id not in previous_runs:
            previous_runs[entry.run_id] = get_previous_run(connection, entry.run_id)
        previous_run = previous_runs[entry.run_id]

        explanation = None
        if previous_run is not None:
            previous_assignment = get_customer_assignment_for_run(
                connection, previous_run.run_id, entry.assignment.customer_id
            )
            explanation = explain_migration(previous_assignment, entry.assignment)

        rows.append(ReportRow(entry=entry, explanation=explanation))

    return ReportPage(rows=tuple(rows), total=total, page=page, page_count=total_pages)
