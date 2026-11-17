"""RFM rules and K-means, compared on stated criteria (F23-06, #818).

    python -m analysis.method_comparison

Regenerates every table in `docs/evidence/f23-06-method-comparison.md` over the
evaluation dataset (F23-01). It reads no database and writes no file.

`web/services/model_comparison.py` says how far two runs agree and, by RN-39, nothing
about which is better; two methods can disagree on every customer and both be right.
This study is what decides, and it decides from three criteria whose definitions,
scores and margins are fixed in the constants below, before any result, and applied
by the same code to both methods on the same customers:

1. **Interpretability:** does each segment behave as its label says? The share of the
   label's claims (the measurable words of its description in the seed) that hold,
   the checks of F23-05 generalised to every label.
2. **Stability:** do the same customers keep their segment when something that should
   not matter changes? Mean adjusted Rand index (ARI) over bootstrap resamples of the
   customers and over windows shifted by 30 days, as in F23-04.
3. **Usefulness for decisions:** do the segments tell a marketer who will spend next?
   Segment the customers on an earlier window, then measure how much of their later
   spend the segment explains (adjusted R-squared of log spend), on two origins.

A difference between the methods counts only when it exceeds the criterion's margin,
and a method is favoured overall only when it wins two criteria and loses none.
Agreement between the methods is reported and is not an input to the verdict.

The RFM scoring is a Python replica of `web/db/segments.py` (`_SCORE_AND_MATCH`) over
the seed's bands, so the evaluation dataset, which lives in memory, can be scored by
both methods. A test checks it on hand-worked ties, and the evidence document records
that it was also checked against the production query on the loaded dataset.
"""

from __future__ import annotations

import functools
import math
import random
import re
import statistics
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from analysis.dataset import DEFAULT_SEED, Dataset, generate
from analysis.k_study import VOCABULARY
from analysis.normalization_study import Features, adjusted_rand, table
from analysis.segment_profile import (
    INACTIVE_DAYS,
    MOST,
    RECENT_DAYS,
    Check,
    Profile,
    _ever_before,
    _prior_window,
    profile_of,
)
from analysis.stability_study import BOOTSTRAP_SEED, BOOTSTRAPS, SHIFTS, seed_study
from analysis.stability_study import _points as window_points
from web.services import model_comparison
from web.services.cluster_labels import label_clusters
from web.services.kmeans import KMeansFit, KMeansParams, Point, fit, nearest

_DAY = 86400
WINDOW_DAYS = 180
QUINTILES = 5
SEED = 11  # the registered seed (F23-04)
K_CHOSEN = 4  # F23-03
K_SENSITIVITY = 6  # the one-to-one case ADR-0018 started from

_SEED_SQL = Path(__file__).resolve().parents[1] / "sql" / "02_seed_30_per_table.sql"

# ---------- the criteria, as fixed before the results ----------

# Usefulness is measured from two origins: customers are segmented on a window ending
# `offset` days back and judged on what they spend in the `horizon` days after it.
ORIGINS = ((180, 180), (90, 90))

# A score difference smaller than the margin is a tie. The margins are judgements, on
# each criterion's own scale, and the owner may move them; the verdict is also shown
# at half and at double each margin so a reader can see whether it depends on them.
MARGINS = {"interpretability": 0.20, "stability": 0.10, "usefulness": 0.05}
CRITERIA = tuple(MARGINS)

# F23-03's commercial limit: a segment under this share of customers is not one a
# team can act on. It is applied only as a sensitivity of interpretability, because
# the tables showed that a segment of a handful passes its claims trivially, and the
# criterion as fixed before the results counts every claim.
MIN_SHARE = 0.02


# ---------- raw windows ----------

Raw = dict[str, tuple[int, int, Decimal]]  # newest sale (s ago), sales, spend


def window_raw(dataset: Dataset, offset_days: int, window_days: int) -> Raw:
    """Each customer's last purchase, purchases and spend in the window that ends
    `offset_days` back. A customer with no sale in it is absent (RN-21)."""
    low, high = offset_days * _DAY, (offset_days + window_days) * _DAY
    newest: dict[str, int] = {}
    count: Counter[str] = Counter()
    total: dict[str, Decimal] = {}
    for sale in dataset.sales:
        if not low < sale.seconds_ago <= high:
            continue
        who = sale.customer_id
        newest[who] = min(newest.get(who, high), sale.seconds_ago)
        count[who] += 1
        total[who] = total.get(who, Decimal(0)) + sale.amount
    return {who: (newest[who], count[who], total[who]) for who in sorted(newest)}


# ---------- RFM rules: a replica of the production query ----------

Cut = list[tuple[int, object]]  # (score, lowest key that earns it), best first


@dataclass(frozen=True)
class Cuts:
    r: Cut
    f: Cut
    m: Cut


def _cut(keys: Sequence[object]) -> Cut:
    """`ntile(5)` over the DISTINCT values, best first, as the query does (#352): the
    first `n mod 5` groups hold one value more, and score = 6 minus the group."""
    distinct = sorted(set(keys), reverse=True)  # type: ignore[type-var]
    base, extra = divmod(len(distinct), QUINTILES)
    cut: Cut = []
    start = 0
    for group in range(QUINTILES):
        size = base + (1 if group < extra else 0)
        if size:
            cut.append((QUINTILES - group, distinct[start + size - 1]))
            start += size
    return cut


def _score(cut: Cut, key: object) -> int:
    """The score a value earns under a cut. A value the cut was not built from takes
    the best group whose floor it reaches, and the worst group below them all."""
    for score, floor in cut:
        if key >= floor:  # type: ignore[operator]
            return score
    return cut[-1][0]


def fit_cuts(raw: Raw) -> Cuts:
    """The quintile boundaries of a population: recency (a more recent last
    purchase is better), frequency and spend."""
    values = list(raw.values())
    return Cuts(
        r=_cut([-newest for newest, _, _ in values]),
        f=_cut([count for _, count, _ in values]),
        m=_cut([total for _, _, total in values]),
    )


@functools.cache
def rules() -> tuple[tuple[int, tuple[int, ...], str], ...]:
    """The 30 bands of the seed: (rule id, r/f/m minimums and maximums, label). The
    label of rule n is the vocabulary's position (n - 1) mod 6 (the seed's own
    expression), so this reads the bands from the file and never copies them."""
    text = _SEED_SQL.read_text()
    block = text[text.index("INSERT INTO segment_rule") :]
    block = block[: block.index(";")]
    found = re.findall(r"\(\s*(\d+),\s*'RULE_\d+',((?:\s*\d,){5}\s*\d)\)", block)
    out = []
    for rule_id, bounds in found:
        number = int(rule_id)
        out.append(
            (
                number,
                tuple(int(x) for x in bounds.split(",")),
                VOCABULARY[(number - 1) % 6],
            )
        )
    return tuple(sorted(out))


def band_label(r: int, f: int, m: int) -> str:
    """The label of the lowest rule whose band holds the triple, else the worst label
    (the query's own fallback, which a seeded run never reaches)."""
    for _, (r0, r1, f0, f1, m0, m1), label in rules():
        if r0 <= r <= r1 and f0 <= f <= f1 and m0 <= m <= m1:
            return label
    return VOCABULARY[-1]


def rfm_labels(raw: Raw, cuts: Cuts | None = None) -> dict[str, str]:
    """Every customer's RFM_RULES label. Scored under `cuts` when given, which is how
    a bootstrap resample's boundaries are applied to the whole population."""
    cuts = cuts or fit_cuts(raw)
    return {
        who: band_label(
            _score(cuts.r, -newest), _score(cuts.f, count), _score(cuts.m, total)
        )
        for who, (newest, count, total) in raw.items()
    }


# ---------- K-means, labelled as production labels it ----------


def kmeans_fit(ids: Sequence[str], points: Sequence[Point], k: int) -> KMeansFit:
    return fit(ids, points, KMeansParams(k=k, seed=SEED))


def kmeans_labels(result: KMeansFit) -> dict[str, str]:
    """Each customer's label: the cluster's, by ADR-0030's proportional rank."""
    by_cluster = label_clusters(result, VOCABULARY)
    return {
        who: by_cluster[cluster]
        for who, cluster in zip(result.customer_ids, result.assignments, strict=True)
    }


# ---------- the two methods behind one interface ----------


class Method:
    """What the criteria need of a method, and nothing about how it works: the labels
    it gives the customers of a window, and the labels a resample of them gives to
    everyone. Both methods are scored by the same code through this."""

    name: str

    def __init__(self, dataset: Dataset) -> None:
        self.dataset = dataset
        self._labels: dict[int, dict[str, str]] = {}

    def window(self, offset: int) -> dict[str, str]:
        if offset not in self._labels:
            self._labels[offset] = self._label_window(offset)
        return self._labels[offset]

    def _label_window(self, offset: int) -> dict[str, str]:
        raise NotImplementedError

    def resampled(self, drawn: Sequence[int]) -> dict[str, str]:
        raise NotImplementedError


class RfmRules(Method):
    name = "RFM rules"

    def _label_window(self, offset: int) -> dict[str, str]:
        return rfm_labels(window_raw(self.dataset, offset, WINDOW_DAYS))

    def resampled(self, drawn: Sequence[int]) -> dict[str, str]:
        raw = window_raw(self.dataset, 0, WINDOW_DAYS)
        ids = list(raw)
        # Each draw under its own key, so a customer drawn twice counts twice.
        sample = {f"{n:06d}": raw[ids[i]] for n, i in enumerate(drawn)}
        return rfm_labels(raw, fit_cuts(sample))


class KMeans(Method):
    def __init__(self, dataset: Dataset, k: int) -> None:
        super().__init__(dataset)
        self.k = k
        self.name = f"K-means, k = {k}"

    def _label_window(self, offset: int) -> dict[str, str]:
        ids, points = window_points(self.dataset, offset, WINDOW_DAYS)
        return kmeans_labels(kmeans_fit(ids, points, self.k))

    def resampled(self, drawn: Sequence[int]) -> dict[str, str]:
        ids, points = window_points(self.dataset, 0, WINDOW_DAYS)
        sample = kmeans_fit(
            [f"{n:06d}" for n in range(len(drawn))], [points[i] for i in drawn], self.k
        )
        by_cluster = label_clusters(sample, VOCABULARY)
        return {
            who: by_cluster[nearest(point, sample.centroids)]
            for who, point in zip(ids, points, strict=True)
        }


# ---------- 1. interpretability ----------


@dataclass(frozen=True)
class Overall:
    """What every clustered customer looks like together, the reference a label's
    words such as "recent" and "often" are read against."""

    median_recency: float
    median_frequency: float
    median_monetary: float
    prior_frequency: float


def profiles_of(
    dataset: Dataset, labels: Mapping[str, str]
) -> tuple[list[Profile], Overall]:
    """One profile per populated label, best first, over the current window."""
    features = Features(dataset, WINDOW_DAYS)
    prior = _prior_window(dataset, WINDOW_DAYS)
    before = _ever_before(dataset, WINDOW_DAYS)
    total = sum(features.monetary)
    found = []
    for label in VOCABULARY:
        at = [i for i, who in enumerate(features.ids) if labels[who] == label]
        if at:
            found.append(
                profile_of(label, len(found), at, features, prior, before, total)
            )
    overall = Overall(
        statistics.median(features.recency_days),
        statistics.median(features.frequency),
        statistics.median(features.monetary),
        statistics.fmean(prior[who] for who in features.ids),
    )
    return found, overall


def claims(found: Sequence[Profile], overall: Overall) -> list[Check]:
    """Each populated label's measurable words, put to what its customers do. The
    rules, the limits (30 and 90 days, 80%) and the wording are F23-05's; a label's
    words the model cannot see ("consistent spend") are not claims here."""
    by = {p.label: p for p in found}
    rows: list[Check] = []

    def add(label: str, claim: str, measured: str, holds: bool) -> None:
        rows.append(Check(label, claim, measured, holds))

    if p := by.get("CHAMPION"):
        top = (
            p.recency_days == min(x.recency_days for x in found)
            and p.frequency == max(x.frequency for x in found)
            and p.monetary == max(x.monetary for x in found)
        )
        add(
            "CHAMPION",
            "highest recency, frequency and monetary value",
            f"R {p.recency_days:.0f} d, F {p.frequency:.1f}, M {p.monetary:,.0f}",
            top,
        )
    if p := by.get("LOYAL"):
        add(
            "LOYAL",
            "buys often",
            f"F {p.frequency:.1f} against a median of {overall.median_frequency:.0f}",
            p.frequency > overall.median_frequency,
        )
        add(
            "LOYAL",
            "buys recently",
            f"R {p.recency_days:.0f} d against a median of "
            f"{overall.median_recency:.0f}",
            p.recency_days < overall.median_recency,
        )
    if p := by.get("POTENTIAL"):
        add(
            "POTENTIAL",
            "recent",
            f"{p.bought_recently:.1%} bought in the last {RECENT_DAYS} days",
            p.bought_recently >= MOST,
        )
        best = max(x.frequency for x in found)
        add(
            "POTENTIAL",
            "room to grow frequency",
            f"F {p.frequency:.1f} against {best:.1f} for the best segment",
            p.frequency < best,
        )
        add(
            "POTENTIAL",
            "a customer who is recent (new)",
            f"{p.new_customers:.1%} had no purchase before the window",
            p.new_customers >= MOST,
        )
    if p := by.get("AT_RISK"):
        add(
            "AT_RISK",
            "used to buy often",
            f"{p.prior_frequency:.1f} purchases in the 180 days before the window, "
            f"against {overall.prior_frequency:.1f} for all customers",
            p.prior_frequency > overall.prior_frequency,
        )
        add(
            "AT_RISK",
            "recency has slipped",
            f"R {p.recency_days:.0f} d against a median of "
            f"{overall.median_recency:.0f}; F {p.frequency:.1f} now, "
            f"{p.prior_frequency:.1f} before",
            p.recency_days > overall.median_recency and p.frequency < p.prior_frequency,
        )
    if p := by.get("HIBERNATING"):
        add(
            "HIBERNATING",
            "low recency",
            f"R {p.recency_days:.0f} d against a median of "
            f"{overall.median_recency:.0f}",
            p.recency_days > overall.median_recency,
        )
        add(
            "HIBERNATING",
            "low frequency and monetary value",
            f"F {p.frequency:.1f} (median {overall.median_frequency:.0f}), "
            f"M {p.monetary:,.0f} (median {overall.median_monetary:,.0f})",
            p.frequency <= overall.median_frequency
            and p.monetary <= overall.median_monetary,
        )
    if p := by.get("LOST"):
        add(
            "LOST",
            "no recent activity",
            f"{p.bought_lately:.1%} bought in the last {INACTIVE_DAYS} days; "
            f"R {p.recency_days:.0f} d",
            p.bought_lately < 1 - MOST,
        )
        add(
            "LOST",
            "across all three measures",
            f"F {p.frequency:.1f} and M {p.monetary:,.0f}, the lowest of the segments",
            p.frequency == min(x.frequency for x in found)
            and p.monetary == min(x.monetary for x in found),
        )
    return rows


def interpretability(dataset: Dataset, method: Method) -> dict:
    found, overall = profiles_of(dataset, method.window(0))
    rows = claims(found, overall)
    held = sum(c.holds for c in rows)
    share = {p.label: p.customer_share for p in found}
    sized = [c for c in rows if share[c.label] >= MIN_SHARE]
    return {
        "profiles": found,
        "claims": rows,
        "held": held,
        "score": held / len(rows),
        "labels": len(found),
        "small": [p.label for p in found if p.customer_share < MIN_SHARE],
        "sized_score": sum(c.holds for c in sized) / len(sized),
        "sized_claims": len(sized),
    }


# ---------- 2. stability ----------


def _keep(a: Mapping[str, str], b: Mapping[str, str]) -> tuple[float, float, int]:
    """The ARI between two labellings and the share of customers who kept their
    label, over the customers both have."""
    common = sorted(set(a) & set(b))
    first = [a[who] for who in common]
    second = [b[who] for who in common]
    kept = sum(x == y for x, y in zip(first, second, strict=True)) / len(common)
    return adjusted_rand(first, second), kept, len(common)


def draws(n: int, resamples: int = BOOTSTRAPS) -> list[list[int]]:
    """The resamples, drawn once and given to every method, so the two are shaken by
    the same ones."""
    rng = random.Random(BOOTSTRAP_SEED)
    return [[rng.randrange(n) for _ in range(n)] for _ in range(resamples)]


def stability(method: Method, drawn: Sequence[Sequence[int]]) -> dict:
    reference = method.window(0)
    bootstrap = [adjusted_rand_labels(reference, method.resampled(d)) for d in drawn]
    steps = [
        _keep(method.window(a), method.window(b))
        for a, b in zip(SHIFTS, SHIFTS[1:], strict=False)
    ]
    boot_mean = statistics.fmean(bootstrap)
    window_mean = statistics.fmean(ari for ari, _, _ in steps)
    return {
        "bootstrap_mean": boot_mean,
        "bootstrap_lowest": min(bootstrap),
        "window_mean": window_mean,
        "window_lowest": min(ari for ari, _, _ in steps),
        "window_kept": statistics.fmean(kept for _, kept, _ in steps),
        "score": (boot_mean + window_mean) / 2,
    }


def adjusted_rand_labels(a: Mapping[str, str], b: Mapping[str, str]) -> float:
    return _keep(a, b)[0]


def seed_mean(dataset: Dataset, k: int) -> float:
    """F23-04's seed measure, for the K-means rows only."""
    ids, points = window_points(dataset, 0, WINDOW_DAYS)
    return seed_study(ids, points, k)["mean"]


# ---------- 3. usefulness for decisions ----------


def later_spend(dataset: Dataset, horizon_days: int) -> dict[str, float]:
    """Each customer's spend in the `horizon_days` before today, 0 if none."""
    limit = horizon_days * _DAY
    spend: dict[str, Decimal] = {}
    for sale in dataset.sales:
        if sale.seconds_ago <= limit:
            spend[sale.customer_id] = (
                spend.get(sale.customer_id, Decimal(0)) + sale.amount
            )
    return {who: float(total) for who, total in spend.items()}


def adjusted_r2(groups: Mapping[str, Sequence[float]]) -> float:
    """How much of the variance of a value the groups explain, one-way, adjusted for
    how many groups there are so that more segments are not rewarded for being more."""
    values = [v for g in groups.values() for v in g]
    n, k = len(values), len(groups)
    grand = statistics.fmean(values)
    total = sum((v - grand) ** 2 for v in values)
    if total == 0 or n <= k:
        return 0.0
    between = sum(len(g) * (statistics.fmean(g) - grand) ** 2 for g in groups.values())
    return 1 - (1 - between / total) * (n - 1) / (n - k)


def usefulness(dataset: Dataset, method: Method) -> dict:
    rows = []
    for offset, horizon in ORIGINS:
        labels = method.window(offset)
        spend = later_spend(dataset, horizon)
        by_label: dict[str, list[float]] = {}
        again: dict[str, list[float]] = {}
        for who, label in labels.items():
            by_label.setdefault(label, []).append(math.log1p(spend.get(who, 0.0)))
            again.setdefault(label, []).append(1.0 if spend.get(who, 0.0) > 0 else 0.0)
        raw: dict[str, list[float]] = {}
        for who, label in labels.items():
            raw.setdefault(label, []).append(spend.get(who, 0.0))
        rows.append(
            {
                "offset": offset,
                "horizon": horizon,
                "customers": len(labels),
                "r2": adjusted_r2(by_label),
                "r2_raw": adjusted_r2(raw),
                "again": {
                    label: statistics.fmean(v)
                    for label, v in sorted(
                        again.items(), key=lambda kv: VOCABULARY.index(kv[0])
                    )
                },
                "sizes": {label: len(v) for label, v in again.items()},
            }
        )
    return {"origins": rows, "score": statistics.fmean(r["r2"] for r in rows)}


# ---------- the verdict, from the criteria and nothing else ----------


def result(first: float, second: float, margin: float) -> str:
    """`first` or `second` when it leads by at least the margin, else `tie`."""
    if first - second >= margin:
        return "first"
    if second - first >= margin:
        return "second"
    return "tie"


def verdict(
    first: Mapping[str, float], second: Mapping[str, float], scale: float = 1.0
):
    """Per criterion who leads, and overall: a method is favoured only when it wins
    at least two criteria and loses none. Anything else favours neither."""
    per = {c: result(first[c], second[c], MARGINS[c] * scale) for c in CRITERIA}
    wins_first = sum(v == "first" for v in per.values())
    wins_second = sum(v == "second" for v in per.values())
    if wins_first >= 2 and wins_second == 0:
        overall = "first"
    elif wins_second >= 2 and wins_first == 0:
        overall = "second"
    else:
        overall = "neither"
    return per, overall


# ---------- context that is not in the verdict ----------


def agreement(dataset: Dataset, first: Method, second: Method) -> dict:
    """What `model_comparison` reports for the two methods over the same customers:
    agreement and the cross-tabulation. It says how different the groups are, which
    is not a reason to prefer one of them (RN-39)."""
    ordinals = {label: n for n, label in enumerate(VOCABULARY, start=1)}
    a, b = first.window(0), second.window(0)
    comparison = model_comparison.compare_runs(
        [(who, who, label) for who, label in a.items()],
        [(who, who, label) for who, label in b.items()],
        ordinals,
        first_run_id=1,
        second_run_id=2,
    )
    common = sorted(a)
    return {
        "comparison": comparison,
        "ari": adjusted_rand([a[w] for w in common], [b[w] for w in common]),
    }


def against_planted(dataset: Dataset, method: Method) -> float:
    """The ARI of a method's labels with the groups the generator planted. Only
    synthetic data has them, so it is context and never a criterion."""
    group = {c.customer_id: c.group for c in dataset.customers}
    labels = method.window(0)
    ids = sorted(labels)
    return adjusted_rand([labels[w] for w in ids], [group[w] for w in ids])


# ---------- tables ----------


def claims_table(rows: Sequence[Check]) -> str:
    return table(
        ("Label", "What its description claims", "What the segment measures", "Holds"),
        [(c.label, c.claim, c.measured, "yes" if c.holds else "**no**") for c in rows],
    )


def populations_table(
    methods: Sequence[Method], found: Sequence[Sequence[Profile]]
) -> str:
    counts = [{p.label: p for p in f} for f in found]
    return table(
        ("Label", *(f"{m.name}: customers (of all, of revenue)" for m in methods)),
        [
            (
                label,
                *(
                    (
                        f"{c[label].customers:,} ({c[label].customer_share:.1%}, "
                        f"{c[label].revenue_share:.1%})"
                        if label in c
                        else "none"
                    )
                    for c in counts
                ),
            )
            for label in VOCABULARY
        ],
    )


def stability_table(methods: Sequence[Method], found: Sequence[dict]) -> str:
    return table(
        (
            "Method",
            f"Bootstrap ARI, mean of {BOOTSTRAPS}",
            "Bootstrap ARI, lowest",
            f"Window ARI, mean of {len(SHIFTS) - 1} 30-day steps",
            "Window ARI, lowest",
            "Customers keeping their label per step",
            "Stability score",
        ),
        [
            (
                m.name,
                f"{s['bootstrap_mean']:.2f}",
                f"{s['bootstrap_lowest']:.2f}",
                f"{s['window_mean']:.2f}",
                f"{s['window_lowest']:.2f}",
                f"{s['window_kept']:.1%}",
                f"{s['score']:.2f}",
            )
            for m, s in zip(methods, found, strict=True)
        ],
    )


def usefulness_table(methods: Sequence[Method], found: Sequence[dict]) -> str:
    rows = []
    for m, u in zip(methods, found, strict=True):
        for o in u["origins"]:
            rows.append(
                (
                    m.name,
                    f"{o['offset']} d back, {o['horizon']} d ahead",
                    f"{o['customers']:,}",
                    f"{o['r2']:.3f}",
                    f"{o['r2_raw']:.3f}",
                )
            )
        rows.append(
            (
                m.name,
                "usefulness score (mean of the log-spend column)",
                "",
                f"{u['score']:.3f}",
                "",
            )
        )
    return table(
        (
            "Method",
            "Origin",
            "Customers",
            "Adjusted R-squared, log spend (scored)",
            "Adjusted R-squared, raw spend (not scored)",
        ),
        rows,
    )


def again_table(methods: Sequence[Method], found: Sequence[dict]) -> str:
    out = []
    for m, u in zip(methods, found, strict=True):
        first = u["origins"][0]
        out.append(
            (
                m.name,
                ", ".join(
                    f"{label} {share:.0%} ({first['sizes'][label]:,})"
                    for label, share in first["again"].items()
                ),
            )
        )
    return table(
        ("Method", "Share who buy again in the 180 days after, by label (customers)"),
        out,
    )


def verdict_table(
    names: tuple[str, str], first: Mapping[str, float], second: Mapping[str, float]
) -> str:
    name = {"first": names[0], "second": names[1], "tie": "tie"}
    rows = []
    for c in CRITERIA:
        rows.append(
            (
                c,
                f"{first[c]:.3f}",
                f"{second[c]:.3f}",
                f"{abs(first[c] - second[c]):.3f}",
                f"{MARGINS[c]:.2f}",
                name[result(first[c], second[c], MARGINS[c])],
            )
        )
    return table(("Criterion", *names, "Difference", "Margin", "Leads"), rows)


def agreement_table(a: dict, first: Method, second: Method) -> str:
    c = a["comparison"]
    columns = [x for x in c.column_labels if any(c.cells[row][x] for row in c.cells)]
    rows = []
    for label in VOCABULARY:
        if label in c.cells and any(c.cells[label].values()):
            rows.append((label, *(c.cells[label][x] for x in columns)))
    return table((f"{first.name} (rows) by {second.name} (columns)", *columns), rows)


# ---------- the report ----------


def report() -> str:
    dataset = generate()
    methods: list[Method] = [
        RfmRules(dataset),
        KMeans(dataset, K_CHOSEN),
        KMeans(dataset, K_SENSITIVITY),
    ]
    drawn = draws(len(window_raw(dataset, 0, WINDOW_DAYS)))
    interp = [interpretability(dataset, m) for m in methods]
    stable = [stability(m, drawn) for m in methods]
    useful = [usefulness(dataset, m) for m in methods]
    scores = [
        {
            "interpretability": i["score"],
            "stability": s["score"],
            "usefulness": u["score"],
        }
        for i, s, u in zip(interp, stable, useful, strict=True)
    ]

    out = [
        f"fingerprint {dataset.fingerprint()}  dataset seed {DEFAULT_SEED}",
        f"{len(methods[0].window(0)):,} customers with a sale in the window, labelled "
        "by every method; windows of 180 days\n",
        "## The two methods on the current window\n",
        populations_table(methods, [i["profiles"] for i in interp]) + "\n",
        "## 1. Interpretability: does each segment behave as its label says\n",
    ]
    for m, i in zip(methods, interp, strict=True):
        out += [
            f"### {m.name}: {i['held']} of {len(i['claims'])} claims hold "
            f"({i['score']:.2f}), {i['labels']} labels used\n",
            claims_table(i["claims"]) + "\n",
        ]
    out += [
        "## 2. Stability: do the same customers keep their segment\n",
        stability_table(methods, stable) + "\n",
        "Seeds, which only K-means has (RFM rules take none and give the same labels "
        "every time): mean pairwise ARI over 20 seeds "
        + ", ".join(
            f"k = {k}: {seed_mean(dataset, k):.2f}" for k in (K_CHOSEN, K_SENSITIVITY)
        )
        + ".\n",
        "## 3. Usefulness: do the segments say who will spend next\n",
        usefulness_table(methods, useful) + "\n",
        again_table(methods, useful) + "\n",
        "## Verdict, from the criteria\n",
    ]
    for second in (1, 2):
        names = (methods[0].name, methods[second].name)
        per, overall = verdict(scores[0], scores[second])
        out += [
            f"### {names[0]} against {names[1]}\n",
            verdict_table(names, scores[0], scores[second]) + "\n",
            f"Wins: {names[0]} {sum(v == 'first' for v in per.values())}, "
            f"{names[1]} {sum(v == 'second' for v in per.values())}, "
            f"ties {sum(v == 'tie' for v in per.values())}. Overall: "
            + {
                "first": f"favours {names[0]}",
                "second": f"favours {names[1]}",
                "neither": "favours neither",
            }[overall]
            + ". At half the margins: "
            + verdict(scores[0], scores[second], 0.5)[1]
            + "; at double: "
            + verdict(scores[0], scores[second], 2.0)[1]
            + ".\n",
        ]
    small = {m.name: i["small"] for m, i in zip(methods, interp, strict=True)}
    out += [
        "### Sensitivity: only labels held by at least "
        f"{MIN_SHARE:.0%} of customers\n",
        table(
            ("Method", "Labels under the limit", "Claims counted", "Interpretability"),
            [
                (
                    m.name,
                    ", ".join(small[m.name]) or "none",
                    i["sized_claims"],
                    f"{i['sized_score']:.3f}",
                )
                for m, i in zip(methods, interp, strict=True)
            ],
        )
        + "\n",
    ]
    for second in (1, 2):
        alt = [dict(scores[0]), dict(scores[second])]
        alt[0]["interpretability"] = interp[0]["sized_score"]
        alt[1]["interpretability"] = interp[second]["sized_score"]
        per, overall = verdict(alt[0], alt[1])
        out.append(
            f"With that count, {methods[0].name} against {methods[second].name}: "
            f"interpretability {per['interpretability']}, overall {overall}.\n"
        )
    ag = agreement(dataset, methods[0], methods[1])
    c = ag["comparison"]
    out += [
        "## Context that is not in the verdict\n",
        f"Agreement between {methods[0].name} and {methods[1].name} "
        f"(`model_comparison`): {c.agree:,} of {c.compared:,} customers share a label "
        f"({c.agreement_rate:.1%}); ARI between the two partitions {ag['ari']:.2f}.\n",
        agreement_table(ag, methods[0], methods[1]) + "\n",
        "Recovery of the planted groups (synthetic only), ARI: "
        + ", ".join(f"{m.name} {against_planted(dataset, m):.2f}" for m in methods)
        + ".\n",
    ]
    return "\n".join(out)


def main(argv: Sequence[str] | None = None) -> int:
    del argv
    print(report())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
