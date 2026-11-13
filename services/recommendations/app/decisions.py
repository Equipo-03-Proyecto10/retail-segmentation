"""The recorded explanation of one recommendation (ADR-0067, F24-05).

A `Decision` is what the service decided and why, in plain values: the
customer, the reference store and channel, the segmentation run, the status, the
exclusion counts and every item offered with its quantity and its reasons. It
holds identifiers, figures and codes only. The response a client receives is a
view of it, not the other way round: `v1_items` and `as_dict` are the two views
this module gives, and a decision read back from the database renders the same
as the one that was written.

Nothing derivable is carried. An item's factors are its reasons' figures, its
availability channel is the decision's channel, and its availability store is
the decision's store unless it is an `elsewhere` entry (the schema section in
sql/01_schema.sql says the same).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from mosaiq_kit.rendering import wire_value

from .rule import Result

# The orders the contract fixes: reasons in RN-40's order, exclusions in the
# order the service applies them (ADR-0067).
SIGNALS = ("segment", "preferred_category", "purchase_history", "relation_substitute")
EXCLUSIONS = (
    "already_bought",
    "inactive",
    "not_sellable",
    "store_not_selling",
    "channel_not_enabled",
    "out_of_stock",
    "PERSONALIZATION_WITHDRAWN",
)
STATUSES = (
    "recommended",
    "none_match",
    "no_open_assignment",
    "unassigned_result",
    "no_usual_store",
    "PERSONALIZATION_WITHDRAWN",
)
KINDS = ("primary", "alternative", "elsewhere")


@dataclass(frozen=True)
class Reason:
    signal: str
    segment_buyers: int | None = None
    category_id: int | None = None
    share: Decimal | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "signal": self.signal,
            "segment_buyers": self.segment_buyers,
            "category_id": self.category_id,
            "share": self.share,
        }


@dataclass(frozen=True)
class Item:
    product_id: int
    category_id: int
    kind: str
    position: int
    quantity_on_hand: int
    reasons: tuple[Reason, ...]
    held_at_store_id: int | None = None  # an `elsewhere` entry's own store
    alternative_for: int | None = None
    unavailable_reason: str | None = None

    def factors(self) -> dict[str, Any] | None:
        """The figures RN-40 orders by, read from the reasons. An alternative is
        not scored, so it has none."""
        if self.kind == "alternative":
            return None
        buyers = next(
            (r.segment_buyers for r in self.reasons if r.signal == "segment"), None
        )
        share = next(
            (r.share for r in self.reasons if r.signal == "purchase_history"), None
        )
        return {
            "signals_matched": len(self.reasons),
            "segment_buyers": buyers,
            "category_share": share,
        }


@dataclass(frozen=True)
class Decision:
    decision_id: str
    customer_id: str | None
    status: str
    computed_at: datetime
    run_id: int | None = None
    model_version_id: int | None = None
    store_id: int | None = None
    store_source: str | None = None
    channel_id: int | None = None
    exclusions: tuple[tuple[str, int], ...] = ()
    items: tuple[Item, ...] = field(default_factory=tuple)

    def item_dict(self, item: Item) -> dict[str, Any]:
        """The contract's RecommendationItemV2 for one item."""
        return {
            "product_id": item.product_id,
            "category_id": item.category_id,
            "kind": item.kind,
            "position": item.position,
            "factors": item.factors(),
            "availability": {
                "store_id": (
                    item.held_at_store_id if item.kind == "elsewhere" else self.store_id
                ),
                "channel_id": self.channel_id,
                "quantity_on_hand": item.quantity_on_hand,
            },
            "alternative_for": item.alternative_for,
            "unavailable_reason": item.unavailable_reason,
            "reasons": [reason.as_dict() for reason in item.reasons],
        }

    def as_dict(self) -> dict[str, Any]:
        """The contract's RecommendationDecision, as the wire carries it."""
        return wire_value(
            {
                "decision_id": self.decision_id,
                "customer_id": self.customer_id,
                "status": self.status,
                "computed_at": self.computed_at,
                "run_id": self.run_id,
                "model_version_id": self.model_version_id,
                "store_id": self.store_id,
                "store_source": self.store_source,
                "channel_id": self.channel_id,
                "exclusions": [
                    {"reason": reason, "count": count}
                    for reason, count in self.exclusions
                ],
                "items": [self.item_dict(item) for item in self.items],
            }
        )

    def v1_items(self) -> list[dict[str, Any]]:
        """The items of a `/v1` answer, which has no kind, position, factors or
        availability: the view of a decision that was judged at the usual store."""
        return wire_value(
            [
                {
                    "product_id": item.product_id,
                    "category_id": item.category_id,
                    "quantity_on_hand": item.quantity_on_hand,
                    "reasons": [reason.as_dict() for reason in item.reasons],
                }
                for item in self.items
            ]
        )


def from_v1(result: Result, *, decision_id: str, computed_at: datetime) -> Decision:
    """The decision behind a `/v1` answer: judged at the usual store, no channel,
    only primary items, and no model version (the open assignment names none).
    The v1 rule counts no exclusions, so none are recorded."""
    store = result.usual_store_id
    items = tuple(
        Item(
            product_id=raw["product_id"],
            category_id=raw["category_id"],
            kind="primary",
            position=position,
            quantity_on_hand=raw["quantity_on_hand"],
            reasons=tuple(
                Reason(
                    signal=reason["signal"],
                    segment_buyers=reason["segment_buyers"],
                    category_id=reason["category_id"],
                    share=reason["share"],
                )
                for reason in raw["reasons"]
            ),
        )
        for position, raw in enumerate(result.items, start=1)
    )
    return Decision(
        decision_id=decision_id,
        customer_id=result.customer_id,
        status=result.status,
        computed_at=computed_at,
        run_id=result.run_id,
        store_id=store,
        store_source="usual_store" if store is not None else None,
        items=items,
    )
