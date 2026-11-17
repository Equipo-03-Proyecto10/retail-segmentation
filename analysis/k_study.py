"""Choosing k (F23-03, #815).

    python -m analysis.k_study

Fits K-means over the evaluation dataset (F23-01) for a range of k, with the
production fit and production normalization (min-max, R reversed), and prints the
tables in `docs/evidence/f23-03-choosing-k.md`. It reads no database and writes no
file. There is no SQL in this module. Past `SILHOUETTE_MAX_CUSTOMERS` the fit's
silhouette is a seeded sample, and every table says so.
"""

from __future__ import annotations

import statistics
import sys
from collections import Counter
from collections.abc import Sequence

from analysis.dataset import DEFAULT_SEED, generate
from analysis.normalization_study import Features, adjusted_rand, n_minmax, table
from web.services import kmeans
from web.services.cluster_labels import describe_mapping
from web.services.kmeans import KMeansParams, fit

K_RANGE = range(2, 13)
SEEDS = (11, 12, 13, 14, 15)
# The seeded vocabulary, best to worst (sql/02_seed_30_per_table.sql).
VOCABULARY = ("CHAMPION", "LOYAL", "POTENTIAL", "AT_RISK", "HIBERNATING", "LOST")
# Commercial limits on the number of segments (stated, not measured): a team can run a
# distinct treatment for about this many, and a segment under the share below is too
# small to hold an experiment arm or justify a campaign.
MIN_SEGMENTS = 3
MAX_SEGMENTS = 8
MIN_SHARE = 0.02


def study(f: Features, points: Sequence[kmeans.Point]) -> list[dict]:
    rows = []
    for k in K_RANGE:
        fits = [fit(f.ids, points, KMeansParams(k=k, seed=s)) for s in SEEDS]
        main = fits[0]
        rows.append(
            {
                "k": k,
                "inertia": main.inertia,
                "silhouette": main.silhouette,
                "sample": main.silhouette_sample,
                "silhouette_mean": statistics.fmean(x.silhouette for x in fits),
                "smallest": min(main.cluster_sizes) / len(f.ids),
                "smallest_n": min(main.cluster_sizes),
                "stability": statistics.fmean(
                    adjusted_rand(fits[i].assignments, fits[j].assignments)
                    for i in range(len(fits))
                    for j in range(i + 1, len(fits))
                ),
                "ari_planted": adjusted_rand(main.assignments, f.group),
                "mapping": describe_mapping(k, VOCABULARY),
            }
        )
    return rows


def _labels(mapping: dict) -> str:
    counts = Counter(mapping["labels_by_rank"])
    parts = []
    for label in VOCABULARY:
        if counts[label]:
            suffix = f" x{counts[label]}" if counts[label] > 1 else ""
            parts.append(f"{label}{suffix}")
    return ", ".join(parts)


def k_table(rows: list[dict]) -> str:
    header = (
        "k",
        "Inertia",
        "Silhouette (sampled)",
        "Silhouette, mean of 5 seeds",
        "Smallest cluster",
        "Seed stability (ARI)",
        "ARI vs planted",
        "Within commercial limits",
    )
    body = []
    for r in rows:
        ok = MIN_SEGMENTS <= r["k"] <= MAX_SEGMENTS and r["smallest"] >= MIN_SHARE
        body.append(
            (
                r["k"],
                f"{r['inertia']:.1f}",
                f"{r['silhouette']:.3f}",
                f"{r['silhouette_mean']:.3f}",
                f"{r['smallest_n']} ({r['smallest']:.1%})",
                f"{r['stability']:.2f}",
                f"{r['ari_planted']:.2f}",
                "yes" if ok else "no",
            )
        )
    return table(header, body)


def label_table(rows: list[dict]) -> str:
    body = []
    for r in rows:
        m = r["mapping"]
        body.append(
            (
                r["k"],
                _labels(m),
                ", ".join(m["labels_without_cluster"]) or "none",
                ", ".join(m["shared_labels"]) or "none",
            )
        )
    return table(("k", "Labels produced", "Labels no cluster takes", "Shared"), body)


def report() -> str:
    dataset = generate()
    f = Features(dataset)
    rows = study(f, n_minmax(f))
    return "\n".join(
        [
            f"fingerprint {dataset.fingerprint()}  dataset seed {DEFAULT_SEED}",
            f"customers clustered: {len(f.ids)}; silhouette is a seeded sample of "
            f"{kmeans.SILHOUETTE_SAMPLE_SIZE} "
            f"(limit {kmeans.SILHOUETTE_MAX_CUSTOMERS}); fit seeds {SEEDS}\n",
            "## k\n\n" + k_table(rows) + "\n",
            "## Labels\n\n" + label_table(rows) + "\n",
        ]
    )


def main(argv: Sequence[str] | None = None) -> int:
    del argv
    print(report())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
