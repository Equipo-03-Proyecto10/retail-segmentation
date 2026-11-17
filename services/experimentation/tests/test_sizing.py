"""The sample size against values worked by hand (F25-09, ADR-0068 section 4).

Each expected value is derived in the test from the stated normal quantiles, with
the arithmetic written out. A required size is an integer (a ceiling), so it must
match exactly; a continuous value is compared within a stated tolerance of 0.5e-4,
half the last of the four decimals a smallest detectable effect is rounded to.
"""

from __future__ import annotations

import math

import pytest

from app import sizing

# Standard normal quantiles, from a table to six decimals.
Z_975 = 1.959964  # z(0.975): alpha = 0.05 two-sided
Z_80 = 0.841621  # z(0.80): power 0.80
Z_90 = 1.281552  # z(0.90): power 0.90
Z_99167 = 2.393980  # z(1 - 0.05 / 6) = z(0.991667): alpha' = 0.05 / 3 two-sided
TOLERANCE = 0.5e-4


def test_the_quantile_is_the_standard_normal_one() -> None:
    assert sizing.z(0.975) == pytest.approx(Z_975, abs=1e-6)
    assert sizing.z(0.80) == pytest.approx(Z_80, abs=1e-6)
    assert sizing.z(1 - 0.05 / 6) == pytest.approx(Z_99167, abs=1e-6)


def test_conversion_baseline_ten_percent_mde_two_points_is_3839_per_arm() -> None:
    """ADR-0068's worked case. k = 1.959964 + 0.841621 = 2.801585, k^2 = 7.848879.

    p1 = 0.10, p2 = 0.12: p1 (1 - p1) + p2 (1 - p2) = 0.09 + 0.1056 = 0.1956.
    n = 7.848879 * 0.1956 / 0.02^2 = 1.535241 / 0.0004 = 3838.10, so 3839.
    """
    k = Z_975 + Z_80
    assert k * k * 0.1956 / 0.0004 == pytest.approx(3838.10, abs=0.01)
    assert sizing.required_per_arm_conversion(0.10, 0.02, 0.05, 0.80) == 3839


def test_conversion_with_three_treatments_splits_alpha_by_bonferroni() -> None:
    """m = 3: alpha' = 0.05 / 3. k = 2.393980 + 0.841621 = 3.235601, k^2 = 10.469114.

    p1 = 0.20, p2 = 0.25: 0.2 * 0.8 + 0.25 * 0.75 = 0.16 + 0.1875 = 0.3475.
    n = 10.469114 * 0.3475 / 0.05^2 = 3.638 / 0.0025 = 1455.2, so 1456.
    """
    alpha_each = sizing.comparison_alpha(0.05, 3)
    assert alpha_each == pytest.approx(0.05 / 3)
    k = Z_99167 + Z_80
    assert k * k * 0.3475 / 0.0025 == pytest.approx(1455.21, abs=0.01)
    assert sizing.required_per_arm_conversion(0.20, 0.05, alpha_each, 0.80) == 1456


def test_revenue_per_customer_size_follows_the_two_means_formula() -> None:
    """s = 40 money units, MDE = 10, alpha 0.05, power 0.90.

    k = 1.959964 + 1.281552 = 3.241516, k^2 = 10.507423.
    n = 2 * 10.507423 * 40^2 / 10^2 = 2 * 10.507423 * 16 = 336.24, so 337.
    """
    k = Z_975 + Z_90
    assert 2 * k * k * 1600 / 100 == pytest.approx(336.24, abs=0.01)
    assert sizing.required_per_arm_revenue(40.0, 10.0, 0.05, 0.90) == 337


def test_the_smallest_effect_of_revenue_is_the_formula_solved_for_the_effect() -> None:
    """n = 100 per arm, s = 40, alpha 0.05, power 0.80.

    effect = k * s * sqrt(2 / n) = 2.801585 * 40 * sqrt(0.02) = 15.8482 (rounded up).
    """
    k = Z_975 + Z_80
    exact = k * 40 * math.sqrt(2 / 100)
    assert exact == pytest.approx(15.8482, abs=TOLERANCE)
    found = sizing.smallest_detectable_effect(
        sizing.REVENUE_PER_CUSTOMER,
        available_per_arm=100,
        baseline=0.0,
        std_dev=40.0,
        alpha_each=0.05,
        power=0.80,
    )
    assert found == pytest.approx(exact, abs=TOLERANCE)
    assert found >= exact - 1e-9


def test_the_smallest_effect_of_conversion_reproduces_the_contract_example() -> None:
    """600 customers per arm over a 10% baseline can detect 0.0537 (the contract's
    example): n(0.0537) = 7.848879 * (0.09 + 0.1537 * 0.8463) / 0.0537^2 = 598.8 <= 600.
    """
    found = sizing.smallest_detectable_effect(
        sizing.CONVERSION,
        available_per_arm=600,
        baseline=0.10,
        std_dev=None,
        alpha_each=0.05,
        power=0.80,
    )
    assert found == pytest.approx(0.0537, abs=TOLERANCE)


def test_the_smallest_effect_and_the_required_size_are_each_others_inverse() -> None:
    needed = sizing.required_per_arm_conversion(0.10, 0.02, 0.05, 0.80)
    found = sizing.smallest_detectable_effect(
        sizing.CONVERSION,
        available_per_arm=needed,
        baseline=0.10,
        std_dev=None,
        alpha_each=0.05,
        power=0.80,
    )
    assert found == pytest.approx(0.02, abs=TOLERANCE)


def test_no_customer_or_too_few_to_detect_anything_has_no_smallest_effect() -> None:
    kwargs = dict(
        baseline=0.5, std_dev=None, alpha_each=0.05, power=0.80
    )  # a rate cannot rise past 1
    assert (
        sizing.smallest_detectable_effect(
            sizing.CONVERSION, available_per_arm=0, **kwargs
        )
        is None
    )
    assert (
        sizing.smallest_detectable_effect(
            sizing.CONVERSION, available_per_arm=1, **kwargs
        )
        is None
    )


def test_a_bonferroni_level_divides_alpha_by_the_comparisons() -> None:
    assert sizing.comparison_alpha(0.05, 1) == 0.05
    assert sizing.comparison_alpha(0.05, 2) == pytest.approx(0.025)
    with pytest.raises(ValueError):
        sizing.comparison_alpha(0.05, 0)


@pytest.mark.parametrize(
    "call",
    [
        lambda: sizing.required_per_arm_conversion(0.10, 0.0, 0.05, 0.80),
        lambda: sizing.required_per_arm_conversion(0.10, -0.02, 0.05, 0.80),
        lambda: sizing.required_per_arm_conversion(0.95, 0.05, 0.05, 0.80),
        lambda: sizing.required_per_arm_conversion(-0.1, 0.02, 0.05, 0.80),
        lambda: sizing.required_per_arm_conversion(1.0, 0.02, 0.05, 0.80),
        lambda: sizing.required_per_arm_conversion(0.10, 0.02, 0.0, 0.80),
        lambda: sizing.required_per_arm_conversion(0.10, 0.02, 1.0, 0.80),
        lambda: sizing.required_per_arm_conversion(0.10, 0.02, 0.05, 0.0),
        lambda: sizing.required_per_arm_conversion(0.10, 0.02, 0.05, 1.0),
        lambda: sizing.required_per_arm_revenue(10.0, 0.0, 0.05, 0.80),
        lambda: sizing.required_per_arm_revenue(10.0, -1.0, 0.05, 0.80),
        lambda: sizing.required_per_arm_revenue(-1.0, 1.0, 0.05, 0.80),
        lambda: sizing.required_per_arm(
            "AVERAGE_TICKET",
            baseline=0,
            std_dev=0,
            effect=1,
            alpha_each=0.05,
            power=0.8,
        ),
        lambda: sizing.z(0.0),
        lambda: sizing.z(1.0),
    ],
)
def test_values_outside_the_formulas_domain_are_refused(call) -> None:
    with pytest.raises(ValueError):
        call()


def test_a_flat_metric_needs_one_customer_not_none() -> None:
    """No spread at all (nobody bought) is still at least one customer per arm."""
    assert sizing.required_per_arm_revenue(0.0, 5.0, 0.05, 0.80) == 1


def test_a_baseline_of_zero_is_a_valid_rate() -> None:
    """p1 = 0, MDE 0.05: n = 7.848879 * (0 + 0.05 * 0.95) / 0.05^2 = 149.1, so 150."""
    assert sizing.required_per_arm_conversion(0.0, 0.05, 0.05, 0.80) == 150
