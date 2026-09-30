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
  store here is their dominant store there.
* **A shift is claimed only between two clear leaders (RN-50, #341).** Each
  period needs at least `MIN_PURCHASES_PER_PERIOD` purchases -- for a category,
  purchases with product lines -- and a leader with strictly more purchases than
  the runner-up. Spend still names the dominant value on a tie, as RN-35 says,
  but it never decides a shift: a 1-vs-1 tie that the spend happened to break
  differently in each period is not a change of behaviour. Such a customer is
  compared and counted as undecided, not shifted.
* **Shares are shares of purchases.** A channel's or a store's share is its
  purchases over the customer's purchases in the period. A category's is the
  purchases containing it over the purchases with product lines, so the shares of
  several categories can add up to more than 100 %: one basket can hold two.
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

All totals for both periods and all three dimensions are fetched in one SQL
statement. This gives detection one READ COMMITTED snapshot even when a CSV
load containing back-dated sales commits while a report is being produced.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Any

from psycopg import Connection

from web.db.consumption import CategoryTotal, GroupTotal
from web.db.consumption_shift import list_totals_for_periods
from web.services.consumption_profile import (
    TOP_CATEGORIES,
    rank_categories,
    rank_dominant,
)

# RN-50: below this many purchases in a period, one purchase decides who leads,
# so no shift is claimed. With three, a clear leader needs at least two of them.
MIN_PURCHASES_PER_PERIOD = 3


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
    """The channel, store or category that led a customer's buying in a period.

    Two leaders are the same leader when their ids are: the counts below say how
    clearly it led and are left out of equality (#341). `period_purchases` is the
    base its share is taken over, and `runner_up_purchases` the purchases of the
    next one, None when there was no other.
    """

    item_id: int
    name: str
    purchases: int = field(default=0, compare=False)
    period_purchases: int = field(default=0, compare=False)
    runner_up_purchases: int | None = field(default=None, compare=False)

    @property
    def share(self) -> Decimal | None:
        """Whole percent of the period's purchases, rounding half up."""
        if not self.period_purchases:
            return None
        return (Decimal(self.purchases) * 100 / self.period_purchases).quantize(
            Decimal(1), rounding=ROUND_HALF_UP
        )

    @property
    def too_few(self) -> bool:
        return self.period_purchases < MIN_PURCHASES_PER_PERIOD

    @property
    def tied(self) -> bool:
        return self.runner_up_purchases == self.purchases

    @property
    def decisive(self) -> bool:
        """Whether this period can take part in a claimed shift (RN-50)."""
        return not self.too_few and not self.tied


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
    # The top categories with their shares, for the profile (#341).
    categories: tuple[Leader, ...] = field(default=(), compare=False)
    purchases: int = field(default=0, compare=False)


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
    is in `compared` or in `absences`. `undecided` is the part of `unchanged`
    whose leader did change but not clearly enough to claim it (RN-50).
    """

    earlier: Period
    later: Period
    shifts: tuple[CustomerShift, ...]
    absences: tuple[CustomerAbsence, ...]
    compared: int
    unchanged: int
    undecided: int = 0


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


def _group_leader(groups: list[GroupTotal], period_purchases: int) -> Leader | None:
    """The dominant channel or store (RN-35), with how clearly it led."""
    top = rank_dominant(groups)
    if top is None:
        return None
    others = [g.purchases for g in groups if g.item_id != top.item_id]
    return Leader(
        top.item_id,
        top.name,
        purchases=top.purchases,
        period_purchases=period_purchases,
        runner_up_purchases=max(others) if others else None,
    )


def _category_leaders(
    groups: list[CategoryTotal], lined_purchases: int
) -> tuple[Leader, ...]:
    """Every category in RN-35's order, each with its share of the purchases
    that have product lines; the first carries the runner-up's purchases."""
    ranked = rank_categories(groups, limit=len(groups))
    return tuple(
        Leader(
            category.category_id,
            category.name,
            purchases=category.purchases,
            period_purchases=lined_purchases,
            runner_up_purchases=(
                ranked[1].purchases if index == 0 and len(ranked) > 1 else None
            ),
        )
        for index, category in enumerate(ranked)
    )


def build_leaders(
    channels_by_customer: dict[str, list[GroupTotal]],
    stores_by_customer: dict[str, list[GroupTotal]],
    categories_by_customer: dict[str, list[CategoryTotal]],
    lined_by_customer: dict[str, int] | None = None,
) -> dict[str, CustomerLeaders]:
    """Rank each customer's channels, stores and categories for one period.

    A customer has accepted sales in the period exactly when they have a channel
    row: every purchase has one. Ranking is the consumption profile's (RN-35), so
    it does not depend on the order the rows arrive in.
    """
    leaders: dict[str, CustomerLeaders] = {}
    for customer_id, channels in channels_by_customer.items():
        # Every purchase has exactly one channel, so this is the period's count.
        purchases = sum(group.purchases for group in channels)
        channel = _group_leader(channels, purchases)
        store = _group_leader(stores_by_customer[customer_id], purchases)
        if channel is None or store is None:
            continue
        lined = (
            purchases
            if lined_by_customer is None
            else lined_by_customer.get(customer_id, 0)
        )
        categories = _category_leaders(
            categories_by_customer.get(customer_id, []), lined
        )
        leaders[customer_id] = CustomerLeaders(
            channel=channel,
            store=store,
            category=categories[0] if categories else None,
            categories=categories[:TOP_CATEGORIES],
            purchases=purchases,
        )
    return leaders


# ---------- comparing two periods ----------


def _differs(before: Leader | None, after: Leader | None) -> bool:
    return before is not None and after is not None and before.item_id != after.item_id


def _changed(before: Leader | None, after: Leader | None) -> DimensionShift | None:
    """A change needs a leader on both sides, a different one, and both of them
    clear (RN-50)."""
    if not _differs(before, after) or not (before.decisive and after.decisive):
        return None
    return DimensionShift(before=before, after=after)


def _undecided(before: Leader | None, after: Leader | None) -> bool:
    """The leader changed, but not clearly enough to claim a shift."""
    return _differs(before, after) and _changed(before, after) is None


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
    compared = undecided = 0

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
        elif (
            _undecided(was.channel, now.channel)
            or _undecided(was.store, now.store)
            or _undecided(was.category, now.category)
        ):
            undecided += 1

    return ShiftReport(
        earlier=earlier,
        later=later,
        shifts=tuple(shifts),
        absences=tuple(absences),
        compared=compared,
        unchanged=compared - len(shifts),
        undecided=undecided,
    )


def detect_shifts(
    connection: Connection[Any], first: Period, second: Period
) -> ShiftReport:
    """Report every customer whose dominant channel, dominant store or leading
    category changed between two periods.

    The periods are validated and ordered before anything is read, so a bad pair
    costs no query. One statement reads both periods from one database snapshot.
    """
    earlier, later = order_periods(first, second)
    earlier_totals, later_totals = list_totals_for_periods(
        connection,
        earlier.start,
        earlier.end,
        later.start,
        later.end,
    )
    return compare_periods(
        earlier,
        later,
        build_leaders(*earlier_totals),
        build_leaders(*later_totals),
    )


# ---------- one customer, on their profile (#341) ----------


class ShiftStatus(Enum):
    SHIFTED = "shifted"
    UNCHANGED = "unchanged"
    UNDECIDED = "undecided"
    NOT_COMPARED = "not compared"


@dataclass(frozen=True)
class DimensionComparison:
    """One dimension's leader in each period, and what can be said about it."""

    name: str
    before: Leader | None
    after: Leader | None
    # What the period's purchases are, for a sentence: "purchases", or
    # "purchases with product lines" for categories.
    basis: str = "purchases"

    @property
    def status(self) -> ShiftStatus:
        if self.before is None or self.after is None:
            return ShiftStatus.NOT_COMPARED
        if _changed(self.before, self.after):
            return ShiftStatus.SHIFTED
        if _differs(self.before, self.after):
            return ShiftStatus.UNDECIDED
        return ShiftStatus.UNCHANGED

    @property
    def reason(self) -> str | None:
        """Why no shift is claimed, when the leader changed or cannot be
        compared; None when there is nothing to explain."""
        if self.before is None or self.after is None:
            side = "earlier" if self.before is None else "later"
            return f"No {self.basis} in the {side} period."
        if self.status is not ShiftStatus.UNDECIDED:
            return None
        for side, leader in (("earlier", self.before), ("later", self.after)):
            if leader.too_few:
                return (
                    f"Not enough {self.basis}: {leader.period_purchases} in the "
                    f"{side} period, {MIN_PURCHASES_PER_PERIOD} needed."
                )
        for side, leader in (("earlier", self.before), ("later", self.after)):
            if leader.tied:
                return (
                    f"Tied in the {side} period: {leader.purchases} "
                    f"{self.basis} each for the top two."
                )
        return None


@dataclass(frozen=True)
class ProfileShifts:
    """A customer's leaders in the two halves of their profile window, side by
    side. `half_days` is the length of each half: the report over the same two
    periods is the one with `window_days=half_days`."""

    earlier: Period
    later: Period
    half_days: int
    earlier_purchases: int
    later_purchases: int
    channel: DimensionComparison
    store: DimensionComparison
    category: DimensionComparison
    categories_before: tuple[Leader, ...]
    categories_after: tuple[Leader, ...]

    @property
    def dimensions(self) -> tuple[DimensionComparison, ...]:
        return (self.channel, self.store, self.category)


def build_profile_shifts(
    connection: Connection[Any],
    customer_id: Any,
    *,
    until: datetime,
    window_days: int,
) -> ProfileShifts | None:
    """Compare the two halves of a customer's profile window (#341).

    Each half is `window_days // 2` days, taken by `consecutive_periods` back
    from `until` -- exactly the periods the shift report uses for that many
    days, so the profile and the report agree about the same customer. An odd
    window therefore leaves its oldest day out of the comparison. A one-day
    window has no halves to compare, and gives None.
    """
    half_days = window_days // 2
    if half_days < 1:
        return None
    earlier, later = consecutive_periods(until, half_days)
    earlier_totals, later_totals = list_totals_for_periods(
        connection,
        earlier.start,
        earlier.end,
        later.start,
        later.end,
        customer_id=customer_id,
    )
    key = str(customer_id)
    was = build_leaders(*earlier_totals).get(key)
    now = build_leaders(*later_totals).get(key)

    def _pick(leaders: CustomerLeaders | None, attribute: str) -> Leader | None:
        return getattr(leaders, attribute) if leaders else None

    return ProfileShifts(
        earlier=earlier,
        later=later,
        half_days=half_days,
        earlier_purchases=was.purchases if was else 0,
        later_purchases=now.purchases if now else 0,
        channel=DimensionComparison(
            "Channel", _pick(was, "channel"), _pick(now, "channel")
        ),
        store=DimensionComparison("Store", _pick(was, "store"), _pick(now, "store")),
        category=DimensionComparison(
            "Category",
            _pick(was, "category"),
            _pick(now, "category"),
            basis="purchases with product lines",
        ),
        categories_before=was.categories if was else (),
        categories_after=now.categories if now else (),
    )
