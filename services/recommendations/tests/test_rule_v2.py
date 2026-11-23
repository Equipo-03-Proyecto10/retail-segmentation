"""The /v2 rule over plain rows (F24-04, F24-07, ADR-0067, ADR-0084).

Each exclusion code, its order, the alternatives and the elsewhere entries are
checked on hand-made rows, so every case is one the seed does not happen to
hold. The same rule over the seeded database is in test_v2.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from app import rule_v2
from app.rule import Label, Line
from app.rule_v2 import Here, Product, Store, distance_km, judge, nearest

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
# Monterrey, Guadalupe (about 9 km east), Saltillo (about 70 km west).
REFERENCE = Store(1, True, "OPEN", frozenset({1, 3}), 25.6866, -100.3161)
NEAR = Store(2, True, "OPEN", frozenset({1, 3}), 25.6767, -100.2565)
FAR = Store(3, True, "OPEN", frozenset({1, 3}), 25.4232, -101.0053)
UNLOCATED = Store(4, True, "OPEN", frozenset({1, 3}))
STORES = {s.store_id: s for s in (REFERENCE, NEAR, FAR, UNLOCATED)}
PARENTS: dict[int, int | None] = {10: None, 11: 10, 20: None}


def _signal(product_id: int, category_id: int = 11) -> dict[str, Any]:
    return {
        "product_id": product_id,
        "category_id": category_id,
        "quantity_on_hand": 1,
        "reasons": [
            {
                "signal": "preferred_category",
                "segment_buyers": None,
                "category_id": category_id,
                "share": None,
            }
        ],
    }


def _judge(
    ranked: list[dict[str, Any]],
    *,
    products: dict[int, Product] | None = None,
    here: dict[int, Here] | None = None,
    purchased: set[int] = frozenset(),  # type: ignore[assignment]
    restricted: set[int] = frozenset(),  # type: ignore[assignment]
    reference: Store = REFERENCE,
    channel_id: int | None = None,
    substitutes: dict[int, list[int]] | None = None,
    elsewhere: dict[int, dict[int, int]] | None = None,
    limit: int = 10,
):
    catalog = products or {
        raw["product_id"]: Product(raw["product_id"], raw["category_id"], True)
        for raw in ranked
    }
    return judge(
        ranked,
        products=catalog,
        purchased=purchased,
        restricted=restricted,
        parents=PARENTS,
        reference=reference,
        channel_id=channel_id,
        here=here if here is not None else {p: Here(True, 5) for p in catalog},
        stores=STORES,
        substitutes=substitutes or {},
        elsewhere=elsewhere or {},
        limit=limit,
    )


# ---------- one exclusion code each, and their order ----------


def test_an_available_product_is_a_primary_item_judged_at_the_reference_store() -> None:
    items, exclusions = _judge([_signal(1)], here={1: Here(True, 7)})
    assert [(i.kind, i.product_id, i.quantity_on_hand) for i in items] == [
        ("primary", 1, 7)
    ]
    assert exclusions == ()


@pytest.mark.parametrize(
    "setup, code",
    [
        ({"purchased": {1}}, "already_bought"),
        ({"products": {1: Product(1, 11, False)}}, "not_sellable"),
        (
            {"reference": Store(1, True, "TEMPORARILY_CLOSED", frozenset({1}))},
            "store_not_selling",
        ),
        ({"reference": Store(1, False, "OPEN", frozenset({1}))}, "store_not_selling"),
        ({"reference": Store(1, True, "OPEN", frozenset())}, "store_not_selling"),
        ({"channel_id": 2}, "channel_not_enabled"),
        ({"here": {1: Here(False, 0)}}, "out_of_stock"),
        ({"here": {}}, "out_of_stock"),
        ({"restricted": {10}}, "PERSONALIZATION_WITHDRAWN"),
    ],
)
def test_a_product_that_fails_only_one_test_is_excluded_with_that_code(
    setup: dict[str, Any], code: str
) -> None:
    items, exclusions = _judge([_signal(1)], **setup)
    assert items == ()
    assert exclusions == ((code, 1),)


def test_a_paused_or_discontinued_product_is_not_sellable_never_inactive() -> None:
    _, exclusions = _judge([_signal(1)], products={1: Product(1, 11, False)})
    assert exclusions == (("not_sellable", 1),)


def test_the_first_failing_reason_is_the_one_counted_in_the_contracts_order() -> None:
    ranked = [_signal(p) for p in (1, 2, 3, 4)]
    products = {
        1: Product(1, 11, False),
        2: Product(2, 11, False),
        3: Product(3, 11, True),
        4: Product(4, 11, True),
    }
    _, exclusions = _judge(
        ranked,
        products=products,
        purchased={1},  # bought and not sellable: already_bought
        here={2: Here(False, 0), 3: Here(False, 0), 4: Here(False, 0)},
        restricted={10},  # every product is restricted too
    )
    assert exclusions == (
        ("already_bought", 1),
        ("not_sellable", 1),
        ("out_of_stock", 2),
    )


def test_a_product_without_a_signal_is_never_counted() -> None:
    # rank() leaves out a product with no signal; judge only sees `ranked`.
    _, exclusions = _judge([], products={1: Product(1, 11, False)})
    assert exclusions == ()


# ---------- alternatives ----------


def test_an_unavailable_product_gets_its_declared_substitute_marked() -> None:
    products = {1: Product(1, 11, True), 31: Product(31, 20, True)}
    items, exclusions = _judge(
        [_signal(1)],
        products=products,
        here={1: Here(False, 0), 31: Here(True, 4)},
        substitutes={1: [31]},
    )
    (alternative,) = items
    assert alternative.kind == "alternative"
    assert (alternative.product_id, alternative.category_id) == (31, 20)
    assert alternative.alternative_for == 1
    assert alternative.unavailable_reason == "out_of_stock"
    assert [r.signal for r in alternative.reasons] == ["relation_substitute"]
    assert alternative.factors() is None
    assert alternative.quantity_on_hand == 4
    # The replaced product is still counted.
    assert exclusions == (("out_of_stock", 1),)


def test_a_not_sellable_product_gets_an_alternative() -> None:
    products = {1: Product(1, 11, False), 31: Product(31, 11, True)}
    (alternative,), _ = _judge([_signal(1)], products=products, substitutes={1: [31]})
    assert (alternative.kind, alternative.unavailable_reason) == (
        "alternative",
        "not_sellable",
    )


@pytest.mark.parametrize(
    "setup",
    [
        {"purchased": {1}},
        {"restricted": {10}},
        {"reference": Store(1, True, "CLOSED", frozenset({1, 3}), 25.6, -100.3)},
    ],
    ids=["already_bought", "consent", "store_not_selling"],
)
def test_no_alternative_for_a_reason_an_alternative_may_not_answer(
    setup: dict[str, Any],
) -> None:
    products = {1: Product(1, 11, True), 31: Product(31, 20, True)}
    here = {1: Here(False, 0), 31: Here(True, 4)}
    items, _ = _judge(
        [_signal(1)], products=products, here=here, substitutes={1: [31]}, **setup
    )
    assert items == ()


@pytest.mark.parametrize(
    "candidate, setup",
    [
        (Product(31, 20, False), {}),  # not sellable itself
        (Product(31, 20, True), {"here": {1: Here(False, 0), 31: Here(False, 0)}}),
        (Product(31, 20, True), {"purchased": {31}}),
        (Product(31, 11, True), {"restricted": {11}}),
    ],
    ids=["not-sellable", "out-of-stock", "bought", "restricted"],
)
def test_a_substitute_that_is_not_itself_valid_is_never_offered(
    candidate: Product, setup: dict[str, Any]
) -> None:
    products = {1: Product(1, 10, True), 31: candidate}
    here = setup.pop("here", {1: Here(False, 0), 31: Here(True, 4)})
    items, _ = _judge(
        [_signal(1, 10)], products=products, here=here, substitutes={1: [31]}, **setup
    )
    assert items == ()


def test_no_relation_no_alternative_and_nothing_is_invented() -> None:
    products = {1: Product(1, 11, True), 2: Product(2, 11, True)}
    items, _ = _judge(
        [_signal(1)], products=products, here={1: Here(False, 0), 2: Here(True, 9)}
    )
    assert items == ()


def test_a_substitute_already_in_the_result_is_not_offered_twice() -> None:
    products = {p: Product(p, 11, True) for p in (1, 2, 31, 32)}
    here = {1: Here(True, 3), 2: Here(False, 0), 31: Here(True, 1), 32: Here(True, 2)}
    items, _ = _judge(
        [_signal(1), _signal(2)],
        products=products,
        here=here,
        substitutes={2: [1, 31, 32]},
    )
    assert [(i.kind, i.product_id, i.alternative_for) for i in items] == [
        ("primary", 1, None),
        ("alternative", 31, 2),  # 1 is already shown; 31 is the lowest left
    ]


def test_alternatives_only_fill_what_the_primary_items_leave() -> None:
    products = {p: Product(p, 11, True) for p in (1, 2, 3, 31)}
    here = {1: Here(False, 0), 2: Here(True, 1), 3: Here(True, 1), 31: Here(True, 1)}
    ranked = [_signal(1), _signal(2), _signal(3)]
    items, _ = _judge(
        ranked, products=products, here=here, substitutes={1: [31]}, limit=2
    )
    assert [(i.kind, i.product_id) for i in items] == [("primary", 2), ("primary", 3)]
    items, _ = _judge(
        ranked, products=products, here=here, substitutes={1: [31]}, limit=3
    )
    assert [(i.kind, i.product_id, i.position) for i in items] == [
        ("primary", 2, 1),
        ("primary", 3, 2),
        ("alternative", 31, 3),
    ]


# ---------- elsewhere ----------


def test_the_elsewhere_store_is_the_nearest_by_great_circle_distance() -> None:
    assert distance_km(REFERENCE, NEAR) < distance_km(REFERENCE, FAR)
    assert distance_km(REFERENCE, NEAR) == pytest.approx(6.0, abs=1.0)
    items, exclusions = _judge(
        [_signal(1)],
        here={1: Here(False, 0)},
        elsewhere={1: {3: 50, 2: 4, 4: 99}},
    )
    (entry,) = items
    assert (entry.kind, entry.product_id, entry.held_at_store_id) == (
        "elsewhere",
        1,
        2,
    )
    assert entry.quantity_on_hand == 4
    assert entry.unavailable_reason == "out_of_stock"
    assert entry.alternative_for is None
    assert [r.signal for r in entry.reasons] == ["preferred_category"]
    assert exclusions == (("out_of_stock", 1),)


def test_a_tie_goes_to_the_lowest_store_id_and_unlocated_stores_are_skipped() -> None:
    twin = Store(9, True, "OPEN", frozenset({1}), NEAR.latitude, NEAR.longitude)
    stores = {**STORES, 9: twin}
    assert nearest(REFERENCE, {9, 2, 4}, stores) == 2
    assert nearest(REFERENCE, {4}, stores) is None
    assert nearest(UNLOCATED, {2, 3}, stores) is None


def test_no_elsewhere_entry_from_a_reference_store_without_coordinates() -> None:
    items, _ = _judge(
        [_signal(1)],
        reference=Store(1, True, "OPEN", frozenset({1, 3})),
        here={1: Here(False, 0)},
        elsewhere={1: {2: 4}},
    )
    assert items == ()


def test_elsewhere_only_when_no_substitute_qualifies() -> None:
    products = {1: Product(1, 11, True), 31: Product(31, 11, True)}
    items, _ = _judge(
        [_signal(1)],
        products=products,
        here={1: Here(False, 0), 31: Here(True, 2)},
        substitutes={1: [31]},
        elsewhere={1: {2: 4}},
    )
    assert [i.kind for i in items] == ["alternative"]


def test_a_channel_not_enabled_here_is_reported_where_it_is() -> None:
    items, exclusions = _judge(
        [_signal(1)], channel_id=2, elsewhere={1: {3: 8}}, here={1: Here(False, 5)}
    )
    (entry,) = items
    assert (entry.kind, entry.held_at_store_id, entry.unavailable_reason) == (
        "elsewhere",
        3,
        "channel_not_enabled",
    )
    assert exclusions == (("channel_not_enabled", 1),)


def test_a_not_sellable_product_is_never_reported_elsewhere() -> None:
    items, _ = _judge(
        [_signal(1)], products={1: Product(1, 11, False)}, elsewhere={1: {2: 4}}
    )
    assert items == ()


# ---------- the statuses, over a stub ----------


class _Stub:
    def __init__(self, label: Label, lines: list[Line] | None = None):
        self.label = label
        self.lines = lines or []
        self.read: list[str] = []

    def current_label(self, customer_id: str) -> Label:
        return self.label

    def model_version(self, run_id: int) -> int | None:
        self.read.append("model_version")
        return 7

    def purchase_lines(self, *args: Any) -> list[Line]:
        self.read.append("purchase_lines")
        return self.lines

    def segment_buyers(self, *args: Any) -> dict[int, int]:
        return {}

    def interest_categories(self, customer_id: str) -> set[int]:
        return {11}

    def category_parents(self) -> dict[int, int | None]:
        return PARENTS

    def products(self) -> list[Product]:
        return [Product(1, 11, True), Product(2, 11, True)]

    def stores(self) -> dict[int, Store]:
        self.read.append("stores")
        return STORES

    def here(self, store_id: int, channel_id: int | None) -> dict[int, Here]:
        self.read.append(f"here:{store_id}:{channel_id}")
        return {1: Here(True, 3), 2: Here(False, 0)}

    def selling_elsewhere(self, *args: Any) -> dict[int, dict[int, int]]:
        return {}

    def substitutes(self, product_ids: list[int]) -> dict[int, list[int]]:
        return {}


ASSIGNED = Label("assigned", "LOYAL", 41)
SALE = Line(1, 2, 9, 20, 1, Decimal("10.00"))  # a sale at store 2


@pytest.mark.parametrize(
    "label, status",
    [
        (Label("no_open_assignment", None, None), "no_open_assignment"),
        (Label("unassigned_result", None, 40), "unassigned_result"),
    ],
)
def test_a_customer_without_a_segment_is_answered_before_anything_else(
    label: Label, status: str
) -> None:
    stub = _Stub(label)
    result = rule_v2.recommend(stub, "c", NOW, 10, store=NEAR, channel_id=1)
    assert result.status == status
    assert (result.label_code, result.run_id, result.model_version_id) == (
        None,
        None,
        None,
    )
    # The named store and channel are what the caller gave; nothing else read.
    assert (result.store_id, result.store_source, result.channel_id) == (
        2,
        "given",
        1,
    )
    assert stub.read == []
    assert result.items == () and result.exclusions == ()


def test_no_usual_store_and_no_store_named_is_no_usual_store() -> None:
    result = rule_v2.recommend(_Stub(ASSIGNED), "c", NOW, 10)
    assert result.status == "no_usual_store"
    assert (result.store_id, result.store_source, result.usual_store_id) == (
        None,
        None,
        None,
    )
    assert (result.run_id, result.model_version_id) == (41, 7)


def test_a_named_store_needs_no_usual_store() -> None:
    stub = _Stub(ASSIGNED)
    result = rule_v2.recommend(stub, "c", NOW, 10, store=REFERENCE)
    assert result.status == "recommended"
    assert (result.store_id, result.store_source, result.usual_store_id) == (
        1,
        "given",
        None,
    )
    assert "here:1:None" in stub.read


def test_with_no_store_named_the_usual_store_is_the_reference() -> None:
    stub = _Stub(ASSIGNED, [SALE])
    result = rule_v2.recommend(stub, "c", NOW, 10, channel_id=3)
    assert (result.store_id, result.store_source, result.usual_store_id) == (
        2,
        "usual_store",
        2,
    )
    assert "here:2:3" in stub.read


def test_a_named_store_beats_the_usual_store() -> None:
    stub = _Stub(ASSIGNED, [SALE])
    result = rule_v2.recommend(stub, "c", NOW, 10, store=FAR)
    assert (result.store_id, result.store_source, result.usual_store_id) == (
        3,
        "given",
        2,
    )


def test_nothing_available_is_none_match_with_its_exclusions() -> None:
    class Empty(_Stub):
        def here(self, store_id: int, channel_id: int | None) -> dict[int, Here]:
            return {}

    result = rule_v2.recommend(Empty(ASSIGNED, [SALE]), "c", NOW, 10)
    assert result.status == "none_match"
    assert result.items == ()
    assert result.exclusions == (("out_of_stock", 2),)


def test_withdrawn_names_only_what_the_caller_named() -> None:
    result = rule_v2.withdrawn("c", NOW, store=NEAR, channel_id=1)
    assert result.status == "PERSONALIZATION_WITHDRAWN"
    assert (result.store_id, result.store_source, result.channel_id) == (
        2,
        "given",
        1,
    )
    assert (result.label_code, result.run_id, result.usual_store_id) == (
        None,
        None,
        None,
    )
    plain = rule_v2.withdrawn("c", NOW)
    assert (plain.store_id, plain.store_source) == (None, None)
