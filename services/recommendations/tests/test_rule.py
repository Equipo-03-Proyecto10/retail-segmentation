"""RN-40, rule by rule, over plain rows. Each test is the fault it guards
against: break the rule in app/rule.py and the test fails."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.rule import (
    Label,
    Line,
    Stocked,
    dominant_store,
    lineage,
    rank,
    recommend,
    share,
    top_categories,
)

AS_OF = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
CUSTOMER = "00000000-0000-0000-0000-000000000001"

# Dairy(1) > Milk(2) > Lactose-free(3); Dairy(1) > Cheese(4); Bakery(5).
PARENTS: dict[int, int | None] = {1: None, 2: 1, 3: 2, 4: 1, 5: None}


def line(txn: int, product: int, category: int, store: int = 1, qty: int = 1) -> Line:
    return Line(txn, store, product, category, qty, Decimal("10.00"))


def ranked(stocked: list[Stocked], **overrides: object) -> list[dict]:
    arguments: dict = {
        "preferred": set(),
        "category_purchases": {},
        "parents": PARENTS,
        "total_purchases": 4,
        "purchased": set(),
        "buyers": {},
        "limit": 10,
    }
    arguments.update(overrides)
    return rank(stocked, **arguments)


def ids(items: list[dict]) -> list[int]:
    return [item["product_id"] for item in items]


class FakeReads:
    def __init__(self, label: Label, lines: list[Line] | None = None):
        self.label = label
        self.lines = lines or []
        self.calls: list[str] = []

    def current_label(self, customer_id):
        self.calls.append("current_label")
        return self.label

    def purchase_lines(self, customer_id, since, until):
        self.calls.append("purchase_lines")
        assert until - since == timedelta(days=180)
        return self.lines

    def stocked_products(self, store_id):
        self.calls.append("stocked_products")
        return [Stocked(100, 3, 5), Stocked(101, 5, 5)]

    def segment_buyers(self, label_code, customer_id, since, until):
        self.calls.append("segment_buyers")
        return {}

    def interest_categories(self, customer_id):
        self.calls.append("interest_categories")
        return {1}

    def category_parents(self):
        self.calls.append("category_parents")
        return PARENTS


# ---------- eligibility ----------


def test_only_positive_stock_is_recommended() -> None:
    items = ranked([Stocked(100, 2, 0), Stocked(101, 2, 3)], preferred={2})
    assert ids(items) == [101]


def test_a_product_already_bought_in_the_window_is_not_recommended() -> None:
    items = ranked(
        [Stocked(100, 2, 3), Stocked(101, 2, 3)], preferred={2}, purchased={100}
    )
    assert ids(items) == [101]


def test_a_product_with_no_signal_is_not_recommended() -> None:
    assert ranked([Stocked(100, 5, 3)], preferred={2}) == []


# ---------- categories: the subtree, never above or beside ----------


def test_an_interest_covers_its_whole_subtree() -> None:
    items = ranked([Stocked(100, 3, 1)], preferred={1})
    assert items[0]["reasons"][0] == {
        "signal": "preferred_category",
        "segment_buyers": None,
        "category_id": 1,
        "share": None,
    }


def test_an_interest_never_matches_a_category_above_or_beside() -> None:
    above = ranked([Stocked(100, 1, 1)], preferred={2})
    beside = ranked([Stocked(100, 4, 1)], preferred={2})
    assert above == [] and beside == []


def test_the_nearest_matching_category_is_the_one_reported() -> None:
    items = ranked([Stocked(100, 3, 1)], preferred={1, 2})
    assert items[0]["reasons"][0]["category_id"] == 2
    assert items[0]["category_id"] == 3


def test_purchase_history_names_the_nearest_bought_category_and_its_share() -> None:
    items = ranked(
        [Stocked(100, 3, 1)], category_purchases={1: 1, 2: 3}, total_purchases=4
    )
    reason = items[0]["reasons"][0]
    assert reason["signal"] == "purchase_history"
    assert reason["category_id"] == 2
    assert reason["share"] == Decimal("0.75")


def test_reasons_come_in_the_fixed_order() -> None:
    items = ranked(
        [Stocked(100, 2, 1)],
        preferred={2},
        category_purchases={2: 1},
        buyers={100: 3},
    )
    assert [r["signal"] for r in items[0]["reasons"]] == [
        "segment",
        "preferred_category",
        "purchase_history",
    ]
    assert items[0]["reasons"][0]["segment_buyers"] == 3


def test_lineage_walks_up_to_the_root_nearest_first() -> None:
    assert lineage(3, PARENTS) == [3, 2, 1]


def test_lineage_stops_on_a_cycle_instead_of_looping() -> None:
    assert lineage(7, {7: 8, 8: 7}) == [7, 8]


# ---------- ordering ----------


def test_more_signals_rank_first() -> None:
    items = ranked(
        [Stocked(100, 2, 1), Stocked(101, 2, 1)], preferred={2}, buyers={101: 1}
    )
    assert ids(items) == [101, 100]


def test_then_more_segment_buyers() -> None:
    items = ranked([Stocked(100, 5, 1), Stocked(101, 5, 1)], buyers={100: 1, 101: 4})
    assert ids(items) == [101, 100]


def test_then_the_categorys_share_of_the_customers_buying() -> None:
    items = ranked(
        [Stocked(100, 2, 1), Stocked(101, 4, 1)],
        category_purchases={2: 1, 4: 3},
    )
    assert ids(items) == [101, 100]


def test_then_product_id_and_the_limit_cuts() -> None:
    stocked = [Stocked(103, 2, 1), Stocked(101, 2, 1), Stocked(102, 2, 1)]
    assert ids(ranked(stocked, preferred={2})) == [101, 102, 103]
    assert ids(ranked(stocked, preferred={2}, limit=2)) == [101, 102]


# ---------- the profile pieces ----------


def test_the_usual_store_has_most_purchases_then_spend_then_lowest_id() -> None:
    assert dominant_store([line(1, 9, 2, store=7), line(2, 9, 2, store=3)]) == 3
    assert (
        dominant_store(
            [line(1, 9, 2, store=3), line(2, 9, 2, store=7), line(3, 9, 2, store=7)]
        )
        == 7
    )
    spend = [line(1, 9, 2, store=3), line(2, 9, 2, store=7, qty=5)]
    assert dominant_store(spend) == 7
    assert dominant_store([]) is None


def test_a_purchase_is_a_transaction_not_a_line_or_a_product() -> None:
    # Store 3: one purchase of two products. Store 7: two purchases of one.
    lines = [
        line(1, 9, 2, store=3),
        line(1, 8, 2, store=3),
        line(2, 9, 2, store=7),
        line(3, 9, 2, store=7),
    ]
    assert dominant_store(lines) == 7


def test_the_top_three_categories_count_purchases_containing_them() -> None:
    lines = [
        line(1, 9, 2),
        line(1, 8, 2),
        line(2, 9, 2),
        line(3, 7, 4),
        line(4, 6, 5),
        line(5, 5, 3),
    ]
    assert top_categories(lines) == {2: 2, 3: 1, 4: 1}


def test_share_rounds_half_up_to_four_places() -> None:
    assert share(1, 3) == Decimal("0.3333")
    assert share(2, 3) == Decimal("0.6667")
    assert share(4, 4) == Decimal("1")


# ---------- statuses, and that a refusal reads nothing more ----------


@pytest.mark.parametrize(
    "label, status",
    [
        (Label("no_open_assignment", None, None), "no_open_assignment"),
        (Label("unassigned_result", None, 41), "unassigned_result"),
    ],
)
def test_no_segment_is_answered_without_reading_further(label, status) -> None:
    reads = FakeReads(label)
    result = recommend(reads, CUSTOMER, AS_OF, 10)
    assert result.status == status
    assert result.run_id is None and result.label_code is None
    assert reads.calls == ["current_label"]


def test_no_sale_in_the_window_is_no_usual_store() -> None:
    reads = FakeReads(Label("assigned", "LOYAL", 41))
    result = recommend(reads, CUSTOMER, AS_OF, 10)
    assert (result.status, result.label_code, result.run_id) == (
        "no_usual_store",
        "LOYAL",
        41,
    )
    assert reads.calls == ["current_label", "purchase_lines"]


def test_an_eligible_customer_with_no_match_is_none_match() -> None:
    reads = FakeReads(Label("assigned", "LOYAL", 41), [line(1, 100, 3, store=2)])
    reads.interest_categories = lambda customer_id: {4}  # type: ignore[method-assign]
    result = recommend(reads, CUSTOMER, AS_OF, 10)
    assert result.status == "none_match"
    assert result.usual_store_id == 2
    assert result.items == []


def test_a_match_is_recommended_with_its_store_and_window() -> None:
    reads = FakeReads(Label("assigned", "LOYAL", 41), [line(1, 999, 4, store=2)])
    result = recommend(reads, CUSTOMER, AS_OF, 10)
    assert result.status == "recommended"
    # 100 (Lactose-free) sits under the Dairy interest; 101 (Bakery) matches nothing.
    assert ids(result.items) == [100]
    data = result.as_dict(AS_OF)
    assert data["window"] == {
        "days": 180,
        "start": AS_OF - timedelta(days=180),
        "end": AS_OF,
    }
    assert set(data) == {
        "customer_id",
        "status",
        "computed_at",
        "window",
        "label_code",
        "run_id",
        "usual_store_id",
        "items",
    }
