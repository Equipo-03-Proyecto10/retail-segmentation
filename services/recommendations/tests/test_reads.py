"""GET /v1/recommendations over HTTP: refusals, roles, both formats, and the
static guards of the service's own code."""

from __future__ import annotations

import json
import re
import subprocess
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest
from flask.testing import FlaskClient
from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
NS = "{https://mosaiq.maxthecoder.online/xml/recommendations/v1}"
FIXTURE = json.loads((ROOT / "tests/golden/recommendations.json").read_text())
RECOMMENDED = next(
    case["input"]["customer_id"]
    for case in FIXTURE["cases"]
    if case["outcome"]["status"] == "recommended"
)
VIEWS = {
    "api_products.product_ref_v1",
    "api_categories.category_tree_v1",
    "api_stock.stock_level_v1",
    "api_customers.customer_ref_v1",
    "api_preferences.interest_category_v1",
    "api_sales.purchase_line_v1",
    "api_segments.current_label_v1",
}


def _get(client: FlaskClient, headers: dict[str, str], **query: str):
    return client.get("/v1/recommendations", query_string=query, headers=headers)


@pytest.mark.parametrize(
    "query, field, code",
    [
        ({}, "customer_id", "required"),
        ({"customer_id": "abc"}, "customer_id", "invalid_format"),
        ({"customer_id": RECOMMENDED, "limit": "0"}, "limit", "out_of_range"),
        ({"customer_id": RECOMMENDED, "limit": "51"}, "limit", "out_of_range"),
        ({"customer_id": RECOMMENDED, "limit": "9" * 5000}, "limit", "out_of_range"),
        ({"customer_id": RECOMMENDED, "limit": "abc"}, "limit", "invalid_type"),
    ],
)
def test_a_bad_query_is_invalid_query_naming_the_parameter(
    client: FlaskClient, token: Callable[..., dict], query: dict, field: str, code: str
) -> None:
    response = _get(client, token(), **query)
    assert response.status_code == 422
    body = response.get_json()
    assert body["code"] == "invalid_query"
    assert body["details"] == [{"field": field, "code": code}]


def test_an_unknown_customer_is_unknown_customer(
    client: FlaskClient, token: Callable[..., dict]
) -> None:
    response = _get(client, token(), customer_id=str(uuid.uuid4()))
    assert response.status_code == 422
    assert response.get_json()["code"] == "unknown_customer"


@pytest.mark.parametrize("role", ["INVENTORY_PLANNER", "CUSTOMER"])
def test_roles_outside_the_policy_are_forbidden(
    client: FlaskClient, token: Callable[..., dict], role: str
) -> None:
    response = _get(client, token(role), customer_id=RECOMMENDED)
    assert response.status_code == 403
    assert response.get_json()["code"] == "forbidden_role"


@pytest.mark.parametrize(
    "role", ["ADMIN", "ANALYST", "STORE_MANAGER", "MARKETING", "AUDITOR"]
)
def test_every_allowed_role_reads(
    client: FlaskClient, token: Callable[..., dict], role: str
) -> None:
    assert _get(client, token(role), customer_id=RECOMMENDED).status_code == 200


def test_json_carries_ids_and_figures_only(
    client: FlaskClient, token: Callable[..., dict]
) -> None:
    response = _get(client, token(), customer_id=RECOMMENDED, limit="5")
    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "recommended"
    assert 1 <= len(body["items"]) <= 5
    for item in body["items"]:
        assert set(item) == {"product_id", "category_id", "quantity_on_hand", "reasons"}
        for reason in item["reasons"]:
            assert set(reason) == {"signal", "segment_buyers", "category_id", "share"}
    text = response.get_data(as_text=True)
    assert "name" not in text and "price" not in text


def test_xml_validates_against_the_contract(
    client: FlaskClient, token: Callable[..., dict]
) -> None:
    schema = etree.XMLSchema(
        etree.parse(str(ROOT / "contracts/recommendations/recommendations.xsd"))
    )
    response = _get(
        client, {**token(), "Accept": "application/xml"}, customer_id=RECOMMENDED
    )
    assert response.status_code == 200
    document = etree.fromstring(response.data)
    schema.assertValid(document)
    assert document.find(f"{NS}items") is not None
    assert b"name" not in response.data and b"price" not in response.data


# ---------- static guards over app/ ----------


def test_the_code_names_only_the_seven_views() -> None:
    found = subprocess.run(
        ["grep", "-rhoE", r"\b(public|api_[a-z]+)\.[a-z0-9_]+", str(APP)],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.split()
    assert set(found) == VIEWS


def test_every_query_names_its_columns() -> None:
    for path in APP.rglob("*.py"):
        assert not re.search(r"SELECT\s+\*", path.read_text(), re.I), path


def test_the_service_never_imports_the_web() -> None:
    for path in ROOT.rglob("*.py"):
        if "contracts" in path.parts or ".venv" in path.parts:
            continue
        assert not re.search(
            r"^\s*(from|import)\s+web(\.|\s|$)", path.read_text(), re.M
        ), path
