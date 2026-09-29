"""Experiment setup (F11-03): the measurement rules are fixed before the first
assignment, so a result cannot be tuned once the outcome is visible (ADR-0019).

An experiment is created with exactly one control group and at least one
treatment group, a target metric, a conversion window and a data origin, all
in one transaction. After its first assignment the conversion window and the
target metric can no longer change. The data origin can never change: it is
the mark every later result carries (RN-26). A campaign cannot be activated
while an experiment attached to it lacks a control or a treatment group
(RN-24).

The service owns the transaction and the rules; SQL stays in
web/db/experiments.py (ADR-0003, ADR-0014).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from psycopg import Connection
from psycopg.errors import ForeignKeyViolation, IntegrityError

from web.db import experiments
from web.db.transactions import atomic

# What F11-07 measures. The schema stores free text; the vocabulary is this
# service's, and matches the values the seed already uses.
TARGET_METRICS: dict[str, str] = {
    "CONVERSION": "Conversion rate",
    "AVERAGE_TICKET": "Average ticket",
}

# experiment_data_origin_check's values (RN-26). SEEDED is ADR-0019's A/A
# validation and INJECTED its fixed-seed synthetic uplift; both are Synthetic.
DATA_ORIGINS: dict[str, str] = {
    "OBSERVED": "Observed",
    "SEEDED": "Seeded (A/A validation)",
    "INJECTED": "Injected (synthetic uplift)",
}
SYNTHETIC_ORIGINS = frozenset({"SEEDED", "INJECTED"})

# conversion_window_days is a SMALLINT: the schema requires it positive, and
# anything above this would be refused by the column type, not by a rule.
WINDOW_MAX_DAYS = 32767
# A bound for the form, not a rule of the design: the schema allows any number
# of treatment arms, and ten is far beyond what one campaign can split.
TREATMENT_GROUPS_MAX = 10

_NAME_MAX = 120


class ExperimentNotFound(Exception):
    """The id names no experiment."""


class ExperimentRefused(Exception):
    """A write refused with a message for one form field ('' = the whole form)."""

    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field


@dataclass(frozen=True)
class ExperimentInput:
    name: str
    campaign_id: int | None
    target_metric: str
    starts_on: date
    ends_on: date | None
    conversion_window_days: int
    # Only a new experiment chooses these two; an edit never changes them.
    data_origin: str | None = None
    treatment_groups: int | None = None


def is_synthetic(data_origin: str) -> bool:
    """Whether figures from an experiment of this origin carry `Synthetic`."""
    return data_origin in SYNTHETIC_ORIGINS


def _parse_date(raw: str, label: str) -> tuple[date | None, str | None]:
    try:
        return date.fromisoformat(raw), None
    except ValueError:
        return None, f"{label} must be a date (YYYY-MM-DD)."


def _parse_whole(raw: str, low: int, high: int) -> int | None:
    if not (raw.isascii() and raw.isdigit()):
        return None
    value = int(raw)
    return value if low <= value <= high else None


def validate_experiment(
    *,
    name: str,
    campaign_id: str,
    target_metric: str,
    starts_on: str,
    ends_on: str,
    conversion_window_days: str,
    data_origin: str | None = None,
    treatment_groups: str | None = None,
) -> tuple[ExperimentInput | None, dict[str, str]]:
    """Validate the setup form. `data_origin` and `treatment_groups` are given
    only when creating; an edit passes neither and can change neither."""
    errors: dict[str, str] = {}
    creating = data_origin is not None or treatment_groups is not None

    if not name or not name.strip():
        errors["name"] = "Name is required."
    elif len(name.strip()) > _NAME_MAX:
        errors["name"] = f"Name must be {_NAME_MAX} characters or fewer."

    campaign: int | None = None
    if campaign_id:
        campaign = _parse_whole(campaign_id, 1, 2**31 - 1)
        if campaign is None:
            errors["campaign_id"] = "Choose a campaign from the list."

    if target_metric not in TARGET_METRICS:
        errors["target_metric"] = "Choose the metric this experiment is measured on."

    start: date | None = None
    if not starts_on:
        errors["starts_on"] = "Start date is required."
    else:
        start, error = _parse_date(starts_on, "Start date")
        if error:
            errors["starts_on"] = error
    end: date | None = None
    if ends_on:
        end, error = _parse_date(ends_on, "End date")
        if error:
            errors["ends_on"] = error
        elif start and end and end < start:
            errors["ends_on"] = "End date cannot be before the start date."

    window = _parse_whole(conversion_window_days, 1, WINDOW_MAX_DAYS)
    if window is None:
        errors["conversion_window_days"] = (
            f"The conversion window is a whole number of days, 1 to {WINDOW_MAX_DAYS}."
        )

    treatments: int | None = None
    if creating:
        if data_origin not in DATA_ORIGINS:
            errors["data_origin"] = "Choose where this experiment's data comes from."
        treatments = _parse_whole(treatment_groups or "", 1, TREATMENT_GROUPS_MAX)
        if treatments is None:
            errors["treatment_groups"] = (
                "An experiment needs at least one treatment group "
                f"(at most {TREATMENT_GROUPS_MAX})."
            )

    if errors:
        return None, errors
    return (
        ExperimentInput(
            name=name.strip(),
            campaign_id=campaign,
            target_metric=target_metric,
            starts_on=start,
            ends_on=end,
            conversion_window_days=window,
            data_origin=data_origin if creating else None,
            treatment_groups=treatments,
        ),
        {},
    )


def _refusal(error: IntegrityError) -> ExperimentRefused:
    """Turn a database refusal into a message the form can show."""
    if isinstance(error, ForeignKeyViolation):
        return ExperimentRefused("campaign_id", "That campaign does not exist.")
    return ExperimentRefused(
        "", "That experiment was refused by a database constraint."
    )


@atomic
def _create(connection: Connection, data: ExperimentInput) -> int:
    experiment_id = experiments.create_experiment(
        connection,
        name=data.name,
        campaign_id=data.campaign_id,
        target_metric=data.target_metric,
        starts_on=data.starts_on,
        ends_on=data.ends_on,
        conversion_window_days=data.conversion_window_days,
        data_origin=data.data_origin,
        treatment_groups=data.treatment_groups,
    )
    if experiment_id is None:
        raise ExperimentRefused(
            "", "An experiment with this name, campaign and start date already exists."
        )
    return experiment_id


def create_experiment(connection: Connection, data: ExperimentInput) -> int:
    """Create an experiment with one control and its treatment groups."""
    if data.data_origin is None or data.treatment_groups is None:
        raise ValueError("a new experiment needs a data origin and treatment groups")
    try:
        return _create(connection, data)
    except IntegrityError as error:
        raise _refusal(error) from error


def fixed_after_assignment(experiment_id: int, count: int, what: str) -> str:
    """The refusal shown for a measurement rule an assignment has locked."""
    noun = "assignment" if count == 1 else "assignments"
    return (
        f"The {what} is fixed: experiment {experiment_id} already has {count} "
        f"{noun} (ADR-0019)."
    )


@atomic
def _update(connection: Connection, experiment_id: int, data: ExperimentInput) -> None:
    if not experiments.lock_experiment(connection, experiment_id):
        raise ExperimentNotFound(experiment_id)
    current = experiments.get_experiment(connection, experiment_id)
    if current is None:
        raise ExperimentNotFound(experiment_id)
    if current.assignments:
        if data.conversion_window_days != current.conversion_window_days:
            raise ExperimentRefused(
                "conversion_window_days",
                fixed_after_assignment(
                    experiment_id, current.assignments, "conversion window"
                ),
            )
        if data.target_metric != current.target_metric:
            raise ExperimentRefused(
                "target_metric",
                fixed_after_assignment(
                    experiment_id, current.assignments, "target metric"
                ),
            )
    experiments.update_experiment(
        connection,
        experiment_id,
        name=data.name,
        campaign_id=data.campaign_id,
        target_metric=data.target_metric,
        starts_on=data.starts_on,
        ends_on=data.ends_on,
        conversion_window_days=data.conversion_window_days,
    )


def update_experiment(
    connection: Connection, experiment_id: int, data: ExperimentInput
) -> None:
    """Edit an experiment, refusing a change to a rule its assignments fixed."""
    try:
        _update(connection, experiment_id, data)
    except IntegrityError as error:
        raise _refusal(error) from error


def activation_refusal(connection: Connection, campaign_id: int) -> str | None:
    """Why the campaign cannot be activated yet, or None if it can (RN-24).

    Every experiment attached to it needs exactly one control group and at
    least one treatment group before anyone can be assigned. The unique index
    already stops a second control; this is the half a constraint cannot say.
    """
    for counts in experiments.list_group_counts_for_campaign(connection, campaign_id):
        if counts.control_groups < 1:
            return (
                f"Campaign {campaign_id} cannot be activated: experiment "
                f"{counts.experiment_id} ({counts.name}) has no control group."
            )
        if counts.treatment_groups < 1:
            return (
                f"Campaign {campaign_id} cannot be activated: experiment "
                f"{counts.experiment_id} ({counts.name}) has no treatment group."
            )
    return None
