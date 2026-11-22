"""Assembling the filtered segment history report (F12-02).

Every applied filter -- run, label, period -- reaches both the count and the
list read, so the page and its total always agree (RN-43). Each row carries a
per-customer explanation, built the same way F7-06 already builds one: from
the row's own values and that customer's immediately preceding stored
assignment. A customer's first-ever row has no previous assignment and so no
explanation. Looking at the preceding history row, rather than the preceding
global run, remains correct when intervening incremental runs changed another
customer or when this customer changed more than once after the last full run.

Nothing here reads or is told which method produced a run.

A row is marked Synthetic when its run is SEEDED, or when its explanation
compares it with a SEEDED previous run (ADR-0052): an explanation is a
figure from both runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from web.db.segment_history_report import (
    HistoryEntry,
    count_history_entries,
    get_previous_history_assignment,
    list_history_entries,
)
from web.services.pagination import page_count
from web.services.provenance import is_seeded
from web.services.segment_migration import MigrationExplanation, explain_migration

PAGE_SIZE = 25


class InvalidPeriod(ValueError):
    """A period whose start is after its end."""


@dataclass(frozen=True)
class ReportRow:
    entry: HistoryEntry
    explanation: MigrationExplanation | None
    # The origin of the preceding assignment's run; None when there is no
    # explanation.
    previous_data_origin: str | None = None

    @property
    def synthetic(self) -> bool:
        return is_seeded(self.entry.data_origin) or (
            self.explanation is not None and is_seeded(self.previous_data_origin)
        )


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

    @property
    def synthetic(self) -> bool:
        """Whether any row on the page is Synthetic, for the banner."""
        return any(row.synthetic for row in self.rows)


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

    rows = []
    for entry in entries:
        previous = get_previous_history_assignment(connection, entry.history_id)
        explanation = previous_origin = None
        if previous is not None:
            previous_origin = previous.data_origin
            explanation = explain_migration(previous.assignment, entry.assignment)

        rows.append(
            ReportRow(
                entry=entry,
                explanation=explanation,
                previous_data_origin=previous_origin if explanation else None,
            )
        )

    return ReportPage(rows=tuple(rows), total=total, page=page, page_count=total_pages)
