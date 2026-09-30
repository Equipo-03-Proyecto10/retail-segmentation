"""Experiment setup (F11-03) and group assignment (F11-04): the measurement
rules are fixed before the first assignment, so a result cannot be tuned once
the outcome is visible (ADR-0019).

Assignment writes the campaign's current target population into the arms
once, in one transaction, before anything is delivered; it is never rewritten
afterwards (RN-42, ADR-0026).

An experiment is created with at least one treatment group and zero or one
control group, a target metric, a conversion window, a data origin, and
explicit arm definitions. A no-control experiment has exactly two treatments.
After its first assignment the conversion window, target metric and arm
definitions can no longer change. The data origin can never change: it is the
mark every later result carries (RN-26).

The service owns the transaction and the rules; SQL stays in
web/db/experiments.py (ADR-0003, ADR-0014).
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from psycopg import Connection
from psycopg.errors import ForeignKeyViolation, IntegrityError, UniqueViolation

from web.db import experiments
from web.db.campaigns import get_campaign
from web.db.transactions import atomic
from web.parsing import iso_date, whole_number

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

_NO_CONTROL_VALUES = frozenset({"NONE", "NO", "NO_CONTROL", "NO-CONTROL", "FALSE", "0"})

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
    has_control: bool = True
    group_definitions: tuple[tuple[str, str], ...] = ()


def is_synthetic(data_origin: str) -> bool:
    """Whether figures from an experiment of this origin carry `Synthetic`."""
    return data_origin in SYNTHETIC_ORIGINS


def _parse_date(raw: str, label: str) -> tuple[date | None, str | None]:
    value = iso_date(raw)
    if value is None:
        return None, f"{label} must be a date (YYYY-MM-DD)."
    return value, None


def _parse_whole(raw: str, low: int, high: int) -> int | None:
    value = whole_number(raw, high)
    return value if value is not None and value >= low else None


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
    control_group: str | None = None,
    control_mode: str | None = None,
    group_names: dict[str, str] | None = None,
    group_descriptions: dict[str, str] | None = None,
    **arm_fields: str,
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
    has_control = True
    definitions: list[tuple[str, str]] = []
    if creating:
        selected_control = control_group if control_group is not None else control_mode
        selected_control = (selected_control or "CONTROL").strip().upper()
        if selected_control != "CONTROL" and selected_control not in _NO_CONTROL_VALUES:
            errors["control_group"] = "Choose whether this experiment has a control."
        if data_origin not in DATA_ORIGINS:
            errors["data_origin"] = "Choose where this experiment's data comes from."
        no_control = selected_control in _NO_CONTROL_VALUES
        has_control = not no_control
        minimum = 2 if no_control else 1
        treatments = _parse_whole(treatment_groups or "", minimum, TREATMENT_GROUPS_MAX)
        if no_control and treatments is not None and treatments != 2:
            treatments = None
        if treatments is None:
            if no_control:
                errors["treatment_groups"] = (
                    "A no-control experiment needs exactly two treatment groups."
                )
            else:
                errors["treatment_groups"] = (
                    "An experiment needs at least one treatment group "
                    f"(at most {TREATMENT_GROUPS_MAX})."
                )
        if treatments is not None:
            keys = (["control"] if has_control else []) + [
                f"treatment_{index}" for index in range(1, treatments + 1)
            ]
            names = group_names or {
                key: arm_fields.get(f"{key}_name", "") for key in keys
            }
            descriptions = group_descriptions or {
                key: arm_fields.get(f"{key}_description", "") for key in keys
            }
            for key in keys:
                arm_name = names.get(key, "")
                description = descriptions.get(key, "")
                if not arm_name.strip():
                    errors[f"{key}_name"] = "Arm name is required."
                elif len(arm_name.strip()) > _NAME_MAX:
                    errors[f"{key}_name"] = (
                        f"Arm name must be {_NAME_MAX} characters or fewer."
                    )
                if not description.strip():
                    errors[f"{key}_description"] = "Treatment description is required."
                if len(description.strip()) > 500:
                    errors[f"{key}_description"] = (
                        "Treatment description must be 500 characters or fewer."
                    )
                definitions.append((arm_name.strip(), description.strip()))

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
            has_control=has_control,
            group_definitions=tuple(definitions),
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
        control_group=data.has_control,
        group_definitions=list(data.group_definitions),
    )
    if experiment_id is None:
        raise ExperimentRefused(
            "", "An experiment with this name, campaign and start date already exists."
        )
    return experiment_id


def create_experiment(connection: Connection, data: ExperimentInput) -> int:
    """Create an experiment with explicit, user-defined arm metadata."""
    if (
        data.data_origin is None
        or data.treatment_groups is None
        or not data.group_definitions
    ):
        raise ValueError(
            "a new experiment needs a data origin, treatment groups, and "
            "arm definitions"
        )
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

    Every experiment attached to it needs at least one treatment group before
    anyone can be assigned. A control is optional for the two-treatment-arm
    design (ADR-0028); the unique index still stops a second control.
    """
    for counts in experiments.list_group_counts_for_campaign(connection, campaign_id):
        if counts.control_groups == 0 and counts.treatment_groups != 2:
            return (
                f"Campaign {campaign_id} cannot be activated: experiment "
                f"{counts.experiment_id} ({counts.name}) has no control group "
                "and must have exactly two treatment groups."
            )
        if counts.treatment_groups < 1:
            return (
                f"Campaign {campaign_id} cannot be activated: experiment "
                f"{counts.experiment_id} ({counts.name}) has no treatment group."
            )
    return None


# ---------- assignment (F11-04) ----------

# web.services.campaigns.ACTIVE; not imported, because that module imports this
# one for activation_refusal.
_CAMPAIGN_ACTIVE = "ACTIVE"


class AssignmentRefused(Exception):
    """The experiment cannot be assigned now; the message says why."""


@dataclass(frozen=True)
class Arm:
    group_id: int
    kind: str
    customers: tuple[str, ...]
    name: str = ""
    treatment_description: str = ""


@dataclass(frozen=True)
class AssignmentPlan:
    experiment: experiments.Experiment
    label_code: str
    arms: tuple[Arm, ...]

    @property
    def population(self) -> int:
        return sum(len(arm.customers) for arm in self.arms)


def split(
    experiment_id: int, customers: list[str], groups: list[tuple[int, str]]
) -> tuple[Arm, ...]:
    """Deal the population into the groups, control first.

    The customers are shuffled by a generator seeded on the experiment, so the
    split is random with respect to anything about the customers yet
    reproducible when audited, and then dealt round-robin, so no two arms
    differ in size by more than one.
    """
    order = sorted(customers)
    random.Random(f"experiment-{experiment_id}").shuffle(order)
    dealt: dict[int, list[str]] = {group_id: [] for group_id, _ in groups}
    for position, customer in enumerate(order):
        dealt[groups[position % len(groups)][0]].append(customer)
    return tuple(
        Arm(group_id, kind, tuple(dealt[group_id])) for group_id, kind in groups
    )


def _plan(connection: Connection, experiment_id: int) -> AssignmentPlan:
    experiment = experiments.get_experiment(connection, experiment_id)
    if experiment is None:
        raise ExperimentNotFound(experiment_id)
    if experiment.assignments:
        raise AssignmentRefused(
            f"Experiment {experiment_id} already has {experiment.assignments} "
            "assignments. Its arms were fixed when they were written (ADR-0019)."
        )
    if experiment.campaign_id is None:
        raise AssignmentRefused(
            f"Experiment {experiment_id} has no campaign, so there is no population "
            "to assign. Attach it to a campaign first."
        )
    campaign = get_campaign(connection, experiment.campaign_id)
    if campaign is None or campaign.status != _CAMPAIGN_ACTIVE:
        status = "gone" if campaign is None else campaign.status.lower()
        raise AssignmentRefused(
            f"Campaign {experiment.campaign_id} is {status}. Customers are assigned "
            "once it is active, when its target label can no longer change."
        )
    if experiment.control_groups == 0 and experiment.treatment_groups != 2:
        raise AssignmentRefused(
            f"Experiment {experiment_id} has no control group and must have "
            "exactly two treatment groups."
        )
    if experiment.treatment_groups < 1:
        missing = "treatment"
        raise AssignmentRefused(
            f"Experiment {experiment_id} has no {missing} group to assign into."
        )
    customers = experiments.read_target_population(connection, campaign.label_code)
    if not customers:
        raise AssignmentRefused(
            f"No customer currently holds the label {campaign.label_code}, so there "
            "is nobody to assign."
        )
    groups = experiments.list_groups(connection, experiment_id)
    if len(customers) < len(groups):
        raise AssignmentRefused(
            f"Experiment {experiment_id} needs at least {len(groups)} customers "
            f"to fill its {len(groups)} groups evenly; only {len(customers)} "
            "customer population is available. Nothing was written."
        )
    definitions = {
        group.group_id: group
        for group in experiments.list_group_definitions(connection, experiment_id)
    }
    arms = split(experiment_id, customers, groups)
    arms = tuple(
        Arm(
            arm.group_id,
            arm.kind,
            arm.customers,
            (
                definitions[arm.group_id].name
                if arm.group_id in definitions
                else arm.kind.title()
            ),
            (
                definitions[arm.group_id].treatment_description
                if arm.group_id in definitions
                else ""
            ),
        )
        for arm in arms
    )
    return AssignmentPlan(experiment, campaign.label_code, arms)


def plan_assignment(connection: Connection, experiment_id: int) -> AssignmentPlan:
    """What assigning the experiment would write now, for the preview. Writes
    nothing, and raises AssignmentRefused exactly when `assign` would."""
    return _plan(connection, experiment_id)


@atomic
def _assign(connection: Connection, experiment_id: int) -> AssignmentPlan:
    # The lock comes before the checks, so the "no assignments yet" they read
    # is still true when the rows are written: a second attempt waits here and
    # then finds the first one's assignments.
    if not experiments.lock_experiment(connection, experiment_id):
        raise ExperimentNotFound(experiment_id)
    plan = _plan(connection, experiment_id)
    experiments.insert_assignments(
        connection,
        experiment_id,
        [(arm.group_id, customer) for arm in plan.arms for customer in arm.customers],
    )
    return plan


def assign(connection: Connection, experiment_id: int) -> AssignmentPlan:
    """Assign the campaign's current population to the experiment's arms, once.

    Every row is written in one transaction before any exposure or conversion
    can exist (both reference an assignment), and a failure part way writes
    nothing. The database's UNIQUE (experiment_id, customer_id) is the final
    word on a second assignment; its refusal is reported, not raised as a 500.
    """
    try:
        return _assign(connection, experiment_id)
    except UniqueViolation as error:
        raise AssignmentRefused(
            f"A customer in this population is already assigned in experiment "
            f"{experiment_id}; nothing was written."
        ) from error


# ---------- exposure (F11-05) ----------


class ExposureRefused(Exception):
    """An exposure cannot be recorded; the message says why."""


@dataclass(frozen=True)
class ExposureSummary:
    experiment: experiments.Experiment
    groups: tuple[experiments.GroupExposure, ...]

    @property
    def treatment_assigned(self) -> int:
        return sum(g.assigned for g in self.groups if g.kind == experiments.TREATMENT)

    @property
    def treatment_exposed(self) -> int:
        return sum(g.exposed for g in self.groups if g.kind == experiments.TREATMENT)

    @property
    def not_exposed(self) -> int:
        return self.treatment_assigned - self.treatment_exposed

    @property
    def exposure_rate(self) -> float | None:
        """Share of the assigned treatment customers who were reached. A
        delivery diagnostic: it says whether the campaign reached people, not
        whether it worked (ADR-0019)."""
        if not self.treatment_assigned:
            return None
        return self.treatment_exposed / self.treatment_assigned


def exposure_summary(connection: Connection, experiment_id: int) -> ExposureSummary:
    experiment = experiments.get_experiment(connection, experiment_id)
    if experiment is None:
        raise ExperimentNotFound(experiment_id)
    return ExposureSummary(
        experiment, tuple(experiments.list_group_exposure(connection, experiment_id))
    )


def parse_customer_id(raw: str) -> str | None:
    """The canonical UUID text, or None; a malformed id is refused before it
    reaches a UUID column and surfaces as a database error."""
    try:
        return str(UUID(raw.strip()))
    except ValueError:
        return None


@atomic
def _record_exposure(connection: Connection, experiment_id: int, customer_id: str):
    # Same lock as assignment: an exposure can only follow an assignment that
    # is already committed, never race the one that would create it.
    if not experiments.lock_experiment(connection, experiment_id):
        raise ExperimentNotFound(experiment_id)
    found = experiments.find_assignment(connection, experiment_id, customer_id)
    if found is None:
        raise ExposureRefused(
            f"Customer {customer_id} is not assigned in experiment {experiment_id}, "
            "so there is nothing to expose. Exposure follows assignment."
        )
    assignment_id, kind = found
    if kind == experiments.CONTROL:
        raise ExposureRefused(
            f"Customer {customer_id} is in the control group of experiment "
            f"{experiment_id}. The control group is never exposed (ADR-0019)."
        )
    experiments.insert_exposure(connection, assignment_id)


def record_exposure(connection: Connection, experiment_id: int, customer_id: str):
    """Record that an assigned treatment customer was exposed, as its own event
    with its own timestamp. The control group is refused: exposing it would
    contaminate the comparison the experiment exists to make."""
    _record_exposure(connection, experiment_id, customer_id)


@atomic
def _record_group_exposures(
    connection: Connection,
    experiment_id: int,
    group_id: int,
    customer_ids: list[str] | None,
) -> int:
    if not experiments.lock_experiment(connection, experiment_id):
        raise ExperimentNotFound(experiment_id)
    definitions = experiments.list_group_definitions(connection, experiment_id)
    group = next((item for item in definitions if item.group_id == group_id), None)
    if group is None:
        raise ExposureRefused(
            f"Arm {group_id} does not belong to experiment {experiment_id}."
        )
    if group.kind == experiments.CONTROL:
        raise ExposureRefused(
            f"Arm {group.name} is the control group. "
            "The control group is never exposed."
        )
    ids = experiments.list_assignment_ids(
        connection, experiment_id, group_id, customer_ids or None
    )
    if customer_ids and len(ids) != len(customer_ids):
        raise ExposureRefused(
            "Select only customers assigned to this treatment arm; nothing was written."
        )
    for assignment_id in ids:
        experiments.insert_exposure(connection, assignment_id)
    return len(ids)


def record_group_exposures(
    connection: Connection,
    experiment_id: int,
    group_id: int,
    customer_ids: list[str] | None = None,
) -> int:
    """Append exposure events for all or a selected treatment-arm subset."""
    if customer_ids is not None and not customer_ids:
        raise ExposureRefused(
            "Select at least one customer or choose all assigned customers; "
            "nothing was written."
        )
    parsed = [parse_customer_id(value) for value in (customer_ids or [])]
    if any(value is None for value in parsed):
        raise ExposureRefused("Every selected customer id must be a UUID.")
    return _record_group_exposures(connection, experiment_id, group_id, parsed)
