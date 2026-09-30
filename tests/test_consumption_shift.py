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
    MIN_PURCHASES_PER_PERIOD,
    CustomerAbsence,
    DimensionShift,
    InvalidPeriods,
    Leader,
    Period,
    ShiftStatus,
    Side,
    build_leaders,
    build_profile_shifts,
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


def test_a_tie_that_flips_between_periods_by_spend_is_not_a_shift() -> None:
    """Before: channels 1 and 2 tie on purchases and 2 has the higher spend.
    After: they tie again and 1 does. RN-35 still names a different dominant
    channel in each period, but RN-50 claims no shift between two ties (#341):
    the spend broke the tie, not the customer's behaviour."""
    before = build_leaders(
        {_ADA: [_g(1, 3, "100.00"), _g(2, 3, "200.00")]}, {_ADA: [_g(1, 6)]}, {}
    )
    after = build_leaders(
        {_ADA: [_g(1, 3, "300.00"), _g(2, 3, "200.00")]}, {_ADA: [_g(1, 6)]}, {}
    )

    report = compare_periods(_EARLIER, _LATER, before, after)

    assert before[_ADA].channel.item_id == 2 and after[_ADA].channel.item_id == 1
    assert report.shifts == ()
    assert report.unchanged == 1 and report.undecided == 1


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


# ---------- RN-50: enough purchases and a clear leader (#341) ----------


def _channels(*groups: tuple[int, int]) -> dict[str, list[GroupTotal]]:
    """One customer's channels as (channel id, purchases)."""
    return {_ADA: [_g(item, purchases) for item, purchases in groups]}


def _one_store(channels: dict[str, list[GroupTotal]]) -> dict[str, list[GroupTotal]]:
    return {_ADA: [_g(10, sum(g.purchases for g in channels[_ADA]))]}


def _compare(before_channels, after_channels):
    before = build_leaders(before_channels, _one_store(before_channels), {})
    after = build_leaders(after_channels, _one_store(after_channels), {})
    return compare_periods(_EARLIER, _LATER, before, after)


def test_the_minimum_is_three_purchases_per_period() -> None:
    assert MIN_PURCHASES_PER_PERIOD == 3


def test_three_purchases_on_each_side_with_a_clear_leader_is_a_shift() -> None:
    report = _compare(_channels((1, 2), (2, 1)), _channels((2, 3)))

    (shift,) = report.shifts
    assert shift.channel.before.item_id == 1 and shift.channel.after.item_id == 2
    assert report.undecided == 0


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (((1, 2),), ((2, 3),)),  # two purchases before
        (((1, 3),), ((2, 2),)),  # two purchases now
        (((1, 1),), ((2, 1),)),  # the 1-vs-1 of the issue
    ],
)
def test_fewer_than_three_purchases_in_a_period_claims_no_shift(before, after) -> None:
    report = _compare(_channels(*before), _channels(*after))

    assert report.shifts == ()
    assert report.unchanged == 1 and report.undecided == 1


def test_a_tie_on_purchases_in_either_period_claims_no_shift() -> None:
    tied_before = _compare(_channels((1, 2), (2, 2)), _channels((2, 4)))
    tied_after = _compare(_channels((1, 4)), _channels((1, 2), (2, 2)))

    assert tied_before.shifts == () and tied_before.undecided == 1
    assert tied_after.shifts == () and tied_after.undecided == 0  # 1 leads in both


def test_a_tie_still_names_the_profiles_dominant_value_for_display() -> None:
    leaders = build_leaders(
        {_ADA: [_g(1, 2, "100.00"), _g(2, 2, "250.00")]}, {_ADA: [_g(10, 4)]}, {}
    )
    channel = leaders[_ADA].channel

    assert channel.item_id == 2
    assert channel.tied and not channel.decisive


def test_the_same_clear_leader_on_both_sides_is_unchanged_not_undecided() -> None:
    report = _compare(_channels((1, 3)), _channels((1, 5)))

    assert report.shifts == () and report.undecided == 0


def test_a_category_shift_needs_three_purchases_with_product_lines() -> None:
    channels = {_ADA: [_g(1, 5)]}
    stores = {_ADA: [_g(10, 5)]}
    before = build_leaders(channels, stores, {_ADA: [_c(100, 2)]}, {_ADA: 2})
    after = build_leaders(channels, stores, {_ADA: [_c(200, 5)]}, {_ADA: 5})

    report = compare_periods(_EARLIER, _LATER, before, after)

    assert report.shifts == () and report.undecided == 1
    assert before[_ADA].category.period_purchases == 2


# ---------- shares are shares of purchases (#341) ----------


def test_a_channel_share_is_its_purchases_over_the_periods_purchases() -> None:
    leaders = build_leaders(
        _channels((1, 3), (2, 1)), _one_store(_channels((1, 3), (2, 1))), {}
    )
    channel = leaders[_ADA].channel

    assert (channel.purchases, channel.period_purchases) == (3, 4)
    assert channel.share == Decimal(75)


def test_a_share_is_a_whole_percent_rounded_half_up() -> None:
    assert Leader(1, "a", purchases=1, period_purchases=8).share == Decimal(13)
    assert Leader(1, "a", purchases=2, period_purchases=3).share == Decimal(67)


def test_category_shares_are_over_purchases_with_lines_and_can_exceed_100() -> None:
    leaders = build_leaders(
        {_ADA: [_g(1, 5)]},
        {_ADA: [_g(10, 5)]},
        {_ADA: [_c(100, 3), _c(200, 3), _c(300, 1)]},
        {_ADA: 4},
    )
    shares = [category.share for category in leaders[_ADA].categories]

    assert shares == [Decimal(75), Decimal(75), Decimal(25)]
    assert sum(shares) > 100


def test_the_profile_keeps_the_top_three_categories() -> None:
    leaders = build_leaders(
        {_ADA: [_g(1, 5)]},
        {_ADA: [_g(10, 5)]},
        {_ADA: [_c(i, 5 - i) for i in range(1, 5)]},
        {_ADA: 5},
    )
    assert [c.item_id for c in leaders[_ADA].categories] == [1, 2, 3]


def test_leaders_are_equal_by_id_whatever_their_counts() -> None:
    assert Leader(1, "a", purchases=1, period_purchases=2) == Leader(1, "a")


# ---------- the profile: the two halves of its window (#341) ----------


def _profile_read(monkeypatch, earlier_rows, later_rows) -> Mock:
    return _fake_read(monkeypatch, earlier_rows, later_rows)


def _rows(channels, stores, categories, lined):
    return ({_ADA: channels}, {_ADA: stores}, {_ADA: categories}, {_ADA: lined})


def test_the_profile_compares_the_two_halves_of_its_window(monkeypatch) -> None:
    read = _profile_read(monkeypatch, ({}, {}, {}, {}), ({}, {}, {}, {}))

    shifts = build_profile_shifts(MagicMock(), _ADA, until=_T2, window_days=180)

    assert shifts.half_days == 90
    assert shifts.later == Period(_T2 - timedelta(days=90), _T2)
    assert shifts.earlier == Period(_T2 - timedelta(days=180), _T2 - timedelta(days=90))
    assert read.call_args.kwargs == {"customer_id": _ADA}


def test_an_odd_window_is_halved_down_like_consecutive_periods(monkeypatch) -> None:
    _profile_read(monkeypatch, ({}, {}, {}, {}), ({}, {}, {}, {}))

    shifts = build_profile_shifts(MagicMock(), _ADA, until=_T2, window_days=7)

    assert shifts.half_days == 3
    assert (shifts.earlier, shifts.later) == consecutive_periods(_T2, 3)


def test_a_one_day_window_has_no_halves_and_reads_nothing(monkeypatch) -> None:
    read = _profile_read(monkeypatch, ({}, {}, {}, {}), ({}, {}, {}, {}))

    assert build_profile_shifts(MagicMock(), _ADA, until=_T2, window_days=1) is None
    read.assert_not_called()


def test_the_profile_flags_a_shift_with_shares_on_both_sides(monkeypatch) -> None:
    _profile_read(
        monkeypatch,
        _rows([_g(1, 4), _g(2, 2)], [_g(10, 6)], [_c(100, 4), _c(200, 3)], 6),
        _rows([_g(2, 3), _g(1, 1)], [_g(20, 4)], [_c(200, 3), _c(100, 1)], 4),
    )

    shifts = build_profile_shifts(MagicMock(), _ADA, until=_T2, window_days=180)

    assert (shifts.earlier_purchases, shifts.later_purchases) == (6, 4)
    assert shifts.channel.status is ShiftStatus.SHIFTED
    assert (shifts.channel.before.share, shifts.channel.after.share) == (
        Decimal(67),
        Decimal(75),
    )
    assert shifts.store.status is ShiftStatus.SHIFTED
    assert shifts.category.status is ShiftStatus.SHIFTED
    assert [c.item_id for c in shifts.categories_before] == [100, 200]
    assert [c.share for c in shifts.categories_after] == [Decimal(75), Decimal(25)]
    assert all(d.reason is None for d in shifts.dimensions)


def test_the_profile_says_why_no_shift_is_claimed(monkeypatch) -> None:
    _profile_read(
        monkeypatch,
        _rows([_g(1, 2)], [_g(10, 1), _g(11, 1)], [_c(100, 2)], 2),
        _rows([_g(2, 3)], [_g(20, 3)], [_c(100, 3)], 3),
    )

    shifts = build_profile_shifts(MagicMock(), _ADA, until=_T2, window_days=180)

    assert shifts.channel.status is ShiftStatus.UNDECIDED
    assert shifts.channel.reason == (
        "Not enough purchases: 2 in the earlier period, 3 needed."
    )
    assert shifts.store.status is ShiftStatus.UNDECIDED
    assert shifts.store.reason.startswith("Not enough purchases")
    assert shifts.category.status is ShiftStatus.UNCHANGED


def test_the_profile_names_a_tie(monkeypatch) -> None:
    _profile_read(
        monkeypatch,
        _rows([_g(1, 2), _g(2, 2)], [_g(10, 4)], [], 0),
        _rows([_g(2, 4)], [_g(10, 4)], [], 0),
    )

    shifts = build_profile_shifts(MagicMock(), _ADA, until=_T2, window_days=180)

    assert shifts.channel.reason == (
        "Tied in the earlier period: 2 purchases each for the top two."
    )
    assert shifts.category.status is ShiftStatus.NOT_COMPARED
    assert shifts.category.reason == (
        "No purchases with product lines in the earlier period."
    )


def test_a_customer_with_no_sales_in_a_half_is_not_compared(monkeypatch) -> None:
    _profile_read(
        monkeypatch,
        ({}, {}, {}, {}),
        _rows([_g(2, 3)], [_g(20, 3)], [_c(100, 3)], 3),
    )

    shifts = build_profile_shifts(MagicMock(), _ADA, until=_T2, window_days=180)

    assert shifts.earlier_purchases == 0
    assert shifts.channel.status is ShiftStatus.NOT_COMPARED
    assert shifts.channel.reason == "No purchases in the earlier period."
    assert shifts.categories_before == ()
