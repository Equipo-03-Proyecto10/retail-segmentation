"""Consumption shifts between two periods (F8-05): noticing that a customer's
behaviour changed when it changes, not at the end of the year.

A **shift** is a change in what a customer mostly buys through, where, and of:
their dominant channel, their dominant store or their leading category. The
detector compares two periods and reports, per customer, which of the three
changed with the value before and the value after.

The definitions below are decisions (RN-36), written here because a report that
quietly meant something else would look plausible and be wrong:

* **The periods are stated, not implied.** They are arguments, and the report
  carries both, in order. Passed the wrong way round they are put in order by
  their start, never by anything else.
* **Half-open.** A period is [start, end). Two adjacent periods, one ending where
  the next begins, never both count the same instant. Overlapping periods are
  refused, because a sale in both would make the two sides less independent than
  the comparison assumes. Their lengths may differ, and each period reports its
  own, since "dominant" is a ranking and not a count.
* **"Dominant" is what the profile says it is (RN-35).** The dominant channel and
  store, and the leading category, are ranked by the same functions the
  consumption profile uses, with the same tie-breaks, so a customer's dominant
  store here is their dominant store there. A tie that resolves differently in
  the two periods because the spend moved is a change under that rule, and it is
  reported as one.
* **Absence is not a shift.** A customer with accepted sales in only one period
  is reported as absent from the other, with the side that is empty. They are not
  compared, because "no sales" is not a channel, a store or a category. A
  customer with none in either is not in the report.
* **Unchanged is not reported.** A customer with sales in both periods whose
  three leaders did not change has no entry. They are counted, so the report
  reconciles: every customer with sales is either compared or absent, and every
  compared customer is either shifted or unchanged.
* **A category needs a leader on both sides.** A purchase can exist with no
  product lines. With nothing to lead the category in one period there is nothing
  to compare, so no category shift is reported for that customer.

Only accepted sales are read (ADR-0020). Nothing here writes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from psycopg import Connection

from web.db.consumption import CategoryTotal, GroupTotal
from web.db.consumption_shift import (
    list_category_totals_by_customer,
    list_channel_totals_by_customer,
    list_store_totals_by_customer,
)
from web.services.consumption_profile import rank_categories, rank_dominant


class InvalidPeriods(ValueError):
    """The two periods cannot be compared."""


class Side(Enum):
    """Which of the two periods a customer has no sales in."""

    EARLIER = "earlier"
    LATER = "later"


@dataclass(frozen=True)
class Period:
    """A stated span of time, [start, end): first instant included, last not.
    Both ends carry a timezone."""

    start: datetime
    end: datetime

    @property
    def length(self) -> timedelta:
        return self.end - self.start


@dataclass(frozen=True)
class Leader:
    """The channel, store or category that led a customer's buying in a period."""

    item_id: int
    name: str


@dataclass(frozen=True)
class DimensionShift:
    """One dimension that changed, with the value in each period."""

    before: Leader
    after: Leader


@dataclass(frozen=True)
class CustomerLeaders:
    """What led one customer's buying in one period. A customer with accepted
    sales always has a channel and a store; `category` is None when none of
    their purchases in the period has a product line."""

    channel: Leader
    store: Leader
    category: Leader | None


@dataclass(frozen=True)
class CustomerShift:
    """A customer whose leaders changed. A dimension is None when it did not."""

    customer_id: str
    channel: DimensionShift | None = None
    store: DimensionShift | None = None
    category: DimensionShift | None = None


@dataclass(frozen=True)
class CustomerAbsence:
    """A customer with accepted sales in only one period."""

    customer_id: str
    absent_from: Side


@dataclass(frozen=True)
class ShiftReport:
    """Every shift between two stated periods.

    `compared` counts the customers with accepted sales in both periods, and
    equals `len(shifts) + unchanged`. Every customer with sales in either period
    is in `compared` or in `absences`.
    """

    earlier: Period
    later: Period
    shifts: tuple[CustomerShift, ...]
    absences: tuple[CustomerAbsence, ...]
    compared: int
    unchanged: int


# ---------- periods ----------


def _check(period: Period) -> None:
    if period.start.tzinfo is None or period.end.tzinfo is None:
        raise InvalidPeriods("A period must carry a timezone at both ends.")
    if not period.start < period.end:
        raise InvalidPeriods("A period must end after it starts.")


def order_periods(first: Period, second: Period) -> tuple[Period, Period]:
    """Return (earlier, later), ordered by each period's own start.

    Refuses a period that is empty or lacks a timezone, and two periods that
    overlap. Adjacent periods, one ending where the next begins, are comparable.
    """
    _check(first)
    _check(second)
    earlier, later = sorted((first, second), key=lambda period: period.start)
    if earlier.end > later.start:
        raise InvalidPeriods(
            "The periods overlap, so a sale could count on both sides. Choose two "
            "periods where the earlier ends no later than the later begins."
        )
    return earlier, later


def consecutive_periods(as_of: datetime, days: int) -> tuple[Period, Period]:
    """The two adjacent periods of `days` days that end at `as_of`: the
    `days` before the last `days`, and the last `days`."""
    if days < 1:
        raise InvalidPeriods("A period is at least one day.")
    span = timedelta(days=days)
    return order_periods(
        Period(as_of - 2 * span, as_of - span), Period(as_of - span, as_of)
    )


# ---------- who led, in one period ----------


def _leader(item: GroupTotal | CategoryTotal | None) -> Leader | None:
    if item is None:
        return None
    if isinstance(item, CategoryTotal):
        return Leader(item.category_id, item.name)
    return Leader(item.item_id, item.name)


def build_leaders(
    channels_by_customer: dict[str, list[GroupTotal]],
    stores_by_customer: dict[str, list[GroupTotal]],
    categories_by_customer: dict[str, list[CategoryTotal]],
) -> dict[str, CustomerLeaders]:
    """Rank each customer's channels, stores and categories for one period.

    A customer has accepted sales in the period exactly when they have a channel
    row: every purchase has one. Ranking is the consumption profile's (RN-35), so
    it does not depend on the order the rows arrive in.
    """
    leaders: dict[str, CustomerLeaders] = {}
    for customer_id, channels in channels_by_customer.items():
        channel = rank_dominant(channels)
        store = rank_dominant(stores_by_customer[customer_id])
        if channel is None or store is None:
            continue
        top_categories = rank_categories(
            categories_by_customer.get(customer_id, []), limit=1
        )
        leaders[customer_id] = CustomerLeaders(
            channel=_leader(channel),
            store=_leader(store),
            category=_leader(top_categories[0]) if top_categories else None,
        )
    return leaders


# ---------- comparing two periods ----------


def _changed(before: Leader | None, after: Leader | None) -> DimensionShift | None:
    """A change needs a leader on both sides, and a different one."""
    if before is None or after is None or before.item_id == after.item_id:
        return None
    return DimensionShift(before=before, after=after)


def compare_periods(
    earlier: Period,
    later: Period,
    before: dict[str, CustomerLeaders],
    after: dict[str, CustomerLeaders],
) -> ShiftReport:
    """Compare who led in the earlier period with who led in the later one.

    Pure: it takes the two periods as given, already ordered, and the two leader
    maps, and returns the report. Customers come out ordered by id.
    """
    shifts: list[CustomerShift] = []
    absences: list[CustomerAbsence] = []
    compared = 0

    for customer_id in sorted(set(before) | set(after)):
        if customer_id not in before:
            absences.append(CustomerAbsence(customer_id, Side.EARLIER))
            continue
        if customer_id not in after:
            absences.append(CustomerAbsence(customer_id, Side.LATER))
            continue

        compared += 1
        was, now = before[customer_id], after[customer_id]
        channel = _changed(was.channel, now.channel)
        store = _changed(was.store, now.store)
        category = _changed(was.category, now.category)
        if channel or store or category:
            shifts.append(
                CustomerShift(
                    customer_id, channel=channel, store=store, category=category
                )
            )

    return ShiftReport(
        earlier=earlier,
        later=later,
        shifts=tuple(shifts),
        absences=tuple(absences),
        compared=compared,
        unchanged=compared - len(shifts),
    )


def _leaders_in(
    connection: Connection[Any], period: Period
) -> dict[str, CustomerLeaders]:
    return build_leaders(
        list_channel_totals_by_customer(connection, period.start, period.end),
        list_store_totals_by_customer(connection, period.start, period.end),
        list_category_totals_by_customer(connection, period.start, period.end),
    )


def detect_shifts(
    connection: Connection[Any], first: Period, second: Period
) -> ShiftReport:
    """Report every customer whose dominant channel, dominant store or leading
    category changed between two periods.

    The periods are validated and ordered before anything is read, so a bad pair
    costs no query. The report carries them.
    """
    earlier, later = order_periods(first, second)
    return compare_periods(
        earlier,
        later,
        _leaders_in(connection, earlier),
        _leaders_in(connection, later),
    )
