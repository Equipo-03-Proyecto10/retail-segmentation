"""Consumption shifts between two periods (F8-05).

A shift is a change in what a customer mostly buys through, where and of: their
dominant channel, their dominant store or their leading category. Every rule that
decides one is a pure function in web/services/consumption_shift.py (ADR-0003),
so these tests drive it with plain rows. `detect_shifts` is exercised with its
single database read replaced by a fake, making the mapping of its earlier and
later result rows explicit.

"Dominant" means what it means in the consumption profile (RN-35), and the tests
prove the two agree by feeding the same tied rows through both.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, Mock

import pytest

from web.db.consumption import CategoryTotal, GroupTotal
from web.services import consumption_shift as shift_service
from web.services.consumption_profile import rank_categories, rank_dominant
from web.services.consumption_shift import (
    CustomerAbsence,
    DimensionShift,
    InvalidPeriods,
    Leader,
    Period,
    Side,
    build_leaders,
    compare_periods,
    consecutive_periods,
    detect_shifts,
    order_periods,
)

_T0 = datetime(2026, 3, 1, tzinfo=UTC)
_T1 = datetime(2026, 6, 1, tzinfo=UTC)
_T2 = datetime(2026, 9, 1, tzinfo=UTC)
_EARLIER = Period(_T0, _T1)
_LATER = Period(_T1, _T2)

_ADA = "00000000-0000-0000-0000-000000000001"
_BOB = "00000000-0000-0000-0000-000000000002"
_CAL = "00000000-0000-0000-0000-000000000003"


def _g(item_id: int, purchases: int, spend: str = "100.00") -> GroupTotal:
    return GroupTotal(item_id, f"item {item_id}", purchases, Decimal(spend))


def _c(category_id: int, purchases: int, units: int = 1) -> CategoryTotal:
    return CategoryTotal(
        category_id, f"cat {category_id}", purchases, units, Decimal("10.00")
    )


def _leaders(customers: dict[str, tuple[int, int, int | None]]):
    """{customer: (channel id, store id, category id)} -> what build_leaders
    returns, built through build_leaders itself so the two never drift."""
    channels = {c: [_g(ch, 5)] for c, (ch, _s, _cat) in customers.items()}
    stores = {c: [_g(st, 5)] for c, (_ch, st, _cat) in customers.items()}
    categories = {
        c: [_c(cat, 5)] for c, (_ch, _s, cat) in customers.items() if cat is not None
    }
    return build_leaders(channels, stores, categories)


# ---------- periods are stated, ordered and validated ----------


def test_the_result_states_both_periods() -> None:
    report = compare_periods(_EARLIER, _LATER, {}, {})

    assert report.earlier == _EARLIER
    assert report.later == _LATER


def test_periods_given_the_wrong_way_round_are_put_in_order_by_their_start() -> None:
    assert order_periods(_LATER, _EARLIER) == (_EARLIER, _LATER)
    assert order_periods(_EARLIER, _LATER) == (_EARLIER, _LATER)


def test_adjacent_periods_are_comparable() -> None:
    """Half-open: the first ends where the second begins, so no instant is in both."""
    assert order_periods(_EARLIER, _LATER) == (_EARLIER, _LATER)


def test_overlapping_periods_are_refused() -> None:
    overlapping = Period(_T1 - timedelta(days=1), _T2)
    with pytest.raises(InvalidPeriods, match="overlap"):
        order_periods(_EARLIER, overlapping)


def test_the_same_period_twice_is_refused() -> None:
    with pytest.raises(InvalidPeriods, match="overlap"):
        order_periods(_EARLIER, _EARLIER)


@pytest.mark.parametrize("start,end", [(_T1, _T0), (_T0, _T0)])
def test_a_period_that_does_not_end_after_it_starts_is_refused(start, end) -> None:
    with pytest.raises(InvalidPeriods, match="end after"):
        order_periods(Period(start, end), _LATER)


def test_a_period_without_a_timezone_is_refused() -> None:
    naive = Period(datetime(2026, 1, 1), datetime(2026, 2, 1))
    with pytest.raises(InvalidPeriods, match="timezone"):
        order_periods(naive, _LATER)


def test_consecutive_periods_are_adjacent_and_equally_long() -> None:
    earlier, later = consecutive_periods(_T2, 30)

    assert earlier.end == later.start
    assert later.end == _T2
    assert later.end - later.start == timedelta(days=30)
    assert earlier.end - earlier.start == timedelta(days=30)


@pytest.mark.parametrize("days", [0, -1])
def test_consecutive_periods_need_at_least_one_day(days: int) -> None:
    with pytest.raises(InvalidPeriods):
        consecutive_periods(_T2, days)


# ---------- a shift: a changed dominant channel, store or leading category ----------


def test_a_changed_dominant_channel_is_reported_with_both_values() -> None:
    before = _leaders({_ADA: (1, 10, 100)})
    after = _leaders({_ADA: (2, 10, 100)})

    report = compare_periods(_EARLIER, _LATER, before, after)

    (shift,) = report.shifts
    assert shift.customer_id == _ADA
    assert shift.channel == DimensionShift(Leader(1, "item 1"), Leader(2, "item 2"))
    assert shift.store is None
    assert shift.category is None


def test_a_changed_dominant_store_is_reported_with_both_values() -> None:
    before = _leaders({_ADA: (1, 10, 100)})
    after = _leaders({_ADA: (1, 20, 100)})

    (shift,) = compare_periods(_EARLIER, _LATER, before, after).shifts

    assert shift.store == DimensionShift(Leader(10, "item 10"), Leader(20, "item 20"))
    assert shift.channel is None and shift.category is None


def test_a_changed_leading_category_is_reported_with_both_values() -> None:
    before = _leaders({_ADA: (1, 10, 100)})
    after = _leaders({_ADA: (1, 10, 200)})

    (shift,) = compare_periods(_EARLIER, _LATER, before, after).shifts

    assert shift.category == DimensionShift(
        Leader(100, "cat 100"), Leader(200, "cat 200")
    )
    assert shift.channel is None and shift.store is None


def test_several_shifts_at_once_are_reported_together() -> None:
    before = _leaders({_ADA: (1, 10, 100)})
    after = _leaders({_ADA: (2, 20, 200)})

    (shift,) = compare_periods(_EARLIER, _LATER, before, after).shifts

    assert shift.channel and shift.store and shift.category


def test_a_customer_whose_dominant_values_did_not_change_is_not_reported() -> None:
    before = _leaders({_ADA: (1, 10, 100), _BOB: (1, 10, 100)})
    after = _leaders({_ADA: (1, 10, 100), _BOB: (2, 10, 100)})

    report = compare_periods(_EARLIER, _LATER, before, after)

    assert [s.customer_id for s in report.shifts] == [_BOB]
    assert report.unchanged == 1


def test_no_shifts_at_all_is_an_empty_list_and_not_an_error() -> None:
    same = _leaders({_ADA: (1, 10, 100)})

    report = compare_periods(_EARLIER, _LATER, same, same)

    assert report.shifts == ()
    assert report.unchanged == 1


# ---------- absence is not a shift ----------


def test_a_customer_with_no_sales_in_the_earlier_period_is_an_absence_not_a_shift() -> (
    None
):
    report = compare_periods(_EARLIER, _LATER, {}, _leaders({_ADA: (1, 10, 100)}))

    assert report.shifts == ()
    assert report.absences == (CustomerAbsence(_ADA, Side.EARLIER),)


def test_a_customer_with_no_sales_in_the_later_period_is_an_absence_not_a_shift() -> (
    None
):
    report = compare_periods(_EARLIER, _LATER, _leaders({_ADA: (1, 10, 100)}), {})

    assert report.shifts == ()
    assert report.absences == (CustomerAbsence(_ADA, Side.LATER),)


def test_a_customer_with_no_sales_in_either_period_is_not_in_the_report() -> None:
    report = compare_periods(_EARLIER, _LATER, {}, {})

    assert report.shifts == () and report.absences == ()
    assert report.compared == 0


def test_absent_customers_are_not_counted_as_compared() -> None:
    before = _leaders({_ADA: (1, 10, 100), _BOB: (1, 10, 100)})
    after = _leaders({_ADA: (2, 10, 100), _CAL: (1, 10, 100)})

    report = compare_periods(_EARLIER, _LATER, before, after)

    assert report.compared == 1  # only Ada has sales in both
    assert {a.customer_id: a.absent_from for a in report.absences} == {
        _BOB: Side.LATER,
        _CAL: Side.EARLIER,
    }


def test_every_customer_with_sales_is_accounted_for_exactly_once() -> None:
    """compared = shifted + unchanged, and every customer seen is either
    compared or absent, so the report reconciles."""
    before = _leaders({_ADA: (1, 10, 100), _BOB: (1, 10, 100), _CAL: (1, 10, 100)})
    after = _leaders({_ADA: (2, 10, 100), _BOB: (1, 10, 100)})

    report = compare_periods(_EARLIER, _LATER, before, after)

    assert report.compared == len(report.shifts) + report.unchanged == 2
    assert report.compared + len(report.absences) == 3


# ---------- a category needs a leader on both sides ----------


def test_a_missing_category_on_one_side_is_not_a_category_shift() -> None:
    """A purchase can exist with no product lines. With nothing to lead the
    category on one side there is nothing to compare, so nothing is reported."""
    before = _leaders({_ADA: (1, 10, None)})
    after = _leaders({_ADA: (1, 10, 200)})

    report = compare_periods(_EARLIER, _LATER, before, after)

    assert report.shifts == ()
    assert report.unchanged == 1


# ---------- "dominant" means what it means in the profile (RN-35) ----------


def test_the_leader_of_a_tie_is_the_profiles_leader() -> None:
    """Equal purchases; the higher spend wins, then the lowest id."""
    rows = [_g(1, 3, "100.00"), _g(2, 3, "250.00")]

    leaders = build_leaders({_ADA: rows}, {_ADA: rows}, {})

    assert leaders[_ADA].channel.item_id == rank_dominant(rows).item_id == 2


def test_the_leading_category_is_the_profiles_first_favourite() -> None:
    categories = [_c(5, 2, units=9), _c(4, 3, units=1), _c(3, 3, units=1)]

    leaders = build_leaders({_ADA: [_g(1, 1)]}, {_ADA: [_g(1, 1)]}, {_ADA: categories})

    assert (
        leaders[_ADA].category.item_id
        == rank_categories(categories)[0].category_id
        == 3
    )


def test_the_leader_does_not_depend_on_the_order_rows_arrive_in() -> None:
    rows = [_g(3, 2), _g(1, 2), _g(2, 2)]
    answers = {
        build_leaders({_ADA: list(order)}, {_ADA: list(order)}, {})[
            _ADA
        ].channel.item_id
        for order in itertools.permutations(rows)
    }

    assert answers == {1}


def test_a_tie_that_flips_between_periods_is_a_shift_by_the_stated_rule() -> None:
    """Before: channels 1 and 2 tie on purchases and 2 has the higher spend.
    After: they tie again and 1 does. Under RN-35 that is a change of dominant
    channel, and the report says so rather than second-guessing the rule."""
    before = build_leaders(
        {_ADA: [_g(1, 3, "100.00"), _g(2, 3, "200.00")]}, {_ADA: [_g(1, 1)]}, {}
    )
    after = build_leaders(
        {_ADA: [_g(1, 3, "300.00"), _g(2, 3, "200.00")]}, {_ADA: [_g(1, 1)]}, {}
    )

    (shift,) = compare_periods(_EARLIER, _LATER, before, after).shifts

    assert shift.channel == DimensionShift(Leader(2, "item 2"), Leader(1, "item 1"))


# ---------- the report is deterministic ----------


def test_the_report_is_ordered_by_customer_id_whatever_order_the_input_had() -> None:
    before = _leaders({_CAL: (1, 10, 100), _ADA: (1, 10, 100), _BOB: (1, 10, 100)})
    after = _leaders({_BOB: (2, 10, 100), _CAL: (2, 10, 100), _ADA: (2, 10, 100)})

    report = compare_periods(_EARLIER, _LATER, before, after)

    assert [s.customer_id for s in report.shifts] == [_ADA, _BOB, _CAL]


# ---------- wiring: one snapshot supplies both periods ----------


def _fake_read(monkeypatch, earlier_rows, later_rows) -> Mock:
    read = Mock(return_value=(earlier_rows, later_rows))
    monkeypatch.setattr(shift_service, "list_totals_for_periods", read)
    return read


def test_the_single_read_is_asked_for_both_ordered_periods(monkeypatch) -> None:
    read = _fake_read(monkeypatch, ({}, {}, {}), ({}, {}, {}))

    connection = Mock()
    detect_shifts(connection, _LATER, _EARLIER)

    read.assert_called_once_with(connection, _T0, _T1, _T1, _T2)


def test_detect_shifts_issues_exactly_one_statement() -> None:
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = []

    detect_shifts(connection, _EARLIER, _LATER)

    cursor.execute.assert_called_once()


def test_detect_shifts_compares_earlier_against_later(monkeypatch) -> None:
    earlier = ({_ADA: [_g(1, 5)]}, {_ADA: [_g(10, 5)]}, {_ADA: [_c(100, 5)]})
    later = ({_ADA: [_g(2, 5)]}, {_ADA: [_g(10, 5)]}, {_ADA: [_c(100, 5)]})
    _fake_read(monkeypatch, earlier, later)

    report = detect_shifts(Mock(), _EARLIER, _LATER)

    (shift,) = report.shifts
    assert shift.channel.before.item_id == 1
    assert shift.channel.after.item_id == 2


def test_detect_shifts_gives_the_same_report_for_periods_passed_in_either_order(
    monkeypatch,
) -> None:
    earlier = ({_ADA: [_g(1, 5)]}, {_ADA: [_g(10, 5)]}, {})
    later = ({_ADA: [_g(2, 5)]}, {_ADA: [_g(10, 5)]}, {})
    _fake_read(monkeypatch, earlier, later)

    forward = detect_shifts(Mock(), _EARLIER, _LATER)
    backward = detect_shifts(Mock(), _LATER, _EARLIER)

    assert forward == backward


def test_detect_shifts_refuses_bad_periods_before_reading_anything(monkeypatch) -> None:
    read = _fake_read(monkeypatch, ({}, {}, {}), ({}, {}, {}))

    with pytest.raises(InvalidPeriods):
        detect_shifts(Mock(), _EARLIER, Period(_T1 - timedelta(days=1), _T2))

    read.assert_not_called()
