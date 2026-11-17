"""What makes a version valid, from the contract's own request examples."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from app import rules

EXAMPLES = Path(__file__).resolve().parents[3] / "contracts/model-registry/examples"


def example(name: str) -> dict[str, Any]:
    return json.loads((EXAMPLES / name).read_text())


@pytest.mark.parametrize(
    "name", ["model-version.request.json", "model-version-kmeans.request.json"]
)
def test_the_contract_examples_are_valid(name: str) -> None:
    assert rules.invalid_details(example(name)) == []


def codes(body: dict[str, Any]) -> set[tuple[str, str]]:
    return {(d["field"], d["code"]) for d in rules.invalid_details(body)}


def test_an_rfm_version_with_a_seed_or_centroids_is_a_method_mismatch() -> None:
    body = example("model-version.request.json")
    body["seed"] = 1
    body["parameters"]["k"] = 4
    assert {("seed", "method_mismatch"), ("parameters", "method_mismatch")} <= codes(
        body
    )
    body = example("model-version.request.json")
    body["frozen_scoring"]["centroids"] = [
        {"cluster_index": 0, "label_code": "A", "r": 0, "f": 0, "m": 0}
    ]
    assert ("frozen_scoring", "method_mismatch") in codes(body)


def test_a_kmeans_version_needs_its_seed_one_centroid_per_cluster_and_bounds() -> None:
    body = example("model-version-kmeans.request.json")
    body["seed"] = None
    body["parameters"]["rule_set_id"] = 3
    assert codes(body) == {
        ("seed", "method_mismatch"),
        ("parameters", "method_mismatch"),
    }
    body = example("model-version-kmeans.request.json")
    body["frozen_scoring"]["centroids"].pop()
    assert codes(body) == {("frozen_scoring.centroids", "method_mismatch")}
    body = example("model-version-kmeans.request.json")
    body["frozen_scoring"]["quintile_boundaries"] = {
        "recency_boundaries": [1, 2, 3, 4],
        "frequency_boundaries": [4, 3, 2, 1],
        "monetary_boundaries": ["4.00", "3.00", "2.00", "1.00"],
    }
    assert ("frozen_scoring", "method_mismatch") in codes(body)


@pytest.mark.parametrize(
    ("key", "values"),
    [
        ("recency_boundaries", [14, 35, 20, 120]),
        ("frequency_boundaries", [2, 5, 3, 2]),
        ("monetary_boundaries", ["150.00", "700.00", "350.00", "150.00"]),
    ],
)
def test_boundaries_that_move_the_wrong_way_are_not_monotonic(
    key: str, values: list[Any]
) -> None:
    body = example("model-version.request.json")
    body["frozen_scoring"]["quintile_boundaries"][key] = values
    assert codes(body) == {
        ("frozen_scoring.quintile_boundaries." + key, "not_monotonic")
    }


def test_equal_boundaries_are_monotonic() -> None:
    body = example("model-version.request.json")
    boundaries = body["frozen_scoring"]["quintile_boundaries"]
    boundaries["recency_boundaries"] = [30, 30, 30, 30]
    boundaries["frequency_boundaries"] = [3, 3, 3, 3]
    boundaries["monetary_boundaries"] = ["10.00"] * 4
    assert rules.invalid_details(body) == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda f: f.update(bin_edges=[0, 30, 30, 120, 180]),
        lambda f: f.update(bin_edges=[0, 30, 60, 180]),
        lambda f: f.update(proportions=[0.5, 0.2, 0.2, 0.2]),
    ],
)
def test_a_malformed_histogram_is_a_distribution_mismatch(mutate: Any) -> None:
    body = example("model-version.request.json")
    mutate(body["reference_distribution"]["features"][0])
    assert codes(body) == {
        ("reference_distribution.features.R", "distribution_mismatch")
    }


def test_label_shares_must_sum_to_one_and_be_distinct() -> None:
    body = example("model-version.request.json")
    body["reference_distribution"]["labels"][0]["share"] = 0.5
    assert codes(body) == {("reference_distribution.labels", "distribution_mismatch")}
    body = example("model-version.request.json")
    body["reference_distribution"]["labels"][1]["label_code"] = "CHAMPIONS"
    assert codes(body) == {("reference_distribution.labels", "distribution_mismatch")}


def test_a_feature_the_version_does_not_use_is_refused() -> None:
    body = copy.deepcopy(example("model-version.request.json"))
    body["parameters"]["features"] = ["R", "F"]
    assert codes(body) == {
        ("reference_distribution.features.M", "distribution_mismatch")
    }
