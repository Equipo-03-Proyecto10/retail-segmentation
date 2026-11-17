"""Variables, normalization and extreme values for K-means (F23-02, #814).

    python -m analysis.normalization_study

Regenerates every table in `docs/evidence/f23-02-variables-and-normalization.md`
from the evaluation dataset (F23-01). It reads no database and writes no file: the
tables go to standard output. Nothing here touches production code; the fits use
`web.services.kmeans.fit` as it is, over points this module prepares differently.
There is no SQL in this module.

Planted groups are what the generator put there, not ground truth about what a model
ought to call a segment (F23-06 states the criteria). They are used as one yardstick,
the adjusted Rand index (ARI), next to sizes and stability, which need no labels.
"""

from __future__ import annotations

import math
import statistics
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence

from analysis.dataset import (
    AS_OF,
    DEFAULT_WINDOW_DAYS,
    Dataset,
    generate,
    rfm_rows,
)
from web.services.kmeans import KMeansParams, Point, fit, min_max

K = 6
MAIN_SEED = 11
STABILITY_SEEDS = tuple(range(1, 9))
_DAY = 86400

Vector = list[float]


# ---------- statistics, by hand: the project venv has no numpy ----------


def quantile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    position = q * (len(ordered) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def pearson(a: Sequence[float], b: Sequence[float]) -> float:
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b, strict=True))
    den = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
    return 0.0 if den == 0 else num / den


def ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for t in range(i, j + 1):
            out[order[t]] = (i + j) / 2
        i = j + 1
    return out


def spearman(a: Sequence[float], b: Sequence[float]) -> float:
    return pearson(ranks(a), ranks(b))


def eta_squared(values: Sequence[float], groups: Sequence[str]) -> float:
    """Share of a variable's variance that sits between planted groups."""
    mean = statistics.fmean(values)
    total = sum((v - mean) ** 2 for v in values)
    if total == 0:
        return 0.0
    by: dict[str, list[float]] = defaultdict(list)
    for v, g in zip(values, groups, strict=True):
        by[g].append(v)
    between = sum(len(v) * (statistics.fmean(v) - mean) ** 2 for v in by.values())
    return between / total


def adjusted_rand(a: Sequence[object], b: Sequence[object]) -> float:
    n = len(a)

    def pairs(x: int) -> float:
        return x * (x - 1) / 2

    table_ = Counter(zip(a, b, strict=True))
    sum_cells = sum(pairs(c) for c in table_.values())
    sum_a = sum(pairs(c) for c in Counter(a).values())
    sum_b = sum(pairs(c) for c in Counter(b).values())
    expected = sum_a * sum_b / pairs(n)
    top = (sum_a + sum_b) / 2
    return 1.0 if top == expected else (sum_cells - expected) / (top - expected)


# ---------- features ----------


class Features:
    """Raw and candidate variables per customer in the window, ordered by id."""

    def __init__(self, dataset: Dataset, window_days: int = DEFAULT_WINDOW_DAYS):
        rows = rfm_rows(dataset, window_days)
        self.ids = [r.customer_id for r in rows]
        index = {c: i for i, c in enumerate(self.ids)}
        info = {c.customer_id: c for c in dataset.customers}
        self.group = [info[c].group for c in self.ids]
        self.extreme = [info[c].extreme for c in self.ids]
        latest = max(r.last_purchase_at for r in rows)
        self.recency_days = [
            (latest - r.last_purchase_at).total_seconds() / _DAY for r in rows
        ]
        self.frequency = [float(r.frequency) for r in rows]
        self.monetary = [float(r.monetary) for r in rows]

        n = len(rows)
        limit = window_days * _DAY
        cats: list[set[int]] = [set() for _ in range(n)]
        stores: list[set[int]] = [set() for _ in range(n)]
        chan: list[Counter[int]] = [Counter() for _ in range(n)]
        cat_count: list[Counter[int]] = [Counter() for _ in range(n)]
        for s in dataset.sales:
            if s.seconds_ago > limit:
                continue
            i = index[s.customer_id]
            cats[i].add(s.category)
            stores[i].add(s.store)
            chan[i][s.channel] += 1
            cat_count[i][s.category] += 1
        self.candidates: dict[str, Vector] = {
            "average ticket (M / F)": [
                m / f for m, f in zip(self.monetary, self.frequency, strict=True)
            ],
            "distinct categories": [float(len(c)) for c in cats],
            "distinct stores": [float(len(s)) for s in stores],
            "top-category share": [
                max(c.values()) / sum(c.values()) for c in cat_count
            ],
        }
        for channel in sorted({s.channel for s in dataset.sales}):
            self.candidates[f"channel {channel} share"] = [
                c[channel] / sum(c.values()) for c in chan
            ]


# ---------- normalizations and extreme-value treatments ----------


def n_minmax(f: Features) -> list[Point]:
    """What production does (ADR-0018 as amended by ADR-0030): min-max, R reversed."""
    r = min_max(f.recency_days, reverse=True)
    return list(zip(r, min_max(f.frequency), min_max(f.monetary), strict=True))


def n_log(f: Features) -> list[Point]:
    """log1p on all three, then min-max. R is logged before it is reversed."""
    r = min_max([math.log1p(v) for v in f.recency_days], reverse=True)
    fr = min_max([math.log1p(v) for v in f.frequency])
    m = min_max([math.log1p(v) for v in f.monetary])
    return list(zip(r, fr, m, strict=True))


def _robust(values: Vector) -> Vector:
    median = statistics.median(values)
    iqr = quantile(values, 0.75) - quantile(values, 0.25)
    return [(v - median) / (iqr or 1.0) for v in values]


def n_robust(f: Features) -> list[Point]:
    """(x - median) / IQR on raw values; R reversed by negation. Unbounded."""
    r = [-v for v in _robust(f.recency_days)]
    return list(zip(r, _robust(f.frequency), _robust(f.monetary), strict=True))


def n_rank(f: Features) -> list[Point]:
    """Percentile rank to [0, 1]: the extreme case of outlier resistance."""
    r = min_max(ranks(f.recency_days), reverse=True)
    fr = min_max(ranks(f.frequency))
    m = min_max(ranks(f.monetary))
    return list(zip(r, fr, m, strict=True))


def winsorized(percentile: float) -> Callable[[Features], list[Point]]:
    """Min-max after capping F and M at an upper percentile (R is bounded already)."""

    def build(f: Features) -> list[Point]:
        def cap(values: Vector) -> Vector:
            top = quantile(values, percentile)
            return [min(v, top) for v in values]

        r = min_max(f.recency_days, reverse=True)
        return list(
            zip(r, min_max(cap(f.frequency)), min_max(cap(f.monetary)), strict=True)
        )

    return build


def n_log_winsor(f: Features) -> list[Point]:
    """log1p, then cap at p99, then min-max."""

    def prep(values: Vector) -> Vector:
        logged = [math.log1p(v) for v in values]
        top = quantile(logged, 0.99)
        return [min(v, top) for v in logged]

    r = min_max([math.log1p(v) for v in f.recency_days], reverse=True)
    return list(
        zip(r, min_max(prep(f.frequency)), min_max(prep(f.monetary)), strict=True)
    )


NORMALIZATIONS: dict[str, Callable[[Features], list[Point]]] = {
    "min-max (production)": n_minmax,
    "log1p + min-max": n_log,
    "robust (median / IQR)": n_robust,
    "percentile rank": n_rank,
}
EXTREME_TREATMENTS: dict[str, Callable[[Features], list[Point]]] = {
    "none (min-max)": n_minmax,
    "winsorize F, M at p99": winsorized(0.99),
    "winsorize F, M at p95": winsorized(0.95),
    "transform: log1p": n_log,
    "transform + winsorize p99": n_log_winsor,
}


# ---------- fitting ----------


def cluster(f: Features, points: Sequence[Point], seed: int, k: int = K) -> list[int]:
    """Assignments in the order of f.ids, which is already sorted by id."""
    result = fit(f.ids, points, KMeansParams(k=k, seed=seed))
    return list(result.assignments)


def describe(f: Features, build: Callable[[Features], list[Point]]) -> dict:
    points = build(f)
    main = cluster(f, points, MAIN_SEED)
    sizes = sorted(Counter(main).values(), reverse=True)
    fits = [main] + [cluster(f, points, s) for s in STABILITY_SEEDS]
    smallest = [min(Counter(a).values()) for a in fits]
    pair = [
        adjusted_rand(fits[i], fits[j])
        for i in range(len(fits))
        for j in range(i + 1, len(fits))
    ]
    in_cluster = Counter(a for a, e in zip(main, f.extreme, strict=True) if e)
    return {
        "sizes": sizes,
        "ari_planted": adjusted_rand(main, f.group),
        "ari_planted_mean": statistics.fmean(adjusted_rand(a, f.group) for a in fits),
        "smallest_min": min(smallest),
        "tiny_fits": sum(1 for s in smallest if s < 0.01 * len(f.ids)),
        "fits": len(fits),
        "stability": statistics.fmean(pair),
        "extreme_clusters": len(in_cluster),
        "extreme_top": in_cluster.most_common(1)[0][1] if in_cluster else 0,
    }


# ---------- tables ----------


def table(header: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(out)


REDUNDANT_RHO = 0.8
INFORMATIVE_ETA = 0.10


def decide(core: bool, eta: float, rho: Sequence[float]) -> str:
    """The rule: R, F and M are the definition (RN-58); a candidate is dropped when
    it tracks one of them (|rho| >= 0.8) or separates the planted groups barely at
    all (eta2 < 0.10), and otherwise is a candidate worth a model version."""
    if core:
        return "kept (R, F, M are the definition)"
    if max(abs(r) for r in rho) >= REDUNDANT_RHO:
        return "dropped: redundant with R, F or M"
    if eta < INFORMATIVE_ETA:
        return "dropped: does not separate behaviour"
    return "candidate: separates behaviour on its own"


def variables_table(f: Features) -> str:
    rfm = {
        "R (recency, days)": f.recency_days,
        "F (frequency)": f.frequency,
        "M (monetary)": f.monetary,
    }
    rows = []
    for name, values in {**rfm, **f.candidates}.items():
        rho = [
            spearman(values, f.recency_days),
            spearman(values, f.frequency),
            spearman(values, f.monetary),
        ]
        eta = eta_squared(values, f.group)
        rows.append(
            [
                name,
                f"{eta:.2f}",
                f"{rho[1]:+.2f}",
                f"{rho[2]:+.2f}",
                f"{rho[0]:+.2f}",
                decide(name in rfm, eta, rho),
            ]
        )
    return table(
        [
            "Variable",
            "eta2 by planted group",
            "rho with F",
            "rho with M",
            "rho with R",
            "Decision",
        ],
        rows,
    )


def _sizes(sizes: Sequence[int]) -> str:
    return " / ".join(str(s) for s in sizes)


def normalization_table(results: dict[str, dict]) -> str:
    rows = [
        [
            name,
            _sizes(r["sizes"]),
            f"{r['ari_planted']:.3f}",
            f"{r['ari_planted_mean']:.3f}",
            f"{r['stability']:.3f}",
            f"{r['smallest_min']}",
            f"{r['tiny_fits']} of {r['fits']}",
        ]
        for name, r in results.items()
    ]
    return table(
        [
            "Method",
            f"Cluster sizes (k={K}, seed {MAIN_SEED})",
            "ARI planted",
            "ARI planted, mean of 9 seeds",
            "Seed stability (mean pairwise ARI)",
            "Smallest cluster, any seed",
            "Fits with a cluster under 1%",
        ],
        rows,
    )


def extreme_table(f: Features, results: dict[str, dict]) -> str:
    rows = [
        [
            name,
            _sizes(r["sizes"]),
            f"{r['extreme_top']} of {sum(f.extreme)}",
            r["extreme_clusters"],
            f"{r['ari_planted']:.3f}",
            f"{r['ari_planted_mean']:.3f}",
            f"{r['stability']:.3f}",
            f"{r['smallest_min']}",
            f"{r['tiny_fits']} of {r['fits']}",
        ]
        for name, r in results.items()
    ]
    return table(
        [
            "Treatment",
            "Cluster sizes",
            "Extreme spenders in their commonest cluster",
            "Clusters holding any",
            "ARI planted",
            "ARI planted, mean of 9 seeds",
            "Seed stability",
            "Smallest cluster, any seed",
            "Fits with a cluster under 1%",
        ],
        rows,
    )


def spread_table(f: Features) -> str:
    rows = []
    for name, values in (("F", f.frequency), ("M", f.monetary)):
        mm = min_max(values)
        lg = min_max([math.log1p(v) for v in values])
        rows.append(
            [
                name,
                f"{statistics.median(mm):.4f}",
                f"{quantile(mm, 0.99):.4f}",
                f"{statistics.median(lg):.3f}",
                f"{quantile(lg, 0.99):.3f}",
                f"{max(values):,.0f} vs median {statistics.median(values):,.0f}",
            ]
        )
    return table(
        [
            "Variable",
            "min-max median",
            "min-max p99",
            "log median",
            "log p99",
            "Maximum",
        ],
        rows,
    )


def report() -> str:
    dataset = generate()
    f = Features(dataset)
    norms = {name: describe(f, b) for name, b in NORMALIZATIONS.items()}
    treat = {name: describe(f, b) for name, b in EXTREME_TREATMENTS.items()}
    return "\n".join(
        [
            f"fingerprint {dataset.fingerprint()}  as_of {AS_OF.date()}",
            f"customers in window: {len(f.ids)}, extreme: {sum(f.extreme)}\n",
            "## Variables\n\n" + variables_table(f) + "\n",
            "## Spread after scaling\n\n" + spread_table(f) + "\n",
            "## Normalizations\n\n" + normalization_table(norms) + "\n",
            "## Extreme values\n\n" + extreme_table(f, treat) + "\n",
        ]
    )


def main(argv: Sequence[str] | None = None) -> int:
    del argv
    print(report())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
