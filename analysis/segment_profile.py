"""What each K-means segment is, in business terms (F23-05, #817).

    python -m analysis.segment_profile

Regenerates every table in `docs/evidence/f23-05-segment-profiles.md` over the
evaluation dataset (F23-01), with the model F23-03 chose (k = 4, seed 11, 180-day
window, production fit and normalization) and the labels ADR-0030 pairs with it. It
reads no database and writes no file. The commercial actions and the measures that
would show them working are written in the document, not computed here: this module
supplies the numbers they rest on, and checks each label's claim against them.

For each segment: the centroid in raw units, its size, its share of the window's
revenue, how recently and how often it bought, how it bought in the 180 days before
the window, and, as a check that only synthetic data allows, which planted group its
customers came from (ADR-0052).
"""

from __future__ import annotations

import statistics
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from analysis.dataset import DEFAULT_SEED, Dataset, generate
from analysis.k_study import VOCABULARY
from analysis.normalization_study import Features, n_minmax, table
from web.services.cluster_labels import labels_by_rank, rank_clusters
from web.services.kmeans import KMeansFit, KMeansParams, fit

_DAY = 86400
K = 4
SEED = 11
WINDOW_DAYS = 180

# The words each label carries, from the seed (`segment_label.description`).
DESCRIPTION = {
    "CHAMPION": "Highest recency, frequency and monetary value",
    "LOYAL": "Buys often and recently, consistent spend",
    "POTENTIAL": "Recent customer with room to grow frequency",
    "AT_RISK": "Used to buy often, recency has slipped",
    "HIBERNATING": "Low recency, frequency and monetary value",
    "LOST": "No recent activity across all three measures",
}

# Stated before the results, like F23-04's thresholds: a share this high is "all".
RECENT_DAYS = 30
INACTIVE_DAYS = 90
MOST = 0.8


@dataclass(frozen=True)
class Profile:
    label: str
    rank: int
    customers: int
    customer_share: float
    revenue: float
    revenue_share: float
    recency_days: float  # centroid, raw units: days since the last purchase
    frequency: float  # centroid, raw units: purchases in the window
    monetary: float  # centroid, raw units: spend in the window
    median_recency: float
    median_frequency: float
    median_monetary: float
    bought_recently: float  # share with a purchase in the last RECENT_DAYS
    bought_lately: float  # share with a purchase in the last INACTIVE_DAYS
    prior_frequency: float  # purchases in the 180 days before the window
    new_customers: float  # share with no purchase before the window
    extreme: int  # customers who spend far more than their group
    extreme_revenue_share: float  # of this segment's revenue
    groups: dict[str, int]  # planted group of each customer (synthetic only)


@dataclass(frozen=True)
class Check:
    label: str
    claim: str
    measured: str
    holds: bool


def _prior_window(dataset: Dataset, window_days: int) -> Counter[str]:
    """Purchases per customer in the window_days before the window."""
    low, high = window_days * _DAY, 2 * window_days * _DAY
    return Counter(s.customer_id for s in dataset.sales if low < s.seconds_ago <= high)


def _ever_before(dataset: Dataset, window_days: int) -> set[str]:
    limit = window_days * _DAY
    return {s.customer_id for s in dataset.sales if s.seconds_ago > limit}


def profiles(dataset: Dataset | None = None) -> tuple[list[Profile], KMeansFit]:
    """One profile per cluster, best first, with the label ADR-0030 gives it."""
    dataset = dataset or generate()
    features = Features(dataset, WINDOW_DAYS)
    result = fit(features.ids, n_minmax(features), KMeansParams(k=K, seed=SEED))
    labels = labels_by_rank(K, VOCABULARY)
    prior, before = _prior_window(dataset, WINDOW_DAYS), _ever_before(
        dataset, WINDOW_DAYS
    )
    total = sum(features.monetary)
    out = []
    for rank, cluster in enumerate(rank_clusters(result)):
        at = [i for i, c in enumerate(result.assignments) if c == cluster]
        r = [features.recency_days[i] for i in at]
        f = [features.frequency[i] for i in at]
        m = [features.monetary[i] for i in at]
        revenue = sum(m)
        out.append(
            Profile(
                label=labels[rank],
                rank=rank,
                customers=len(at),
                customer_share=len(at) / len(features.ids),
                revenue=revenue,
                revenue_share=revenue / total,
                # The centroid is the mean of its members, and min-max is a straight
                # line, so the raw centroid is the members' mean (a test checks it
                # against the normalized centroid turned back into raw units).
                recency_days=statistics.fmean(r),
                frequency=statistics.fmean(f),
                monetary=statistics.fmean(m),
                median_recency=statistics.median(r),
                median_frequency=statistics.median(f),
                median_monetary=statistics.median(m),
                bought_recently=sum(x <= RECENT_DAYS for x in r) / len(at),
                bought_lately=sum(x <= INACTIVE_DAYS for x in r) / len(at),
                prior_frequency=statistics.fmean(prior[features.ids[i]] for i in at),
                new_customers=sum(features.ids[i] not in before for i in at) / len(at),
                extreme=sum(features.extreme[i] for i in at),
                extreme_revenue_share=(
                    sum(features.monetary[i] for i in at if features.extreme[i])
                    / revenue
                ),
                groups=dict(Counter(features.group[i] for i in at)),
            )
        )
    return out, result


def checks(found: Sequence[Profile], overall_prior: float) -> list[Check]:
    """Each label's own words, put to what its cluster measures. A claim the model
    cannot see is stated as such by the document, not passed here."""
    by = {p.label: p for p in found}
    best, worst = found[0], found[-1]
    rows = []

    def add(label: str, claim: str, measured: str, holds: bool) -> None:
        rows.append(Check(label, claim, measured, holds))

    c = by["CHAMPION"]
    top = (
        c.recency_days == min(p.recency_days for p in found)
        and c.frequency == max(p.frequency for p in found)
        and c.monetary == max(p.monetary for p in found)
    )
    add(
        "CHAMPION",
        "highest recency, frequency and monetary value",
        f"R {c.recency_days:.0f} d, F {c.frequency:.1f}, M {c.monetary:,.0f}: "
        "the best of the four on each",
        top,
    )
    p = by["POTENTIAL"]
    add(
        "POTENTIAL",
        "recent",
        f"{p.bought_recently:.0%} bought in the last {RECENT_DAYS} days",
        p.bought_recently >= MOST,
    )
    add(
        "POTENTIAL",
        "room to grow frequency",
        f"F {p.frequency:.1f} against {best.frequency:.1f} for the best segment",
        p.frequency < best.frequency,
    )
    add(
        "POTENTIAL",
        "a customer who is recent (new)",
        f"{p.new_customers:.0%} had no purchase before the window",
        p.new_customers >= MOST,
    )
    a = by["AT_RISK"]
    add(
        "AT_RISK",
        "used to buy often",
        f"{a.prior_frequency:.1f} purchases in the 180 days before the window, "
        f"against {overall_prior:.1f} for all customers",
        a.prior_frequency > overall_prior,
    )
    add(
        "AT_RISK",
        "recency has slipped",
        f"R {a.recency_days:.0f} d, now {a.frequency:.1f} purchases against "
        f"{a.prior_frequency:.1f} before",
        a.recency_days > p.recency_days and a.frequency < a.prior_frequency,
    )
    lost = by["LOST"]
    add(
        "LOST",
        "no recent activity",
        f"{lost.bought_lately:.0%} bought in the last {INACTIVE_DAYS} days; "
        f"R {lost.recency_days:.0f} d",
        lost.bought_lately < 1 - MOST,
    )
    add(
        "LOST",
        "across all three measures",
        f"F {lost.frequency:.1f} and M {lost.monetary:,.0f}, the lowest of the four",
        lost.frequency == worst.frequency == min(x.frequency for x in found)
        and lost.monetary == min(x.monetary for x in found),
    )
    return rows


# ---------- tables ----------


def segment_table(found: Sequence[Profile]) -> str:
    return table(
        (
            "Segment (k = 4)",
            "Customers",
            "Share of customers",
            "Share of revenue",
            "R centroid (days since last purchase)",
            "F centroid (purchases)",
            "M centroid (spend)",
        ),
        [
            (
                p.label,
                f"{p.customers:,}",
                f"{p.customer_share:.1%}",
                f"{p.revenue_share:.1%}",
                f"{p.recency_days:.0f}",
                f"{p.frequency:.1f}",
                f"{p.monetary:,.0f}",
            )
            for p in found
        ],
    )


def behaviour_table(found: Sequence[Profile], overall_prior: float) -> str:
    return table(
        (
            "Segment",
            "Median R, F, M",
            f"Bought in last {RECENT_DAYS} d",
            f"Bought in last {INACTIVE_DAYS} d",
            "Purchases in the 180 d before the window",
            "No purchase before the window",
            "Extreme spenders, and their share of the segment's revenue",
        ),
        [
            (
                p.label,
                f"{p.median_recency:.0f} d, {p.median_frequency:.0f}, "
                f"{p.median_monetary:,.0f}",
                f"{p.bought_recently:.0%}",
                f"{p.bought_lately:.0%}",
                f"{p.prior_frequency:.1f}",
                f"{p.new_customers:.0%}",
                f"{p.extreme}, {p.extreme_revenue_share:.1%}",
            )
            for p in found
        ]
        + [("all customers", "", "", "", f"{overall_prior:.1f}", "", "")],
    )


def groups_table(found: Sequence[Profile]) -> str:
    names = [g for g in VOCABULARY if any(g in p.groups for p in found)]
    return table(
        ("Segment", *(f"planted {g}" for g in names)),
        [(p.label, *(p.groups.get(g, 0) for g in names)) for p in found],
    )


def checks_table(rows: Sequence[Check]) -> str:
    return table(
        ("Label", "What its description claims", "What the cluster measures", "Holds"),
        [(c.label, c.claim, c.measured, "yes" if c.holds else "**no**") for c in rows],
    )


def report() -> str:
    dataset = generate()
    found, result = profiles(dataset)
    prior = _prior_window(dataset, WINDOW_DAYS)
    ids = Features(dataset, WINDOW_DAYS).ids
    overall = statistics.fmean(prior[i] for i in ids)
    mapping = ", ".join(f"{p.label}" for p in found)
    return "\n".join(
        [
            f"fingerprint {dataset.fingerprint()}  dataset seed {DEFAULT_SEED}",
            f"k = {K}, seed {SEED}, {WINDOW_DAYS}-day window; {len(ids):,} customers "
            f"clustered, {len(dataset.customers) - len(ids):,} with no sale in the "
            f"window (unassigned, RN-21); cluster sizes {list(result.cluster_sizes)}",
            f"labels, best first (ADR-0030): {mapping}\n",
            "## Segments\n\n" + segment_table(found) + "\n",
            "## How each segment behaves\n\n" + behaviour_table(found, overall) + "\n",
            "## Does each label's claim hold\n\n"
            + checks_table(checks(found, overall))
            + "\n",
            "## Planted groups in each segment (synthetic only)\n\n"
            + groups_table(found)
            + "\n",
        ]
    )


def main(argv: Sequence[str] | None = None) -> int:
    del argv
    print(report())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
