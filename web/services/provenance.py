"""Data provenance vocabulary and the `Synthetic` marker's rules (ADR-0052).

Every sale and every segmentation run records where its data came from, in
``data_origin``: ``SEEDED`` for the demonstration seed, ``OBSERVED`` for
everything a real writer records (the CSV import, sales-service). The schema's
CHECK constraints hold the same two values; these constants are how the web
names them.

A page marks a figure ``Synthetic`` from the rows it read, never from a setting
of the instance (#560):

* a figure from one run is marked by that run, and a figure that compares two
  runs is marked when either is not ``OBSERVED`` (`runs_synthetic`);
* a figure aggregated directly from sales is marked when any sale it
  aggregates is ``SEEDED``. The reads return that as a flag beside the figure
  (``bool_or`` over the same rows), so it costs no extra query;
* an experiment figure is marked when the experiment is not ``OBSERVED`` or
  any conversion it records links a ``SEEDED`` sale (`experiment_synthetic`).

Experiments keep their own vocabulary (``OBSERVED``, ``SEEDED``, ``INJECTED``,
ADR-0019), so "not ``OBSERVED``" is the rule everywhere rather than
"is ``SEEDED``".
"""

from __future__ import annotations

from typing import Any

OBSERVED = "OBSERVED"
SEEDED = "SEEDED"

DATA_ORIGINS = (OBSERVED, SEEDED)

# The literal every marked page, chart label and export line carries (ADR-0019).
SYNTHETIC_LABEL = "Synthetic"


def is_seeded(data_origin: str | None) -> bool:
    """Whether a row of this origin makes a figure Synthetic. None means there
    is no row, which marks nothing."""
    return data_origin is not None and data_origin != OBSERVED


def runs_synthetic(*runs: Any) -> bool:
    """Whether a figure built from these runs is Synthetic: any of them is not
    OBSERVED. A missing run (None), such as the previous run of the first run
    there is, marks nothing."""
    return any(run is not None and is_seeded(run.data_origin) for run in runs)


def experiment_synthetic(experiment: Any) -> bool:
    """Whether an experiment's figures are Synthetic: the experiment is not
    OBSERVED, or a conversion it records links a SEEDED sale."""
    return is_seeded(experiment.data_origin) or bool(experiment.seeded_conversion)
