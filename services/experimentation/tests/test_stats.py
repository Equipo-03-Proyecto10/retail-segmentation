"""The statistics of a result, and their own validation (F25-13, ADR-0068).

No database. The expected values of the small fixtures are worked out by hand in
the comments and were cross-checked once against SciPy's `ttest_ind(equal_var=False)`
and `chi2.sf`, which the service does not depend on. The validations run seeded
replications, so each is the same run every time.
"""

from __future__ import annotations

import math
import random

import pytest

from app import stats


def _summary(values: list[float]) -> tuple[int, float, float]:
    return (len(values), *stats.mean_and_variance(values))


def _buyers(
    rng: random.Random, n: int, share: float, mu: float = 3.0, sigma: float = 1.0
) -> list[float]:
    """Zero for most customers, a skewed amount for the rest."""
    return [
        rng.lognormvariate(mu, sigma) if rng.random() < share else 0.0 for _ in range(n)
    ]


class TestWelchByHand:
    """Six customers: control 0, 0, 10 and treatment 0, 10, 20.

    Means 10/3 and 10; sample variances 100/3 and 100. The variance of the
    difference is 100/9 + 100/3 = 400/9, so its standard error is 20/3 and the
    difference is 20/3, a t of 1. Satterthwaite: (400/9)^2 divided by
    ((100/9)^2 / 2 + (100/3)^2 / 2) = 3.2 degrees of freedom.
    """

    control = [0.0, 0.0, 10.0]
    treatment = [0.0, 10.0, 20.0]

    def test_the_interval_the_test_and_the_degrees(self) -> None:
        result = stats.welch(_summary(self.control), _summary(self.treatment), 0.05)
        assert result.difference == pytest.approx(20 / 3)
        assert result.degrees_of_freedom == pytest.approx(3.2)
        # t = 1 on 3.2 degrees of freedom; the 97.5% quantile there is 3.0731.
        assert result.p_value == pytest.approx(0.386809, abs=1e-6)
        assert result.lower == pytest.approx(-13.818860, abs=1e-5)
        assert result.upper == pytest.approx(27.152193, abs=1e-5)

    def test_zeros_count(self) -> None:
        mean, variance = stats.mean_and_variance(self.control)
        assert mean == pytest.approx(10 / 3)
        assert variance == pytest.approx(100 / 3)
        # Dropping the zeros would give the mean of the buyers: 10.
        assert mean != 10.0

    def test_equal_constant_arms_have_no_spread_and_no_difference(self) -> None:
        result = stats.welch((5, 2.0, 0.0), (5, 2.0, 0.0), 0.05)
        assert (result.lower, result.upper, result.p_value) == (0.0, 0.0, 1.0)

    def test_one_customer_cannot_be_compared(self) -> None:
        with pytest.raises(ValueError):
            stats.welch((1, 1.0, 0.0), (5, 2.0, 1.0), 0.05)


class TestProportions:
    def test_the_arithmetic_of_the_web_services_uplift(self) -> None:
        # 60 of 600 against 75 of 600: the contract's inconclusive example.
        result = stats.compare_proportions(600, 60, 600, 75, 0.05)
        assert result.difference == pytest.approx(0.025)
        assert result.lower == pytest.approx(-0.0107, abs=5e-5)
        assert result.upper == pytest.approx(0.0607, abs=5e-5)
        assert result.p_value == pytest.approx(0.170569, abs=5e-6)
        assert result.degrees_of_freedom is None

    def test_the_interval_is_unpooled_and_the_test_is_pooled(self) -> None:
        result = stats.compare_proportions(100, 10, 100, 30, 0.05)
        se = math.sqrt(0.1 * 0.9 / 100 + 0.3 * 0.7 / 100)
        assert result.upper - result.lower == pytest.approx(2 * 1.959963984540054 * se)
        pooled = math.sqrt(0.2 * 0.8 * (2 / 100))
        z = 0.2 / pooled
        assert result.p_value == pytest.approx(math.erfc(z / math.sqrt(2)))

    def test_nobody_converting_in_either_arm_is_no_difference(self) -> None:
        result = stats.compare_proportions(50, 0, 50, 0, 0.05)
        assert (result.difference, result.p_value) == (0.0, 1.0)

    def test_a_level_below_five_percent_widens_the_interval(self) -> None:
        wide = stats.compare_proportions(600, 60, 600, 75, 0.025)
        narrow = stats.compare_proportions(600, 60, 600, 75, 0.05)
        assert wide.upper - wide.lower > narrow.upper - narrow.lower


class TestDistributions:
    @pytest.mark.parametrize(
        ("probability", "degrees", "expected"),
        [(0.975, 10, 2.228139), (0.975, 1, 12.706205), (0.995, 30, 2.749996)],
    )
    def test_the_student_quantile(
        self, probability: float, degrees: float, expected: float
    ) -> None:
        assert stats.t_quantile(probability, degrees) == pytest.approx(
            expected, abs=2e-6
        )

    def test_a_large_sample_gives_the_normal_quantile(self) -> None:
        assert stats.t_quantile(0.975, 1e7) == pytest.approx(1.959964, abs=1e-5)

    @pytest.mark.parametrize(
        ("x", "degrees", "expected"),
        [(3.841459, 1, 0.05), (5.991465, 2, 0.05), (0.5, 1, 0.4795001)],
    )
    def test_the_chi_square_tail(self, x: float, degrees: int, expected: float) -> None:
        assert stats.chi_square_sf(x, degrees) == pytest.approx(expected, abs=1e-6)


class TestSampleRatio:
    def test_an_exact_deal_passes(self) -> None:
        assert stats.sample_ratio_p([10_000, 10_000]) == 1.0

    def test_five_percent_of_an_arm_dropped_at_analysis_fails(self) -> None:
        assert stats.sample_ratio_p([10_000, 9_500]) < stats.SAMPLE_RATIO_THRESHOLD

    def test_a_clean_fixture_is_not_marked(self) -> None:
        assert stats.sample_ratio_p([4_000, 4_001, 3_999]) > 0.9

    def test_the_contracts_invalid_example(self) -> None:
        # 10,000 against 9,400, the counts of examples/result-invalid.json: p = 1.6e-05.
        assert stats.sample_ratio_p([10_000, 9_400]) == pytest.approx(1.6e-5, rel=0.05)


class TestBalance:
    def test_the_standardized_difference(self) -> None:
        # Means 1 and 2, both with sample variance 1: a difference of one deviation.
        assert stats.standardized_mean_difference([0.0, 1.0, 2.0], [1.0, 2.0, 3.0]) == (
            pytest.approx(1.0)
        )

    def test_constant_measures_do_not_divide_by_zero(self) -> None:
        assert stats.standardized_mean_difference([1.0, 1.0], [1.0, 1.0]) == 0.0


class TestBootstrap:
    def test_it_is_seeded(self) -> None:
        values = [0.0] * 90 + [12.5] * 10
        assert stats.bootstrap_means(values, "a", 50) == stats.bootstrap_means(
            values, "a", 50
        )
        assert stats.bootstrap_means(values, "a", 50) != stats.bootstrap_means(
            values, "b", 50
        )

    def test_it_is_centered_on_the_mean_and_never_visits_the_zeros(self) -> None:
        values = [0.0] * 900 + [10.0] * 100
        means = stats.bootstrap_means(values, "centre", 2000)
        assert sum(means) / len(means) == pytest.approx(1.0, abs=0.02)
        assert all(m >= 0.0 for m in means)

    def test_nobody_buying_resamples_to_zero(self) -> None:
        assert stats.bootstrap_means([0.0] * 20, "zero", 5) == [0.0] * 5

    def test_it_agrees_with_welch_on_a_real_effect_and_on_none(self) -> None:
        rng = random.Random(2261)
        for share, expect_exclusion in ((0.16, True), (0.10, False)):
            control = _buyers(rng, 2000, 0.10)
            treatment = _buyers(rng, 2000, share)
            welch = stats.welch(_summary(control), _summary(treatment), 0.025)
            low, high = stats.bootstrap_interval(
                stats.bootstrap_means(control, "c", stats.RESAMPLES),
                stats.bootstrap_means(treatment, "t", stats.RESAMPLES),
                0.025,
            )
            assert stats.bootstrap_agrees(welch, low, high)
            assert (welch.lower > 0 or welch.upper < 0) is expect_exclusion

    def test_a_disagreement_is_reported_not_hidden(self) -> None:
        welch = stats.Difference(1.0, 0.1, 1.9, 0.01, 50.0)
        assert not stats.bootstrap_agrees(welch, -0.2, 2.0)
        assert not stats.bootstrap_agrees(welch, -2.0, -0.1)


class TestValidation:
    """The validations ADR-0068 asks for, run on seeded replications.

    Revenue is zero for nine customers in ten and lognormal (skewed) for the
    tenth; conversion is a rate of one in ten.
    """

    n = 1000

    def test_a_vs_a_rejects_about_five_percent_for_both_metrics(self) -> None:
        rng = random.Random(2262)
        replications = 1500
        revenue = conversion = 0
        for _ in range(replications):
            a, b = _buyers(rng, self.n, 0.1), _buyers(rng, self.n, 0.1)
            revenue += stats.welch(_summary(a), _summary(b), 0.05).p_value < 0.05
            x, y = rng.binomialvariate(self.n, 0.1), rng.binomialvariate(self.n, 0.1)
            conversion += (
                stats.compare_proportions(self.n, x, self.n, y, 0.05).p_value < 0.05
            )
        assert 0.03 <= revenue / replications <= 0.07
        assert 0.03 <= conversion / replications <= 0.07

    def test_an_injected_revenue_effect_is_recovered_within_its_interval(self) -> None:
        # Buyers go from 10% to 13% of customers, each spending exp(3.5) on average
        # (the mean of lognormal(3, 1)), so the true difference is 0.03 * exp(3.5).
        truth = 0.03 * math.exp(3.5)
        rng = random.Random(2263)
        replications = 2000
        covered = 0
        estimates = []
        for _ in range(replications):
            a, b = _buyers(rng, self.n, 0.10), _buyers(rng, self.n, 0.13)
            result = stats.welch(_summary(a), _summary(b), 0.05)
            covered += result.lower <= truth <= result.upper
            estimates.append(result.difference)
        assert 0.93 <= covered / replications <= 0.97
        assert sum(estimates) / replications == pytest.approx(truth, rel=0.05)

    def test_an_injected_conversion_effect_is_recovered_within_its_interval(
        self,
    ) -> None:
        rng = random.Random(2264)
        replications = 2000
        covered = 0
        for _ in range(replications):
            x, y = rng.binomialvariate(self.n, 0.10), rng.binomialvariate(self.n, 0.13)
            result = stats.compare_proportions(self.n, x, self.n, y, 0.05)
            covered += result.lower <= 0.03 <= result.upper
        assert 0.93 <= covered / replications <= 0.97
