"""What makes a version a valid one (RN-66, ADR-0064), as pure functions.

The kit has already validated the body against the contract's schema, so every
field is present and typed. What is left is what a schema cannot say: that the
parameters and the frozen scoring belong to the method, that the quintile
boundaries are monotonic, and that a histogram is one. Each refusal is a
detail `{field, code}` of a 422 `invalid_body`: `method_mismatch`,
`not_monotonic` or `distribution_mismatch`.
"""

from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation
from typing import Any

Detail = dict[str, str]
SHARE_TOLERANCE = 1e-6
_FITTED = ("k", "max_iterations", "tolerance")


def _mismatch(field: str) -> Detail:
    return {"field": field, "code": "method_mismatch"}


def method_details(body: dict[str, Any]) -> list[Detail]:
    """The parameters, the seed and the frozen scoring against the method."""
    parameters = body["parameters"]
    scoring = body["frozen_scoring"]
    centroids = scoring["centroids"]
    boundaries = scoring["quintile_boundaries"]
    bounds = scoring["normalization_bounds"]
    details: list[Detail] = []
    if body["method"] == "RFM_RULES":
        if body.get("seed") is not None:
            details.append(_mismatch("seed"))
        if any(parameters[key] is not None for key in _FITTED):
            details.append(_mismatch("parameters"))
        elif parameters["normalization"] != "NONE":
            details.append(_mismatch("parameters.normalization"))
        if boundaries is None or centroids or bounds is not None:
            details.append(_mismatch("frozen_scoring"))
        return details
    if body.get("seed") is None:
        details.append(_mismatch("seed"))
    if parameters["rule_set_id"] is not None or any(
        parameters[key] is None for key in _FITTED
    ):
        details.append(_mismatch("parameters"))
    elif parameters["normalization"] != "MIN_MAX":
        details.append(_mismatch("parameters.normalization"))
    if boundaries is not None or bounds is None:
        details.append(_mismatch("frozen_scoring"))
    elif sorted(c["cluster_index"] for c in centroids) != list(
        range(parameters["k"] or 0)
    ):
        details.append(_mismatch("frozen_scoring.centroids"))
    return details


def _money(value: str) -> Decimal | None:
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def monotonic_details(body: dict[str, Any]) -> list[Detail]:
    """Recency never falls, frequency and monetary value never rise, from the
    boundary of score 5 to that of score 2."""
    boundaries = body["frozen_scoring"]["quintile_boundaries"]
    if boundaries is None:
        return []
    base = "frozen_scoring.quintile_boundaries."
    details: list[Detail] = []
    recency = boundaries["recency_boundaries"]
    if any(a > b for a, b in zip(recency, recency[1:], strict=False)):
        details.append({"field": base + "recency_boundaries", "code": "not_monotonic"})
    frequency = boundaries["frequency_boundaries"]
    if any(a < b for a, b in zip(frequency, frequency[1:], strict=False)):
        details.append(
            {"field": base + "frequency_boundaries", "code": "not_monotonic"}
        )
    money = [_money(value) for value in boundaries["monetary_boundaries"]]
    if any(
        a is None or b is None or a < b for a, b in zip(money, money[1:], strict=False)
    ):
        details.append({"field": base + "monetary_boundaries", "code": "not_monotonic"})
    return details


def _sums_to_one(shares: list[float]) -> bool:
    return math.isclose(sum(shares), 1.0, abs_tol=SHARE_TOLERANCE)


def distribution_details(body: dict[str, Any]) -> list[Detail]:
    """Edges strictly increase, a histogram has one fewer share than edges and
    they sum to 1, each feature appears once and is one the version uses."""
    reference = body["reference_distribution"]
    used = set(body["parameters"]["features"])
    base = "reference_distribution."
    details: list[Detail] = []
    seen: set[str] = set()
    for feature in reference["features"]:
        name = feature["feature"]
        edges = feature["bin_edges"]
        shares = feature["proportions"]
        if (
            name in seen
            or name not in used
            or any(a >= b for a, b in zip(edges, edges[1:], strict=False))
            or len(shares) != len(edges) - 1
            or not _sums_to_one(shares)
        ):
            details.append(
                {"field": base + "features." + name, "code": "distribution_mismatch"}
            )
        seen.add(name)
    labels = reference["labels"]
    codes = [label["label_code"] for label in labels]
    if len(set(codes)) != len(codes) or not _sums_to_one(
        [label["share"] for label in labels]
    ):
        details.append({"field": base + "labels", "code": "distribution_mismatch"})
    return details


def invalid_details(body: dict[str, Any]) -> list[Detail]:
    """Every refusal of a version request, in a stable order."""
    return method_details(body) + monotonic_details(body) + distribution_details(body)
