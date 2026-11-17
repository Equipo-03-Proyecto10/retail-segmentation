"""Sample size before assignment (F25-09, ADR-0068 section 4): pure functions.

An experiment compares each treatment arm with the control (or the two offers
of an offer comparison) on one primary metric, measured per assigned customer
(intent to treat). `alpha` is the two-sided, family-wise level; with `m`
treatments each compared with the control, each comparison runs at the
Bonferroni level `alpha' = alpha / m`. `power` is `1 - beta`. With the standard
normal quantile `z`, and `k = z(1 - alpha'/2) + z(power)`:

CONVERSION (a difference of two proportions, the metric is a rate)::

    n = ceil( k^2 * (p1 (1 - p1) + p2 (1 - p2)) / (p2 - p1)^2 ),  p2 = p1 + MDE

REVENUE_PER_CUSTOMER (a difference of two means, the metric is money, with zero
for a customer who bought nothing; `s` is the standard deviation of that metric
in the baseline period)::

    n = ceil( 2 * k^2 * s^2 / MDE^2 )

`n` is the number of customers each arm needs. The formula for revenue treats
the metric as normal, which a zero-inflated, skewed amount is not: it is an
approximation, which ADR-0068's validation checks, and never a promise.

Nothing here reads a clock, a database or a random source.
"""

from __future__ import annotations

import math
from statistics import NormalDist

_NORMAL = NormalDist()

CONVERSION = "CONVERSION"
REVENUE_PER_CUSTOMER = "REVENUE_PER_CUSTOMER"

# What the smallest detectable effect is rounded to: four decimals of a rate or
# of money, the precision the contract's examples carry.
EFFECT_DECIMALS = 4


def z(probability: float) -> float:
    """The standard normal quantile `z(p)`: the value with a share `p` below it."""
    if not 0.0 < probability < 1.0:
        raise ValueError("a quantile needs a probability strictly between 0 and 1")
    return _NORMAL.inv_cdf(probability)


def comparison_alpha(alpha: float, comparisons: int) -> float:
    """Bonferroni: the level of each of `comparisons` comparisons within `alpha`."""
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be strictly between 0 and 1")
    if comparisons < 1:
        raise ValueError("a plan has at least one comparison")
    return alpha / comparisons


def _spread(alpha_each: float, power: float) -> float:
    """`k = z(1 - alpha'/2) + z(power)`, the distance the two means must be apart in standard errors."""
    if not 0.0 < alpha_each < 1.0:
        raise ValueError("alpha must be strictly between 0 and 1")
    if not 0.0 < power < 1.0:
        raise ValueError("power must be strictly between 0 and 1")
    return z(1.0 - alpha_each / 2.0) + z(power)


def required_per_arm_conversion(
    baseline: float, effect: float, alpha_each: float, power: float
) -> int:
    """Customers per arm to detect `effect` points over a conversion rate `baseline`.

    Refused with ValueError when the effect is not positive, or when the rate it
    leads to, `baseline + effect`, is not below 1: no rate can be that high.
    """
    if not 0.0 <= baseline < 1.0:
        raise ValueError("a baseline rate is in [0, 1)")
    if effect <= 0.0:
        raise ValueError("the minimum detectable effect must be positive")
    treated = baseline + effect
    if treated >= 1.0:
        raise ValueError("the baseline plus the effect must stay below 1")
    k = _spread(alpha_each, power)
    variance = baseline * (1.0 - baseline) + treated * (1.0 - treated)
    return max(1, math.ceil(k * k * variance / (effect * effect)))


def required_per_arm_revenue(
    std_dev: float, effect: float, alpha_each: float, power: float
) -> int:
    """Customers per arm to detect `effect` money per customer with spread `std_dev`."""
    if std_dev < 0.0:
        raise ValueError("a standard deviation is not negative")
    if effect <= 0.0:
        raise ValueError("the minimum detectable effect must be positive")
    k = _spread(alpha_each, power)
    return max(1, math.ceil(2.0 * k * k * std_dev * std_dev / (effect * effect)))


def required_per_arm(
    metric: str,
    *,
    baseline: float,
    std_dev: float | None,
    effect: float,
    alpha_each: float,
    power: float,
) -> int:
    if metric == CONVERSION:
        return required_per_arm_conversion(baseline, effect, alpha_each, power)
    if metric == REVENUE_PER_CUSTOMER:
        return required_per_arm_revenue(std_dev or 0.0, effect, alpha_each, power)
    raise ValueError(f"unknown metric {metric!r}")


def smallest_detectable_effect(
    metric: str,
    *,
    available_per_arm: int,
    baseline: float,
    std_dev: float | None,
    alpha_each: float,
    power: float,
) -> float | None:
    """The smallest effect `available_per_arm` customers can detect, or None if none.

    For revenue it is the formula solved for the effect, `k * s * sqrt(2 / n)`.
    For conversion the variance depends on the effect too, so the least effect
    whose required size fits is found by bisection (the required size falls as the
    effect grows). It is None when no customer is available, or when even an effect
    that takes the rate to 1 would need more customers than there are. The result is
    rounded up to four decimals, so that rounding never promises a detectable
    effect the frame cannot detect.
    """
    if available_per_arm < 1:
        return None
    k = _spread(alpha_each, power)
    if metric == REVENUE_PER_CUSTOMER:
        exact = k * (std_dev or 0.0) * math.sqrt(2.0 / available_per_arm)
        return _round_up(exact)
    if metric != CONVERSION:
        raise ValueError(f"unknown metric {metric!r}")
    if not 0.0 <= baseline < 1.0:
        raise ValueError("a baseline rate is in [0, 1)")

    def needed(effect: float) -> float:
        treated = baseline + effect
        variance = baseline * (1.0 - baseline) + treated * (1.0 - treated)
        return k * k * variance / (effect * effect)

    high = (1.0 - baseline) * (1.0 - 1e-12)
    if math.ceil(needed(high)) > available_per_arm:
        return None
    low = 1e-12
    for _ in range(200):
        middle = (low + high) / 2.0
        if math.ceil(needed(middle)) <= available_per_arm:
            high = middle
        else:
            low = middle
    return _round_up(high)


def _round_up(value: float) -> float:
    scale = 10**EFFECT_DECIMALS
    return math.ceil(value * scale - 1e-9) / scale
