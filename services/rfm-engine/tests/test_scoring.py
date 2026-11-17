"""The scoring of one customer against a rule set's frozen boundaries (RN-46, RN-65).

No database: the rule set is the one the seed freezes (recency 20, 44, 68, 92 days;
frequency 15, 12, 9, 6; monetary 4510.00, 3210.00, 2190.00, 1405.00), with two
bands, so every edge is read off a known number.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app import profile
from app.profile import Band, Measures, RuleSet

AS_OF = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
RULES = RuleSet(
    rule_set_id=7,
    window_days=180,
    recency=(20, 44, 68, 92),
    frequency=(15, 12, 9, 6),
    monetary=(
        Decimal("4510.00"),
        Decimal("3210.00"),
        Decimal("2190.00"),
        Decimal("1405.00"),
    ),
    bands=(
        Band("CHAMPION", (5, 5), (5, 5), (4, 5)),
        Band("LOST", (1, 1), (1, 3), (1, 2)),
    ),
    fallback_label="LOST",
)


@pytest.mark.parametrize(
    ("r_days", "score"),
    [(0, 5), (20, 5), (21, 4), (44, 4), (45, 3), (68, 3), (69, 2), (92, 2), (93, 1)],
)
def test_recency_is_the_largest_value_that_still_earns_each_score(
    r_days: int, score: int
) -> None:
    assert profile.recency_score(r_days, RULES.recency) == score


@pytest.mark.parametrize(
    ("count", "score"),
    [
        (1, 1),
        (5, 1),
        (6, 2),
        (8, 2),
        (9, 3),
        (11, 3),
        (12, 4),
        (14, 4),
        (15, 5),
        (40, 5),
    ],
)
def test_frequency_is_the_smallest_count_that_earns_each_score(
    count: int, score: int
) -> None:
    assert profile.frequency_score(count, RULES.frequency) == score


@pytest.mark.parametrize(
    ("total", "score"),
    [
        ("0.01", 1),
        ("1404.99", 1),
        ("1405.00", 2),
        ("2189.99", 2),
        ("2190.00", 3),
        ("3210.00", 4),
        ("4509.99", 4),
        ("4510.00", 5),
    ],
)
def test_monetary_is_the_smallest_total_that_earns_each_score(
    total: str, score: int
) -> None:
    assert profile.monetary_score(Decimal(total), RULES.monetary) == score


def _measures(count: int, total: str, last_days: float, first_days: float) -> Measures:
    return Measures(
        count,
        Decimal(total),
        AS_OF - timedelta(days=first_days),
        AS_OF - timedelta(days=last_days),
    )


def test_a_customer_with_no_net_purchase_is_unassigned_with_zero_totals() -> None:
    result = profile.build(RULES, Measures(0, Decimal("0.00"), None, None), AS_OF)
    assert (result.state, result.f_count, result.m_total) == (
        "unassigned",
        0,
        Decimal("0.00"),
    )
    assert result.r_days is None and result.label_code is None
    assert result.r_score is None and result.via_fallback is None
    assert result.next_change_at is None and result.computed_at == AS_OF


def test_a_scored_customer_takes_the_label_of_the_band_that_holds_the_scores() -> None:
    result = profile.build(RULES, _measures(15, "4510.00", 3.5, 100), AS_OF)
    assert (result.state, result.r_days) == ("scored", 3)
    assert (result.r_score, result.f_score, result.m_score) == (5, 5, 5)
    assert (result.label_code, result.via_fallback) == ("CHAMPION", False)


def test_scores_no_band_holds_take_the_fallback_label_and_say_so() -> None:
    result = profile.build(RULES, _measures(9, "2190.00", 50, 100), AS_OF)
    assert (result.r_score, result.f_score, result.m_score) == (3, 3, 3)
    assert (result.label_code, result.via_fallback) == ("LOST", True)


def test_a_band_holding_the_scores_is_no_fallback_even_with_the_fallback_label() -> (
    None
):
    result = profile.build(RULES, _measures(1, "10.00", 120, 120), AS_OF)
    assert (result.r_score, result.f_score, result.m_score) == (1, 1, 1)
    assert (result.label_code, result.via_fallback) == ("LOST", False)


def test_the_lowest_band_wins_when_two_hold_the_scores() -> None:
    rules = RuleSet(
        RULES.rule_set_id,
        RULES.window_days,
        RULES.recency,
        RULES.frequency,
        RULES.monetary,
        (Band("A", (1, 5), (1, 5), (1, 5)), Band("B", (1, 5), (1, 5), (1, 5))),
        "B",
    )
    assert profile.build(rules, _measures(1, "10.00", 1, 1), AS_OF).label_code == "A"


def test_next_change_is_the_earlier_of_the_window_and_the_recency_crossing() -> None:
    # Last purchase 3 days ago, score 5 holds up to day 20: crosses 18 days from now.
    # The oldest purchase (100 days ago) leaves the window in 80 days.
    result = profile.build(RULES, _measures(4, "100.00", 3, 100), AS_OF)
    assert result.next_change_at == AS_OF - timedelta(days=3) + timedelta(days=21)
    # Last purchase 120 days ago scores recency 1: only the window can move it.
    old = profile.build(RULES, _measures(2, "100.00", 120, 170), AS_OF)
    assert old.next_change_at == AS_OF - timedelta(days=170) + timedelta(days=180)
    # The window can come first: a single purchase 175 days ago.
    single = profile.build(RULES, _measures(1, "10.00", 175, 175), AS_OF)
    assert single.next_change_at == AS_OF + timedelta(days=5)


def test_equal_values_ignore_the_clock_bound_columns() -> None:
    first = profile.build(RULES, _measures(4, "100.00", 3, 100), AS_OF)
    later = profile.build(
        RULES, _measures(4, "100.00", 3, 100), AS_OF + timedelta(hours=2)
    )
    assert first.computed_at != later.computed_at
    assert first.values() == later.values()
