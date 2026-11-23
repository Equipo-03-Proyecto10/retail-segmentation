"""The statistics of a result (F25-13, ADR-0068 section 8): pure functions.

Two estimators, one per metric, each over every assigned customer (intent to
treat). Nothing here reads a clock, a database or an unseeded random source.

CONVERSION: the difference of two proportions. The interval is the unpooled
Wald interval at ``1 - alpha``; the two-sided test is the pooled z-test. It is
the arithmetic of ``web/services/experiment_uplift.py::compare_proportions``,
ported without importing it (ADR-0045).

REVENUE_PER_CUSTOMER: the difference of two means, each customer's revenue being
the net sales of the window, zero for a customer who bought nothing. The
interval and the two-sided test are Welch's (unequal variances, Satterthwaite
degrees of freedom). A seeded percentile bootstrap of ten thousand resamples
runs beside it and is reported with whether it agrees (ADR-0068).

The sample ratio check is a chi-square goodness-of-fit test of the arm counts
against an equal allocation.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import NormalDist

_NORMAL = NormalDist()

RESAMPLES = 10_000
SAMPLE_RATIO_THRESHOLD = 0.001
BALANCE_THRESHOLD = 0.25


def normal_quantile(probability: float) -> float:
    return _NORMAL.inv_cdf(probability)


def _beta_continued_fraction(a: float, b: float, x: float) -> float:
    """Lentz's continued fraction for the incomplete beta function."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    d = tiny if abs(d) < tiny else d
    d = 1.0 / d
    h = d
    for m in range(1, 1000):
        m2 = 2 * m
        numerator = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + numerator * d
        d = tiny if abs(d) < tiny else d
        c = 1.0 + numerator / c
        c = tiny if abs(c) < tiny else c
        d = 1.0 / d
        h *= d * c
        numerator = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + numerator * d
        d = tiny if abs(d) < tiny else d
        c = 1.0 + numerator / c
        c = tiny if abs(c) < tiny else c
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-15:
            break
    return h


def regularized_beta(a: float, b: float, x: float) -> float:
    """The regularized incomplete beta function `I_x(a, b)`."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(
        math.lgamma(a + b)
        - math.lgamma(a)
        - math.lgamma(b)
        + a * math.log(x)
        + b * math.log1p(-x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _beta_continued_fraction(a, b, x) / a
    return 1.0 - front * _beta_continued_fraction(b, a, 1.0 - x) / b


def t_two_sided_p(t: float, degrees: float) -> float:
    """The two-sided p-value of Student's `t` with `degrees` of freedom."""
    if math.isinf(t):
        return 0.0
    return regularized_beta(degrees / 2.0, 0.5, degrees / (degrees + t * t))


def t_quantile(probability: float, degrees: float) -> float:
    """The Student quantile: the value with a share `probability` below it."""
    if not 0.5 < probability < 1.0:
        raise ValueError("an upper-tail probability is strictly between 0.5 and 1")
    two_sided = 2.0 * (1.0 - probability)
    low, high = 0.0, 1.0
    while t_two_sided_p(high, degrees) > two_sided:
        high *= 2.0
        if high > 1e12:
            break
    for _ in range(200):
        middle = (low + high) / 2.0
        if t_two_sided_p(middle, degrees) > two_sided:
            low = middle
        else:
            high = middle
        if high - low <= 1e-13 * high:
            break
    return (low + high) / 2.0


def chi_square_sf(x: float, degrees: int) -> float:
    """The upper tail of a chi-square distribution: `P(X >= x)`."""
    if x <= 0.0:
        return 1.0
    a, y = degrees / 2.0, x / 2.0
    if y < a + 1.0:
        term = total = 1.0 / a
        n = a
        for _ in range(10_000):
            n += 1.0
            term *= y / n
            total += term
            if abs(term) < abs(total) * 1e-16:
                break
        lower = total * math.exp(-y + a * math.log(y) - math.lgamma(a))
        return max(0.0, 1.0 - lower)
    tiny = 1e-300
    b = y + 1.0 - a
    c = 1.0 / tiny
    d = 1.0 / b
    h = d
    for i in range(1, 10_000):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        d = tiny if abs(d) < tiny else d
        c = b + an / c
        c = tiny if abs(c) < tiny else c
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-15:
            break
    return math.exp(-y + a * math.log(y) - math.lgamma(a)) * h


@dataclass(frozen=True, slots=True)
class Difference:
    """One comparison: the arm minus the other arm, its interval and its test."""

    difference: float
    lower: float
    upper: float
    p_value: float
    degrees_of_freedom: float | None


def compare_proportions(
    other_n: int,
    other_converted: int,
    arm_n: int,
    arm_converted: int,
    alpha: float,
) -> Difference:
    """`arm` minus `other`: the unpooled Wald interval, the pooled two-sided z-test."""
    if other_n < 1 or arm_n < 1:
        raise ValueError("each arm needs at least one customer")
    p_other = other_converted / other_n
    p_arm = arm_converted / arm_n
    difference = p_arm - p_other
    se = math.sqrt(p_other * (1 - p_other) / other_n + p_arm * (1 - p_arm) / arm_n)
    critical = normal_quantile(1.0 - alpha / 2.0)
    pooled = (other_converted + arm_converted) / (other_n + arm_n)
    pooled_se = math.sqrt(pooled * (1 - pooled) * (1 / other_n + 1 / arm_n))
    if pooled_se == 0.0:
        p_value = 1.0
    else:
        p_value = math.erfc(abs(difference / pooled_se) / math.sqrt(2.0))
    return Difference(
        difference,
        difference - critical * se,
        difference + critical * se,
        p_value,
        None,
    )


def mean_and_variance(values: Sequence[float]) -> tuple[float, float]:
    """The mean and the sample variance (`n - 1`); 0 variance for one value."""
    n = len(values)
    if n == 0:
        raise ValueError("an arm needs at least one customer")
    mean = math.fsum(values) / n
    if n == 1:
        return mean, 0.0
    return mean, math.fsum((v - mean) ** 2 for v in values) / (n - 1)


def welch(
    other: tuple[int, float, float],
    arm: tuple[int, float, float],
    alpha: float,
) -> Difference:
    """`arm` minus `other` from `(n, mean, variance)`: Welch's interval and test."""
    (n_other, mean_other, var_other), (n_arm, mean_arm, var_arm) = other, arm
    if n_other < 2 or n_arm < 2:
        raise ValueError("Welch's test needs at least two customers in each arm")
    difference = mean_arm - mean_other
    part_other, part_arm = var_other / n_other, var_arm / n_arm
    variance = part_other + part_arm
    if variance == 0.0:
        return Difference(
            difference, difference, difference, 1.0 if difference == 0.0 else 0.0, None
        )
    degrees = (
        variance
        * variance
        / (part_other * part_other / (n_other - 1) + part_arm * part_arm / (n_arm - 1))
    )
    se = math.sqrt(variance)
    critical = t_quantile(1.0 - alpha / 2.0, degrees)
    return Difference(
        difference,
        difference - critical * se,
        difference + critical * se,
        t_two_sided_p(difference / se, degrees),
        degrees,
    )


def bootstrap_means(values: Sequence[float], seed: str, resamples: int) -> list[float]:
    """The mean of each of `resamples` resamples of `values`, drawn with `seed`.

    A resample of a zero-inflated metric draws `n` values with replacement; the
    number of non-zero draws is binomial and each of them is uniform over the
    non-zero values, so the sum is drawn from that and the zeros are never visited.
    It is the same distribution as resampling all `n`, at the cost of the buyers.
    """
    n = len(values)
    buyers = [v for v in values if v != 0.0]
    rng = random.Random(seed)
    if not buyers:
        return [0.0] * resamples
    share = len(buyers) / n
    means = []
    for _ in range(resamples):
        drawn = rng.binomialvariate(n, share)
        means.append(math.fsum(rng.choices(buyers, k=drawn)) / n)
    return means


def bootstrap_interval(
    other_means: Sequence[float], arm_means: Sequence[float], alpha: float
) -> tuple[float, float]:
    """The percentile interval of the difference of two arms' resampled means."""
    differences = sorted(a - o for o, a in zip(other_means, arm_means, strict=True))
    count = len(differences)
    low = min(count - 1, max(0, math.floor(alpha / 2.0 * count)))
    high = min(count - 1, max(0, math.ceil((1.0 - alpha / 2.0) * count) - 1))
    return differences[low], differences[high]


def bootstrap_agrees(welch_interval: Difference, lower: float, upper: float) -> bool:
    """The bootstrap agrees on the sign and on whether the interval excludes zero."""
    welch_excludes = welch_interval.lower > 0.0 or welch_interval.upper < 0.0
    boot_excludes = lower > 0.0 or upper < 0.0
    boot_sign = (lower + upper) / 2.0
    same_sign = (welch_interval.difference >= 0.0) == (boot_sign >= 0.0)
    return welch_excludes == boot_excludes and same_sign


def sample_ratio_p(counts: Sequence[int]) -> float:
    """The chi-square goodness-of-fit p-value of the arm counts against equal shares."""
    total = sum(counts)
    if len(counts) < 2 or total == 0:
        return 1.0
    expected = total / len(counts)
    statistic = math.fsum((c - expected) ** 2 / expected for c in counts)
    return chi_square_sf(statistic, len(counts) - 1)


def standardized_mean_difference(other: Sequence[float], arm: Sequence[float]) -> float:
    """`arm` minus `other` over the pooled deviation; 0 when both are constant."""
    if not other or not arm:
        return 0.0
    mean_other, var_other = mean_and_variance(other)
    mean_arm, var_arm = mean_and_variance(arm)
    spread = math.sqrt((var_other + var_arm) / 2.0)
    if spread == 0.0:
        return 0.0
    return (mean_arm - mean_other) / spread
