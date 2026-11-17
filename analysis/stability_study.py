"""K-means stability, and a registered result reproduced (F23-04, #816).

    python -m analysis.stability_study

Regenerates every table in `docs/evidence/f23-04-kmeans-stability.md` over the
evaluation dataset (F23-01), with the production fit and production normalization
(min-max, R reversed), as F23-03 does. It reads no database and writes no file: the
tables go to standard output. Nothing here touches production code, and there is no
SQL in this module.

Four questions, one section each:

1. Reproduction: a registered configuration, fitted again from nothing but what the
   run records (RN-58), gives the same assignments and the same metrics.
2. Seeds: the same customers fitted at many seeds. Measure: the mean and the lowest
   pairwise adjusted Rand index (ARI).
3. Bootstrap: customers resampled with replacement, the fit repeated on each
   resample, and every customer then assigned to the nearest of that fit's
   centroids. Measures: the ARI against the full-data fit, and, per cluster, the
   Jaccard index of its best match in each resample (the cluster-wise measure of
   Hennig, 2007).
4. Adjacent windows: the same fit over feature windows shifted by 30 days. Measures:
   how many customers change rank, how far the centroids move, and the ARI, next to
   the same numbers for a change of seed only, which is the movement that is noise.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
import sys
from collections.abc import Sequence
from decimal import Decimal

from analysis.dataset import DEFAULT_CUSTOMERS, DEFAULT_SEED, Dataset, generate
from analysis.normalization_study import adjusted_rand, table
from web.services import kmeans
from web.services.cluster_labels import rank_clusters
from web.services.kmeans import (
    KMeansFit,
    KMeansParams,
    Point,
    fit,
    min_max,
    nearest,
    recorded_parameters,
)

_DAY = 86400

# The registered result: what a run would record (RN-58) plus the dataset that
# stands in for the customers. k = 4 is the choice of F23-03.
REGISTERED = {
    "dataset_seed": DEFAULT_SEED,
    "customers": DEFAULT_CUSTOMERS,
    "k": 4,
    "seed": 11,
    "max_iterations": 100,
    "tolerance": 1e-4,
    "window_days": 180,
}

# Only the k a team could run (F23-03: at least 3 segments, at most 8).
K_RANGE = range(2, 9)
SEEDS = tuple(range(1, 21))
BOOTSTRAPS = 20
BOOTSTRAP_SEED = 7
STEP_DAYS = 30
NOISE_SEEDS = (12, 13, 14, 15, 16)  # against the registered seed, 11
SHIFTS = tuple(range(0, 181, STEP_DAYS))  # window offsets: 0, 30, ..., 180 days back

# Thresholds, stated before the results and used to read every table. They are
# judgements, like F23-03's commercial limits, and the owner may move them.
STABLE_ARI = 0.80  # mean ARI at or above: the partition is the same one
POOR_ARI = 0.60  # mean ARI below: the partition depends on luck
STABLE_JACCARD = 0.75  # a cluster whose best match is at least this similar is found
POOR_JACCARD = 0.60  # below: the cluster is not a stable group of customers


# ---------- 1. reproduction ----------


def _points(dataset: Dataset, offset_days: int, window_days: int):
    """Customers with a sale in the window that ends `offset_days` back and is
    `window_days` long, and their min-max points as production builds them (R
    reversed). Recency is measured from the newest sale in the window: min-max is
    unchanged by moving every value by the same amount, so the offset cancels."""
    low, high = offset_days * _DAY, (offset_days + window_days) * _DAY
    newest: dict[str, int] = {}
    count: dict[str, int] = {}
    total: dict[str, Decimal] = {}
    for sale in dataset.sales:
        if not low < sale.seconds_ago <= high:
            continue
        who = sale.customer_id
        newest[who] = min(newest.get(who, high), sale.seconds_ago)
        count[who] = count.get(who, 0) + 1
        total[who] = total.get(who, Decimal(0)) + sale.amount
    ids = sorted(newest)
    # Days since the newest sale of the window, as Features measures them.
    latest = min(newest.values())
    recency = [(newest[i] - latest) / _DAY for i in ids]
    r = min_max(recency, reverse=True)
    f = min_max([float(count[i]) for i in ids])
    m = min_max([float(total[i]) for i in ids])
    return ids, list(zip(r, f, m, strict=True))


def _fit_registered(registered: dict) -> KMeansFit:
    """A fit built from nothing but the registered values, the way a run is
    reproduced from the database: regenerate the customers, rebuild the window,
    fit with the recorded parameters."""
    dataset = generate(registered["dataset_seed"], registered["customers"])
    ids, points = _points(dataset, 0, registered["window_days"])
    params = KMeansParams(
        k=registered["k"],
        seed=registered["seed"],
        max_iterations=registered["max_iterations"],
        tolerance=registered["tolerance"],
    )
    return fit(ids, points, params)


def digest(result: KMeansFit) -> str:
    """Identifies a partition: customer ids with their cluster numbers."""
    h = hashlib.sha256()
    for who, cluster in zip(result.customer_ids, result.assignments, strict=True):
        h.update(f"{who}|{cluster}\n".encode())
    return h.hexdigest()


def reproduce(registered: dict = REGISTERED, repeats: int = 3) -> dict:
    """Fit the registered configuration `repeats` times, each from scratch, and say
    what differs from the first. Anything but an empty list is a failure."""
    fits = [_fit_registered(registered) for _ in range(repeats)]
    first = fits[0]
    record = json.dumps(
        recorded_parameters(first, registered["window_days"]), sort_keys=True
    )
    differences = []
    for index, other in enumerate(fits[1:], start=2):
        if other.assignments != first.assignments:
            differences.append(f"fit {index}: assignments")
        if other.centroids != first.centroids:
            differences.append(f"fit {index}: centroids")
        if (
            json.dumps(
                recorded_parameters(other, registered["window_days"]), sort_keys=True
            )
            != record
        ):
            differences.append(f"fit {index}: recorded metrics")
    return {
        "fit": first,
        "digest": digest(first),
        "record": json.loads(record),
        "repeats": repeats,
        "differences": differences,
    }


# ---------- 2. seeds ----------


def seed_study(ids: Sequence[str], points: Sequence[Point], k: int) -> dict:
    fits = [fit(ids, points, KMeansParams(k=k, seed=s)) for s in SEEDS]
    pairs = [
        adjusted_rand(fits[i].assignments, fits[j].assignments)
        for i in range(len(fits))
        for j in range(i + 1, len(fits))
    ]
    return {
        "k": k,
        "mean": statistics.fmean(pairs),
        "lowest": min(pairs),
        "pairs": len(pairs),
        "singleton_fits": sum(1 for x in fits if min(x.cluster_sizes) == 1),
        "fits": len(fits),
    }


# ---------- 3. bootstrap ----------


def _jaccard(a: set[int], b: set[int]) -> float:
    return len(a & b) / len(a | b) if a | b else 1.0


def bootstrap_study(
    ids: Sequence[str],
    points: Sequence[Point],
    k: int,
    reference: KMeansFit | None = None,
    resamples: int = BOOTSTRAPS,
) -> dict:
    """Refit on resamples of the customers and compare, on every customer, the
    partition that fit's centroids give with the full-data partition."""
    reference = reference or fit(
        ids, points, KMeansParams(k=k, seed=REGISTERED["seed"])
    )
    ref_members = [
        {i for i, c in enumerate(reference.assignments) if c == cluster}
        for cluster in range(k)
    ]
    rng = random.Random(BOOTSTRAP_SEED)
    n = len(ids)
    aris: list[float] = []
    best: list[list[float]] = [[] for _ in range(k)]
    for _ in range(resamples):
        drawn = [rng.randrange(n) for _ in range(n)]
        # A customer can be drawn twice: the fit takes each draw under its own id.
        sample_ids = [f"{position:06d}" for position in range(n)]
        sample = fit(
            sample_ids,
            [points[i] for i in drawn],
            KMeansParams(k=k, seed=REGISTERED["seed"]),
        )
        partition = [nearest(p, sample.centroids) for p in points]
        aris.append(adjusted_rand(reference.assignments, partition))
        found = [
            {i for i, c in enumerate(partition) if c == cluster} for cluster in range(k)
        ]
        for index, ref in enumerate(ref_members):
            best[index].append(max(_jaccard(ref, members) for members in found))
    per_cluster = [statistics.fmean(values) for values in best]
    return {
        "k": k,
        "mean": statistics.fmean(aris),
        "lowest": min(aris),
        "resamples": resamples,
        "jaccard": per_cluster,
        "jaccard_lowest": min(per_cluster),
        "reference_sizes": [len(m) for m in ref_members],
    }


# ---------- 4. adjacent windows ----------


def _ranks(result: KMeansFit) -> dict[str, int]:
    """Each customer's rank of cluster, best first (RN-38), which does mean the same
    thing in two fits, unlike a cluster number."""
    position = {cluster: rank for rank, cluster in enumerate(rank_clusters(result))}
    return {
        who: position[cluster]
        for who, cluster in zip(result.customer_ids, result.assignments, strict=True)
    }


def compare_fits(first: KMeansFit, second: KMeansFit) -> dict:
    """How far apart two fits are, over the customers both have."""
    a, b = _ranks(first), _ranks(second)
    common = sorted(set(a) & set(b))
    index_a = {who: i for i, who in enumerate(first.customer_ids)}
    index_b = {who: i for i, who in enumerate(second.customer_ids)}
    order_a, order_b = rank_clusters(first), rank_clusters(second)
    moved = [
        math.dist(first.centroids[x], second.centroids[y])
        for x, y in zip(order_a, order_b, strict=True)
    ]
    return {
        "common": len(common),
        "ari": adjusted_rand(
            [first.assignments[index_a[who]] for who in common],
            [second.assignments[index_b[who]] for who in common],
        ),
        "rank_changed": sum(1 for who in common if a[who] != b[who]) / len(common),
        "centroid_mean": statistics.fmean(moved),
        "centroid_max": max(moved),
    }


def window_study(dataset: Dataset, k: int, window_days: int = 180) -> dict:
    """Fits over windows shifted back by `SHIFTS` days, adjacent ones compared, and
    the same window at other seeds as the movement that is only noise. One other
    seed is not a fair baseline: a single seed can land in a poor optimum, so the
    baseline is the mean over `NOISE_SEEDS` of them."""
    fits = []
    for offset in SHIFTS:
        ids, points = _points(dataset, offset, window_days)
        fits.append(fit(ids, points, KMeansParams(k=k, seed=REGISTERED["seed"])))
    ids, points = _points(dataset, 0, window_days)
    others = [
        compare_fits(fits[0], fit(ids, points, KMeansParams(k=k, seed=seed)))
        for seed in NOISE_SEEDS
    ]
    noise = {
        name: statistics.fmean(x[name] for x in others)
        for name in ("ari", "rank_changed", "centroid_mean")
    }
    adjacent = [compare_fits(a, b) for a, b in zip(fits, fits[1:], strict=False)]
    disjoint = compare_fits(fits[0], fits[-1])  # 0 to 180 days back: no shared sales
    return {
        "k": k,
        "adjacent": adjacent,
        "disjoint": disjoint,
        "noise": noise,
        "windows": len(fits),
    }


# ---------- reading the numbers ----------


def verdict(seed: dict, boot: dict) -> str:
    """One word per k, by the thresholds above and nothing else."""
    if (
        seed["mean"] < POOR_ARI
        or boot["mean"] < POOR_ARI
        or boot["jaccard_lowest"] < POOR_JACCARD
    ):
        return "poor"
    if (
        seed["mean"] >= STABLE_ARI
        and boot["mean"] >= STABLE_ARI
        and boot["jaccard_lowest"] >= STABLE_JACCARD
    ):
        return "stable"
    return "moderate"


# ---------- tables ----------


def reproduction_table(result: dict) -> str:
    fitted: KMeansFit = result["fit"]
    quality = result["record"]["quality"]
    rows = [
        ("registered k, seed", f"{fitted.params.k}, {fitted.params.seed}"),
        (
            "window, iteration limit, tolerance",
            f"{result['record']['window_days']} days, "
            f"{fitted.params.max_iterations}, {fitted.params.tolerance}",
        ),
        ("customers clustered", quality["customers_clustered"]),
        ("inertia", quality["inertia"]),
        ("silhouette (sampled)", quality["silhouette"]),
        ("cluster sizes", quality["cluster_sizes"]),
        ("iterations, converged", f"{quality['iterations']}, {quality['converged']}"),
        ("partition digest (SHA-256)", result["digest"]),
        (
            f"fits repeated from scratch: {result['repeats']}",
            (
                "identical"
                if not result["differences"]
                else "; ".join(result["differences"])
            ),
        ),
    ]
    return table(("Registered result", "Value"), rows)


def seed_table(seeds: list[dict], boots: list[dict]) -> str:
    rows = []
    for s, b in zip(seeds, boots, strict=True):
        rows.append(
            (
                s["k"],
                f"{s['mean']:.2f}",
                f"{s['lowest']:.2f}",
                f"{s['singleton_fits']} of {s['fits']}",
                f"{b['mean']:.2f}",
                f"{b['lowest']:.2f}",
                f"{b['jaccard_lowest']:.2f}",
                verdict(s, b),
            )
        )
    return table(
        (
            "k",
            f"Seed ARI, mean of {seeds[0]['pairs']} pairs",
            "Seed ARI, lowest",
            "Fits with a cluster of one",
            f"Bootstrap ARI, mean of {boots[0]['resamples']}",
            "Bootstrap ARI, lowest",
            "Weakest cluster (Jaccard)",
            "Verdict",
        ),
        rows,
    )


def jaccard_table(boots: list[dict]) -> str:
    rows = []
    for b in boots:
        rows.append(
            (
                b["k"],
                ", ".join(
                    f"{j:.2f} ({size})"
                    for j, size in zip(b["jaccard"], b["reference_sizes"], strict=True)
                ),
            )
        )
    return table(("k", "Jaccard of each cluster (its size in customers)"), rows)


def window_table(windows: list[dict]) -> str:
    rows = []
    for w in windows:
        adj = w["adjacent"]
        rows.append(
            (
                w["k"],
                f"{statistics.fmean(x['ari'] for x in adj):.2f}",
                f"{statistics.fmean(x['rank_changed'] for x in adj):.1%}",
                f"{statistics.fmean(x['centroid_mean'] for x in adj):.3f}",
                f"{w['disjoint']['ari']:.2f}",
                f"{w['disjoint']['rank_changed']:.1%}",
                f"{w['noise']['ari']:.2f}",
                f"{w['noise']['rank_changed']:.1%}",
            )
        )
    return table(
        (
            "k",
            f"ARI, {STEP_DAYS}-day steps (mean of {windows[0]['windows'] - 1})",
            "Customers changing rank",
            "Centroid movement (mean)",
            "ARI, disjoint windows",
            "Customers changing rank",
            f"ARI, other seeds only (mean of {len(NOISE_SEEDS)})",
            "Customers changing rank",
        ),
        rows,
    )


def report() -> str:
    dataset = generate()
    ids, points = _points(dataset, 0, REGISTERED["window_days"])
    seeds, boots, windows = [], [], []
    for k in K_RANGE:
        seeds.append(seed_study(ids, points, k))
        boots.append(bootstrap_study(ids, points, k))
        windows.append(window_study(dataset, k))
    return "\n".join(
        [
            f"fingerprint {dataset.fingerprint()}  dataset seed {DEFAULT_SEED}",
            f"customers clustered: {len(ids)}; silhouette is a seeded sample of "
            f"{kmeans.SILHOUETTE_SAMPLE_SIZE} "
            f"(limit {kmeans.SILHOUETTE_MAX_CUSTOMERS})",
            f"seeds {SEEDS[0]} to {SEEDS[-1]}; {BOOTSTRAPS} bootstrap resamples "
            f"(resampling seed {BOOTSTRAP_SEED}, fit seed {REGISTERED['seed']})\n",
            "## Registered result\n\n" + reproduction_table(reproduce()) + "\n",
            "## Seeds and bootstrap\n\n" + seed_table(seeds, boots) + "\n",
            "## Clusters found again\n\n" + jaccard_table(boots) + "\n",
            "## Adjacent windows\n\n" + window_table(windows) + "\n",
        ]
    )


def main(argv: Sequence[str] | None = None) -> int:
    del argv
    print(report())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
