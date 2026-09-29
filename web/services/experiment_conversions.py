"""Conversion as a recorded link from an assignment to a sale (F11-06, ADR-0019).

A conversion is reproducible from data: the assignment, the experiment's fixed
window and the `transaction` rows that fell inside it. A sale carries no
experiment column. The window cannot move once assignment has happened (F11-03),
so evaluating twice, or later, attributes the same sales.

The service owns the transaction and the rules; SQL stays in
web/db/experiment_conversions.py (ADR-0003, ADR-0014).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from psycopg import Connection

from web.db import experiment_conversions as conversions
from web.db import experiments
from web.db.transactions import atomic
from web.services.experiments import ExperimentNotFound


class ConversionRefused(Exception):
    """Conversion cannot be evaluated; the message says why."""


@dataclass(frozen=True)
class ConversionSummary:
    experiment: experiments.Experiment
    groups: tuple[conversions.GroupConversion, ...]
    evaluated_at: datetime

    @property
    def pending(self) -> int:
        return sum(group.pending for group in self.groups)


def conversion_summary(
    connection: Connection, experiment_id: int, now: datetime | None = None
) -> ConversionSummary:
    experiment = experiments.get_experiment(connection, experiment_id)
    if experiment is None:
        raise ExperimentNotFound(experiment_id)
    moment = now or datetime.now(UTC)
    return ConversionSummary(
        experiment,
        tuple(conversions.list_group_conversion(connection, experiment_id, moment)),
        moment,
    )


@atomic
def _evaluate(connection: Connection, experiment_id: int) -> int:
    # The experiment lock is the one assignment and edits take: the window and
    # the assignments this reads are the ones committed, not ones changing.
    if not experiments.lock_experiment(connection, experiment_id):
        raise ExperimentNotFound(experiment_id)
    experiment = experiments.get_experiment(connection, experiment_id)
    if experiment is None:
        raise ExperimentNotFound(experiment_id)
    if not experiment.assignments:
        raise ConversionRefused(
            f"Experiment {experiment_id} has no assignments, so there is nothing "
            "to evaluate. Conversion is judged from an assignment."
        )
    return conversions.evaluate_conversions(connection, experiment_id)


def evaluate(connection: Connection, experiment_id: int) -> int:
    """Attribute every qualifying sale of the experiment's assigned customers.
    Returns the number of conversions newly recorded; already recorded ones are
    left alone, so this is safe to run repeatedly."""
    return _evaluate(connection, experiment_id)
