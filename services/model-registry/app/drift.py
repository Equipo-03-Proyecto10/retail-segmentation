"""Data-drift statistics of a published version against its own reference.

Everything here is a pure function of its arguments: the same counts and the
same reference give the same numbers, in the same order, every time (F23-09
criterion 4). Thresholds, bins and the persistence rule are ADR-0065's; none is
chosen here. The wording rule is ADR-0065's too: this module says "distribution
change" and never the name of the claim only an outcome test may make, and a
test scans every sentence it can produce.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

WINDOW_DAYS = 30
MIN_CUSTOMERS = 200
FLOOR = 0.0005
PSI_WATCH, PSI_ALARM = 0.10, 0.25
P_WATCH, P_ALARM = 0.05, 0.01
FEATURES = ("R", "F", "M")
NO_VALUE = "NO_VALUE"
SEGMENT_SHARES = "SEGMENT_SHARES"
PENDING_STATES = ("ALARM_PENDING", "ALARM")


@dataclass(frozen=True, slots=True)
class Bin:
    label: str
    lower: float | None
    upper: float | None
    reference_share: float
    current_share: float
    count: int

    @property
    def contribution(self) -> float:
        reference = max(self.reference_share, FLOOR)
        current = max(self.current_share, FLOOR)
        return (current - reference) * math.log(current / reference)


@dataclass(frozen=True, slots=True)
class Statistic:
    statistic: str
    value: float
    p_value: float | None
    degrees_of_freedom: int | None
    level: str


@dataclass(frozen=True, slots=True)
class Variable:
    variable: str
    state: str
    statistics: tuple[Statistic, ...]
    bins: tuple[Bin, ...]


def psi(bins: list[Bin]) -> float:
    """Population stability index: the sum of the bins' contributions."""
    return sum(item.contribution for item in bins)


def gammaincc(a: float, x: float) -> float:
    """The regularized upper incomplete gamma function Q(a, x)."""
    if x <= 0:
        return 1.0
    log_prefix = -x + a * math.log(x) - math.lgamma(a)
    if x < a + 1:
        term = total = 1.0 / a
        n = a
        for _ in range(1000):
            n += 1
            term *= x / n
            total += term
            if abs(term) < abs(total) * 1e-16:
                break
        return max(0.0, 1.0 - total * math.exp(log_prefix))
    tiny = 1e-300
    b = x + 1 - a
    c = 1 / tiny
    d = 1 / b
    h = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        d = tiny if abs(d) < tiny else d
        c = b + an / c
        c = tiny if abs(c) < tiny else c
        d = 1 / d
        delta = d * c
        h *= delta
        if abs(delta - 1) < 1e-16:
            break
    return min(1.0, h * math.exp(log_prefix))


def chi_square(bins: list[Bin], n: int) -> tuple[float, float, int]:
    """Goodness of fit of the observed counts to the reference shares:
    the statistic, its p-value and its degrees of freedom."""
    value = 0.0
    for item in bins:
        expected = max(item.reference_share, FLOOR) * n
        value += (item.count - expected) ** 2 / expected
    dof = len(bins) - 1
    return value, gammaincc(dof / 2, value / 2), dof


def psi_level(value: float) -> str:
    if value >= PSI_ALARM:
        return "ALARM"
    return "WATCH" if value >= PSI_WATCH else "NORMAL"


def p_level(p_value: float) -> str:
    if p_value < P_ALARM:
        return "ALARM"
    return "WATCH" if p_value < P_WATCH else "NORMAL"


def bin_index(edges: list[float], value: float) -> int:
    """The reference's own binning: a value goes to the first bin whose upper
    edge it is under, and past the last edge to the last bin."""
    last = len(edges) - 2
    for index in range(last):
        if value < edges[index + 1]:
            return index
    return last


def feature_bins(reference: dict[str, Any], values: list[float | None]) -> list[Bin]:
    """The reference's bins of one feature plus one bin for no value."""
    edges = [float(edge) for edge in reference["bin_edges"]]
    shares = [float(share) for share in reference["proportions"]]
    counts = [0] * len(shares)
    missing = 0
    for value in values:
        if value is None:
            missing += 1
        else:
            counts[bin_index(edges, float(value))] += 1
    n = len(values)
    bins = [
        Bin(
            f"B{index + 1}",
            edges[index],
            edges[index + 1],
            shares[index],
            counts[index] / n,
            counts[index],
        )
        for index in range(len(shares))
    ]
    bins.append(Bin(NO_VALUE, None, None, 0.0, missing / n, missing))
    return bins


def segment_bins(labels: list[dict[str, Any]], assigned: list[str]) -> list[Bin]:
    """One bin per segment of the reference, in the reference's order."""
    n = len(assigned)
    return [
        Bin(
            label["label_code"],
            None,
            None,
            float(label["share"]),
            assigned.count(label["label_code"]) / n,
            assigned.count(label["label_code"]),
        )
        for label in labels
    ]


def _state(alarm: bool, levels: list[str], previous: str | None) -> str:
    if alarm:
        return "ALARM" if previous in PENDING_STATES else "ALARM_PENDING"
    return "WATCH" if any(level != "NORMAL" for level in levels) else "NORMAL"


def judge(
    variable: str,
    bins: list[Bin],
    n: int,
    previous: str | None,
) -> Variable:
    """The statistics of one variable and the state they put it in, given the
    state it had in the report before (the persistence rule)."""
    value = round(psi(bins), 12)
    first = Statistic("PSI", value, None, None, psi_level(value))
    if variable != SEGMENT_SHARES:
        return Variable(
            variable,
            _state(first.level == "ALARM", [first.level], previous),
            (first,),
            tuple(bins),
        )
    stat, p_value, dof = chi_square(bins, n)
    second = Statistic("CHI_SQUARE", stat, p_value, dof, p_level(p_value))
    alarm = first.level == "ALARM" and second.level == "ALARM"
    return Variable(
        variable,
        _state(alarm, [first.level, second.level], previous),
        (first, second),
        tuple(bins),
    )


def raises_alert(state: str, previous: str | None) -> bool:
    """An alert is raised when the alarm holds for the second window running, and
    not again while it goes on: ALARM after ALARM is the same finding."""
    return state == "ALARM" and previous == "ALARM_PENDING"


def review_code(variable: str) -> str:
    if variable == SEGMENT_SHARES:
        return "REVIEW_SEGMENT_MIX"
    return "REVIEW_FEATURE_DISTRIBUTION"


def _percent(share: float) -> str:
    return f"{share * 100:.1f}%"


def review_text(variable: str, bins: list[Bin]) -> str:
    """English text naming the variable and the change, for a client to show."""
    moved = sorted(bins, key=lambda item: (-item.contribution, item.label))[:3]
    parts = []
    for item in moved:
        verb = "rose" if item.current_share > item.reference_share else "fell"
        name = item.label
        if item.lower is not None and item.upper is not None:
            name = f"{item.label} ({item.lower:g} to {item.upper:g})"
        parts.append(
            f"{name} {verb} from {_percent(item.reference_share)} to"
            f" {_percent(item.current_share)}"
        )
    subject = (
        "The segment mix" if variable == SEGMENT_SHARES else f"The {variable} values"
    )
    return (
        f"{subject} moved against the reference of this version: "
        + "; ".join(parts)
        + ". Check whether its frozen scoring still fits the current customers; if it"
        " does not, register a draft version fitted on current data and compare it"
        " with this one. A distribution change does not show that the segments"
        " stopped predicting anything."
    )[:500]
