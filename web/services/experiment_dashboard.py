"""The experiment dashboard's figures (AN-03, ADR-0019, ADR-0028).

Three views of one experiment, all built from counts the uplift page already
reads, so a chart can never disagree with the table beside it:

* the conversion rate per arm twice, over every assigned customer (intent to
  treat, the figure that decides) and over the customers exposed (secondary);
* the difference in that intent-to-treat rate against the control, with its 95%
  interval; or, when the experiment has no control, against a *reference arm*
  (RN-57);
* the share of an arm's assigned customers who had converted by each day since
  their own assignment, inside the conversion window.

Nothing is written. The service computes; SQL stays in web/db (ADR-0003).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from psycopg import Connection

from web.db import experiment_conversions as conversions
from web.db import experiment_dashboard as dashboard_db
from web.db import experiments
from web.db.experiment_conversions import ExposedConversion, GroupConversion
from web.services import experiment_uplift as uplift
from web.services.experiments import ExperimentNotFound, is_synthetic

# The most days drawn on the over-time chart. A window is a SMALLINT, so it can
# be tens of thousands of days; a chart of that many points answers nothing.
MAX_CHART_DAYS = 366

CONTROL_BASELINE = "control"
REFERENCE_BASELINE = "reference"


@dataclass(frozen=True)
class ArmRates:
    group_id: int
    kind: str
    name: str
    assigned: int
    exposed: int
    converted: int
    exposed_converted: int

    @property
    def rate_assigned(self) -> float | None:
        return self.converted / self.assigned if self.assigned else None

    @property
    def rate_exposed(self) -> float | None:
        return self.exposed_converted / self.exposed if self.exposed else None


@dataclass(frozen=True)
class Difference:
    """One treatment arm against the baseline arm."""

    group_id: int
    name: str
    comparison: uplift.Comparison


@dataclass(frozen=True)
class Baseline:
    kind: str  # CONTROL_BASELINE or REFERENCE_BASELINE
    group_id: int
    name: str


@dataclass(frozen=True)
class OverTime:
    window_days: int
    drawn_days: int
    series: tuple[tuple[ArmRates, tuple[float, ...]], ...]

    @property
    def truncated(self) -> bool:
        return self.drawn_days < self.window_days


@dataclass(frozen=True)
class ExperimentDashboard:
    experiment: experiments.Experiment
    arms: tuple[ArmRates, ...]
    baseline: Baseline | None
    differences: tuple[Difference, ...]
    refusal: str | None
    over_time: OverTime | None
    pending: int
    unrecorded: int
    measured_at: datetime

    @property
    def label(self) -> str | None:
        return (
            uplift.SYNTHETIC_LABEL
            if is_synthetic(self.experiment.data_origin)
            else None
        )


def _arm_rates(
    groups: list[GroupConversion], exposed: list[ExposedConversion]
) -> tuple[ArmRates, ...]:
    by_group = {e.group_id: e for e in exposed}
    return tuple(
        ArmRates(
            g.group_id,
            g.kind,
            g.name or g.kind.title(),
            g.assigned,
            by_group[g.group_id].exposed if g.group_id in by_group else 0,
            g.converted,
            by_group[g.group_id].converted if g.group_id in by_group else 0,
        )
        for g in groups
    )


def _differences(
    experiment: experiments.Experiment, groups: list[GroupConversion]
) -> tuple[Baseline | None, tuple[Difference, ...], str | None]:
    """The baseline and each treatment arm's difference from it, or why there is
    none. Whatever uplift refuses, this refuses with the same words; the only
    case it answers that uplift does not is an experiment with no control."""
    if experiment.target_metric != uplift.CONVERSION_METRIC:
        return (
            None,
            (),
            (
                f"Experiment {experiment.experiment_id} is measured on "
                f"{experiment.target_metric}. "
                "Only the conversion rate is measured so far."
            ),
        )
    if not experiment.assignments:
        return (
            None,
            (),
            (
                f"Experiment {experiment.experiment_id} has no assignments, so there "
                "is nothing to compare."
            ),
        )
    if experiment.control_groups >= 1:
        try:
            control, arms = uplift.compare_arms(experiment.experiment_id, groups)
        except uplift.UpliftRefused as refusal:
            return None, (), str(refusal)
        return (
            Baseline(CONTROL_BASELINE, control.group_id, control.name or "Control"),
            tuple(Difference(a.group_id, a.name, a.comparison) for a in arms),
            None,
        )
    return _against_reference_arm(experiment.experiment_id, groups)


def _against_reference_arm(
    experiment_id: int, groups: list[GroupConversion]
) -> tuple[Baseline | None, tuple[Difference, ...], str | None]:
    """No control (ADR-0028): the treatment arm defined first is the reference
    and each other arm is set against it. This is a difference between two
    treatments, never an uplift over no treatment, and the page says so."""
    unrecorded = sum(g.unrecorded for g in groups)
    if unrecorded:
        return (
            None,
            (),
            (
                f"Experiment {experiment_id} has {unrecorded} assigned "
                f"{'customer' if unrecorded == 1 else 'customers'} with a qualifying "
                "sale that has not been recorded yet. Evaluate conversion before "
                "measuring the difference."
            ),
        )
    arms = sorted(
        (g for g in groups if g.kind == experiments.TREATMENT),
        key=lambda g: g.group_id,
    )
    empty = [g.group_id for g in arms if g.assigned == 0]
    if len(arms) < 2 or empty:
        return (
            None,
            (),
            (
                f"Experiment {experiment_id} needs two treatment arms that each have "
                "assigned customers to compare them."
            ),
        )
    reference = arms[0]
    return (
        Baseline(REFERENCE_BASELINE, reference.group_id, reference.name or "Arm"),
        tuple(
            Difference(
                g.group_id,
                g.name or "Arm",
                uplift.compare_proportions(
                    reference.assigned, reference.converted, g.assigned, g.converted
                ),
            )
            for g in arms[1:]
        ),
        None,
    )


def cumulative_share(
    days: list[dashboard_db.ConversionDay], group_id: int, assigned: int, drawn: int
) -> tuple[float, ...]:
    """The share (0 to 1) of an arm's assigned customers who had first converted
    by the end of each of the first `drawn` days. It never falls, and on the
    last day of the window it equals the arm's intent-to-treat rate."""
    if assigned <= 0:
        return tuple(0.0 for _ in range(drawn))
    first = {d.day: d.customers for d in days if d.group_id == group_id}
    running = 0
    shares = []
    for day in range(drawn):
        running += first.get(day, 0)
        shares.append(running / assigned)
    return tuple(shares)


def _over_time(
    experiment: experiments.Experiment,
    arms: tuple[ArmRates, ...],
    days: list[dashboard_db.ConversionDay],
) -> OverTime:
    window = experiment.conversion_window_days
    drawn = min(window, MAX_CHART_DAYS)
    return OverTime(
        window,
        drawn,
        tuple(
            (arm, cumulative_share(days, arm.group_id, arm.assigned, drawn))
            for arm in arms
        ),
    )


def build_dashboard(
    connection: Connection, experiment_id: int, now: datetime | None = None
) -> ExperimentDashboard:
    """Everything the dashboard shows for one experiment."""
    experiment = experiments.get_experiment(connection, experiment_id)
    if experiment is None:
        raise ExperimentNotFound(experiment_id)
    moment = now or datetime.now(UTC)
    groups = conversions.list_group_conversion(connection, experiment_id, moment)
    exposed = conversions.list_exposed_conversion(connection, experiment_id)
    arms = _arm_rates(groups, exposed)
    baseline, differences, refusal = _differences(experiment, groups)
    over_time = None
    if experiment.assignments:
        over_time = _over_time(
            experiment,
            arms,
            dashboard_db.list_conversion_days(connection, experiment_id),
        )
    return ExperimentDashboard(
        experiment,
        arms,
        baseline,
        differences,
        refusal,
        over_time,
        sum(g.pending for g in groups),
        sum(g.unrecorded for g in groups),
        moment,
    )
