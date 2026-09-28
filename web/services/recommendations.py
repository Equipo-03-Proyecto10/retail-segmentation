"""Product recommendations with a stated reason for each (F10-01).

A recommendation has to be defensible to the business and actionable in a real store,
so three things are decided here (RN-40) and not left to look plausible:

**What is eligible.** A product is eligible when it is active, has stock on hand in
the customer's *usual store*, and the customer has not already bought it in the
window. The usual store is the consumption profile's dominant store (RN-35), so it is
the same store the profile names. A product with no stock there never appears, and
neither does one that only another store holds.

**What is relevant.** Eligible is not a reason. A product is recommended only when at
least one of three signals, the three things the story combines, says why *this*
customer:

* *segment*: other customers whose open assignment carries the same label bought it
  in the window;
* *preferred category*: it is in a category the customer registered an interest in;
* *purchase history*: it is in a category the customer buys from (their top
  categories over the window, RN-35).

**How they are ordered.** By the number of signals that match, then by how many
segment customers bought it, then by how much of the customer's buying its category
is, then by product id. There are no weights, so there is nothing to tune and every
position can be explained by the reasons it carries. Each recommendation states its
reasons, in a fixed order.

**When it cannot recommend, it says so.** A customer with no open assignment, or one
whose latest assignment is the unassigned result (RN-21), has no segment, and the
result says that instead of falling back to something else. A customer with no
accepted sale in the window has no usual store, so stock cannot be checked, and the
result says that too. Nothing is guessed in either case.

Only the stable label is read (ADR-0018). Nothing here is told, or reads, how a run
was produced.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from psycopg import Connection

from web.db.consumption import list_product_totals
from web.db.customers import list_interest_categories
from web.db.recommendations import (
    StockedProduct,
    list_segment_buyers,
    list_stocked_products,
)
from web.services.consumption_profile import (
    DEFAULT_PROFILE_WINDOW_DAYS,
    build_profile,
)

DEFAULT_LIMIT = 10
MAX_LIMIT = 50


class InvalidLimit(ValueError):
    """A number of recommendations that is not a whole number from 1 to MAX_LIMIT."""


class Status(Enum):
    RECOMMENDED = "recommended"
    NO_SEGMENT = "no_segment"
    NO_USUAL_STORE = "no_usual_store"
    NONE_MATCH = "none_match"


class Signal(Enum):
    SEGMENT = "segment"
    PREFERRED_CATEGORY = "preferred_category"
    PURCHASE_HISTORY = "purchase_history"


@dataclass(frozen=True)
class Reason:
    """Why one product was chosen: the signal, and it in words."""

    signal: Signal
    text: str


@dataclass(frozen=True)
class Recommendation:
    product_id: int
    name: str
    category_id: int
    category_name: str
    in_stock: int
    reasons: tuple[Reason, ...]


@dataclass(frozen=True)
class RecommendationResult:
    """What was asked, what was found, and, when nothing could be recommended, why.

    `message` always says what happened in words. `store_*` is None until a usual
    store is known, and `label_*` until a segment is.
    """

    customer_id: str
    customer_name: str
    status: Status
    message: str
    window_days: int
    window_start: datetime
    window_end: datetime
    label_code: str | None = None
    label_name: str | None = None
    store_id: int | None = None
    store_name: str | None = None
    recommendations: tuple[Recommendation, ...] = ()


# ---------- ranking: a pure function of plain rows ----------


def _plural(count: int, singular: str, plural: str) -> str:
    return singular if count == 1 else plural


def _days(count: int) -> str:
    return f"{count} {_plural(count, 'day', 'days')}"


def rank_candidates(
    stocked: Sequence[StockedProduct],
    *,
    preferred_category_ids: Collection[int],
    category_purchases: Mapping[int, int],
    total_purchases: int,
    purchased_product_ids: Collection[int],
    segment_buyers: Mapping[int, int],
    label_name: str,
    window_days: int,
    limit: int,
) -> tuple[Recommendation, ...]:
    """The products to recommend, best first, each with its reasons.

    A product needs stock, must not have been bought already, and must match at
    least one signal. Quantity is checked here as well as in the read: stock is the
    one thing a recommendation must never get wrong, so it is not trusted to a
    single place.
    """
    ranked: list[tuple[tuple[int, int, int, int], Recommendation]] = []
    for product in stocked:
        if product.quantity_on_hand <= 0:
            continue
        if product.product_id in purchased_product_ids:
            continue

        reasons: list[Reason] = []
        buyers = segment_buyers.get(product.product_id, 0)
        if buyers >= 1:
            reasons.append(
                Reason(
                    Signal.SEGMENT,
                    f"{buyers} other {label_name} "
                    f"{_plural(buyers, 'customer', 'customers')} bought it in the "
                    f"last {_days(window_days)}",
                )
            )
        if product.category_id in preferred_category_ids:
            reasons.append(
                Reason(
                    Signal.PREFERRED_CATEGORY,
                    f"In {product.category_name}, a category the customer said "
                    "they like",
                )
            )
        history = category_purchases.get(product.category_id, 0)
        if history >= 1:
            reasons.append(
                Reason(
                    Signal.PURCHASE_HISTORY,
                    f"The customer bought from {product.category_name} in {history} "
                    f"of their {total_purchases} purchases in the last "
                    f"{_days(window_days)}",
                )
            )
        if not reasons:
            continue

        key = (-len(reasons), -buyers, -history, product.product_id)
        ranked.append(
            (
                key,
                Recommendation(
                    product.product_id,
                    product.name,
                    product.category_id,
                    product.category_name,
                    product.quantity_on_hand,
                    tuple(reasons),
                ),
            )
        )

    ranked.sort(key=lambda item: item[0])
    return tuple(recommendation for _, recommendation in ranked[:limit])


# ---------- the whole computation ----------


def _check_limit(limit: Any) -> None:
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not (1 <= limit <= MAX_LIMIT)
    ):
        raise InvalidLimit(f"The limit is a whole number from 1 to {MAX_LIMIT}.")


def recommend(
    connection: Connection[Any],
    customer_id: Any,
    *,
    window_days: int = DEFAULT_PROFILE_WINDOW_DAYS,
    limit: int = DEFAULT_LIMIT,
    as_of: datetime | None = None,
) -> RecommendationResult:
    """Recommend products to one customer, from what their usual store has in stock.

    The window is the consumption profile's, and is refused the same way
    (InvalidWindow); an unknown customer raises UnknownCustomer. Nothing is read
    beyond the profile until a segment and a usual store are both known.
    """
    _check_limit(limit)
    profile = build_profile(
        connection, customer_id, window_days=window_days, as_of=as_of
    )
    base = dict(
        customer_id=profile.customer_id,
        customer_name=profile.customer_name,
        window_days=profile.window_days,
        window_start=profile.window_start,
        window_end=profile.window_end,
    )

    segment = profile.current_segment
    if segment is None:
        return RecommendationResult(
            **base,
            status=Status.NO_SEGMENT,
            message=(
                "No segment is available for this customer: no segmentation run has "
                "assigned them one, so no recommendations are given."
            ),
        )
    if segment.label_code is None:
        return RecommendationResult(
            **base,
            status=Status.NO_SEGMENT,
            message=(
                "No segment is available for this customer: run "
                f"{segment.run_id} left them unassigned, because they had no sales "
                "in its window, so no recommendations are given."
            ),
        )

    label_name = segment.label_name or segment.label_code
    store = profile.dominant_store
    if store is None:
        return RecommendationResult(
            **base,
            status=Status.NO_USUAL_STORE,
            label_code=segment.label_code,
            label_name=label_name,
            message=(
                "No usual store is available: the customer has no accepted sale in "
                f"the last {_days(profile.window_days)}, so stock cannot be checked "
                "and no recommendations are given."
            ),
        )

    interests = list_interest_categories(connection, profile.customer_id)
    purchased = list_product_totals(
        connection, profile.customer_id, profile.window_start, profile.window_end
    )
    stocked = list_stocked_products(connection, store.item_id)
    buyers = list_segment_buyers(
        connection,
        segment.label_code,
        profile.customer_id,
        profile.window_start,
        profile.window_end,
    )

    items = rank_candidates(
        stocked,
        preferred_category_ids={category.category_id for category in interests},
        category_purchases={
            category.category_id: category.purchases
            for category in profile.favourite_categories
        },
        total_purchases=profile.purchase_count or 0,
        purchased_product_ids={product.product_id for product in purchased},
        segment_buyers=buyers,
        label_name=label_name,
        window_days=profile.window_days,
        limit=limit,
    )

    known = dict(
        label_code=segment.label_code,
        label_name=label_name,
        store_id=store.item_id,
        store_name=store.name,
    )
    if not items:
        return RecommendationResult(
            **base,
            **known,
            status=Status.NONE_MATCH,
            message=(
                f"Nothing in stock at {store.name} matches this customer: no product "
                "there is in a category they prefer or buy from, or was bought by "
                "their segment, that they have not already bought."
            ),
        )
    return RecommendationResult(
        **base,
        **known,
        status=Status.RECOMMENDED,
        recommendations=items,
        message=(
            f"{len(items)} {_plural(len(items), 'recommendation', 'recommendations')} "
            f"from what {store.name} has in stock."
        ),
    )
