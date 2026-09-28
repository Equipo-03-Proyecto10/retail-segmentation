"""Product recommendations with a stated reason for each (F10-01).

A recommendation has to be defensible to the business and actionable in a real
store, so three things are decided here and pinned by these tests: what makes a
product *eligible* (in stock in the customer's usual store, active, and not already
bought), what makes it *relevant* (the three signals the story names), and that every
recommendation says *why*. Ranking is a pure function of plain rows; `recommend` is
exercised with the reads replaced by fakes so each test states exactly what the
customer, their segment and the store looked like.

The recommendation reads the stable label and never how a run was produced
(ADR-0018): the two modules that compute it contain neither that word nor any
cluster vocabulary, and a test reads them to prove it.
"""

from __future__ import annotations

import inspect
import itertools
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

from web.db import recommendations as recommendations_db
from web.db.categories import Category
from web.db.consumption import CategoryTotal, GroupTotal, ProductTotal
from web.db.recommendations import StockedProduct
from web.services import recommendations as service
from web.services.consumption_profile import (
    ConsumptionProfile,
    InvalidWindow,
    SegmentState,
    UnknownCustomer,
)
from web.services.recommendations import (
    InvalidLimit,
    Signal,
    Status,
    rank_candidates,
    recommend,
)

_ID = "00000000-0000-0000-0000-000000000001"
_END = datetime(2026, 9, 28, 9, 0, tzinfo=UTC)
_START = _END - timedelta(days=180)


def _stock(product_id: int, category_id: int = 1, quantity: int = 5) -> StockedProduct:
    return StockedProduct(
        product_id,
        f"Product {product_id}",
        category_id,
        f"Category {category_id}",
        quantity,
    )


def _rank(stocked, **overrides):
    defaults = dict(
        preferred_category_ids=frozenset(),
        category_purchases={},
        categories={},
        total_purchases=10,
        purchased_product_ids=frozenset(),
        segment_buyers={},
        label_name="Loyal",
        window_days=180,
        limit=10,
    )
    return rank_candidates(stocked, **{**defaults, **overrides})


def _ids(recommendations) -> list[int]:
    return [r.product_id for r in recommendations]


# ---------- what is eligible ----------


def test_a_product_with_no_stock_never_appears_however_relevant_it_is() -> None:
    result = _rank(
        [_stock(1, quantity=0), _stock(2, quantity=3)],
        preferred_category_ids={1},
        category_purchases={1: 5},
        segment_buyers={1: 9, 2: 1},
    )

    assert _ids(result) == [2]


def test_a_negative_quantity_is_not_stock_either() -> None:
    assert _rank([_stock(1, quantity=-1)], preferred_category_ids={1}) == ()


def test_a_product_the_customer_already_bought_in_the_window_is_not_recommended() -> (
    None
):
    result = _rank(
        [_stock(1), _stock(2)],
        preferred_category_ids={1},
        purchased_product_ids={1},
    )

    assert _ids(result) == [2]


def test_a_product_that_matches_no_signal_is_not_recommended() -> None:
    """In stock is not a reason. Something has to say why this customer."""
    assert _rank([_stock(1, category_id=7)], preferred_category_ids={1}) == ()


# ---------- the three signals, each with its reason ----------


def test_the_segment_signal_is_what_other_customers_with_the_same_label_bought() -> (
    None
):
    (only,) = _rank([_stock(1)], segment_buyers={1: 4})

    assert [r.signal for r in only.reasons] == [Signal.SEGMENT]
    assert "4 other Loyal customers" in only.reasons[0].text
    assert "180 days" in only.reasons[0].text


def test_one_buyer_is_said_in_the_singular() -> None:
    (only,) = _rank([_stock(1)], segment_buyers={1: 1})

    assert "1 other Loyal customer bought" in only.reasons[0].text


def test_the_preferred_category_signal_names_the_category() -> None:
    (only,) = _rank([_stock(1, category_id=3)], preferred_category_ids={3})

    assert [r.signal for r in only.reasons] == [Signal.PREFERRED_CATEGORY]
    assert "Category 3" in only.reasons[0].text


def test_the_purchase_history_signal_states_how_much_of_their_buying_it_is() -> None:
    (only,) = _rank(
        [_stock(1, category_id=3)], category_purchases={3: 6}, total_purchases=10
    )

    assert [r.signal for r in only.reasons] == [Signal.PURCHASE_HISTORY]
    assert "6 of their 10 purchases" in only.reasons[0].text
    assert "Category 3" in only.reasons[0].text


def test_every_recommendation_carries_at_least_one_reason() -> None:
    result = _rank(
        [_stock(n, category_id=n) for n in range(1, 6)],
        preferred_category_ids={1, 2},
        category_purchases={3: 2},
        segment_buyers={4: 3, 5: 1},
    )

    assert len(result) == 5
    assert all(r.reasons for r in result)


def test_a_product_matching_all_three_lists_all_three_reasons_in_a_fixed_order() -> (
    None
):
    (only,) = _rank(
        [_stock(1, category_id=3)],
        preferred_category_ids={3},
        category_purchases={3: 6},
        segment_buyers={1: 4},
    )

    assert [r.signal for r in only.reasons] == [
        Signal.SEGMENT,
        Signal.PREFERRED_CATEGORY,
        Signal.PURCHASE_HISTORY,
    ]


def test_the_stock_the_store_holds_is_reported_so_it_can_be_acted_on() -> None:
    (only,) = _rank([_stock(1, quantity=17)], preferred_category_ids={1})

    assert only.in_stock == 17


def test_a_one_day_window_is_said_in_the_singular() -> None:
    (only,) = _rank(
        [_stock(1, category_id=3)],
        category_purchases={3: 1},
        segment_buyers={1: 1},
        total_purchases=1,
        window_days=1,
    )

    assert all("last 1 day" in r.text and "1 days" not in r.text for r in only.reasons)


# ---------- how they are ranked ----------


def test_more_signals_rank_first() -> None:
    result = _rank(
        [_stock(1, category_id=1), _stock(2, category_id=2), _stock(3, category_id=3)],
        preferred_category_ids={2, 3},
        category_purchases={3: 4},
        segment_buyers={1: 9, 3: 1},
    )

    # 3 matches all three; 2 matches one (preferred); 1 matches one (segment)
    assert _ids(result)[0] == 3
    assert set(_ids(result)[1:]) == {1, 2}


def test_equal_signals_rank_by_buyers_then_category_share_then_product_id() -> None:
    result = _rank(
        [_stock(4), _stock(3), _stock(2), _stock(1)],
        segment_buyers={1: 2, 2: 5, 3: 5, 4: 5},
    )

    assert _ids(result) == [2, 3, 4, 1]  # 5,5,5 by id, then 2


def test_then_by_how_much_of_their_buying_the_category_is() -> None:
    result = _rank(
        [_stock(1, category_id=1), _stock(2, category_id=2)],
        category_purchases={1: 2, 2: 7},
    )

    assert _ids(result) == [2, 1]


def test_segment_buyers_outrank_category_share() -> None:
    """RN-40 orders buyers before share: more of the segment beats a bigger share."""
    result = _rank(
        [_stock(1, category_id=1), _stock(2, category_id=2)],
        segment_buyers={1: 5, 2: 2},
        category_purchases={1: 1, 2: 8},
    )

    assert _ids(result) == [1, 2]


def test_the_ranking_does_not_depend_on_the_order_products_arrive_in() -> None:
    rng = random.Random(3)
    stocked = [_stock(n, category_id=n % 4) for n in range(1, 13)]
    kwargs = dict(
        preferred_category_ids={1, 2},
        category_purchases={3: 5, 0: 1},
        segment_buyers={n: n % 3 for n in range(1, 13)},
    )
    expected = _ids(_rank(stocked, **kwargs))

    for _ in range(10):
        shuffled = stocked[:]
        rng.shuffle(shuffled)
        assert _ids(_rank(shuffled, **kwargs)) == expected


def test_only_the_top_limit_are_returned() -> None:
    result = _rank(
        [_stock(n) for n in range(1, 21)],
        segment_buyers=dict.fromkeys(range(1, 21), 1),
        limit=5,
    )

    assert _ids(result) == [1, 2, 3, 4, 5]


# ---------- a category covers the categories below it (#277) ----------

_TREE = {
    c.category_id: c
    for c in (
        Category(1, "Dairy", None),
        Category(11, "Milk", 1),
        Category(12, "Yoghurt", 1),
        Category(111, "Skimmed milk", 11),
        Category(2, "Bakery", None),
    )
}


def _stock_in(product_id: int, category_id: int) -> StockedProduct:
    return StockedProduct(
        product_id, f"Product {product_id}", category_id, _TREE[category_id].name, 5
    )


def test_a_preferred_category_covers_the_categories_below_it_at_any_depth() -> None:
    result = _rank(
        [_stock_in(1, 11), _stock_in(2, 111)],
        preferred_category_ids={1},
        categories=_TREE,
    )

    by_id = {r.product_id: r for r in result}
    assert set(by_id) == {1, 2}
    assert by_id[1].reasons[0].signal is Signal.PREFERRED_CATEGORY
    assert by_id[1].reasons[0].text.startswith("In Milk, part of Dairy, a category")
    assert by_id[2].reasons[0].text.startswith("In Skimmed milk, part of Dairy,")


def test_a_category_does_not_cover_those_above_or_beside_it() -> None:
    result = _rank(
        [_stock_in(1, 1), _stock_in(2, 12), _stock_in(3, 2)],
        preferred_category_ids={11},
        category_purchases={11: 4},
        categories=_TREE,
    )

    assert result == ()


def test_a_category_they_buy_from_covers_the_categories_below_it() -> None:
    (only,) = _rank(
        [_stock_in(1, 111)],
        category_purchases={1: 6},
        total_purchases=10,
        categories=_TREE,
    )

    assert [r.signal for r in only.reasons] == [Signal.PURCHASE_HISTORY]
    assert only.reasons[0].text.startswith(
        "In Skimmed milk, part of Dairy; the customer bought from Dairy in 6 of "
        "their 10 purchases"
    )


def test_the_nearest_matching_category_is_the_one_named_and_counted() -> None:
    (only,) = _rank(
        [_stock_in(1, 111)],
        preferred_category_ids={1, 11},
        category_purchases={1: 9, 11: 2},
        categories=_TREE,
    )

    preferred, history = only.reasons
    assert "part of Milk," in preferred.text
    assert "bought from Milk in 2 of" in history.text


def test_the_share_of_the_nearest_bought_category_is_what_ranks() -> None:
    result = _rank(
        [_stock_in(1, 111), _stock_in(2, 12)],
        category_purchases={1: 9, 11: 2},
        categories=_TREE,
    )

    # 1 counts Milk's 2 (nearest), 2 counts Dairy's 9, so 2 ranks first
    assert _ids(result) == [2, 1]


def test_an_exact_match_reads_as_it_always_did() -> None:
    (only,) = _rank(
        [_stock_in(1, 11)],
        preferred_category_ids={11},
        category_purchases={11: 3},
        categories=_TREE,
    )

    assert only.reasons[0].text == "In Milk, a category the customer said they like"
    assert only.reasons[1].text.startswith("The customer bought from Milk in 3 of")


# ---------- recommend: the whole computation ----------


def _profile(**overrides) -> ConsumptionProfile:
    defaults = dict(
        customer_id=_ID,
        customer_name="Ada Lovelace",
        window_days=180,
        window_start=_START,
        window_end=_END,
        has_sales=True,
        total_spend=Decimal("2750.00"),
        average_ticket=Decimal("275.00"),
        purchase_count=10,
        last_purchase_at=_END - timedelta(days=3),
        dominant_channel=GroupTotal(4, "Marketplace", 6, Decimal("1650.00")),
        dominant_store=GroupTotal(8, "Store 8", 10, Decimal("2750.00")),
        favourite_categories=(CategoryTotal(2, "Snacks", 6, 18, Decimal("900.00")),),
        frequent_products=(),
        average_discount_pct=Decimal("10.00"),
        rfm=None,
        current_segment=SegmentState(
            run_id=30,
            label_code="LOYAL",
            label_name="Loyal",
            valid_from=_END,
            valid_to=None,
        ),
        previous_segment=None,
    )
    return ConsumptionProfile(**{**defaults, **overrides})


class _World:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, profile=None) -> None:
        self.profile = Mock(return_value=profile or _profile())
        self.interests = Mock(return_value=[Category(3, "Bakery", None)])
        self.purchased = Mock(return_value=[ProductTotal(11, "Bought before", 2, 4)])
        self.stocked = Mock(
            return_value=[
                _stock(11, category_id=2),  # already bought
                _stock(12, category_id=2),  # a category they buy from
                _stock(13, category_id=3),  # a category they prefer
                _stock(14, category_id=9),  # nothing matches
            ]
        )
        self.buyers = Mock(return_value={14: 3, 15: 9})
        self.categories = Mock(return_value=[])
        for name, fake in {
            "build_profile": self.profile,
            "list_interest_categories": self.interests,
            "list_product_totals": self.purchased,
            "list_stocked_products": self.stocked,
            "list_segment_buyers": self.buyers,
            "list_all_categories": self.categories,
        }.items():
            monkeypatch.setattr(service, name, fake)


def test_it_combines_the_segment_label_the_preferred_categories_and_the_history(
    monkeypatch,
) -> None:
    _World(monkeypatch)

    result = recommend(MagicMock(), _ID)

    assert result.status is Status.RECOMMENDED
    by_id = {r.product_id: r for r in result.recommendations}
    assert {s.signal for s in by_id[12].reasons} == {Signal.PURCHASE_HISTORY}
    assert {s.signal for s in by_id[13].reasons} == {Signal.PREFERRED_CATEGORY}
    assert {s.signal for s in by_id[14].reasons} == {Signal.SEGMENT}
    assert 11 not in by_id  # bought already
    assert result.label_code == "LOYAL"


def test_it_reads_the_hierarchy_so_an_interest_covers_its_subcategories(
    monkeypatch,
) -> None:
    world = _World(monkeypatch)
    world.interests.return_value = [_TREE[1]]
    world.categories.return_value = list(_TREE.values())
    world.stocked.return_value = [_stock_in(21, 111)]
    world.buyers.return_value = {}

    result = recommend(MagicMock(), _ID)

    (only,) = result.recommendations
    assert only.product_id == 21
    assert [r.signal for r in only.reasons] == [Signal.PREFERRED_CATEGORY]


def test_only_the_usual_stores_stock_is_read(monkeypatch) -> None:
    world = _World(monkeypatch)

    recommend(MagicMock(), _ID)

    assert world.stocked.call_args.args[1] == 8  # the profile's dominant store


def test_the_result_names_the_store_and_the_window_it_was_computed_for(
    monkeypatch,
) -> None:
    _World(monkeypatch)

    result = recommend(MagicMock(), _ID)

    assert (result.store_id, result.store_name) == (8, "Store 8")
    assert (result.window_days, result.window_start, result.window_end) == (
        180,
        _START,
        _END,
    )


def test_segment_buyers_are_read_by_label_over_the_profile_window(
    monkeypatch,
) -> None:
    world = _World(monkeypatch)

    recommend(MagicMock(), _ID)

    args = world.buyers.call_args.args
    assert args[1:] == ("LOYAL", _ID, _START, _END)


def test_the_window_and_as_of_reach_the_profile(monkeypatch) -> None:
    world = _World(monkeypatch)

    recommend(MagicMock(), _ID, window_days=90, as_of=_END)

    assert world.profile.call_args.kwargs == {"window_days": 90, "as_of": _END}


def test_a_customer_with_no_open_assignment_is_told_there_is_no_segment(
    monkeypatch,
) -> None:
    _World(monkeypatch, _profile(current_segment=None))

    result = recommend(MagicMock(), _ID)

    assert result.status is Status.NO_SEGMENT
    assert "no segment" in result.message.lower()
    assert result.recommendations == ()


def test_no_segment_is_never_replaced_by_a_fallback(monkeypatch) -> None:
    """Nothing is read that could stand in for the missing segment: not the
    stock, not what other customers bought, not the history."""
    world = _World(monkeypatch, _profile(current_segment=None))

    recommend(MagicMock(), _ID)

    world.stocked.assert_not_called()
    world.buyers.assert_not_called()
    world.purchased.assert_not_called()
    world.categories.assert_not_called()


def test_an_open_assignment_that_is_unassigned_is_also_no_segment(monkeypatch) -> None:
    unassigned = SegmentState(30, None, None, _END, None)
    world = _World(monkeypatch, _profile(current_segment=unassigned))

    result = recommend(MagicMock(), _ID)

    assert result.status is Status.NO_SEGMENT
    assert "run 30" in result.message.lower() and "unassigned" in result.message.lower()
    world.stocked.assert_not_called()


def test_a_customer_never_scored_and_one_left_unassigned_are_told_apart(
    monkeypatch,
) -> None:
    never = recommend_with(monkeypatch, _profile(current_segment=None)).message
    left = recommend_with(
        monkeypatch, _profile(current_segment=SegmentState(30, None, None, _END, None))
    ).message

    assert never != left


def recommend_with(monkeypatch, profile):
    _World(monkeypatch, profile)
    return recommend(MagicMock(), _ID)


def test_a_customer_with_no_usual_store_is_told_so_and_nothing_is_guessed(
    monkeypatch,
) -> None:
    world = _World(
        monkeypatch,
        _profile(
            has_sales=False,
            dominant_store=None,
            purchase_count=None,
            favourite_categories=(),
        ),
    )

    result = recommend(MagicMock(), _ID)

    assert result.status is Status.NO_USUAL_STORE
    assert "usual store" in result.message.lower()
    world.stocked.assert_not_called()


def test_the_segment_check_comes_before_the_store_check(monkeypatch) -> None:
    """A customer with neither is told about the segment, the first thing missing."""
    result = recommend_with(
        monkeypatch,
        _profile(current_segment=None, has_sales=False, dominant_store=None),
    )

    assert result.status is Status.NO_SEGMENT


def test_when_nothing_in_stock_matches_the_result_says_so(monkeypatch) -> None:
    world = _World(monkeypatch)
    world.stocked.return_value = [_stock(14, category_id=9)]
    world.buyers.return_value = {}

    result = recommend(MagicMock(), _ID)

    assert result.status is Status.NONE_MATCH
    assert "Store 8" in result.message
    assert result.recommendations == ()


def test_an_unknown_customer_is_refused(monkeypatch) -> None:
    world = _World(monkeypatch)
    world.profile.side_effect = UnknownCustomer

    with pytest.raises(UnknownCustomer):
        recommend(MagicMock(), _ID)


def test_a_window_the_segment_run_would_refuse_is_refused(monkeypatch) -> None:
    world = _World(monkeypatch)
    world.profile.side_effect = InvalidWindow

    with pytest.raises(InvalidWindow):
        recommend(MagicMock(), _ID, window_days=0)


@pytest.mark.parametrize("limit", [0, -1, 51, True, "3", 2.5])
def test_a_limit_that_is_not_a_sensible_count_is_refused_before_anything_is_read(
    monkeypatch, limit
) -> None:
    world = _World(monkeypatch)

    with pytest.raises(InvalidLimit):
        recommend(MagicMock(), _ID, limit=limit)

    world.profile.assert_not_called()


# ---------- what it may not read (ADR-0018) ----------


def test_the_computation_never_reads_how_a_segmentation_run_was_produced() -> None:
    for module in (service, recommendations_db):
        text = Path(module.__file__).read_text(encoding="utf-8").lower()
        for word in ("method", "cluster", "kmeans", "rfm_rules", "segmentation_run"):
            assert word not in text, (module.__name__, word)


def test_the_recommendation_is_asked_for_nothing_but_a_customer() -> None:
    names = " ".join(inspect.signature(recommend).parameters)

    assert "method" not in names and "run" not in names and "cluster" not in names


def test_every_permutation_of_a_small_stock_gives_the_same_recommendations() -> None:
    stocked = [_stock(n, category_id=n) for n in range(1, 5)]
    kwargs = dict(preferred_category_ids={1, 2}, segment_buyers={3: 2, 4: 2})
    expected = _rank(stocked, **kwargs)

    for order in itertools.permutations(stocked):
        assert _rank(list(order), **kwargs) == expected


def test_the_no_usual_store_message_says_one_day_in_the_singular(monkeypatch) -> None:
    _World(
        monkeypatch,
        _profile(
            window_days=1, has_sales=False, dominant_store=None, purchase_count=None
        ),
    )

    message = recommend(MagicMock(), _ID, window_days=1).message

    assert "last 1 day," in message and "1 days" not in message
