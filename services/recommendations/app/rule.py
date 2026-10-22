"""RN-40, ported from web/services/recommendations.py (ADR-0045, E-17).

The web's `recommend` and the pieces of the consumption profile it needs
(RN-34/RN-35) are carried here over plain rows, and golden fixtures captured
from the web hold this port to the same answer for every seeded customer.

**Eligible.** Active, positive stock at the customer's usual store, and not
bought by the customer in the window.

**Relevant.** At least one signal: other customers in the same open segment
bought it in the window; it is in a category the customer registered an
interest in; or it is in one of the customer's top three bought categories. A
category covers its whole subtree, never a category above or beside it, and
the nearest matching category in the product's lineage is the one reported.

**Ordered.** By the number of signals, then segment buyers, then that
category's purchases, then product id, and cut at the limit.

**When nothing is recommended, the status says why.** The segment is checked
first, and a customer without one is answered without reading further:
`no_open_assignment`, or `unassigned_result` when the open assignment is the
unassigned result (the web reports both as `no_segment`). Then
`no_usual_store` (no accepted sale in the window), then `none_match`.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Protocol

from .config import WINDOW_DAYS

TOP_CATEGORIES = 3
_SHARE = Decimal("0.0001")


@dataclass(frozen=True)
class Label:
    """The customer's open assignment, as api_segments.current_label_v1 says it."""

    state: str
    label_code: str | None
    run_id: int | None


@dataclass(frozen=True)
class Line:
    transaction_id: int
    store_id: int
    product_id: int
    category_id: int
    quantity: int
    unit_price: Decimal


@dataclass(frozen=True)
class Stocked:
    product_id: int
    category_id: int
    quantity_on_hand: int


class Reads(Protocol):
    """What the rule reads, one method per published view read."""

    def current_label(self, customer_id: str) -> Label: ...

    def purchase_lines(
        self, customer_id: str, since: datetime, until: datetime
    ) -> list[Line]: ...

    def stocked_products(self, store_id: int) -> list[Stocked]: ...

    def segment_buyers(
        self, label_code: str, customer_id: str, since: datetime, until: datetime
    ) -> dict[int, int]: ...

    def interest_categories(self, customer_id: str) -> set[int]: ...

    def category_parents(self) -> dict[int, int | None]: ...


@dataclass(frozen=True)
class Result:
    customer_id: str
    status: str
    window_start: datetime
    window_end: datetime
    label_code: str | None = None
    run_id: int | None = None
    usual_store_id: int | None = None
    items: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self, computed_at: datetime) -> dict[str, Any]:
        return {
            "customer_id": self.customer_id,
            "status": self.status,
            "computed_at": computed_at,
            "window": {
                "days": WINDOW_DAYS,
                "start": self.window_start,
                "end": self.window_end,
            },
            "label_code": self.label_code,
            "run_id": self.run_id,
            "usual_store_id": self.usual_store_id,
            "items": self.items,
        }


# ---------- the profile pieces (RN-34, RN-35) ----------


def dominant_store(lines: Sequence[Line]) -> int | None:
    """Most purchases, then highest spend, then lowest id (the web's
    rank_dominant). A purchase is a transaction; spend is the sum of its lines,
    which the schema keeps equal to the transaction total (RN-37)."""
    purchases: dict[int, set[int]] = defaultdict(set)
    spend: dict[int, Decimal] = defaultdict(Decimal)
    for line in lines:
        purchases[line.store_id].add(line.transaction_id)
        spend[line.store_id] += line.quantity * line.unit_price
    if not purchases:
        return None
    return min(purchases, key=lambda s: (-len(purchases[s]), -spend[s], s))


def top_categories(
    lines: Sequence[Line], limit: int = TOP_CATEGORIES
) -> dict[int, int]:
    """The customer's favourite categories and the purchases containing each:
    most purchases, then units, then spend, then lowest id (the web's
    rank_categories), by the category the product itself carries."""
    purchases: dict[int, set[int]] = defaultdict(set)
    units: dict[int, int] = defaultdict(int)
    spend: dict[int, Decimal] = defaultdict(Decimal)
    for line in lines:
        purchases[line.category_id].add(line.transaction_id)
        units[line.category_id] += line.quantity
        spend[line.category_id] += line.quantity * line.unit_price
    ranked = sorted(
        purchases,
        key=lambda c: (-len(purchases[c]), -units[c], -spend[c], c),
    )
    return {category: len(purchases[category]) for category in ranked[:limit]}


# ---------- ranking: a pure function of plain rows ----------


def lineage(category_id: int, parents: Mapping[int, int | None]) -> list[int]:
    """The category and every category above it, nearest first."""
    chain = [category_id]
    seen = {category_id}
    while (parent := parents.get(chain[-1])) is not None and parent not in seen:
        chain.append(parent)
        seen.add(parent)
    return chain


def _nearest(chain: Sequence[int], matches: Collection[int]) -> int | None:
    return next((category for category in chain if category in matches), None)


def share(history: int, total: int) -> Decimal:
    """The fraction of the customer's purchases that were in a category."""
    value = (Decimal(history) / Decimal(total)).quantize(_SHARE, ROUND_HALF_UP)
    return value.normalize() if value else Decimal(0)


def rank(
    stocked: Sequence[Stocked],
    *,
    preferred: Collection[int],
    category_purchases: Mapping[int, int],
    parents: Mapping[int, int | None],
    total_purchases: int,
    purchased: Collection[int],
    buyers: Mapping[int, int],
    limit: int,
) -> list[dict[str, Any]]:
    """The products to recommend, best first, each with its reasons."""
    bought = {c for c, count in category_purchases.items() if count >= 1}
    ranked: list[tuple[tuple[int, int, int, int], dict[str, Any]]] = []
    for product in stocked:
        if product.quantity_on_hand <= 0 or product.product_id in purchased:
            continue
        reasons: list[dict[str, Any]] = []
        segment_buyers = buyers.get(product.product_id, 0)
        if segment_buyers >= 1:
            reasons.append(
                {
                    "signal": "segment",
                    "segment_buyers": segment_buyers,
                    "category_id": None,
                    "share": None,
                }
            )
        chain = lineage(product.category_id, parents)
        liked = _nearest(chain, preferred)
        if liked is not None:
            reasons.append(
                {
                    "signal": "preferred_category",
                    "segment_buyers": None,
                    "category_id": liked,
                    "share": None,
                }
            )
        history_category = _nearest(chain, bought)
        history = (
            category_purchases[history_category] if history_category is not None else 0
        )
        if history_category is not None:
            reasons.append(
                {
                    "signal": "purchase_history",
                    "segment_buyers": None,
                    "category_id": history_category,
                    "share": share(history, total_purchases),
                }
            )
        if not reasons:
            continue
        key = (-len(reasons), -segment_buyers, -history, product.product_id)
        ranked.append(
            (
                key,
                {
                    "product_id": product.product_id,
                    "category_id": product.category_id,
                    "quantity_on_hand": product.quantity_on_hand,
                    "reasons": reasons,
                },
            )
        )
    ranked.sort(key=lambda entry: entry[0])
    return [item for _, item in ranked[:limit]]


# ---------- the whole computation ----------


def recommend(reads: Reads, customer_id: str, as_of: datetime, limit: int) -> Result:
    """Recommend for one known customer, as of a moment. Each read happens only
    once the status still depends on it."""
    until = as_of
    since = until - timedelta(days=WINDOW_DAYS)
    base = {"customer_id": customer_id, "window_start": since, "window_end": until}

    label = reads.current_label(customer_id)
    if label.state != "assigned" or label.label_code is None:
        status = (
            "unassigned_result"
            if label.state == "unassigned_result"
            else "no_open_assignment"
        )
        # label_code and run_id stay null until a segment is known (the contract).
        return Result(**base, status=status)

    known = {"label_code": label.label_code, "run_id": label.run_id}
    lines = reads.purchase_lines(customer_id, since, until)
    store = dominant_store(lines)
    if store is None:
        return Result(**base, **known, status="no_usual_store")

    items = rank(
        reads.stocked_products(store),
        preferred=reads.interest_categories(customer_id),
        category_purchases=top_categories(lines),
        parents=reads.category_parents(),
        total_purchases=len({line.transaction_id for line in lines}),
        purchased={line.product_id for line in lines},
        buyers=reads.segment_buyers(label.label_code, customer_id, since, until),
        limit=limit,
    )
    return Result(
        **base,
        **known,
        usual_store_id=store,
        status="recommended" if items else "none_match",
        items=items,
    )
