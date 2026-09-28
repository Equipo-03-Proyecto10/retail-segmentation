"""The customer consumption profile (F8-03): one customer, understood without
reading their transaction list.

A profile summarises a customer's *accepted* sales (ADR-0020) over a stated
window, and carries the customer's R/F/M values and their current and previous
segment as assignment history recorded them (ADR-0017). Nothing here writes,
and nothing here reads a mutable column: the segments are the open and the most
recently closed rows of `customer_segment_history`.

ADR-0003: the ranking and the arithmetic below are pure functions of plain
values, so the tests exercise every tie-break without a database. `build_profile`
only wires the `web.db.consumption` reads into them.

The definitions below are decisions, and they are written here because a
profile that quietly meant something different from what its reader assumed
would look plausible and be wrong:

* **Window.** The last `window_days` days ending at `as_of`, both ends
  inclusive. Every sales-derived measure uses the same window, and the profile
  carries it so a page can state it. The R/F/M block keeps the window of the
  run that produced it, which is not necessarily this one.
* **Spend and average ticket** come from the purchase headers
  (`transaction.total`), the same quantity the segment run scores as Monetary.
  A profile's spend and its M value are therefore one measurement.
* **Purchase frequency** is the number of accepted purchases in the window, the
  same F the segment run ranks.
* **Dominant channel / dominant store:** the one with the most purchases; a tie
  goes to the one with the higher spend; a tie there goes to the lowest id.
* **Favourite categories** (top 3): ranked by the number of purchases that
  contained the category, then units bought, then spend, then lowest id.
  The category is the one the product carries; there is no roll-up to a parent.
* **Frequent products** (top 5): ranked by the number of purchases that
  included the product, then units bought, then lowest id.
* **Average discount** is how far below the *current* list price the customer
  paid, weighted by value: `(1 - paid / listed) * 100`, where `listed` is the
  same units priced at `product.list_price`. RN-13 stores what was charged but
  not the list price a sale was made against, so a price change since the sale
  moves this number without any discount having been granted, and a customer
  who paid more than today's list price shows a negative figure. It is
  reported as measured rather than clamped, and it is a comparison with the
  current list price, not a record of promotions.
* **Empty.** No accepted sale in the window means every sales-derived measure
  is None (never zero), and the remaining sales reads are skipped. R/F/M and
  segment states still come from assignment history, independent of the
  profile window.

Ties are broken by the rules above and by nothing else, so the same sales give
the same profile whatever order the database returns its rows in.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from psycopg import Connection

from web.db.consumption import (
    CategoryTotal,
    GroupTotal,
    HistoryRow,
    ProductTotal,
    get_current_and_previous_history_rows,
    get_discount_totals,
    get_sales_totals,
    list_category_totals,
    list_channel_totals,
    list_product_totals,
    list_store_totals,
)
from web.db.customers import get_customer
from web.services.segmentation import (
    DEFAULT_WINDOW_DAYS,
    MAX_WINDOW_DAYS,
    MIN_WINDOW_DAYS,
    InvalidWindow,
)

# The profile looks at the same span a segment run does by default, so the two
# describe the same six months unless somebody chooses otherwise.
DEFAULT_PROFILE_WINDOW_DAYS = DEFAULT_WINDOW_DAYS

TOP_CATEGORIES = 3
TOP_PRODUCTS = 5

_CENT = Decimal("0.01")


class UnknownCustomer(ValueError):
    """The id is not a UUID, or names no customer."""


# ---------- what a profile is ----------


@dataclass(frozen=True)
class SegmentState:
    """One assignment a customer held, read from history.

    `label_code` is None for an unassigned result (RN-21): a run scored the
    customer and found no sales to label. That is a state, and it is a
    different thing from having no assignment at all, which the profile
    reports as a `None` SegmentState.
    """

    run_id: int
    label_code: str | None
    label_name: str | None
    valid_from: datetime
    valid_to: datetime | None


@dataclass(frozen=True)
class RfmSnapshot:
    """The raw recency, frequency and monetary values and their quintile
    scores, exactly as the customer's open history row stored them
    (ADR-0017), with the run that measured them and the window it used."""

    run_id: int
    run_at: datetime
    window_days: int
    last_purchase_at: datetime | None
    frequency: int | None
    monetary: Decimal | None
    r_score: int | None
    f_score: int | None
    m_score: int | None


@dataclass(frozen=True)
class ConsumptionProfile:
    """What is known about one customer's buying over one window.

    The customer id and name identify the customer this profile describes.
    Every sales-derived measurement is None when there is nothing to measure;
    a count or total of zero is never used to mean "no data". `has_sales` says
    which case this is. R/F/M and segments remain available from assignment
    history even when the profile window contains no sales.
    """

    customer_id: str
    customer_name: str
    window_days: int
    window_start: datetime
    window_end: datetime
    has_sales: bool
    total_spend: Decimal | None = None
    average_ticket: Decimal | None = None
    purchase_count: int | None = None
    last_purchase_at: datetime | None = None
    dominant_channel: GroupTotal | None = None
    dominant_store: GroupTotal | None = None
    favourite_categories: tuple[CategoryTotal, ...] = ()
    frequent_products: tuple[ProductTotal, ...] = ()
    average_discount_pct: Decimal | None = None
    rfm: RfmSnapshot | None = None
    current_segment: SegmentState | None = None
    previous_segment: SegmentState | None = None


# ---------- ranking: every tie-break is stated and total ----------


def rank_dominant(groups: list[GroupTotal]) -> GroupTotal | None:
    """The dominant channel or store: most purchases, then highest spend, then
    lowest id. The id makes the order total, so two groups can never tie."""
    if not groups:
        return None
    return min(groups, key=lambda g: (-g.purchases, -g.spend, g.item_id))


def rank_categories(
    groups: list[CategoryTotal], limit: int = TOP_CATEGORIES
) -> tuple[CategoryTotal, ...]:
    """Favourite categories: most purchases containing the category, then units,
    then spend, then lowest id."""
    ranked = sorted(
        groups, key=lambda g: (-g.purchases, -g.units, -g.spend, g.category_id)
    )
    return tuple(ranked[:limit])


def rank_products(
    groups: list[ProductTotal], limit: int = TOP_PRODUCTS
) -> tuple[ProductTotal, ...]:
    """Frequent products: most purchases including the product, then units,
    then lowest id."""
    ranked = sorted(groups, key=lambda g: (-g.purchases, -g.units, g.product_id))
    return tuple(ranked[:limit])


# ---------- arithmetic ----------


def average_ticket(total_spend: Decimal, purchases: int) -> Decimal:
    """Spend per purchase, to the cent, rounding half up."""
    return (total_spend / purchases).quantize(_CENT, rounding=ROUND_HALF_UP)


def average_discount_pct(
    paid: Decimal | None, at_list: Decimal | None
) -> Decimal | None:
    """How far below the listed value the customer paid, as a percentage.

    None when there is no positive listed value to compare against. Negative
    when the customer paid more than the listed value. Never negative zero.
    """
    if paid is None or at_list is None or at_list <= 0:
        return None
    discount = ((Decimal(1) - paid / at_list) * 100).quantize(
        _CENT, rounding=ROUND_HALF_UP
    )
    return abs(discount) if discount == 0 else discount


# ---------- assembling a profile ----------


def _segment_state(row: HistoryRow) -> SegmentState:
    return SegmentState(
        run_id=row.run_id,
        label_code=row.label_code,
        label_name=row.label_name,
        valid_from=row.valid_from,
        valid_to=row.valid_to,
    )


def _rfm_snapshot(row: HistoryRow | None) -> RfmSnapshot | None:
    """The R/F/M block of an open history row, or None when it holds none.

    An unassigned result stores no raw values and no scores (there were no sales
    in that run's window to measure), and reporting three NULLs as a snapshot
    would be a measurement of nothing.
    """
    if row is None:
        return None
    values = (
        row.last_purchase_at,
        row.frequency_count,
        row.monetary_total,
        row.r_score,
        row.f_score,
        row.m_score,
    )
    if all(value is None for value in values):
        return None
    return RfmSnapshot(
        run_id=row.run_id,
        run_at=row.run_at,
        window_days=row.window_days,
        last_purchase_at=row.last_purchase_at,
        frequency=row.frequency_count,
        monetary=row.monetary_total,
        r_score=row.r_score,
        f_score=row.f_score,
        m_score=row.m_score,
    )


def build_profile(
    connection: Connection[Any],
    customer_id: Any,
    *,
    window_days: int = DEFAULT_PROFILE_WINDOW_DAYS,
    as_of: datetime | None = None,
) -> ConsumptionProfile:
    """Compute one customer's consumption profile over the last `window_days`.

    A window outside what a segment run accepts raises InvalidWindow, so the
    two surfaces refuse the same inputs. UnknownCustomer is raised for an
    invalid or absent customer; a page maps it to 404. A customer with no
    accepted sale in the window gets an empty sales profile rather than an
    error, while retaining assignment history. When `as_of` is omitted, the
    connection's session time zone keeps the window aligned with database data.
    """
    if not MIN_WINDOW_DAYS <= window_days <= MAX_WINDOW_DAYS:
        raise InvalidWindow(
            f"The window must be between {MIN_WINDOW_DAYS} and "
            f"{MAX_WINDOW_DAYS} days."
        )

    try:
        customer_key = str(uuid.UUID(str(customer_id)))
    except ValueError as exc:
        raise UnknownCustomer from exc
    customer = get_customer(connection, customer_key)
    if customer is None:
        raise UnknownCustomer

    until = as_of if as_of is not None else datetime.now(connection.info.timezone)
    since = until - timedelta(days=window_days)

    totals = get_sales_totals(connection, customer_key, since, until)
    open_row, previous_row = get_current_and_previous_history_rows(
        connection, customer_key
    )
    if totals.purchases == 0:
        return ConsumptionProfile(
            customer_id=customer_key,
            customer_name=customer.name,
            window_days=window_days,
            window_start=since,
            window_end=until,
            has_sales=False,
            rfm=_rfm_snapshot(open_row),
            current_segment=_segment_state(open_row) if open_row else None,
            previous_segment=_segment_state(previous_row) if previous_row else None,
        )

    discount = get_discount_totals(connection, customer_key, since, until)

    return ConsumptionProfile(
        customer_id=customer_key,
        customer_name=customer.name,
        window_days=window_days,
        window_start=since,
        window_end=until,
        has_sales=True,
        total_spend=totals.spend,
        average_ticket=average_ticket(totals.spend, totals.purchases),
        purchase_count=totals.purchases,
        last_purchase_at=totals.last_purchase_at,
        dominant_channel=rank_dominant(
            list_channel_totals(connection, customer_key, since, until)
        ),
        dominant_store=rank_dominant(
            list_store_totals(connection, customer_key, since, until)
        ),
        favourite_categories=rank_categories(
            list_category_totals(connection, customer_key, since, until)
        ),
        frequent_products=rank_products(
            list_product_totals(connection, customer_key, since, until)
        ),
        average_discount_pct=average_discount_pct(discount.paid, discount.at_list),
        rfm=_rfm_snapshot(open_row),
        current_segment=_segment_state(open_row) if open_row else None,
        previous_segment=_segment_state(previous_row) if previous_row else None,
    )
