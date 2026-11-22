"""The promotions applied in sales, totalled per store (F27-16, #1143).

Each acceptance criterion of #1143 has its own section, run against the real
PostgreSQL and Redis of the suite. Sales are recorded through `POST /v1/sales`
with the promotions of `test_pricing.py`, so every line under test carries the
`sales.sale_line_price` row a phone sale writes, and reversals go through
`POST /v1/sale-reversals`, so `returned_quantity` moves as in production. The
seed may hold promotion lines of its own, so each case reads only the
promotions it created.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from lxml import etree

from tests import test_pricing, test_store_scope
from tests.conftest import idempotency_headers
from tests.test_pricing import body, post, promotion
from tests.test_store_scope import other_store

# The fixtures this module shares with the pricing and store-scope cases.
shop = test_pricing.shop
assign = test_store_scope.assign

NS = "https://mosaiq.maxthecoder.online/xml/sales/v1"
COMMON = "https://mosaiq.maxthecoder.online/xml/common/v1"
TOTAL_KEYS = {"store_id", "promotion_id", "lines", "units", "discount"}


def window(days_back: int = 1) -> dict[str, str]:
    now = datetime.now(UTC)
    return {
        "from": (now - timedelta(days=days_back)).isoformat(),
        "to": (now + timedelta(hours=1)).isoformat(),
    }


def sell(client, token, shop: dict, price: str, promotion_id=None, **changes) -> int:
    response = post(
        client, token, body(shop, price, promotion_id, **changes), str(uuid.uuid4())
    )
    assert response.status_code == 201, response.get_data(as_text=True)
    return response.get_json()["sale_id"]


def reverse(client, token, value: dict) -> None:
    response = client.post(
        "/v1/sale-reversals",
        json=value,
        headers=idempotency_headers(token("ADMIN")),
    )
    assert response.status_code == 201, response.get_data(as_text=True)


def totals(client, auth: dict, params: dict, promotions: set[int]) -> list[dict]:
    response = client.get(
        "/v1/sale-promotion-totals", query_string=params, headers=auth
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    document = response.get_json()
    assert set(document) == {"from", "to", "totals"}
    return [row for row in document["totals"] if row["promotion_id"] in promotions]


# Criterion 1. Per store and promotion: how many sale lines applied it, and the
# units and discount they carry, net of reversals, over a stated window.


def test_each_promotion_is_totalled_with_its_lines_units_and_discount(
    client, token, owner, shop
):
    twenty = promotion(owner, shop, "20.00")
    ten = promotion(owner, shop, "10.00")
    sell(client, token, shop, "80.00", twenty)
    sell(client, token, shop, "80.00", twenty)
    sell(client, token, shop, "90.00", ten)
    # Charged at the list price: no promotion applied, so it is in no total.
    sell(client, token, shop, "100.00")

    rows = totals(client, token("ADMIN"), window(), {twenty, ten})

    store = shop["store"]
    assert rows == [
        {
            "store_id": store,
            "promotion_id": twenty,
            "lines": 2,
            "units": 4,
            "discount": "80.00",
        },
        {
            "store_id": store,
            "promotion_id": ten,
            "lines": 1,
            "units": 2,
            "discount": "20.00",
        },
    ]


def test_a_return_takes_its_units_out_and_a_cancelled_line_stops_counting(
    client, token, owner, shop
):
    promoted = promotion(owner, shop, "20.00")
    returned = sell(client, token, shop, "80.00", promoted)
    cancelled = sell(client, token, shop, "80.00", promoted)
    before = totals(client, token("ADMIN"), window(), {promoted})
    assert before[0]["lines"] == 2 and before[0]["units"] == 4

    reverse(
        client,
        token,
        {
            "sale_id": returned,
            "kind": "RETURN",
            "lines": [
                {
                    "product_id": shop["product"],
                    "quantity": 1,
                    "reason": "Customer changed their mind",
                }
            ],
        },
    )
    reverse(
        client,
        token,
        {"sale_id": cancelled, "kind": "CANCELLATION", "reason": "Wrong customer"},
    )

    assert totals(client, token("ADMIN"), window(), {promoted}) == [
        {
            "store_id": shop["store"],
            "promotion_id": promoted,
            "lines": 1,
            "units": 1,
            "discount": "20.00",
        }
    ]


def test_the_window_is_stated_and_holds_only_sales_inside_it(
    client, token, owner, shop
):
    promoted = promotion(owner, shop, "20.00")
    sell(client, token, shop, "80.00", promoted)
    sell(
        client,
        token,
        shop,
        "80.00",
        promoted,
        occurred_at=(datetime.now(UTC) - timedelta(days=3)).isoformat(),
    )
    asked = window(days_back=1)

    response = client.get(
        "/v1/sale-promotion-totals", query_string=asked, headers=token("ADMIN")
    )
    document = response.get_json()
    assert datetime.fromisoformat(document["from"]) == datetime.fromisoformat(
        asked["from"]
    )
    assert datetime.fromisoformat(document["to"]) == datetime.fromisoformat(asked["to"])
    inside = [row for row in document["totals"] if row["promotion_id"] == promoted]
    assert [row["lines"] for row in inside] == [1]

    wider = totals(client, token("ADMIN"), window(days_back=4), {promoted})
    assert [row["lines"] for row in wider] == [2]


@pytest.mark.parametrize(
    ("params", "field", "code"),
    [
        ({"from": "2026-11-01T00:00:00Z"}, "to", "required"),
        ({"to": "2026-11-01T00:00:00Z"}, "from", "required"),
        (
            {"from": "2026-11-01T00:00:00Z", "to": "2026-11-01T00:00:00Z"},
            "from",
            "not_before_to",
        ),
        (
            {"from": "2026-12-01T00:00:00Z", "to": "2026-11-01T00:00:00Z"},
            "from",
            "not_before_to",
        ),
        ({"from": "yesterday", "to": "2026-11-01T00:00:00Z"}, "from", "invalid_format"),
        (
            {"from": "2026-11-01T00:00:00", "to": "2026-12-01T00:00:00Z"},
            "from",
            "missing_offset",
        ),
    ],
    ids=["no-to", "no-from", "empty", "reversed", "malformed", "no-offset"],
)
def test_a_window_without_both_bounds_in_order_is_refused(
    client, token, params, field, code
):
    response = client.get(
        "/v1/sale-promotion-totals", query_string=params, headers=token("ADMIN")
    )
    assert response.status_code == 422, response.get_data(as_text=True)
    error = response.get_json()
    assert error["code"] == "invalid_query"
    assert error["details"] == [{"field": field, "code": code}]


def test_the_xml_totals_validate_against_the_xsd(app, client, token, owner, shop):
    promoted = promotion(owner, shop, "20.00")
    sell(client, token, shop, "80.00", promoted)
    schema = app.extensions["mosaiq_kit"].contract.xml_schema

    response = client.get(
        "/v1/sale-promotion-totals",
        query_string=window(),
        headers={**token("ADMIN"), "Accept": "application/xml"},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.mimetype == "application/xml"
    document = etree.fromstring(response.data)
    schema.assertValid(document)
    assert document.tag == f"{{{NS}}}SalePromotionTotals"
    ours = [
        row
        for row in document.findall(f"{{{NS}}}totals")
        if row.findtext(f"{{{NS}}}promotion_id") == str(promoted)
    ]
    assert len(ours) == 1
    assert ours[0].findtext(f"{{{NS}}}discount") == "40.00"

    empty = client.get(
        "/v1/sale-promotion-totals",
        query_string={"from": "2001-01-01T00:00:00Z", "to": "2001-01-02T00:00:00Z"},
        headers={**token("ADMIN"), "Accept": "application/xml"},
    )
    assert empty.status_code == 200
    document = etree.fromstring(empty.data)
    schema.assertValid(document)
    assert document.findall(f"{{{NS}}}totals") == []


# Criterion 2. In aggregate: no customer id, name or contact in the response.


def test_the_totals_name_no_sale_and_no_customer(client, token, owner, shop):
    promoted = promotion(owner, shop, "20.00")
    sale_id = sell(client, token, shop, "80.00", promoted)

    response = client.get(
        "/v1/sale-promotion-totals", query_string=window(), headers=token("ADMIN")
    )
    document = response.get_json()
    assert all(set(row) == TOTAL_KEYS for row in document["totals"])
    text = response.get_data(as_text=True)
    assert shop["customer"] not in text
    assert f'"sale_id":{sale_id}' not in text.replace(" ", "")


# Criterion 3. A store the caller is not assigned to is not in the totals.


@pytest.mark.parametrize("role", ["STORE_MANAGER", "COMMERCIAL_SUPERVISOR"])
def test_a_floor_role_is_totalled_over_its_own_stores_only(
    client, token, owner, shop, assign, role
):
    promoted = promotion(owner, shop, "20.00")
    sell(client, token, shop, "80.00", promoted)
    elsewhere = other_store(owner, shop["store"])

    subject = assign(role, [elsewhere])
    auth = token(role, sub=subject)
    assert totals(client, auth, window(), {promoted}) == []

    refused = client.get(
        "/v1/sale-promotion-totals",
        query_string={**window(), "store_id": shop["store"]},
        headers=auth,
    )
    assert refused.status_code == 403, refused.get_data(as_text=True)
    assert refused.get_json()["code"] == "forbidden_store"
    assert "totals" not in refused.get_json()

    assign(role, [shop["store"], elsewhere])
    rows = totals(client, auth, window(), {promoted})
    assert [(row["store_id"], row["lines"]) for row in rows] == [(shop["store"], 1)]


def test_a_floor_role_with_no_store_gets_no_totals(client, token, owner, shop, assign):
    promoted = promotion(owner, shop, "20.00")
    sell(client, token, shop, "80.00", promoted)
    subject = assign("COMMERCIAL_SUPERVISOR", [])

    response = client.get(
        "/v1/sale-promotion-totals",
        query_string=window(),
        headers=token("COMMERCIAL_SUPERVISOR", sub=subject),
    )
    assert response.status_code == 200
    assert response.get_json()["totals"] == []


@pytest.mark.parametrize("role", ["MARKETING", "SALES_ASSOCIATE", "AUDITOR"])
def test_a_role_outside_the_policy_is_refused(client, token, role):
    response = client.get(
        "/v1/sale-promotion-totals", query_string=window(), headers=token(role)
    )
    assert response.status_code == 403
    assert response.get_json()["code"] == "forbidden_role"
