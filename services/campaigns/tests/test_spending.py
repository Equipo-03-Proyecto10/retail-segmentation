"""Campaign promotion spending against the real PostgreSQL schema (F25-15)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest
from lxml import etree

from tests.conftest import ADMIN, ANALYST, AUDITOR, MARKETING

pytestmark = pytest.mark.postgres
CAMPAIGN = 10_480_001


@pytest.fixture
def spending_world(owner: psycopg.Connection) -> dict[str, Any]:
    customers = [
        str(row[0])
        for row in owner.execute(
            "SELECT customer_id FROM public.customer ORDER BY customer_id LIMIT 3"
        )
    ]
    store, channel, product, label = owner.execute(
        "SELECT (SELECT min(store_id) FROM public.store),"
        " (SELECT min(channel_id) FROM public.channel),"
        " (SELECT min(product_id) FROM public.product),"
        " (SELECT min(label_code) FROM public.segment_label)"
    ).fetchone()
    owner.execute(
        "INSERT INTO public.campaign"
        " (campaign_id, name, label_code, starts_on, ends_on, status)"
        " VALUES (%s, 'F25-15 spending', %s, CURRENT_DATE - 10,"
        " CURRENT_DATE + 10, 'ACTIVE')",
        (CAMPAIGN, label),
    )
    promotion = owner.execute(
        "INSERT INTO campaigns.promotion"
        " (campaign_id, name, discount_percent, starts_on, ends_on)"
        " VALUES (%s, 'Spending promotion', 30.00, CURRENT_DATE - 10,"
        " CURRENT_DATE + 10) RETURNING promotion_id",
        (CAMPAIGN,),
    ).fetchone()[0]
    owner.execute(
        "INSERT INTO campaigns.promotion_store (promotion_id, store_id)"
        " VALUES (%s, %s)",
        (promotion, store),
    )
    owner.execute(
        "INSERT INTO campaigns.promotion_channel (promotion_id, channel_id)"
        " VALUES (%s, %s)",
        (promotion, channel),
    )
    return {
        "customers": customers,
        "store": store,
        "channel": channel,
        "product": product,
        "promotion": promotion,
    }


def _sale(
    owner: psycopg.Connection,
    world: dict[str, Any],
    customer: str,
    *,
    quantity: int,
    charged: str,
    list_price: str | None = "100.00",
) -> int:
    with owner.transaction():
        transaction_id = owner.execute(
            "INSERT INTO public.transaction"
            " (source_transaction_id, customer_id, store_id, channel_id,"
            " occurred_at, total, data_origin)"
            " VALUES (%s, %s, %s, %s, now(), %s::numeric * %s, 'OBSERVED')"
            " RETURNING transaction_id",
            (
                "f25-15-" + str(uuid.uuid4()),
                customer,
                world["store"],
                world["channel"],
                charged,
                quantity,
            ),
        ).fetchone()[0]
        owner.execute(
            "INSERT INTO public.transaction_line"
            " (transaction_id, product_id, quantity, unit_price)"
            " VALUES (%s, %s, %s, %s)",
            (transaction_id, world["product"], quantity, charged),
        )
        if list_price is not None:
            owner.execute(
                "INSERT INTO sales.sale_line_price"
                " (transaction_id, product_id, list_price, promotion_id)"
                " VALUES (%s, %s, %s, %s)",
                (
                    transaction_id,
                    world["product"],
                    list_price,
                    world["promotion"],
                ),
            )
    return int(transaction_id)


def _reverse_one(
    database: dict[str, str], world: dict[str, Any], transaction_id: int
) -> None:
    with psycopg.connect(database["sales"]) as sales:
        reversal_id = sales.execute(
            "INSERT INTO sales.sale_reversal"
            " (transaction_id, kind, idempotency_key, request_sha256, recorded_by)"
            " VALUES (%s, 'RETURN', %s, %s, %s) RETURNING reversal_id",
            (transaction_id, uuid.uuid4(), bytes(32), ADMIN),
        ).fetchone()[0]
        sales.execute(
            "INSERT INTO sales.sale_reversal_line"
            " (reversal_id, transaction_id, product_id, quantity, reason, resellable)"
            " VALUES (%s, %s, %s, 1, 'Customer return', true)",
            (reversal_id, transaction_id, world["product"]),
        )
        sales.execute(
            "UPDATE public.transaction_line SET returned_quantity = 1"
            " WHERE transaction_id = %s AND product_id = %s",
            (transaction_id, world["product"]),
        )


def _prepare_spending(
    owner: psycopg.Connection,
    database: dict[str, str],
    world: dict[str, Any],
) -> None:
    owner.execute(
        "INSERT INTO campaigns.campaign_budget"
        " (campaign_id, total_amount, per_customer_cap) VALUES (%s, 70.00, 40.00)",
        (CAMPAIGN,),
    )
    first = _sale(
        owner,
        world,
        world["customers"][0],
        quantity=3,
        charged="80.00",
    )
    _reverse_one(database, world, first)
    _sale(
        owner,
        world,
        world["customers"][1],
        quantity=1,
        charged="70.00",
    )
    # A CSV/import-shaped line has no historical price row and cannot be
    # attributed to a campaign, even though its charged price is low.
    _sale(
        owner,
        world,
        world["customers"][2],
        quantity=4,
        charged="1.00",
        list_price=None,
    )


def _get(api: Any, suffix: str = "", **kwargs: Any) -> Any:
    return api(
        "GET",
        f"/v1/campaigns/{CAMPAIGN}/budget/spending{suffix}",
        **kwargs,
    )


def test_spending_uses_sale_time_discount_and_only_units_left(
    api: Any,
    owner: psycopg.Connection,
    database: dict[str, str],
    spending_world: dict[str, Any],
) -> None:
    _prepare_spending(owner, database, spending_world)

    reply = _get(api)

    assert reply.status_code == 200
    body = reply.get_json()
    assert body["total_budget"] == "70.00"
    assert body["total_spent"] == "70.00"
    assert body["total_reached"] is True
    assert body["per_customer_cap"] == "40.00"
    assert body["customers_at_cap"] == 1
    assert body["customers"] == [
        {
            "customer_id": spending_world["customers"][0],
            "spent_amount": "40.00",
            "cap_reached": True,
        },
        {
            "customer_id": spending_world["customers"][1],
            "spent_amount": "30.00",
            "cap_reached": False,
        },
    ]
    assert body["page_info"] == {"page": 1, "size": 20, "total": 2}
    computed = datetime.fromisoformat(body["computed_at"].replace("Z", "+00:00"))
    assert abs((datetime.now(UTC) - computed).total_seconds()) < 300


def test_customer_spending_is_paginated_in_customer_id_order(
    api: Any,
    owner: psycopg.Connection,
    database: dict[str, str],
    spending_world: dict[str, Any],
) -> None:
    _prepare_spending(owner, database, spending_world)

    body = _get(api, "?page=2&size=1").get_json()

    assert body["customers"] == [
        {
            "customer_id": spending_world["customers"][1],
            "spent_amount": "30.00",
            "cap_reached": False,
        }
    ]
    assert body["page_info"] == {"page": 2, "size": 1, "total": 2}
    # The customer at the cap is on page 1; page 2 still says one reached it.
    assert body["customers_at_cap"] == 1
    invalid = _get(api, "?page=0")
    assert (invalid.status_code, invalid.get_json()["code"]) == (
        422,
        "invalid_pagination",
    )


def test_a_campaign_without_spend_reports_zero_against_its_limits(
    api: Any, owner: psycopg.Connection, spending_world: dict[str, Any]
) -> None:
    owner.execute(
        "INSERT INTO campaigns.campaign_budget"
        " (campaign_id, total_amount, per_customer_cap) VALUES (%s, 100.00, 10.00)",
        (CAMPAIGN,),
    )
    body = _get(api).get_json()
    assert body["total_spent"] == "0.00"
    assert body["total_reached"] is False
    assert body["total_budget"] == "100.00"
    assert body["per_customer_cap"] == "10.00"
    assert body["customers_at_cap"] == 0
    assert body["customers"] == []
    assert body["page_info"] == {"page": 1, "size": 20, "total": 0}


def test_without_a_per_customer_cap_no_customer_is_reported(
    api: Any,
    owner: psycopg.Connection,
    database: dict[str, str],
    spending_world: dict[str, Any],
) -> None:
    _prepare_spending(owner, database, spending_world)
    owner.execute(
        "UPDATE campaigns.campaign_budget SET per_customer_cap = NULL"
        " WHERE campaign_id = %s",
        (CAMPAIGN,),
    )
    body = _get(api).get_json()
    assert body["total_spent"] == "70.00"
    assert "per_customer_cap" not in body
    assert "customers_at_cap" not in body
    assert body["customers"] == []
    assert body["page_info"]["total"] == 0


def test_unknown_campaign_is_404(api: Any) -> None:
    reply = api("GET", "/v1/campaigns/2147483647/budget/spending")
    assert (reply.status_code, reply.get_json()["code"]) == (
        404,
        "campaign_not_found",
    )


@pytest.mark.parametrize(
    "caller",
    [
        (ADMIN, "ADMIN"),
        (ANALYST, "ANALYST"),
        (MARKETING, "MARKETING"),
        (AUDITOR, "AUDITOR"),
    ],
)
def test_budget_read_roles_can_read_spending(
    api: Any, spending_world: dict[str, Any], caller: tuple[str, str]
) -> None:
    assert _get(api, as_user=caller).status_code == 200


@pytest.mark.parametrize("role", ["STORE_MANAGER", "SALES_ASSOCIATE"])
def test_other_roles_are_refused(
    api: Any, spending_world: dict[str, Any], role: str
) -> None:
    reply = _get(api, as_user=(ADMIN, role))
    assert (reply.status_code, reply.get_json()["code"]) == (403, "forbidden_role")


def test_spending_is_the_same_schema_valid_report_in_xml(
    api: Any,
    service: Any,
    owner: psycopg.Connection,
    database: dict[str, str],
    spending_world: dict[str, Any],
) -> None:
    _prepare_spending(owner, database, spending_world)

    reply = _get(api, headers={"Accept": "application/xml"})

    assert reply.status_code == 200
    assert reply.mimetype == "application/xml"
    document = etree.fromstring(reply.data)
    service.extensions["mosaiq_kit"].contract.xml_schema.assertValid(document)
    namespace = {"c": "https://mosaiq.maxthecoder.online/xml/campaigns/v1"}
    assert document.xpath("string(c:total_spent)", namespaces=namespace) == "70.00"
    assert document.xpath("count(c:customers)", namespaces=namespace) == 2.0
    assert document.xpath("string(c:customers_at_cap)", namespaces=namespace) == "1"


def test_the_contract_examples_validate_against_the_xsd() -> None:
    contract = Path(__file__).resolve().parents[1] / "contracts/campaigns"
    schema = etree.XMLSchema(etree.parse(str(contract / "campaigns.xsd")))
    schema.assertValid(
        etree.parse(str(contract / "examples/campaign-budget-spending.xml"))
    )
