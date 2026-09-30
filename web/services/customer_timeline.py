"""A customer's segment timeline (#337): where they are, where they were, when
they changed, from what, and which sales moved them.

Everything is read from `customer_segment_history` as runs recorded it
(ADR-0017) -- nothing is rescored. ADR-0003: the rules below are pure
functions of `HistoryRow` values, so the tests exercise every edge without a
database; `build_timeline` and `build_change` only wire the
`web.db.customer_timeline` reads into them. Nothing here reads or is told
which method produced a run (ADR-0018).

The definitions are decisions, written down because each has a plausible
alternative that would give a different answer:

* **Recency in days** is measured from the last purchase to the run that
  measured it, `run_at - last_purchase_at` in whole days. It is what the run
  saw, not how long ago the purchase is today.
* **Since** is when the customer entered the label they hold, not when the
  last run reconfirmed it. Every run closes the open row and opens a new one
  even when the label is unchanged (ADR-0017), so the latest row's
  `valid_from` is the date of the last run. Walking back over contiguous rows
  with the same label finds the first. Unassigned (RN-21) is a state like any
  label: two unassigned rows in a row are one stretch. A gap between two rows
  -- which the pipeline does not produce, since it closes and opens at one
  instant -- ends a stretch rather than being bridged.
* **As of a date** means at the end of that day, in the connection's session
  time zone: the assignment open at the first instant of the next day. A row
  closed exactly at that instant was open for the whole of the day asked
  about, and is the answer; the row opened at that instant is not.
* **Previous** is the assignment immediately before another, whatever its
  label -- the same "previous run" F12-02's report explains against.
* **What moved the customer** between two consecutive assignments is two lists
  of accepted sales: those inside the later run's window and not the earlier
  one's (they *entered* the calculation), and those inside the earlier window
  and not the later one (they *left* it, by ageing out). A customer who drops a
  segment without buying anything has an empty first list and a non-empty
  second, which is the answer the first list alone could not give. The limits
  of rebuilding a window after the fact are stated in
  `web/db/customer_timeline.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from decimal import Decimal
from typing import Any

from psycopg import Connection

from web.db.consumption import HistoryRow
from web.db.customer_timeline import (
    SaleRow,
    list_history_rows,
    list_sales_in_window_only,
)
from web.services.segment_migration import MigrationExplanation, explain_migration


class UnknownChange(LookupError):
    """The run did not assign this customer, so there is no change to open."""


@dataclass(frozen=True)
class TimelineEntry:
    """One assignment, with what the timeline derives from its neighbours.

    `label_since` is when the customer entered this label (see "Since" in the
    module docstring). `changed` is True when the assignment immediately
    before this one held a different label; the first assignment a customer
    ever had has no previous one, so it is False there and `is_first` says so.
    """

    row: HistoryRow
    label_since: datetime
    recency_days: int | None
    changed: bool
    is_first: bool

    @property
    def is_open(self) -> bool:
        return self.row.valid_to is None


@dataclass(frozen=True)
class Timeline:
    """Every assignment newest first, the current and previous one, and the
    answer to an "as of" question when one was asked."""

    entries: tuple[TimelineEntry, ...]
    current: TimelineEntry | None
    previous: TimelineEntry | None
    comparison: MigrationExplanation | None
    as_of: date | None = None
    as_of_entry: TimelineEntry | None = None


@dataclass(frozen=True)
class RunWindow:
    """The span of accepted sales one run measured, both ends inclusive."""

    start: datetime
    end: datetime


@dataclass(frozen=True)
class SegmentChange:
    """One assignment against the one immediately before it, with the sales
    that entered and left the calculation between the two runs. `previous`
    is None for the customer's first assignment, and then there is no
    explanation and no sales to compare."""

    entry: TimelineEntry
    previous: TimelineEntry | None
    explanation: MigrationExplanation | None
    window: RunWindow
    previous_window: RunWindow | None
    entered: tuple[SaleRow, ...]
    left: tuple[SaleRow, ...]

    @property
    def entered_total(self) -> Decimal:
        return sum((sale.total for sale in self.entered), Decimal(0))

    @property
    def left_total(self) -> Decimal:
        return sum((sale.total for sale in self.left), Decimal(0))


# ---------- the rules, pure ----------


def recency_days(last_purchase_at: datetime | None, run_at: datetime) -> int | None:
    """Whole days from the last purchase to the run that measured it, or None
    when the run found no purchase (an unassigned result, RN-21)."""
    if last_purchase_at is None:
        return None
    return (run_at - last_purchase_at).days


def _contiguous(earlier: HistoryRow, later: HistoryRow) -> bool:
    return earlier.valid_to is not None and earlier.valid_to == later.valid_from


def build_entries(rows: list[HistoryRow]) -> tuple[TimelineEntry, ...]:
    """Derive every entry from rows given newest first, and return them in
    the same order."""
    oldest_first = list(reversed(rows))
    entries: list[TimelineEntry] = []
    for index, row in enumerate(oldest_first):
        before = oldest_first[index - 1] if index > 0 else None
        continues = (
            before is not None
            and before.label_code == row.label_code
            and _contiguous(before, row)
        )
        entries.append(
            TimelineEntry(
                row=row,
                label_since=entries[-1].label_since if continues else row.valid_from,
                recency_days=recency_days(row.last_purchase_at, row.run_at),
                changed=before is not None and before.label_code != row.label_code,
                is_first=before is None,
            )
        )
    return tuple(reversed(entries))


def end_of_day(day: date, zone: tzinfo | None) -> datetime:
    """The first instant after `day`, in `zone`."""
    return datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)


def entry_as_of(
    entries: tuple[TimelineEntry, ...], boundary: datetime
) -> TimelineEntry | None:
    """The assignment open at the end of the day `boundary` closes, or None
    when no run had scored the customer by then."""
    for entry in entries:
        row = entry.row
        if row.valid_from < boundary and (
            row.valid_to is None or row.valid_to >= boundary
        ):
            return entry
    return None


def run_window(row: HistoryRow) -> RunWindow:
    """The span a run measured, for the page to state. The sales themselves
    are selected in SQL with the run's own interval arithmetic."""
    return RunWindow(start=row.run_at - timedelta(days=row.window_days), end=row.run_at)


def _neighbours(
    entries: tuple[TimelineEntry, ...], index: int
) -> tuple[TimelineEntry, TimelineEntry | None]:
    entry = entries[index]
    return entry, entries[index + 1] if index + 1 < len(entries) else None


# ---------- wiring the reads in ----------


def build_timeline(
    connection: Connection[Any], customer_id: Any, *, as_of: date | None = None
) -> Timeline:
    """Every assignment the customer has held, and, when `as_of` is given, the
    one open at the end of that day."""
    entries = build_entries(list_history_rows(connection, customer_id))

    current = previous = None
    comparison = None
    if entries and entries[0].is_open:
        current, previous = _neighbours(entries, 0)
        if previous is not None:
            comparison = explain_migration(previous.row, current.row)

    as_of_entry = None
    if as_of is not None:
        as_of_entry = entry_as_of(entries, end_of_day(as_of, connection.info.timezone))

    return Timeline(
        entries=entries,
        current=current,
        previous=previous,
        comparison=comparison,
        as_of=as_of,
        as_of_entry=as_of_entry,
    )


def build_change(
    connection: Connection[Any], customer_id: Any, run_id: int
) -> SegmentChange:
    """The customer's assignment from `run_id` against the one before it.

    Raises UnknownChange when that run did not assign the customer.
    """
    entries = build_entries(list_history_rows(connection, customer_id))
    index = next(
        (i for i, entry in enumerate(entries) if entry.row.run_id == run_id), None
    )
    if index is None:
        raise UnknownChange(run_id)
    entry, previous = _neighbours(entries, index)

    if previous is None:
        return SegmentChange(
            entry=entry,
            previous=None,
            explanation=None,
            window=run_window(entry.row),
            previous_window=None,
            entered=(),
            left=(),
        )

    entered = list_sales_in_window_only(
        connection,
        customer_id,
        run_id=entry.row.run_id,
        excluding_run_id=previous.row.run_id,
    )
    left = list_sales_in_window_only(
        connection,
        customer_id,
        run_id=previous.row.run_id,
        excluding_run_id=entry.row.run_id,
    )
    return SegmentChange(
        entry=entry,
        previous=previous,
        explanation=explain_migration(previous.row, entry.row),
        window=run_window(entry.row),
        previous_window=run_window(previous.row),
        entered=tuple(entered),
        left=tuple(left),
    )
