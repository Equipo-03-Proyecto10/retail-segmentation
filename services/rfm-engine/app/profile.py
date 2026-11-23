"""One customer's R, F and M, their scores and their label (RN-65, ADR-0063).

The profile is a function of the customer's net purchases, the rule set and the
instant, and of nothing else. A sale event only says which customer to recompute,
so a replay, a duplicate or a reordering cannot change the result. The scoring is
the web's (`web/db/segments.py`, ported under ADR-0045) read off boundaries frozen
in the rule set instead of off the population's quintiles.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

ONE_DAY = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class Band:
    label_code: str
    r: tuple[int, int]
    f: tuple[int, int]
    m: tuple[int, int]


@dataclass(frozen=True, slots=True)
class RuleSet:
    rule_set_id: int
    window_days: int
    # The largest recency, in whole days, that still scores 5, 4, 3 and 2.
    recency: tuple[int, int, int, int]
    # The smallest net purchase count that scores 5, 4, 3 and 2.
    frequency: tuple[int, int, int, int]
    # The smallest net total that scores 5, 4, 3 and 2.
    monetary: tuple[Decimal, Decimal, Decimal, Decimal]
    bands: tuple[Band, ...]
    fallback_label: str
    # A draft can still change (F22-09, ADR-0064), so a profile is scored under a
    # rule set only once it is PUBLISHED and can never change.
    published: bool = True


@dataclass(frozen=True, slots=True)
class Measures:
    """What the customer's net purchases in the window add up to."""

    count: int
    total: Decimal
    first_at: datetime | None
    last_at: datetime | None


@dataclass(frozen=True, slots=True)
class Profile:
    state: str
    rule_set_id: int
    r_days: int | None
    f_count: int
    m_total: Decimal
    r_score: int | None
    f_score: int | None
    m_score: int | None
    label_code: str | None
    via_fallback: bool | None
    computed_at: datetime
    next_change_at: datetime | None

    def values(self) -> tuple[object, ...]:
        """What the contract compares to call two profiles equal: every measure,
        score and the label, and not the clock-bound columns."""
        return (
            self.state,
            self.rule_set_id,
            self.r_days,
            self.f_count,
            self.m_total,
            self.r_score,
            self.f_score,
            self.m_score,
            self.label_code,
            self.via_fallback,
        )


def recency_score(r_days: int, boundaries: tuple[int, int, int, int]) -> int:
    for position, largest in enumerate(boundaries):
        if r_days <= largest:
            return 5 - position
    return 1


def frequency_score(count: int, boundaries: tuple[int, int, int, int]) -> int:
    for position, smallest in enumerate(boundaries):
        if count >= smallest:
            return 5 - position
    return 1


def monetary_score(total: Decimal, boundaries: tuple[Decimal, ...]) -> int:
    for position, smallest in enumerate(boundaries):
        if total >= smallest:
            return 5 - position
    return 1


def label_for(rule_set: RuleSet, r: int, f: int, m: int) -> tuple[str, bool]:
    """The first band whose box holds the scores, else the fallback (RN-46)."""
    for band in rule_set.bands:
        if (
            band.r[0] <= r <= band.r[1]
            and band.f[0] <= f <= band.f[1]
            and band.m[0] <= m <= band.m[1]
        ):
            return band.label_code, False
    return rule_set.fallback_label, True


def build(rule_set: RuleSet, measures: Measures, as_of: datetime) -> Profile:
    """The profile of a customer with these measures at `as_of`."""
    if measures.count == 0 or measures.last_at is None or measures.first_at is None:
        # RN-21: no net purchase in the window. Recorded, not skipped.
        return Profile(
            "unassigned", rule_set.rule_set_id, None, 0, Decimal("0.00"),
            None, None, None, None, None, as_of, None,
        )  # fmt: skip
    r_days = int((as_of - measures.last_at) // ONE_DAY)
    r = recency_score(r_days, rule_set.recency)
    f = frequency_score(measures.count, rule_set.frequency)
    m = monetary_score(measures.total, rule_set.monetary)
    label, via_fallback = label_for(rule_set, r, f, m)
    return Profile(
        "scored", rule_set.rule_set_id, r_days, measures.count, measures.total,
        r, f, m, label, via_fallback, as_of,
        next_change(rule_set, measures, r),
    )  # fmt: skip


def next_change(rule_set: RuleSet, measures: Measures, r_score: int) -> datetime:
    """The first instant the profile changes with no new event (ADR-0063).

    Either the oldest purchase leaves the window, or the recency passes the largest
    value its score allows. Both are the customer's own dates plus a whole number of
    days, so no population enters.
    """
    assert measures.first_at is not None and measures.last_at is not None
    due = measures.first_at + timedelta(days=rule_set.window_days)
    if r_score > 1:
        largest = rule_set.recency[5 - r_score]
        due = min(due, measures.last_at + timedelta(days=largest + 1))
    return due
