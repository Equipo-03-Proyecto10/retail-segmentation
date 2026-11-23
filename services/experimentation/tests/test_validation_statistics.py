"""Repeated known-answer validation of experimentation's production statistics.

The fixed seeds make this a reproducible study, not a flaky probability test.
Assignment uses the same HMAC deal as the endpoint and analysis calls the same
proportion comparison as the result endpoint.
"""

from __future__ import annotations

import random
from uuid import UUID

import pytest

from app import stats, verify

REPLICATIONS = 1_000
ALPHA = 0.05


def test_a_a_false_positive_rate_is_within_its_binomial_tolerance() -> None:
    """At alpha .05, 33..69 rejections is the central 99% Binomial(1000, .05)
    acceptance interval (inclusive integer quantiles). The fixed study produces
    46, or 4.6%. Outcomes are fixed before each independently salted deal."""
    rng = random.Random(845_014)
    customers = [str(UUID(int=index + 1)) for index in range(1_200)]
    converted = {customer: rng.random() < 0.10 for customer in customers}
    false_positives = 0

    for experiment_id in range(1, REPLICATIONS + 1):
        dealt = verify.deal(rng.randbytes(32).hex(), experiment_id, [1, 2], customers)
        control = [customer for customer in customers if dealt[customer] == 1]
        treatment = [customer for customer in customers if dealt[customer] == 2]
        comparison = stats.compare_proportions(
            len(control),
            sum(converted[customer] for customer in control),
            len(treatment),
            sum(converted[customer] for customer in treatment),
            ALPHA,
        )
        false_positives += comparison.p_value < ALPHA

    assert false_positives == 46
    assert 33 <= false_positives <= 69


def test_injected_effect_is_recovered_and_has_nominal_coverage() -> None:
    """The central 99% Binomial(1000, .95) interval is 931..967 covered
    intervals. The fixed study covers 961 and recovers the injected five points."""
    rng = random.Random(845_015)
    per_arm = 800
    customers = [str(UUID(int=index + 1)) for index in range(2 * per_arm)]
    truth = 0.05
    covered = 0
    estimates = []
    first = None

    for experiment_id in range(1, REPLICATIONS + 1):
        dealt = verify.deal(rng.randbytes(32).hex(), experiment_id, [1, 2], customers)
        control = sum(
            rng.random() < 0.10 for customer in customers if dealt[customer] == 1
        )
        treatment = sum(
            rng.random() < 0.15 for customer in customers if dealt[customer] == 2
        )
        comparison = stats.compare_proportions(
            per_arm, control, per_arm, treatment, ALPHA
        )
        covered += comparison.lower <= truth <= comparison.upper
        estimates.append(comparison.difference)
        if first is None:
            first = comparison

    assert first is not None
    assert first.difference == pytest.approx(0.0375)
    assert first.lower <= truth <= first.upper
    assert covered == 961
    assert 931 <= covered <= 967
    assert sum(estimates) / REPLICATIONS == pytest.approx(truth, abs=0.002)
