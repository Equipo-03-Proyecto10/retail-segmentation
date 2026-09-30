"""The segmentation pipeline: what produces a run, and what a run is allowed to be
(F3-10, F7-02, F9-01; ADR-0017, ADR-0018).

RF-12, and the demonstration list's "ejecución de un proceso principal".

Each segmentation method is an *adapter*: a function that reads the sales and
returns one labelled `Assignment` per customer plus the parameters that produced
them. The pipeline (`run_method`) takes it from there. It writes the run and every
assignment in one transaction, and it does not know, and after this boundary
nothing downstream may find out, which method sat on the other side. Migration,
dashboards and recommendations read the label code and never branch on
`segmentation_run.method` or consume a raw cluster id (ADR-0018).

`RFM_RULES` is the first adapter: quintile R/F/M scores matched against the bands
in `segment_rule`. `KMEANS` is the second method in the domain; its adapter carries
the mapping from clusters to labels (F9-03) and is supplied to `run_method`
rather than registered here, so no run can reach the database before it exists.

RN-21 lives here: a customer with no sales in the window is *unassigned*, not
left holding a stale segment. An empty segment is information; a wrong one is not.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from typing import Any

from web.db.segments import (
    QUINTILES,
    close_open_assignments,
    create_run,
    get_label_ordinals,
    insert_assignments,
    lock_for_run,
    read_open_assignments,
    read_rfm_inputs,
    score_rfm_rules,
)
from web.db.transactions import atomic
from web.services.cluster_labels import (
    check_vocabulary_size,
    describe_mapping,
    label_clusters,
)
from web.services.kmeans import (
    KMeansFit,
    KMeansParams,
    RawRfm,
    fit_customers,
    recorded_parameters,
)

# The seed covers 180 days of sales, so the default window sees all of it.
# Per-run rather than per-deployment: the operator chooses the window on the
# form, which is what "configurable" has to mean for a process somebody runs
# and then reads the result of.
DEFAULT_WINDOW_DAYS = 180
MIN_WINDOW_DAYS = 1
MAX_WINDOW_DAYS = 3650


class InvalidWindow(ValueError):
    """A window outside what a recalculation will accept."""


@dataclass(frozen=True)
class RunResult:
    """What one recalculation did, and how long it took."""

    window_days: int
    processed: int
    assigned: int
    unmatched: int
    reassigned: int
    cleared: int
    seconds: float
    run_id: int | None = None
    method: str = "RFM_RULES"
    fallback: int = 0

    @property
    def no_segment_changed(self) -> bool:
        """True when no customer's segment assignment changed in the run."""
        return self.reassigned == 0 and self.cleared == 0


def parse_window(raw: str | None) -> int:
    """Read a window from form input, refusing what is not a usable one."""
    if raw is None:
        return DEFAULT_WINDOW_DAYS
    try:
        days = int(str(raw).strip())
    except ValueError:
        raise InvalidWindow(
            "The window is a number of days, for example 180."
        ) from None

    if not MIN_WINDOW_DAYS <= days <= MAX_WINDOW_DAYS:
        raise InvalidWindow(
            f"The window must be between {MIN_WINDOW_DAYS} and "
            f"{MAX_WINDOW_DAYS} days."
        )
    return days


# ---------- the boundary ----------

# Exactly the domain segmentation_run.method's CHECK enforces. The service
# refuses anything else before it reads a row; the constraint is the guard that a
# second writer cannot skip.
METHODS = ("RFM_RULES", "KMEANS")


class UnknownMethod(ValueError):
    """A method outside the domain segmentation_run.method allows."""


class MethodUnavailable(RuntimeError):
    """A method in the domain that has no adapter to run it yet."""


class AdapterMismatch(ValueError):
    """An adapter was supplied for a different segmentation method."""


class InvalidAssignment(ValueError):
    """An assignment the pipeline will not record."""


class LabelMappingIncomplete(InvalidAssignment):
    """A mapping from clusters to labels that leaves a cluster without one."""


@dataclass(frozen=True)
class Assignment:
    """One customer's result, as it crosses the pipeline boundary (ADR-0018).

    It carries the stable label and, when the customer was measured, the raw
    recency, frequency and monetary values (ADR-0017) and whichever quintile
    scores the method produced. It carries no method and no cluster id: those
    would be exactly what a downstream consumer must not be able to read.

    `label_code` is None only for the unassigned result (RN-21): a customer with
    no sales in the window, so nothing measured and nothing to label.
    `segment_id` is the business segment a method resolved the label to, when it
    resolves one; the pipeline records it and does not interpret it.
    """

    customer_id: str
    label_code: str | None
    segment_id: int | None = None
    last_purchase_at: datetime | None = None
    frequency: int | None = None
    monetary: Decimal | None = None
    r_score: int | None = None
    f_score: int | None = None
    m_score: int | None = None
    via_fallback: bool = False

    def validate(self) -> None:
        """Refuse a customer who was measured but left unlabelled: ADR-0018 says
        a customer the run actually scored is always labelled."""
        raw_values = (
            self.last_purchase_at,
            self.frequency,
            self.monetary,
        )
        measured = raw_values + (
            self.r_score,
            self.f_score,
            self.m_score,
        )
        if self.label_code is None and any(value is not None for value in measured):
            raise InvalidAssignment(
                f"Customer {self.customer_id} was measured but is not labelled. "
                "Only a customer with no sales in the window may be left unassigned."
            )
        if self.label_code is not None and any(value is None for value in raw_values):
            raise InvalidAssignment(
                f"Customer {self.customer_id} is labelled but does not carry "
                "all raw recency, frequency and monetary values."
            )

    def as_row(self) -> tuple[Any, ...]:
        """The values `insert_assignments` writes, in its column order."""
        return (
            self.customer_id,
            self.segment_id,
            self.label_code,
            self.last_purchase_at,
            self.frequency,
            self.monetary,
            self.r_score,
            self.f_score,
            self.m_score,
        )


@dataclass(frozen=True)
class MethodOutput:
    """What an adapter hands the pipeline: every customer's assignment, and the
    parameters that produced them, stored on the run (ADR-0017)."""

    assignments: Sequence[Assignment]
    parameters: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MethodAdapter:
    """A read-only segmentation decision function bound to its method."""

    method: str
    decide: Callable[[Any, int], MethodOutput]


@dataclass(frozen=True)
class RunCounts:
    """What one run did to the assignments, from the results and what was open."""

    processed: int
    assigned: int
    unmatched: int
    reassigned: int
    cleared: int
    fallback: int = 0


def summarise(
    assignments: Sequence[Assignment],
    prior: Mapping[str, tuple[int | None, str | None]],
) -> RunCounts:
    """Count what a run changed.

    A customer *changed* when they had no open row, or their open (segment,
    label) differs from the new one. `reassigned` counts changes that leave a
    label, `cleared` changes that leave none. Comparing the pair, and not the
    segment alone, is what makes this true of a method that assigns a label
    without a segment.
    """
    assigned = sum(1 for a in assignments if a.label_code is not None)
    reassigned = cleared = 0
    for a in assignments:
        if a.customer_id in prior and prior[a.customer_id] == (
            a.segment_id,
            a.label_code,
        ):
            continue
        if a.label_code is None:
            cleared += 1
        else:
            reassigned += 1
    return RunCounts(
        processed=len(assignments),
        assigned=assigned,
        unmatched=len(assignments) - assigned,
        reassigned=reassigned,
        cleared=cleared,
        fallback=sum(1 for a in assignments if a.via_fallback),
    )


def _validated(output: MethodOutput) -> list[Assignment]:
    """Every assignment, checked, before any row is written."""
    seen: set[str] = set()
    for assignment in output.assignments:
        assignment.validate()
        if assignment.customer_id in seen:
            raise InvalidAssignment(
                f"Customer {assignment.customer_id} appears twice in one run."
            )
        seen.add(assignment.customer_id)
    return list(output.assignments)


# ---------- the RFM_RULES adapter ----------


def rfm_rules_adapter(connection: Any, window_days: int) -> MethodOutput:
    """Quintile R/F/M scores over the window, matched against the segment bands.

    The scoring and the matching are SQL (`web.db.segments.score_rfm_rules`); this
    turns its rows into assignments and states the parameters that made them.
    """
    assignments = tuple(
        Assignment(
            customer_id=row.customer_id,
            label_code=row.label_code,
            segment_id=row.segment_id,
            last_purchase_at=row.last_purchase_at,
            frequency=row.frequency,
            monetary=row.monetary,
            r_score=row.r_score,
            f_score=row.f_score,
            m_score=row.m_score,
            via_fallback=row.via_fallback,
        )
        for row in score_rfm_rules(connection, window_days)
    )
    return MethodOutput(
        assignments=assignments,
        parameters={"window_days": window_days, "quintiles": QUINTILES},
    )


# ---------- the KMEANS adapter ----------

# Turns a finished fit into a label for every cluster number, {cluster: label}.
# ADR-0018 defines that mapping (F9-03): centroids ordered by descending R + F + M
# and paired with the configured vocabulary, best to worst. It is handed to the
# adapter rather than written into it, so this file never invents a label and a
# raw cluster number cannot become one by any other route.
LabelMapper = Callable[[KMeansFit], Mapping[int, str]]


def kmeans_adapter(
    params: KMeansParams, label_for_clusters: LabelMapper
) -> MethodAdapter:
    """K-means over the window's normalised R/F/M, labelled by `label_for_clusters`.

    Only customers with sales are clustered. Those without have no R/F/M to
    cluster, so they are recorded as the unassigned result (RN-21) rather than
    forced into a cluster (ADR-0018). A clustered customer carries their raw
    recency, frequency and monetary values and no quintile scores: those belong to
    RFM_RULES, and inventing them here would make two measures look like one.

    The run records every parameter of the fit and its quality measures (F9-02). A
    fit that stopped on the iteration limit is recorded as not converged and logged
    as a warning; it is still written, and never presented as settled.
    """

    def adapt(connection: Any, window_days: int) -> MethodOutput:
        inputs = read_rfm_inputs(connection, window_days)
        measured = [row for row in inputs if row.frequency is not None]
        fitted = fit_customers(
            [
                RawRfm(
                    row.customer_id, row.last_purchase_at, row.frequency, row.monetary
                )
                for row in measured
            ],
            params,
        )

        labels = label_for_clusters(fitted)
        unlabelled = [c for c in range(params.k) if c not in labels]
        if unlabelled:
            raise LabelMappingIncomplete(
                f"The mapping leaves {len(unlabelled)} of {params.k} clusters "
                "without a label."
            )
        cluster_of = dict(zip(fitted.customer_ids, fitted.assignments, strict=True))

        assignments = tuple(
            Assignment(
                customer_id=row.customer_id,
                label_code=(
                    labels[cluster_of[row.customer_id]]
                    if row.customer_id in cluster_of
                    else None
                ),
                last_purchase_at=row.last_purchase_at,
                frequency=row.frequency,
                monetary=row.monetary,
            )
            for row in inputs
        )

        parameters = recorded_parameters(fitted, window_days)
        parameters["quality"]["customers_unassigned"] = len(inputs) - len(measured)
        if not fitted.converged:
            logging.getLogger(__name__).warning(
                "kmeans_not_converged window_days=%s iterations=%s final_shift=%.6f",
                window_days,
                fitted.iterations,
                fitted.final_shift,
            )
        return MethodOutput(assignments=assignments, parameters=parameters)

    return MethodAdapter("KMEANS", adapt)


def _run_kmeans_adapter(params: KMeansParams) -> MethodAdapter:
    """`kmeans_adapter` with the label pairing wired in, reading the vocabulary
    itself and refusing before it reads a single sale.

    The vocabulary is read best to worst by `ordinal_position`, the order the
    schema declares. Any `k` of at least two is paired with it by proportional
    rank (ADR-0030); the check is the first thing done, ahead of the sales and
    the fit, so a run that cannot be labelled costs nothing and writes nothing.
    The run records how its clusters were paired, including any label shared by
    several clusters or taken by none, under `label_mapping` in its parameters.
    """

    def adapt(connection: Any, window_days: int) -> MethodOutput:
        ordinals = get_label_ordinals(connection)
        vocabulary = sorted(ordinals, key=ordinals.__getitem__)
        check_vocabulary_size(params.k, vocabulary)
        output = kmeans_adapter(
            params, lambda fitted: label_clusters(fitted, vocabulary)
        ).decide(connection, window_days)
        return replace(
            output,
            parameters={
                **output.parameters,
                "label_mapping": describe_mapping(params.k, vocabulary),
            },
        )

    return MethodAdapter("KMEANS", adapt)


_ADAPTERS: dict[str, MethodAdapter] = {
    "RFM_RULES": MethodAdapter("RFM_RULES", rfm_rules_adapter)
}


# ---------- the pipeline ----------


@atomic
def _record(
    connection: Any, method: str, window_days: int, adapter: MethodAdapter
) -> RunResult:
    """Read, decide, write, and commit, as one unit of work.

    Committed here rather than left to the route because the audit entries the
    triggers write are part of the run: a caller that forgot to commit would roll
    back the assignment and its own record of having made it. The run row and
    every history row roll back together if any part of this fails.
    """
    started = time.perf_counter()
    recorded_at = lock_for_run(connection)
    output = adapter.decide(connection, window_days)
    assignments = _validated(output)

    prior = read_open_assignments(connection)
    run_id = create_run(
        connection,
        method,
        window_days,
        dict(output.parameters),
        len(assignments),
        run_at=recorded_at,
    )
    close_open_assignments(
        connection, [a.customer_id for a in assignments], at=recorded_at
    )
    insert_assignments(
        connection, run_id, [a.as_row() for a in assignments], at=recorded_at
    )

    counts = summarise(assignments, prior)
    return RunResult(
        window_days=window_days,
        processed=counts.processed,
        assigned=counts.assigned,
        unmatched=counts.unmatched,
        reassigned=counts.reassigned,
        cleared=counts.cleared,
        fallback=counts.fallback,
        seconds=time.perf_counter() - started,
        run_id=run_id,
        method=method,
    )


def run_method(
    connection: Any,
    method: str,
    window_days: int,
    adapter: MethodAdapter | None = None,
) -> RunResult:
    """Run one segmentation method over a window and record the outcome.

    `method` must be in the domain (UnknownMethod otherwise, before anything is
    read). An `adapter` may be supplied; without one the method's registered
    adapter runs, and a method with none raises MethodUnavailable. Nothing after
    the adapter looks at the method again.
    """
    if method not in METHODS:
        raise UnknownMethod(
            f"{method!r} is not a segmentation method; use one of {', '.join(METHODS)}."
        )
    chosen = adapter if adapter is not None else _ADAPTERS.get(method)
    if chosen is None:
        raise MethodUnavailable(f"No adapter is available to run {method} yet.")
    if chosen.method != method:
        raise AdapterMismatch(
            f"The {chosen.method} adapter cannot run the requested {method} method."
        )

    logger = logging.getLogger(__name__)
    logger.info("segment_run_started window_days=%s method=%s", window_days, method)
    result = _record(connection, method, window_days, chosen)
    logger.info(
        "segment_run_succeeded window_days=%s method=%s run_id=%s processed=%s "
        "assigned=%s unmatched=%s reassigned=%s cleared=%s seconds=%.3f",
        result.window_days,
        result.method,
        result.run_id,
        result.processed,
        result.assigned,
        result.unmatched,
        result.reassigned,
        result.cleared,
        result.seconds,
    )
    return result


def run(connection: Any, window_days: int) -> RunResult:
    """The RFM_RULES recalculation the segment-run page triggers (F3-10)."""
    return run_method(connection, "RFM_RULES", window_days)


def run_kmeans(connection: Any, window_days: int, params: KMeansParams) -> RunResult:
    """Run K-means over a window, labelled by ADR-0018's deterministic order and
    ADR-0030's pairing by proportional rank.

    Any `params.k` of at least 2 is run; a smaller one is refused
    (VocabularySizeMismatch) before any sale is read or any assignment is
    written. This is how a KMEANS run is started: `run_method` has no default k or
    seed to give, so it cannot start one on its own.
    """
    return run_method(
        connection, "KMEANS", window_days, adapter=_run_kmeans_adapter(params)
    )
