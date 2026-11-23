"""RN-40 judged at a reference store and channel (ADR-0067, ADR-0084).

`/v2` keeps `/v1`'s relevance and order (rule.py) and changes only where a
product is judged and what happens to one that fails.

**The reference store** is the store the caller names, otherwise the customer's
usual store (RN-35). The channel is the one the caller names, never defaulted;
with none, a product is judged over any channel the store enables.

**Available** is stock's own definition (`api_stock.availability_v1`): the
product is sellable, the store is active, `OPEN` and enables the channel, and it
holds more than zero units. A product that matches a signal and is not
recommended is excluded for exactly one reason, the first that applies in
`decisions.EXCLUSIONS`. A paused or discontinued product (`is_active` false) is
`not_sellable`; nothing is `inactive` while products publishes no other flag
(ADR-0084).

**An alternative** stands in for a product that fails only for
`not_sellable`, `channel_not_enabled` or `out_of_stock`: its lowest-id declared
substitute that is itself available here, not bought in the window, not
already in the result and not restricted. **An elsewhere entry** reports the
product itself at the nearest other store selling it on the channel, by
great-circle distance, ties to the lowest store id, stores without coordinates
skipped; only when no substitute qualifies, and never for a `not_sellable`
product, which is sellable nowhere. Both only fill the places the primary items
leave, taken in RN-40's order of the products they answer.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Protocol

from .config import WINDOW_DAYS
from .decisions import EXCLUSIONS, Decision, Item, Reason
from .rule import Label, Line, Stocked, dominant_store, lineage, rank, top_categories

# The exclusions an alternative or an elsewhere entry may answer (ADR-0067).
REPLACEABLE = frozenset({"not_sellable", "channel_not_enabled", "out_of_stock"})
EARTH_RADIUS_KM = 6371.0088


@dataclass(frozen=True)
class Store:
    """A store as api_stores.store_directory_v2 publishes it."""

    store_id: int
    is_active: bool
    status: str
    channel_ids: frozenset[int]
    latitude: float | None = None
    longitude: float | None = None

    def selling(self, channel_id: int | None) -> bool:
        """Whether the store sells on the channel, or on any channel when none
        is given (the stores contract's rule)."""
        if not (self.is_active and self.status == "OPEN"):
            return False
        if channel_id is None:
            return bool(self.channel_ids)
        return channel_id in self.channel_ids

    def permits_sales(self, channel_id: int | None) -> bool:
        """The store's own status, whatever channel was named: with none, a
        store that enables no channel permits no sale (ADR-0084)."""
        if not (self.is_active and self.status == "OPEN"):
            return False
        return channel_id is not None or bool(self.channel_ids)


@dataclass(frozen=True)
class Product:
    product_id: int
    category_id: int
    is_active: bool


@dataclass(frozen=True)
class Here:
    """One product at the reference store: stock's verdict and the units."""

    available: bool
    quantity_on_hand: int


class Reads(Protocol):
    """What /v2 reads beyond /v1's rule.Reads."""

    def current_label(self, customer_id: str) -> Label: ...

    def model_version(self, run_id: int) -> int | None: ...

    def purchase_lines(
        self, customer_id: str, since: datetime, until: datetime
    ) -> list[Line]: ...

    def segment_buyers(
        self, label_code: str, customer_id: str, since: datetime, until: datetime
    ) -> dict[int, int]: ...

    def interest_categories(self, customer_id: str) -> set[int]: ...

    def category_parents(self) -> dict[int, int | None]: ...

    def products(self) -> list[Product]: ...

    def stores(self) -> dict[int, Store]: ...

    def here(self, store_id: int, channel_id: int | None) -> dict[int, Here]: ...

    def selling_elsewhere(
        self, product_ids: list[int], store_id: int, channel_id: int | None
    ) -> dict[int, dict[int, int]]: ...

    def substitutes(self, product_ids: list[int]) -> dict[int, list[int]]: ...


@dataclass(frozen=True)
class ResultV2:
    """What the rule decided, before it has an id. `decision()` is the record
    and `as_dict()` the response, a view of that record."""

    customer_id: str
    status: str
    window_start: datetime
    window_end: datetime
    label_code: str | None = None
    run_id: int | None = None
    model_version_id: int | None = None
    usual_store_id: int | None = None
    store_id: int | None = None
    store_source: str | None = None
    channel_id: int | None = None
    exclusions: tuple[tuple[str, int], ...] = ()
    items: tuple[Item, ...] = field(default_factory=tuple)

    def decision(self, decision_id: str, computed_at: datetime) -> Decision:
        return Decision(
            decision_id=decision_id,
            customer_id=self.customer_id,
            status=self.status,
            computed_at=computed_at,
            run_id=self.run_id,
            model_version_id=self.model_version_id,
            store_id=self.store_id,
            store_source=self.store_source,
            channel_id=self.channel_id,
            exclusions=self.exclusions,
            items=self.items,
        )

    def as_dict(self, decision: Decision) -> dict[str, Any]:
        """The contract's RecommendationV2, in its XSD's element order. The
        exclusions and items are the recorded decision's own."""
        recorded = decision.as_dict()
        return {
            "decision_id": decision.decision_id,
            "customer_id": self.customer_id,
            "status": decision.status,
            "computed_at": decision.computed_at,
            "window": {
                "days": WINDOW_DAYS,
                "start": self.window_start,
                "end": self.window_end,
            },
            "label_code": self.label_code,
            "run_id": decision.run_id,
            "model_version_id": decision.model_version_id,
            "usual_store_id": self.usual_store_id,
            "store_id": decision.store_id,
            "store_source": decision.store_source,
            "channel_id": decision.channel_id,
            "exclusions": recorded["exclusions"],
            "items": recorded["items"],
        }


def distance_km(a: Store, b: Store) -> float:
    """The great-circle (haversine) distance between two located stores."""
    assert a.latitude is not None and a.longitude is not None
    assert b.latitude is not None and b.longitude is not None
    phi1, phi2 = math.radians(a.latitude), math.radians(b.latitude)
    dphi = phi2 - phi1
    dlambda = math.radians(b.longitude - a.longitude)
    h = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(h)))


def nearest(
    reference: Store, candidates: Collection[int], stores: Mapping[int, Store]
) -> int | None:
    """The nearest located store among the candidates, ties to the lowest id;
    None when the reference store, or every candidate, has no coordinates."""
    if reference.latitude is None or reference.longitude is None:
        return None
    located = [
        stores[s]
        for s in candidates
        if s in stores
        and stores[s].latitude is not None
        and stores[s].longitude is not None
    ]
    if not located:
        return None
    best = min(located, key=lambda s: (distance_km(reference, s), s.store_id))
    return best.store_id


def failures(
    product: Product,
    *,
    bought: bool,
    restricted: bool,
    reference: Store,
    channel_id: int | None,
    here: Here | None,
) -> list[str]:
    """Every reason the product fails at the reference store, in the order of
    decisions.EXCLUSIONS. Empty when it is available and allowed."""
    found = []
    if bought:
        found.append("already_bought")
    if not product.is_active:
        found.append("not_sellable")
    if not reference.permits_sales(channel_id):
        found.append("store_not_selling")
    elif channel_id is not None and channel_id not in reference.channel_ids:
        found.append("channel_not_enabled")
    if here is None or here.quantity_on_hand <= 0:
        found.append("out_of_stock")
    if restricted:
        found.append("PERSONALIZATION_WITHDRAWN")
    if not found and (here is None or not here.available):
        # Stock's verdict is the definition; the facts above only name the
        # reason. They agree today, and a disagreement never recommends.
        found.append("out_of_stock")
    return found


def _reasons(raw: Sequence[dict[str, Any]]) -> tuple[Reason, ...]:
    return tuple(
        Reason(r["signal"], r["segment_buyers"], r["category_id"], r["share"])
        for r in raw
    )


def judge(
    ranked: Sequence[dict[str, Any]],
    *,
    products: Mapping[int, Product],
    purchased: Collection[int],
    restricted: Collection[int],
    parents: Mapping[int, int | None],
    reference: Store,
    channel_id: int | None,
    here: Mapping[int, Here],
    stores: Mapping[int, Store],
    substitutes: Mapping[int, Sequence[int]],
    elsewhere: Mapping[int, Mapping[int, int]],
    limit: int,
) -> tuple[tuple[Item, ...], tuple[tuple[str, int], ...]]:
    """The items and the exclusion counts, from every product that matched a
    signal, best first (`ranked`, rule.rank over every product)."""

    def blocked(product_id: int) -> bool:
        chain = lineage(products[product_id].category_id, parents)
        return any(category in restricted for category in chain)

    primaries: list[dict[str, Any]] = []
    unavailable: list[tuple[dict[str, Any], str]] = []
    counts: dict[str, int] = {}
    for raw in ranked:
        product = products[raw["product_id"]]
        failing = failures(
            product,
            bought=product.product_id in purchased,
            restricted=blocked(product.product_id),
            reference=reference,
            channel_id=channel_id,
            here=here.get(product.product_id),
        )
        if not failing:
            primaries.append(raw)
            continue
        counts[failing[0]] = counts.get(failing[0], 0) + 1
        if set(failing) <= REPLACEABLE:
            unavailable.append((raw, failing[0]))

    items: list[Item] = []
    shown: set[int] = set()
    for raw in primaries[:limit]:
        product_id = raw["product_id"]
        items.append(
            Item(
                product_id=product_id,
                category_id=raw["category_id"],
                kind="primary",
                position=len(items) + 1,
                quantity_on_hand=here[product_id].quantity_on_hand,
                reasons=_reasons(raw["reasons"]),
            )
        )
        shown.add(product_id)

    for raw, reason in unavailable:
        if len(items) >= limit:
            break
        product_id = raw["product_id"]
        substitute = next(
            (
                candidate
                for candidate in substitutes.get(product_id, ())
                if candidate in products
                and candidate not in shown
                and not failures(
                    products[candidate],
                    bought=candidate in purchased,
                    restricted=blocked(candidate),
                    reference=reference,
                    channel_id=channel_id,
                    here=here.get(candidate),
                )
            ),
            None,
        )
        if substitute is not None:
            items.append(
                Item(
                    product_id=substitute,
                    category_id=products[substitute].category_id,
                    kind="alternative",
                    position=len(items) + 1,
                    quantity_on_hand=here[substitute].quantity_on_hand,
                    reasons=(Reason("relation_substitute"),),
                    alternative_for=product_id,
                    unavailable_reason=reason,
                )
            )
            shown.add(substitute)
            continue
        if reason == "not_sellable" or product_id in shown:
            continue
        held = elsewhere.get(product_id, {})
        other = nearest(reference, held, stores)
        if other is None:
            continue
        items.append(
            Item(
                product_id=product_id,
                category_id=raw["category_id"],
                kind="elsewhere",
                position=len(items) + 1,
                quantity_on_hand=held[other],
                reasons=_reasons(raw["reasons"]),
                held_at_store_id=other,
                unavailable_reason=reason,
            )
        )
        shown.add(product_id)

    exclusions = tuple(
        (reason, counts[reason]) for reason in EXCLUSIONS if counts.get(reason)
    )
    return tuple(items), exclusions


def recommend(
    reads: Reads,
    customer_id: str,
    as_of: datetime,
    limit: int,
    restricted: Collection[int] = (),
    *,
    store: Store | None = None,
    channel_id: int | None = None,
) -> ResultV2:
    """Recommend for one known, personalized customer at a reference store.
    `store` is the store the caller named, already checked to exist. The
    segment is checked first, as in /v1, and each read happens only once the
    answer still depends on it."""
    until = as_of
    since = until - timedelta(days=WINDOW_DAYS)
    named = (
        {"store_id": store.store_id, "store_source": "given"}
        if store is not None
        else {}
    )
    base: dict[str, Any] = {
        "customer_id": customer_id,
        "window_start": since,
        "window_end": until,
        "channel_id": channel_id,
    }

    label = reads.current_label(customer_id)
    if label.state != "assigned" or label.label_code is None:
        status = (
            "unassigned_result"
            if label.state == "unassigned_result"
            else "no_open_assignment"
        )
        return ResultV2(**base, **named, status=status)

    known = {
        "label_code": label.label_code,
        "run_id": label.run_id,
        "model_version_id": (
            reads.model_version(label.run_id) if label.run_id is not None else None
        ),
    }
    lines = reads.purchase_lines(customer_id, since, until)
    usual = dominant_store(lines)
    stores = reads.stores()
    if store is not None:
        reference = store
    elif usual is not None and usual in stores:
        reference = stores[usual]
        named = {"store_id": usual, "store_source": "usual_store"}
    else:
        return ResultV2(**base, **known, usual_store_id=usual, status="no_usual_store")

    catalog = {product.product_id: product for product in reads.products()}
    parents = reads.category_parents()
    # Every product is ranked as if it were on the shelf, so a product that
    # fails is still known with its signals; rule.rank's order does not depend
    # on the quantity, so the available ones keep /v1's order exactly.
    ranked = rank(
        [Stocked(p.product_id, p.category_id, 1) for p in catalog.values()],
        preferred=reads.interest_categories(customer_id),
        category_purchases=top_categories(lines),
        parents=parents,
        total_purchases=len({line.transaction_id for line in lines}),
        purchased=(),
        buyers=reads.segment_buyers(label.label_code, customer_id, since, until),
        limit=len(catalog),
    )
    purchased = {line.product_id for line in lines}
    candidates = [raw["product_id"] for raw in ranked]
    items, exclusions = judge(
        ranked,
        products=catalog,
        purchased=purchased,
        restricted=restricted,
        parents=parents,
        reference=reference,
        channel_id=channel_id,
        here=reads.here(reference.store_id, channel_id),
        stores=stores,
        substitutes=reads.substitutes(candidates),
        elsewhere=reads.selling_elsewhere(candidates, reference.store_id, channel_id),
        limit=limit,
    )
    primary = any(item.kind == "primary" for item in items)
    return ResultV2(
        **base,
        **known,
        **named,
        usual_store_id=usual,
        status="recommended" if primary else "none_match",
        exclusions=exclusions,
        items=items,
    )


def withdrawn(
    customer_id: str,
    as_of: datetime,
    *,
    store: Store | None = None,
    channel_id: int | None = None,
) -> ResultV2:
    """A customer who is not personalized: nothing of theirs is read, so the
    only store is the one the caller named (ADR-0067, Consent)."""
    return ResultV2(
        customer_id=customer_id,
        status="PERSONALIZATION_WITHDRAWN",
        window_start=as_of - timedelta(days=WINDOW_DAYS),
        window_end=as_of,
        store_id=None if store is None else store.store_id,
        store_source=None if store is None else "given",
        channel_id=channel_id,
    )
