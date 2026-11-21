"""The port held to the web's captured outcomes (ADR-0045, E-17).

tests/golden/model-registry.json is what the web's K-means (`fit_customers`) and its
pairing
of clusters with labels (`label_clusters`) returned on each case, as captured by
`python -m tests.golden.capture --service model-registry`. Each case is replayed here
through
the registry's own code, which shares none of the web's, and has to give the same
partition,
centroids, measures and labels. The registry imports nothing from the web, and neither
does
this file.

The assignments, counts, flags and labels must be equal; a float must agree to a
relative
1e-12, so a difference in the platform's `math` library cannot fail a port that is
right.
"""

from __future__ import annotations

import hashlib
import json
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from app import kmeans, labels

FIXTURE = json.loads((Path(__file__).parent / "golden/model-registry.json").read_text())
CASES = {case["id"]: case for case in FIXTURE["cases"]}
REFERENCE = datetime(2026, 10, 1, tzinfo=UTC)
VOCABULARY = ["CHAMPION", "LOYAL", "POTENTIAL", "AT_RISK", "HIBERNATING", "LOST"]
FLOAT = pytest.approx


def synthetic(n: int, seed: int) -> list[list[Any]]:
    """The recipe the fixture names: `Random(seed)`, and for each customer in turn
    `randrange(0, 4320)` hours, `choice` of the frequency pool and `randrange(500,
    900000)`
    cents."""
    rng = random.Random(seed)
    pool = [1, 1, 1, 2, 3, 5, 8, 13, 30]
    rows = []
    for index in range(n):
        hours = rng.randrange(0, 24 * 180)
        frequency = rng.choice(pool)
        cents = rng.randrange(500, 900000)
        rows.append(
            [f"c{index:05d}", hours, frequency, f"{cents // 100}.{cents % 100:02d}"]
        )
    return rows


def sales(rows: list[list[Any]]) -> list[kmeans.Sales]:
    return [
        kmeans.Sales(cid, REFERENCE - timedelta(hours=hours), frequency, Decimal(money))
        for cid, hours, frequency, money in rows
    ]


def customers_of(given: dict[str, Any]) -> list[list[Any]]:
    if "synthetic" in given:
        spec = given["synthetic"]
        return synthetic(spec["customers"], spec["seed"])
    return given["customers"]


def close(expected: Any, actual: Any, where: str) -> None:
    """Equal, floats to a relative 1e-12, and the same shape."""
    if isinstance(expected, float):
        assert actual == FLOAT(expected, rel=1e-12, abs=1e-12), where
    elif isinstance(expected, list):
        assert isinstance(actual, list) and len(actual) == len(expected), where
        for index, (left, right) in enumerate(zip(expected, actual, strict=True)):
            close(left, right, f"{where}[{index}]")
    elif isinstance(expected, dict):
        assert isinstance(actual, dict) and expected.keys() == actual.keys(), where
        for key in expected:
            close(expected[key], actual[key], f"{where}.{key}")
    else:
        assert actual == expected, where


def observed(case: dict[str, Any]) -> dict[str, Any]:
    given = case["input"]
    rows = customers_of(given)
    dataset = kmeans.prepare(sales(rows))
    try:
        fitted = kmeans.fit(
            dataset,
            k=given["k"],
            seed=given["seed"],
            max_iterations=given["max_iterations"],
            tolerance=given["tolerance"],
        )
    except kmeans.TooFewCustomers:
        return {"outcome": "refused", "error": "TooFewCustomers"}
    names = labels.label_clusters(fitted, VOCABULARY)
    result: dict[str, Any] = {
        "customer_ids": list(fitted.customer_ids),
        "assignments": list(fitted.assignments),
        "centroids": [list(c) for c in fitted.centroids],
        "iterations": fitted.iterations,
        "converged": fitted.converged,
        "stopped_on": fitted.stopped_on,
        "final_shift": fitted.final_shift,
        "empty_cluster_events": fitted.empty_cluster_events,
        "inertia": fitted.inertia,
        "silhouette": fitted.silhouette,
        "silhouette_sample": fitted.silhouette_sample,
        "cluster_sizes": sorted(fitted.sizes, reverse=True),
        "ranked_clusters": labels.rank(fitted),
        "label_by_cluster": {str(c): names[c] for c in sorted(names)},
    }
    expected = case["outcome"].get("observed", {})
    if "points" in expected:
        result["points"] = [list(p) for p in dataset.points]
    if "assignments_sha256" in expected:
        lines = "".join(
            f"{cid}:{cluster}\n"
            for cid, cluster in zip(
                result.pop("customer_ids"), result.pop("assignments"), strict=True
            )
        )
        result["assignments_sha256"] = hashlib.sha256(lines.encode()).hexdigest()
    return {"outcome": "accepted", "observed": result}


FITS = [
    case_id
    for case_id, case in CASES.items()
    if case["input"]["call"] == "fit_customers"
]


@pytest.mark.parametrize("case_id", FITS)
def test_a_fit_is_the_webs(case_id: str) -> None:
    case = CASES[case_id]
    close(case["outcome"], observed(case), case_id)


@pytest.mark.parametrize(
    "case_id", [i for i, c in CASES.items() if c["input"]["call"] == "normalise"]
)
def test_the_normalization_is_the_webs(case_id: str) -> None:
    case = CASES[case_id]
    points = [list(p) for p in kmeans.prepare(sales(case["input"]["customers"])).points]
    close(case["outcome"]["observed"]["points"], points, case_id)


def test_the_ranking_of_tied_clusters_is_the_webs() -> None:
    """Sums that are equal only when taken exactly, then recency, then the smallest
    id."""
    case = CASES["rank-ties-on-the-sum-fall-through-to-recency-then-customer-id"]
    centroids = [tuple(c) for c in case["input"]["centroids"]]
    owner = {m: c for c, group in enumerate(case["input"]["members"]) for m in group}
    ordered = sorted(owner)
    fitted = kmeans.Fit(
        customer_ids=tuple(ordered),
        assignments=tuple(owner[m] for m in ordered),
        centroids=tuple(centroids),
        iterations=1,
        converged=True,
        stopped_on="tolerance",
        final_shift=0.0,
        empty_cluster_events=0,
        inertia=0.0,
        silhouette=None,
        silhouette_sample=None,
        sizes=(1,) * len(centroids),
    )
    names = labels.label_clusters(fitted, VOCABULARY)
    expected = case["outcome"]["observed"]
    assert labels.rank(fitted) == expected["ranked_clusters"]
    assert {str(c): names[c] for c in sorted(names)} == expected["label_by_cluster"]


def test_the_pairing_of_clusters_with_labels_is_the_webs() -> None:
    expected = CASES["label-pairing-by-proportional-rank"]["outcome"]["observed"]
    assert {
        str(k): labels.labels_by_position(k, VOCABULARY) for k in range(2, 9)
    } == expected["by_position"]


def test_the_fixture_has_the_cases_that_matter() -> None:
    """A fixture that lost a hazard's case would still pass; this one names them."""
    seen = {case_id: CASES[case_id]["outcome"].get("observed", {}) for case_id in FITS}
    assert any(o.get("empty_cluster_events", 0) > 0 for o in seen.values())
    assert any(o.get("converged") is False for o in seen.values())
    assert any(o.get("silhouette_sample") for o in seen.values())
    assert any(c["outcome"]["outcome"] == "refused" for c in CASES.values())
    assert {
        "normalise-recency-is-constant",
        "normalise-frequency-is-constant",
    } <= CASES.keys()


def test_nothing_in_the_service_imports_or_names_the_web() -> None:
    """ADR-0045: the service and its tests never import `web/`."""
    here = Path(__file__).resolve().parents[1]
    offenders = []
    for path in [
        *here.joinpath("app").glob("*.py"),
        *here.joinpath("tests").glob("*.py"),
    ]:
        for line in path.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith(("import web", "from web")):
                offenders.append(f"{path.name}: {stripped}")
    assert offenders == []
