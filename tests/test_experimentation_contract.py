"""Attribution is never an effect in the experimentation contract (ADR-0069).

ADR-0069's Compliance asks for a contract test over the OpenAPI and the XSD:
no attribution field may say "incremental", "uplift" or "caused", and no field
that carries an effect may lack its interval. The same words are checked in
the examples, so a sample cannot teach a client the wrong reading.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts/experimentation"
XS = "{http://www.w3.org/2001/XMLSchema}"

# Every type that carries attribution, and nothing else.
ATTRIBUTION_TYPES = (
    "Attribution",
    "ArmAttribution",
    "SaleAttribution",
    "SaleAttributionPage",
)
EFFECT_WORDS = re.compile(r"incremental|uplift|caused|lift", re.IGNORECASE)
# A property that states a quantity derived from a difference between arms.
EFFECT_PROPERTIES = {"difference", "amount"}


@pytest.fixture(scope="module")
def schemas() -> dict[str, dict]:
    document = yaml.safe_load((CONTRACT / "openapi.yaml").read_text())
    return document["components"]["schemas"]


@pytest.fixture(scope="module")
def xsd_types() -> dict[str, list[str]]:
    schema = ET.parse(CONTRACT / "experimentation.xsd").getroot()
    return {
        node.get("name"): [
            element.get("name") or element.get("ref") or ""
            for element in node.iter(f"{XS}element")
        ]
        for node in schema.findall(f"{XS}complexType")
    }


def _words(names: list[str]) -> list[str]:
    return [name for name in names if EFFECT_WORDS.search(name)]


@pytest.mark.parametrize("name", ATTRIBUTION_TYPES)
def test_no_attribution_property_is_named_as_an_effect(schemas, name) -> None:
    properties = list(schemas[name]["properties"])
    assert _words(properties) == []


@pytest.mark.parametrize("name", ATTRIBUTION_TYPES[:3])
def test_no_attribution_element_is_named_as_an_effect(xsd_types, name) -> None:
    assert _words(xsd_types[name]) == []


@pytest.mark.parametrize("name", ["Attribution", "SaleAttribution"])
def test_an_attribution_carries_its_own_label(schemas, name) -> None:
    label = schemas[name]["properties"]["label"]
    assert label == {"$ref": "#/components/schemas/AttributionLabel"}
    assert schemas["AttributionLabel"]["enum"] == ["ATTRIBUTION"]


def test_a_field_that_carries_an_effect_has_its_interval(schemas) -> None:
    carriers = {
        name: schema
        for name, schema in schemas.items()
        if EFFECT_PROPERTIES & set(schema.get("properties", {}))
    }
    assert {"Comparison", "RevenueEffect"} <= set(carriers)
    for name, schema in carriers.items():
        properties = schema["properties"]
        assert {"lower", "upper"} <= set(properties), name
        assert {"lower", "upper"} <= set(schema["required"]), name


def test_incremental_revenue_is_an_interval_object(schemas) -> None:
    option = schemas["Comparison"]["properties"]["incremental_revenue"]["oneOf"][0]
    assert option == {"$ref": "#/components/schemas/RevenueEffect"}


def test_incremental_is_confined_to_the_effect_side(schemas) -> None:
    holders = sorted(
        name
        for name, schema in schemas.items()
        if re.search("incremental|uplift", " ".join(schema.get("properties", {})))
    )
    assert holders == ["Comparison"]


def _keys(node) -> list[str]:
    if isinstance(node, dict):
        return [*node, *(key for child in node.values() for key in _keys(child))]
    if isinstance(node, list):
        return [key for child in node for key in _keys(child)]
    return []


@pytest.mark.parametrize(
    "stem",
    ["sale-attribution", "sale-attributions-page", "sale-attributions-none"],
)
def test_attribution_examples_never_say_effect(stem) -> None:
    document = json.loads((CONTRACT / "examples" / f"{stem}.json").read_text())
    assert _words(_keys(document)) == []


@pytest.mark.parametrize(
    "stem",
    [
        "result-complete",
        "result-revenue",
        "result-inconclusive",
        "result-pending",
        "result-invalid",
        "result-offer-comparison",
    ],
)
def test_a_result_keeps_attribution_and_effect_apart(stem) -> None:
    document = json.loads((CONTRACT / "examples" / f"{stem}.json").read_text())
    attribution = document["attribution"]
    assert attribution is None or _words(_keys(attribution)) == []
    for comparison in document["comparisons"]:
        assert comparison["lower"] is not None and comparison["upper"] is not None
        if document["design_kind"] == "OFFER_COMPARISON":
            assert comparison["label"] == "TREATMENT_DIFFERENCE"
            assert comparison["incremental_revenue"] is None
    if document["design_kind"] == "OFFER_COMPARISON":
        assert document["effect_refusal"]["code"] == "NO_CONTROL"
