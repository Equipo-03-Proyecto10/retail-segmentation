"""The customer consumption profile (F8-03).

Every rule that decides what a profile says is a pure function in
web/services/consumption_profile.py (ADR-0003), so the ranking and tie-break
tests drive it with plain values and no database. `build_profile` is exercised
with the web.db reads replaced by fakes, which is how these tests can state
exactly which window a query was asked for and prove that a customer with no
sales skips every remaining sales query while retaining assignment history.
"""

from __future__ import annotations

import itertools
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

from web.db.consumption import (
    CategoryTotal,
    DiscountTotals,
    GroupTotal,
    HistoryRow,
    ProductTotal,
    SalesTotals,
)
from web.services import consumption_profile as profile_service
from web.services.consumption_profile import (
    ConsumptionProfile,
    RfmSnapshot,
    SegmentState,
    UnknownCustomer,
    average_discount_pct,
    average_ticket,
    build_profile,
    rank_categories,
    rank_dominant,
    rank_products,
)
from web.services.segmentation import DEFAULT_WINDOW_DAYS, InvalidWindow

_CUSTOMER = "00000000-0000-0000-0000-000000000001"
_AS_OF = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
_LAST_PURCHASE = datetime(2026, 9, 20, 9, 30, tzinfo=UTC)


# ---------- dominant channel and store: the stated tie-break ----------


def test_the_dominant_choice_is_the_one_with_most_purchases() -> None:
    groups = [
        GroupTotal(1, "web", 2, Decimal("900.00")),
        GroupTotal(2, "app", 5, Decimal("100.00")),
    ]
    assert rank_dominant(groups).item_id == 2


def test_a_tie_on_purchases_goes_to_the_higher_spend() -> None:
    groups = [
        GroupTotal(1, "web", 3, Decimal("100.00")),
        GroupTotal(2, "app", 3, Decimal("250.00")),
    ]
    assert rank_dominant(groups).item_id == 2


def test_a_tie_on_purchases_and_spend_goes_to_the_lowest_id() -> None:
    groups = [
        GroupTotal(7, "kiosk", 3, Decimal("100.00")),
        GroupTotal(4, "web", 3, Decimal("100.00")),
        GroupTotal(9, "app", 3, Decimal("100.00")),
    ]
    assert rank_dominant(groups).item_id == 4


def test_the_dominant_choice_does_not_depend_on_row_order() -> None:
    """The database returns groups in whatever order the planner likes; a
    profile that changed with it would flap between two equally valid answers."""
    groups = [
        GroupTotal(3, "c", 2, Decimal("50.00")),
        GroupTotal(1, "a", 2, Decimal("50.00")),
        GroupTotal(2, "b", 2, Decimal("50.00")),
    ]
    answers = {
        rank_dominant(list(order)).item_id for order in itertools.permutations(groups)
    }
    assert answers == {1}


def test_no_groups_means_no_dominant_choice() -> None:
    assert rank_dominant([]) is None


# ---------- favourite categories and frequent products ----------


def _category(
    category_id: int, purchases: int, units: int, spend: str
) -> CategoryTotal:
    return CategoryTotal(
        category_id, f"cat {category_id}", purchases, units, Decimal(spend)
    )


def test_categories_rank_by_purchases_then_units_then_spend_then_id() -> None:
    groups = [
        _category(5, 2, 4, "10.00"),  # fewest purchases
        _category(4, 3, 3, "99.00"),  # ties on purchases, fewer units
        _category(3, 3, 6, "10.00"),  # ties on purchases, most units
        _category(2, 3, 6, "20.00"),  # ties on units too, more spend
        _category(1, 3, 6, "20.00"),  # ties on everything, lower id
    ]
    ranked = rank_categories(groups, limit=5)
    assert [c.category_id for c in ranked] == [1, 2, 3, 4, 5]


def test_only_the_three_favourite_categories_are_kept_by_default() -> None:
    groups = [_category(n, 10 - n, 1, "1.00") for n in range(1, 7)]
    assert [c.category_id for c in rank_categories(groups)] == [1, 2, 3]


def test_products_rank_by_purchases_then_units_then_id() -> None:
    groups = [
        ProductTotal(9, "p9", 2, 2),
        ProductTotal(8, "p8", 3, 3),
        ProductTotal(7, "p7", 3, 5),
        ProductTotal(6, "p6", 3, 5),
    ]
    ranked = rank_products(groups, limit=4)
    assert [p.product_id for p in ranked] == [6, 7, 8, 9]


def test_only_the_five_most_frequent_products_are_kept_by_default() -> None:
    groups = [ProductTotal(n, f"p{n}", 20 - n, 1) for n in range(1, 9)]
    assert [p.product_id for p in rank_products(groups)] == [1, 2, 3, 4, 5]


def test_rankings_are_repeatable_under_any_input_order() -> None:
    categories = [_category(n, 2, 2, "5.00") for n in (3, 1, 2)]
    products = [ProductTotal(n, f"p{n}", 2, 2) for n in (3, 1, 2)]
    for order in itertools.permutations(categories):
        assert [c.category_id for c in rank_categories(list(order))] == [1, 2, 3]
    for order in itertools.permutations(products):
        assert [p.product_id for p in rank_products(list(order))] == [1, 2, 3]


# ---------- derived measures ----------


def test_average_ticket_is_total_spend_over_purchases_rounded_half_up() -> None:
    assert average_ticket(Decimal("100.00"), 3) == Decimal("33.33")
    assert average_ticket(Decimal("0.05"), 2) == Decimal("0.03")  # 0.025 rounds up


def test_average_discount_compares_paid_with_the_list_value_of_the_same_units() -> None:
    # Paid 75 for units that list at 100 today: a 25 % difference.
    assert average_discount_pct(Decimal("75.00"), Decimal("100.00")) == Decimal("25.00")


def test_a_customer_who_paid_above_list_has_a_negative_discount() -> None:
    """Not clamped: the schema records what was paid (RN-13) and the catalog
    price may have fallen since, so a negative figure is a true finding."""
    assert average_discount_pct(Decimal("150.00"), Decimal("100.00")) == Decimal(
        "-50.00"
    )


@pytest.mark.parametrize(
    ("paid", "at_list"),
    [(None, None), (Decimal("10.00"), None), (Decimal("10.00"), Decimal("0"))],
)
def test_no_positive_list_value_means_no_discount_rather_than_zero(
    paid, at_list
) -> None:
    assert average_discount_pct(paid, at_list) is None


def test_a_zero_discount_is_never_negative_zero() -> None:
    result = average_discount_pct(Decimal("99.999"), Decimal("100.000"))
    assert str(result) == "0.00"


# ---------- assembling the profile ----------


def _history(
    run_id: int = 30,
    label: str | None = "LOYAL",
    *,
    scores: tuple[int, int, int] | None = (2, 3, 4),
    valid_to: datetime | None = None,
) -> HistoryRow:
    r, f, m = scores if scores else (None, None, None)
    return HistoryRow(
        run_id=run_id,
        label_code=label,
        label_name=label.title() if label else None,
        last_purchase_at=_LAST_PURCHASE if scores else None,
        frequency_count=10 if scores else None,
        monetary_total=Decimal("1234.50") if scores else None,
        r_score=r,
        f_score=f,
        m_score=m,
        valid_from=_AS_OF - timedelta(days=1),
        valid_to=valid_to,
        run_at=_AS_OF - timedelta(days=1),
        window_days=180,
    )


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    *,
    purchases: int = 10,
    open_row: HistoryRow | None = None,
    previous_row: HistoryRow | None = None,
) -> dict[str, Mock]:
    """Replace every web.db read the service uses. Returns the fakes so a test
    can assert on the arguments they were called with."""
    fakes = {
        "get_customer": Mock(return_value=SimpleNamespace(name="Ada Lovelace")),
        "get_sales_totals": Mock(
            return_value=SalesTotals(
                purchases=purchases,
                spend=Decimal("1000.00") if purchases else None,
                last_purchase_at=_LAST_PURCHASE if purchases else None,
            )
        ),
        "list_channel_totals": Mock(
            return_value=[
                GroupTotal(1, "web", 6, Decimal("600.00")),
                GroupTotal(2, "app", 4, Decimal("400.00")),
            ]
        ),
        "list_store_totals": Mock(
            return_value=[GroupTotal(5, "Store 5", 10, Decimal("1000.00"))]
        ),
        "list_category_totals": Mock(return_value=[_category(1, 7, 9, "500.00")]),
        "list_product_totals": Mock(return_value=[ProductTotal(11, "Demo 11", 4, 6)]),
        "get_discount_totals": Mock(
            return_value=DiscountTotals(Decimal("80.00"), Decimal("100.00"))
        ),
        "get_current_and_previous_history_rows": Mock(
            return_value=(open_row, previous_row)
        ),
    }
    for name, fake in fakes.items():
        monkeypatch.setattr(profile_service, name, fake)
    return fakes


def test_a_customer_with_accepted_sales_carries_every_measure(monkeypatch) -> None:
    _wire(
        monkeypatch,
        open_row=_history(),
        previous_row=_history(29, "CHAMPION", valid_to=_AS_OF),
    )

    profile = build_profile(Mock(), _CUSTOMER, as_of=_AS_OF)

    assert profile.has_sales is True
    assert profile.customer_name == "Ada Lovelace"
    assert profile.total_spend == Decimal("1000.00")
    assert profile.purchase_count == 10
    assert profile.average_ticket == Decimal("100.00")
    assert profile.last_purchase_at == _LAST_PURCHASE
    assert profile.dominant_channel.name == "web"
    assert profile.dominant_store.name == "Store 5"
    assert [c.name for c in profile.favourite_categories] == ["cat 1"]
    assert [p.name for p in profile.frequent_products] == ["Demo 11"]
    assert profile.average_discount_pct == Decimal("20.00")


def test_the_window_ends_at_as_of_and_starts_window_days_earlier(monkeypatch) -> None:
    fakes = _wire(monkeypatch)

    profile = build_profile(Mock(), _CUSTOMER, window_days=30, as_of=_AS_OF)

    assert profile.window_days == 30
    assert profile.window_end == _AS_OF
    assert profile.window_start == _AS_OF - timedelta(days=30)
    for name in (
        "get_sales_totals",
        "list_channel_totals",
        "list_store_totals",
        "list_category_totals",
        "list_product_totals",
        "get_discount_totals",
    ):
        args = fakes[name].call_args.args
        assert args[1:] == (_CUSTOMER, _AS_OF - timedelta(days=30), _AS_OF), name


def test_the_default_window_is_the_one_the_segment_run_defaults_to(monkeypatch) -> None:
    _wire(monkeypatch)
    profile = build_profile(Mock(), _CUSTOMER, as_of=_AS_OF)
    assert profile.window_days == DEFAULT_WINDOW_DAYS


def test_as_of_defaults_to_the_present(monkeypatch) -> None:
    _wire(monkeypatch)
    timezone = ZoneInfo("America/Mexico_City")
    connection = Mock()
    connection.info.timezone = timezone
    profile = build_profile(connection, _CUSTOMER)
    assert abs(datetime.now(timezone) - profile.window_end) < timedelta(seconds=5)


def test_the_default_window_end_uses_the_connection_time_zone(monkeypatch) -> None:
    _wire(monkeypatch)
    timezone = ZoneInfo("America/Mexico_City")
    connection = Mock()
    connection.info.timezone = timezone

    profile = build_profile(connection, _CUSTOMER)

    assert profile.window_end.tzinfo is timezone


@pytest.mark.parametrize("days", [0, -1, 3651])
def test_a_window_the_segment_run_would_refuse_is_refused(
    days: int, monkeypatch
) -> None:
    fakes = _wire(monkeypatch)
    with pytest.raises(InvalidWindow):
        build_profile(Mock(), _CUSTOMER, window_days=days, as_of=_AS_OF)
    fakes["get_sales_totals"].assert_not_called()


def test_a_malformed_customer_id_is_refused_without_querying(monkeypatch) -> None:
    fakes = _wire(monkeypatch)

    with pytest.raises(UnknownCustomer):
        build_profile(Mock(), "not-a-uuid", as_of=_AS_OF)

    for fake in fakes.values():
        fake.assert_not_called()


def test_an_unknown_customer_is_refused_before_any_sales_read(monkeypatch) -> None:
    fakes = _wire(monkeypatch)
    fakes["get_customer"].return_value = None

    with pytest.raises(UnknownCustomer):
        build_profile(Mock(), _CUSTOMER, as_of=_AS_OF)

    fakes["get_customer"].assert_called_once()
    for name, fake in fakes.items():
        if name != "get_customer":
            fake.assert_not_called()


# ---------- a customer with no accepted sales ----------


def test_a_customer_with_no_accepted_sales_gets_an_empty_profile(monkeypatch) -> None:
    _wire(monkeypatch, purchases=0)

    profile = build_profile(Mock(), _CUSTOMER, as_of=_AS_OF)

    assert profile.has_sales is False
    assert profile.customer_id == _CUSTOMER
    assert profile.window_days == DEFAULT_WINDOW_DAYS


def test_an_empty_profile_keeps_history_but_no_sales_measurements(
    monkeypatch,
) -> None:
    _wire(
        monkeypatch,
        purchases=0,
        open_row=_history(),
        previous_row=_history(29, "CHAMPION", valid_to=_AS_OF),
    )

    profile = build_profile(Mock(), _CUSTOMER, as_of=_AS_OF)

    assert profile.total_spend is None
    assert profile.purchase_count is None
    assert profile.average_ticket is None
    assert profile.last_purchase_at is None
    assert profile.dominant_channel is None
    assert profile.dominant_store is None
    assert profile.average_discount_pct is None
    assert profile.rfm is not None
    assert profile.rfm.frequency == 10
    assert profile.current_segment.label_code == "LOYAL"
    assert profile.previous_segment.label_code == "CHAMPION"
    assert profile.favourite_categories == ()
    assert profile.frequent_products == ()


def test_an_empty_profile_skips_the_remaining_sales_reads(monkeypatch) -> None:
    fakes = _wire(monkeypatch, purchases=0)

    build_profile(Mock(), _CUSTOMER, as_of=_AS_OF)

    fakes["get_customer"].assert_called_once()
    fakes["get_sales_totals"].assert_called_once()
    fakes["get_current_and_previous_history_rows"].assert_called_once()
    for name in (
        "get_discount_totals",
        "list_channel_totals",
        "list_store_totals",
        "list_category_totals",
        "list_product_totals",
    ):
        fakes[name].assert_not_called()


# ---------- R, F, M and the segments, all from assignment history ----------


def test_the_rfm_values_and_scores_come_from_the_open_history_row(monkeypatch) -> None:
    _wire(monkeypatch, open_row=_history())

    rfm = build_profile(Mock(), _CUSTOMER, as_of=_AS_OF).rfm

    assert rfm == RfmSnapshot(
        run_id=30,
        run_at=_AS_OF - timedelta(days=1),
        window_days=180,
        last_purchase_at=_LAST_PURCHASE,
        frequency=10,
        monetary=Decimal("1234.50"),
        r_score=2,
        f_score=3,
        m_score=4,
    )


def test_the_current_and_previous_segment_come_from_the_open_and_last_closed_rows(
    monkeypatch,
) -> None:
    closed_at = _AS_OF - timedelta(days=1)
    _wire(
        monkeypatch,
        open_row=_history(30, "LOYAL"),
        previous_row=_history(29, "CHAMPION", valid_to=closed_at),
    )

    profile = build_profile(Mock(), _CUSTOMER, as_of=_AS_OF)

    assert profile.current_segment.label_code == "LOYAL"
    assert profile.current_segment.run_id == 30
    assert profile.previous_segment.label_code == "CHAMPION"
    assert profile.previous_segment.run_id == 29
    assert profile.previous_segment.valid_to == closed_at


def test_a_customer_with_one_assignment_only_has_no_previous_segment(
    monkeypatch,
) -> None:
    """Absent, not a repeat of the current one."""
    _wire(monkeypatch, open_row=_history(30, "LOYAL"), previous_row=None)

    profile = build_profile(Mock(), _CUSTOMER, as_of=_AS_OF)

    assert profile.current_segment.label_code == "LOYAL"
    assert profile.previous_segment is None


def test_an_unassigned_previous_result_is_not_the_same_as_an_absent_one(
    monkeypatch,
) -> None:
    """RN-21: a run that left the customer unassigned is a result. Reporting it
    as an absent previous segment would erase that a run did score them."""
    _wire(
        monkeypatch,
        open_row=_history(30, "LOYAL"),
        previous_row=_history(29, None, scores=None, valid_to=_AS_OF),
    )

    previous = build_profile(Mock(), _CUSTOMER, as_of=_AS_OF).previous_segment

    assert previous is not None
    assert previous.label_code is None


def test_an_unassigned_open_row_is_a_current_segment_with_no_label_and_no_rfm(
    monkeypatch,
) -> None:
    _wire(monkeypatch, open_row=_history(30, None, scores=None))

    profile = build_profile(Mock(), _CUSTOMER, as_of=_AS_OF)

    assert profile.current_segment == SegmentState(
        run_id=30,
        label_code=None,
        label_name=None,
        valid_from=_AS_OF - timedelta(days=1),
        valid_to=None,
    )
    assert profile.rfm is None


def test_a_customer_no_run_has_scored_has_no_rfm_and_no_segments(monkeypatch) -> None:
    _wire(monkeypatch, open_row=None, previous_row=None)

    profile = build_profile(Mock(), _CUSTOMER, as_of=_AS_OF)

    assert profile.has_sales is True
    assert profile.rfm is None
    assert profile.current_segment is None
    assert profile.previous_segment is None


# ---------- ADR-0018: a consumer never learns the method ----------


def test_nothing_the_profile_carries_names_a_segmentation_method_or_cluster() -> None:
    for model in (ConsumptionProfile, RfmSnapshot, SegmentState):
        for field in fields(model):
            assert "method" not in field.name, (model.__name__, field.name)
            assert "cluster" not in field.name, (model.__name__, field.name)
