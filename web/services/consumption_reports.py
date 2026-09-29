"""Filtered consumption-shift and recommendation reports (F12-03).

Two independent reports, over the same customer base, share one filter bar:
store, channel, category and a period. Each reuses what already exists
unchanged -- F8-05's `detect_shifts` for the shift report, F10-01's
`recommend` for the recommendation report, once per customer -- and does no
segmentation or ranking of its own. Nothing here reads or is told which
segmentation method produced anything.

**The period** is one control, `as_of` and `window_days`, shared by both
reports rather than picked twice. For the shift report it becomes the two
consecutive periods `web.services.consumption_shift.consecutive_periods`
already builds; for the recommendation report it is exactly `recommend`'s own
window. The same control drives both, so "the last 90 days" means the same 90
days in each report, rather than two independently chosen ranges that could
silently disagree.

**A store, channel or category filter** narrows each report's rows rather than
its customer population: for the shift report, a row matches when the named
dimension's before or after leader is the filter (RN-44); for the
recommendation report, "store" and "channel" narrow to the customer's own
usual store or dominant channel, and "category" narrows the recommended
products themselves, since one customer's recommendations can span several
categories. Every filter that is set must match; a combination that matches
nothing is an empty report, not an error.

**Live stock, on every request.** The recommendation report calls `recommend`
fresh for every customer on every load, exactly as the customer-facing page
(F10-02) already does, so a product whose stock reaches zero is absent the
next time the report runs -- there is nothing cached here that could go stale.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from web.db.consumption_reports import list_customer_names
from web.db.customers import list_customers
from web.services.consumption_shift import (
    CustomerShift,
    Period,
    ShiftReport,
    consecutive_periods,
    detect_shifts,
)
from web.services.pagination import page_count
from web.services.recommendations import (
    DEFAULT_LIMIT,
    Reason,
    RecommendationResult,
    Status,
    recommend,
)

_CUSTOMER_CAP = 500
DEFAULT_PAGE_SIZE = 25


# ---------- shift rows ----------


@dataclass(frozen=True)
class ShiftRow:
    customer_id: str
    customer_name: str
    shift: CustomerShift


@dataclass(frozen=True)
class FilteredShiftReport:
    earlier: Period
    later: Period
    rows: tuple[ShiftRow, ...]
    compared: int
    unchanged: int


def _touches(dimension, item_id: int | None) -> bool:
    """Whether a dimension's before or after leader is the given id. A
    filter that is not set (None) never excludes a row; a dimension the
    customer did not shift in (None) never matches a filter that is set."""
    if item_id is None:
        return True
    if dimension is None:
        return False
    return dimension.before.item_id == item_id or dimension.after.item_id == item_id


def filter_shift_rows(
    shifts: Sequence[CustomerShift],
    *,
    store_id: int | None = None,
    channel_id: int | None = None,
    category_id: int | None = None,
) -> tuple[CustomerShift, ...]:
    """Every shift matching every filter that is set (RN-44)."""
    return tuple(
        shift
        for shift in shifts
        if _touches(shift.store, store_id)
        and _touches(shift.channel, channel_id)
        and _touches(shift.category, category_id)
    )


def build_shift_report(
    connection,
    *,
    as_of: datetime,
    window_days: int,
    store_id: int | None = None,
    channel_id: int | None = None,
    category_id: int | None = None,
) -> FilteredShiftReport:
    """The consumption shifts between the two periods `as_of`/`window_days`
    imply, filtered by store, channel and category."""
    earlier, later = consecutive_periods(as_of, window_days)
    report: ShiftReport = detect_shifts(connection, earlier, later)

    matched = filter_shift_rows(
        report.shifts, store_id=store_id, channel_id=channel_id, category_id=category_id
    )
    names = list_customer_names(connection, [shift.customer_id for shift in matched])
    rows = tuple(
        ShiftRow(shift.customer_id, names.get(shift.customer_id, "—"), shift)
        for shift in matched
    )

    return FilteredShiftReport(
        earlier=report.earlier,
        later=report.later,
        rows=rows,
        compared=report.compared,
        unchanged=report.unchanged,
    )


# ---------- recommendation rows ----------


@dataclass(frozen=True)
class RecommendationRow:
    customer_id: str
    customer_name: str
    store_id: int
    store_name: str
    channel_id: int | None
    channel_name: str | None
    product_id: int
    product_name: str
    category_id: int
    category_name: str
    in_stock: int
    reasons: tuple[Reason, ...]


@dataclass(frozen=True)
class RecommendationReportPage:
    rows: tuple[RecommendationRow, ...]
    total: int
    page: int
    page_size: int
    page_count: int

    @property
    def has_previous(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.page_count


def filter_recommendation_rows(
    results: Sequence[RecommendationResult],
    *,
    store_id: int | None = None,
    channel_id: int | None = None,
    category_id: int | None = None,
) -> tuple[RecommendationRow, ...]:
    """One row per recommended product, for customers a recommendation was
    actually given to, matching every filter that is set."""
    rows: list[RecommendationRow] = []
    for result in results:
        if result.status is not Status.RECOMMENDED:
            continue
        if store_id is not None and result.store_id != store_id:
            continue
        if channel_id is not None and result.channel_id != channel_id:
            continue
        for item in result.recommendations:
            if category_id is not None and item.category_id != category_id:
                continue
            rows.append(
                RecommendationRow(
                    customer_id=result.customer_id,
                    customer_name=result.customer_name,
                    store_id=result.store_id,
                    store_name=result.store_name,
                    channel_id=result.channel_id,
                    channel_name=result.channel_name,
                    product_id=item.product_id,
                    product_name=item.name,
                    category_id=item.category_id,
                    category_name=item.category_name,
                    in_stock=item.in_stock,
                    reasons=item.reasons,
                )
            )
    return tuple(rows)


def build_recommendation_report(
    connection,
    *,
    as_of: datetime,
    window_days: int,
    store_id: int | None = None,
    channel_id: int | None = None,
    category_id: int | None = None,
    limit_per_customer: int = DEFAULT_LIMIT,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> RecommendationReportPage:
    """Recommendations for every customer, filtered and paged, computed fresh
    from live stock on every call."""
    customers, _total = list_customers(
        connection, search=None, page=1, per_page=_CUSTOMER_CAP
    )
    results = [
        recommend(
            connection,
            customer.customer_id,
            window_days=window_days,
            limit=limit_per_customer,
            as_of=as_of,
        )
        for customer in customers
    ]

    rows = filter_recommendation_rows(
        results, store_id=store_id, channel_id=channel_id, category_id=category_id
    )
    total_pages = page_count(len(rows), page_size)
    page = min(max(page, 1), total_pages)
    start = (page - 1) * page_size

    return RecommendationReportPage(
        rows=rows[start : start + page_size],
        total=len(rows),
        page=page,
        page_size=page_size,
        page_count=total_pages,
    )
