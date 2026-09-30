"""The segmentation dashboard: segment sizes, RFM distribution, migration flow
and revenue by label, for one run (F12-01).

Everything here is keyed on the stable label code (ADR-0018). Nothing reads or
is told which method produced a run: the four charts a dashboard carries are the
same whichever method is behind the numbers, and a test builds one of each and
requires identical charts.

**Segment sizes** and **revenue by label** are both listed in the vocabulary's
declared best-to-worst order (segment_label.ordinal_position), with a label
nobody holds, or with no revenue, shown at zero rather than omitted — the same
principle F9-04's population table and RN-40's ranking already use.

**The RFM heatmap** bins every scored customer's raw recency and frequency into
five quintiles each — 5 recency x 5 frequency, 25 cells, always all present —
and counts customers per cell. The binning (`quintile_bins`) is this module's
own, a chart concern only: it does not read or write the stored r_score/f_score,
which exist for only one of the two segmentation methods and would make the
chart mean something different depending on which method made the run. Recomputing the
bins from the raw values, present for either method, is what keeps the chart
method-agnostic. An unassigned customer has no recency or frequency to bin and
is excluded; the heatmap's cell counts sum to the run's *scored* customers, not
its full customer_count.

**Migration flow** is a set of weighted links between an earlier run's labels
and a later run's, reusing F7's compute_migration/build_migration_matrix (the
label comparison itself is fully specified and tested there; this only reshapes
its output for a chart). An unchanged customer is a self-loop link, not omitted:
"stayed" is as much a flow as "moved". A customer only one of the two runs
scored is not a segment-to-segment move and is not a link; the two counts are
reported separately (`new_to_population`, `left_the_population`).

**The window revenue is read over** is the run's own: `window_days` ending at
`run_at`, exactly the window the run's own R/F/M were measured over, so "revenue
by label" means the label's spend in the same period that produced the label.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from web.db import experiment_report as report_db
from web.db.clock import current_date as business_date
from web.db.experiments import Experiment
from web.db.segmentation_dashboard import (
    LabelMeans,
    RunRfmRow,
    get_previous_run,
    list_run_label_means,
    list_run_labelled_customers,
    list_run_revenue_by_label,
    list_run_rfm_rows,
)
from web.db.segments import SegmentationRun, get_label_ordinals, get_run, list_runs
from web.services.consumption_profile import UnknownCustomer
from web.services.experiment_uplift import SYNTHETIC_LABEL
from web.services.experiments import is_synthetic
from web.services.recommendations import recommend
from web.services.segment_migration import (
    CustomerMigration,
    MigrationCategory,
    compute_migration,
)

QUINTILES = 5
UNASSIGNED = "Unassigned"


class NoRuns(ValueError):
    """There is no segmentation run to build a dashboard from."""


# ---------- quintile binning: a chart-only concern ----------


def quintile_bins(pairs: list[tuple[str, float]]) -> dict[str, int]:
    """Bin (id, value) pairs into 1..5, 5 the highest value.

    Ties are broken by the id, ascending, so binning is deterministic and does
    not depend on the order pairs arrive in. The split is PostgreSQL's own
    `ntile(5)`: the first `n // 5` buckets absorb one extra row each when `n`
    is not a multiple of 5, filled in ranked order. With fewer than 5 items
    some buckets are empty and the extremes are not both reached — the same
    behaviour the quintile scoring the pipeline itself writes already has, so
    a screen showing few customers reads the same way there and here.
    """
    ordered = sorted(pairs, key=lambda pair: (-pair[1], pair[0]))
    n = len(ordered)
    base, extra = divmod(n, QUINTILES)
    bins: dict[str, int] = {}
    position = 0
    for bucket in range(1, QUINTILES + 1):
        size = base + (1 if bucket <= extra else 0)
        for _ in range(size):
            item_id, _value = ordered[position]
            bins[item_id] = QUINTILES + 1 - bucket
            position += 1
    return bins


# ---------- segment sizes ----------


@dataclass(frozen=True)
class SegmentSize:
    label: str | None
    count: int


def build_segment_sizes(
    rows: list[RunRfmRow], ordinals: dict[str, int]
) -> tuple[SegmentSize, ...]:
    """How many customers hold each label, best to worst, then unassigned."""
    vocabulary = sorted(ordinals, key=ordinals.__getitem__)
    counts: dict[str | None, int] = dict.fromkeys(vocabulary, 0)
    counts[None] = 0
    for row in rows:
        counts[row.label_code] = counts.get(row.label_code, 0) + 1
    return tuple(SegmentSize(label, counts[label]) for label in (*vocabulary, None))


# ---------- revenue by label ----------


@dataclass(frozen=True)
class LabelRevenue:
    label: str
    total: Decimal


def build_revenue_by_label(
    revenue: dict[str, Decimal], ordinals: dict[str, int]
) -> tuple[LabelRevenue, ...]:
    """Revenue in the run's window, by label, best to worst. A label with no
    revenue in the window shows zero. There is no Unassigned bar: an
    unassigned customer has no sale in the run's own window by construction
    (RN-21), so there is never a figure to show for one."""
    vocabulary = sorted(ordinals, key=ordinals.__getitem__)
    return tuple(
        LabelRevenue(label, revenue.get(label, Decimal("0"))) for label in vocabulary
    )


# ---------- the RFM heatmap ----------


@dataclass(frozen=True)
class HeatmapCell:
    recency_bin: int
    frequency_bin: int
    count: int


def build_rfm_heatmap(rows: list[RunRfmRow]) -> tuple[HeatmapCell, ...]:
    """Every scored customer, binned by raw recency and frequency into a 5x5
    grid, all 25 cells always present. An unassigned customer has neither
    value and is excluded, so the cells sum to the run's scored customers."""
    scored = [row for row in rows if row.label_code is not None]
    recency = quintile_bins(
        [(row.customer_id, row.last_purchase_at.timestamp()) for row in scored]
    )
    frequency = quintile_bins(
        [(row.customer_id, float(row.frequency)) for row in scored]
    )

    counts: dict[tuple[int, int], int] = {}
    for row in scored:
        key = (recency[row.customer_id], frequency[row.customer_id])
        counts[key] = counts.get(key, 0) + 1

    return tuple(
        HeatmapCell(r, f, counts.get((r, f), 0))
        for r in range(1, QUINTILES + 1)
        for f in range(1, QUINTILES + 1)
    )


# ---------- migration flow ----------


@dataclass(frozen=True)
class FlowLink:
    source: str
    target: str
    weight: int


@dataclass(frozen=True)
class MigrationFlow:
    links: tuple[FlowLink, ...]
    unassigned_label: str
    new_to_population: int
    left_the_population: int


def build_migration_flow(
    migrations: list[CustomerMigration], ordinals: dict[str, int]
) -> MigrationFlow:
    """Reshape F7's per-customer migration classification into weighted
    label-to-label links for a flow chart.

    A customer only one of the two runs scored is a change in *population*,
    not a move between segments, and is not a link; the two counts are
    reported alongside the links instead.
    """
    del ordinals  # the vocabulary order does not matter for link weights
    weights: dict[tuple[str, str], int] = {}
    new_to_population = left_the_population = 0

    for migration in migrations:
        if migration.category is MigrationCategory.ABSENT_FROM_EARLIER:
            new_to_population += 1
            continue
        if migration.category is MigrationCategory.ABSENT_FROM_LATER:
            left_the_population += 1
            continue
        source = migration.label_before or UNASSIGNED
        target = migration.label_after or UNASSIGNED
        weights[(source, target)] = weights.get((source, target), 0) + 1

    links = tuple(
        FlowLink(source, target, weight)
        for (source, target), weight in weights.items()
        if weight > 0
    )
    return MigrationFlow(links, UNASSIGNED, new_to_population, left_the_population)


# ---------- assembling the dashboard ----------


@dataclass(frozen=True)
class Dashboard:
    run: SegmentationRun
    previous_run: SegmentationRun | None
    sizes: tuple[SegmentSize, ...]
    revenue: tuple[LabelRevenue, ...]
    heatmap: tuple[HeatmapCell, ...]
    migration: MigrationFlow | None
    revenue_window_start: datetime
    revenue_window_end: datetime


def build_dashboard(connection: Any, run_id: int | None) -> Dashboard:
    """Build every chart for one run: the named run, or the newest run there
    is when `run_id` is None. Raises NoRuns when there is none at all.
    """
    if run_id is None:
        runs, _total = list_runs(connection, page=1, per_page=1)
        run = runs[0] if runs else None
    else:
        run = get_run(connection, run_id)
    if run is None:
        raise NoRuns("There is no segmentation run to build a dashboard from.")

    ordinals = get_label_ordinals(connection)
    rows = list_run_rfm_rows(connection, run.run_id)

    until = run.run_at
    since = until - timedelta(days=run.window_days)
    revenue = list_run_revenue_by_label(connection, run.run_id, since, until)

    previous = get_previous_run(connection, run.run_id)
    migration = None
    if previous is not None:
        migrations = compute_migration(connection, previous.run_id, run.run_id)
        migration = build_migration_flow(migrations, ordinals)

    return Dashboard(
        run=run,
        previous_run=previous,
        sizes=build_segment_sizes(rows, ordinals),
        revenue=build_revenue_by_label(revenue, ordinals),
        heatmap=build_rfm_heatmap(rows),
        migration=migration,
        revenue_window_start=since,
        revenue_window_end=until,
    )


# ---------- the KPIs beyond the charts (#340) ----------
#
# Built apart from `build_dashboard` so the charts' data stays what F12-01 and
# its tests describe. Everything here is read-only and arrives as figures the
# page prints, each one reproducible with a single query.

# The most customers whose recommendations are tallied. `recommend` reads a
# profile, interests, stock and buyers for each, so an unbounded run would make
# opening the dashboard scale with the customer base. The page says when it
# stopped early, and by how much.
RECOMMENDATION_CUSTOMER_CAP = 100
TOP_RECOMMENDED = 5
# The most experiments read for the active list, newest first.
ACTIVE_EXPERIMENT_LIMIT = 200


@dataclass(frozen=True)
class MeansRow:
    """One label's means; the vocabulary's order, so a label with no customers
    still has a row."""

    label: str
    customers: int
    mean_r: Decimal | None = None
    mean_f: Decimal | None = None
    mean_m: Decimal | None = None
    mean_recency_days: Decimal | None = None
    mean_frequency: Decimal | None = None
    mean_monetary: Decimal | None = None


def build_label_means(
    means: list[LabelMeans], ordinals: dict[str, int]
) -> tuple[MeansRow, ...]:
    """Average R, F and M, and the raw means, for every label, best to worst."""
    by_label = {m.label_code: m for m in means}
    rows = []
    for label in sorted(ordinals, key=ordinals.__getitem__):
        found = by_label.get(label)
        rows.append(
            MeansRow(label, 0)
            if found is None
            else MeansRow(
                label,
                found.customers,
                found.mean_r,
                found.mean_f,
                found.mean_m,
                found.mean_recency_days,
                found.mean_frequency,
                found.mean_monetary,
            )
        )
    return tuple(rows)


@dataclass(frozen=True)
class ActiveExperiment:
    experiment: Experiment
    # Reuse the experiment report's final arm shape (#379, #370): it carries
    # the user-facing name and both conversion definitions without a second,
    # dashboard-only representation drifting from it.
    arms: tuple[report_db.ReportGroup, ...]

    @property
    def label(self) -> str | None:
        return SYNTHETIC_LABEL if is_synthetic(self.experiment.data_origin) else None


def build_active_experiments(
    connection: Any, today: date, now: datetime
) -> tuple[ActiveExperiment, ...]:
    """Experiments running today, each with its arms' intent-to-treat conversion
    rate (RN-48). Running means it has started, has not ended, and has assigned
    someone: an experiment with no assignments has no rate to show."""
    found = report_db.list_active_report_experiments(
        connection,
        active_on=today,
        limit=ACTIVE_EXPERIMENT_LIMIT,
    )
    groups = report_db.list_report_groups(
        connection, [e.experiment_id for e in found], now
    )
    return tuple(
        ActiveExperiment(
            e,
            tuple(g for g in groups if g.experiment_id == e.experiment_id),
        )
        for e in found
    )


@dataclass(frozen=True)
class RecommendedProduct:
    product_id: int
    name: str
    category_name: str
    customers: int
    # (store name, units on hand there) for the stores it was recommended from.
    stock: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class TopRecommended:
    products: tuple[RecommendedProduct, ...]
    considered: int
    total: int

    @property
    def capped(self) -> bool:
        return self.considered < self.total


def build_top_recommended(
    connection: Any,
    customer_ids: list[str],
    total: int,
    *,
    limit: int = TOP_RECOMMENDED,
) -> TopRecommended:
    """The products recommended to the most of these customers, each with its
    stock at the stores it was recommended from.

    Each customer is asked with `recommend`, the same call the customer page and
    the F12-03 report make, so a number here is a number there. A customer the
    recommender cannot serve (no segment, no usual store) adds nothing. Ties are
    broken by product id.
    """
    customers: dict[int, int] = {}
    named: dict[int, tuple[str, str]] = {}
    stock: dict[int, dict[int, tuple[str, int]]] = {}
    for customer_id in customer_ids:
        try:
            result = recommend(connection, customer_id)
        except UnknownCustomer:
            continue
        for item in result.recommendations:
            customers[item.product_id] = customers.get(item.product_id, 0) + 1
            named[item.product_id] = (item.name, item.category_name)
            if result.store_id is not None:
                stock.setdefault(item.product_id, {})[result.store_id] = (
                    result.store_name or str(result.store_id),
                    item.in_stock,
                )
    ranked = sorted(
        customers, key=lambda product_id: (-customers[product_id], product_id)
    )
    products = tuple(
        RecommendedProduct(
            product_id,
            named[product_id][0],
            named[product_id][1],
            customers[product_id],
            tuple(sorted(stock.get(product_id, {}).values())),
        )
        for product_id in ranked[:limit]
    )
    return TopRecommended(products, len(customer_ids), total)


@dataclass(frozen=True)
class Kpis:
    label_means: tuple[MeansRow, ...]
    active_experiments: tuple[ActiveExperiment, ...]
    recommended: TopRecommended
    scored: bool


def build_kpis(
    connection: Any,
    run: SegmentationRun,
    now: datetime,
    *,
    today: date | None = None,
) -> Kpis:
    """Average R/F/M per label, running experiments with their conversion rate
    per arm, and the most recommended products with their stock, for one run."""
    active_on = today if today is not None else business_date(connection)
    ordinals = get_label_ordinals(connection)
    means = list_run_label_means(connection, run.run_id, run.run_at)
    ids, total = list_run_labelled_customers(
        connection, run.run_id, RECOMMENDATION_CUSTOMER_CAP
    )
    return Kpis(
        label_means=build_label_means(means, ordinals),
        active_experiments=build_active_experiments(connection, active_on, now),
        recommended=build_top_recommended(connection, ids, total),
        scored=any(m.mean_r is not None for m in means),
    )
