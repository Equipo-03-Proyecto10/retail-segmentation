"""Intent-to-treat uplift and the two checks that earn it trust (F11-07, ADR-0019).

Uplift is the difference in conversion rate between a treatment arm and the
control, over *every assigned customer* in each arm, exposed or not. Comparing
only the exposed would measure how engaged they already were, not what the
campaign did. Assigned customers whose window is still open count as not
converted so far, and the result says so (`pending`).

The measurement is believed only because two executable validations hold
(tests/test_experiment_uplift.py):

* an A/A split, assigned only from data before a cut-off and judged on real
  later transactions, must show no significant difference at alpha 0.05;
* a fixed-seed injected uplift must be recovered, with a 95% interval that
  excludes zero.

The arithmetic is the standard two-proportion normal approximation, written out
rather than taken from a dependency (ADR-0021 makes the same choice for
K-means). The service owns the rules; SQL stays in web/db (ADR-0003).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import UTC, datetime

from psycopg import Connection

from web.db import experiment_conversions as conversions
from web.db import experiments
from web.services.experiments import (
    ExperimentNotFound,
    is_synthetic,
)

ALPHA = 0.05
# Two-sided normal critical value for ALPHA.
Z_CRITICAL = 1.959963984540054

CONVERSION_METRIC = "CONVERSION"

# The literal label every result derived from seeded or injected data carries.
SYNTHETIC_LABEL = "Synthetic"


class UpliftRefused(Exception):
    """Uplift cannot be measured for this experiment; the message says why."""


@dataclass(frozen=True)
class Comparison:
    """A treatment proportion against a control proportion."""

    control_n: int
    control_converted: int
    treatment_n: int
    treatment_converted: int
    control_rate: float
    treatment_rate: float
    uplift: float
    ci_low: float
    ci_high: float
    z: float
    p_value: float

    @property
    def significant(self) -> bool:
        return self.p_value < ALPHA

    @property
    def excludes_zero(self) -> bool:
        return self.ci_low > 0 or self.ci_high < 0


def compare_proportions(
    control_n: int, control_converted: int, treatment_n: int, treatment_converted: int
) -> Comparison:
    """Two-proportion comparison: the difference, its 95% interval (unpooled
    standard error) and a two-sided test (pooled, under no difference)."""
    if control_n <= 0 or treatment_n <= 0:
        raise ValueError("both arms need at least one customer")
    if not (0 <= control_converted <= control_n):
        raise ValueError("control conversions exceed its customers")
    if not (0 <= treatment_converted <= treatment_n):
        raise ValueError("treatment conversions exceed its customers")
    p_control = control_converted / control_n
    p_treatment = treatment_converted / treatment_n
    uplift = p_treatment - p_control
    se = math.sqrt(
        p_control * (1 - p_control) / control_n
        + p_treatment * (1 - p_treatment) / treatment_n
    )
    pooled = (control_converted + treatment_converted) / (control_n + treatment_n)
    pooled_se = math.sqrt(pooled * (1 - pooled) * (1 / control_n + 1 / treatment_n))
    z = uplift / pooled_se if pooled_se > 0 else 0.0
    return Comparison(
        control_n,
        control_converted,
        treatment_n,
        treatment_converted,
        p_control,
        p_treatment,
        uplift,
        uplift - Z_CRITICAL * se,
        uplift + Z_CRITICAL * se,
        z,
        math.erfc(abs(z) / math.sqrt(2)),
    )


@dataclass(frozen=True)
class ArmResult:
    group_id: int
    comparison: Comparison


@dataclass(frozen=True)
class MeasuredUplift:
    experiment: experiments.Experiment
    control: conversions.GroupConversion
    treatments: tuple[conversions.GroupConversion, ...]
    arms: tuple[ArmResult, ...]
    measured_at: datetime

    @property
    def label(self) -> str | None:
        return SYNTHETIC_LABEL if is_synthetic(self.experiment.data_origin) else None

    @property
    def pending(self) -> int:
        return self.control.pending + sum(g.pending for g in self.treatments)


def measure_uplift(
    connection: Connection, experiment_id: int, now: datetime | None = None
) -> MeasuredUplift:
    """Measure every treatment arm against the control over all assigned
    customers. Refused, never computed against 'everyone else', when the
    experiment has no control."""
    experiment = experiments.get_experiment(connection, experiment_id)
    if experiment is None:
        raise ExperimentNotFound(experiment_id)
    if experiment.control_groups < 1:
        raise UpliftRefused(
            f"Experiment {experiment_id} has no control group. Uplift is measured "
            "against the control, never against everyone else."
        )
    if experiment.target_metric != CONVERSION_METRIC:
        raise UpliftRefused(
            f"Experiment {experiment_id} is measured on {experiment.target_metric}. "
            "Only the conversion rate is measured so far."
        )
    if not experiment.assignments:
        raise UpliftRefused(
            f"Experiment {experiment_id} has no assignments, so there is nothing "
            "to compare."
        )
    moment = now or datetime.now(UTC)
    groups = conversions.list_group_conversion(connection, experiment_id, moment)
    unrecorded = sum(group.unrecorded for group in groups)
    if unrecorded:
        raise UpliftRefused(
            f"Experiment {experiment_id} has {unrecorded} assigned "
            f"{'customer' if unrecorded == 1 else 'customers'} with a qualifying "
            "sale that has not been recorded yet. Evaluate conversion before "
            "measuring uplift."
        )
    control = next((g for g in groups if g.kind == experiments.CONTROL), None)
    treatments = [g for g in groups if g.kind == experiments.TREATMENT]
    if control is None or control.assigned == 0:
        raise UpliftRefused(
            f"Experiment {experiment_id}'s control group has no assigned customers."
        )
    empty_treatments = [group.group_id for group in treatments if group.assigned == 0]
    if empty_treatments:
        group_word = "group" if len(empty_treatments) == 1 else "groups"
        group_ids = ", ".join(str(group_id) for group_id in empty_treatments)
        raise UpliftRefused(
            f"Experiment {experiment_id}'s treatment {group_word} "
            f"{group_ids} {'has' if len(empty_treatments) == 1 else 'have'} "
            "no assigned customers."
        )
    arms = tuple(
        ArmResult(
            g.group_id,
            compare_proportions(
                control.assigned, control.converted, g.assigned, g.converted
            ),
        )
        for g in treatments
    )
    if not arms:
        raise UpliftRefused(
            f"Experiment {experiment_id} has no assigned treatment customers."
        )
    return MeasuredUplift(experiment, control, tuple(treatments), arms, moment)


# ---------- validation 1: A/A ----------


def split_in_two(customers: list[str], seed: str) -> tuple[list[str], list[str]]:
    """A deterministic 50/50 split: sorted, shuffled by a generator seeded on
    `seed`, then dealt alternately."""
    order = sorted(customers)
    random.Random(seed).shuffle(order)
    return order[0::2], order[1::2]


def aa_validation(
    population: list[tuple[str, bool]], seed: str = "aa-validation"
) -> Comparison:
    """Compare two arms of the same population, neither treated.

    `population` is (customer_id, converted): customers known before a cut-off,
    and whether each bought after it. The split uses nothing but the customer
    ids, so any difference is noise, and the measurement must not call it
    significant. If it does, the measurement is broken, not the experiment
    successful.
    """
    outcome = dict(population)
    first, second = split_in_two(list(outcome), seed)
    return compare_proportions(
        len(first),
        sum(outcome[c] for c in first),
        len(second),
        sum(outcome[c] for c in second),
    )


def run_aa_validation(
    connection: Connection,
    cutoff: datetime,
    window_days: int,
    seed: str = "aa-validation",
) -> Comparison:
    """A/A over the real `transaction` rows: assign from before `cutoff`, judge
    conversion on sales in the `window_days` after it."""
    population = conversions.read_aa_population(connection, cutoff, window_days)
    if len(population) < 2:
        raise UpliftRefused("Fewer than two customers bought before the cut-off.")
    return aa_validation(population, seed)


# ---------- validation 2: injected uplift ----------

INJECTED_SEED = 226
INJECTED_PER_ARM = 10_000
INJECTED_CONTROL_RATE = 0.10
INJECTED_TREATMENT_RATE = 0.15


def injected_uplift_fixture(
    seed: int = INJECTED_SEED,
    per_arm: int = INJECTED_PER_ARM,
    control_rate: float = INJECTED_CONTROL_RATE,
    treatment_rate: float = INJECTED_TREATMENT_RATE,
) -> Comparison:
    """The record's fixture: `per_arm` assignments per arm, exactly
    `control_rate` and `treatment_rate` converting, who converting decided by a
    generator seeded on `seed`.

    The counts are exact rather than drawn: a Bernoulli sample of 10,000 per
    arm has a standard error near 0.47 points, so it could meet the 0.1-point
    tolerance only by choosing a lucky seed. Fixing the counts keeps the check
    about the measurement and nothing else. Anything derived from this is
    `Synthetic`.
    """
    rng = random.Random(seed)

    def arm(rate: float) -> list[bool]:
        outcomes = [True] * round(per_arm * rate) + [False] * (
            per_arm - round(per_arm * rate)
        )
        rng.shuffle(outcomes)
        return outcomes

    control, treatment = arm(control_rate), arm(treatment_rate)
    return compare_proportions(
        len(control), sum(control), len(treatment), sum(treatment)
    )
