"""The business rules' refusals, through the deployed services (#459).

users-service keeps exactly one administrator (ADR-0008, RN-01): it refuses a
second one, a promotion by PATCH and deactivating the only one. sales-service
refuses a sale its stock cannot cover and one without an idempotency key. All
of these are refusals, so nothing persists. A reused key needs an accepted
sale first, which persists, so that case runs on the local stack only.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime

import pytest

from tests.negative.stack import USERS, Evidence, Stack, require

SALES = "/api/sales/v1/sales"


def test_users_keeps_exactly_one_administrator(
    deployed_stack: Stack, evidence: Evidence
) -> None:
    admin = deployed_stack.accounts["ADMIN"]
    analyst = deployed_stack.accounts["ANALYST"]
    token = deployed_stack.token("ADMIN")

    second = deployed_stack.edge.json(
        "POST",
        USERS,
        {
            "name": "F18-04 second administrator",
            "email": f"f18-04-{deployed_stack.run_id}-second-admin@example.test",
            "role_code": "ADMIN",
            "password": "Refused-" + secrets.token_urlsafe(18),
        },
        token=token,
    )
    promotion = deployed_stack.edge.json(
        "PATCH", f"{USERS}/{analyst.user_id}", {"role_code": "ADMIN"}, token=token
    )
    deactivation = deployed_stack.edge.call(
        "POST", f"{USERS}/{admin.user_id}/deactivation", token=token
    )

    held = evidence.check(
        "users", "POST /v1/users with role ADMIN", second, 409, "single_administrator"
    )
    held &= evidence.check(
        "users",
        "PATCH an ANALYST to role ADMIN",
        promotion,
        409,
        "use_administrator_transfer",
    )
    held &= evidence.check(
        "users",
        "deactivating the only ADMIN",
        deactivation,
        409,
        "administrator_required",
    )
    assert held, (second.outcome(), promotion.outcome(), deactivation.outcome())


@pytest.fixture(scope="module")
def sale(deployed_stack: Stack) -> dict:
    """A sale every rule accepts but its stock, found through the services."""
    token = deployed_stack.token("ADMIN")
    edge = deployed_stack.edge

    customers = edge.call("GET", "/api/customers/v1/customers?size=1", token=token)
    require(customers, 200)
    channels = edge.call("GET", "/api/sales/v1/channels", token=token)
    require(channels, 200)
    levels = edge.call("GET", "/api/stock/v1/stock-levels?size=100", token=token)
    require(levels, 200)
    products = edge.call(
        "GET", "/api/products/v1/products?active=true&size=100", token=token
    )
    require(products, 200)

    active = {product["product_id"] for product in products.json()["products"]}
    level = next(
        level
        for level in levels.json()["stock_levels"]
        if level["product_id"] in active
    )
    return {
        "customer_id": customers.json()["customers"][0]["customer_id"],
        "store_id": level["store_id"],
        "channel_id": channels.json()["channels"][0]["channel_id"],
        "occurred_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "lines": [
            {
                "product_id": level["product_id"],
                "quantity": level["quantity_on_hand"] + 1000,
                "unit_price": "1.00",
            }
        ],
    }


def test_sales_refuses_a_sale_its_stock_cannot_cover(
    deployed_stack: Stack, evidence: Evidence, sale: dict
) -> None:
    reply = deployed_stack.edge.json(
        "POST",
        SALES,
        sale,
        token=deployed_stack.token("ADMIN"),
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    held = evidence.check(
        "sales", "a line 1,000 over the stock on hand", reply, 409, "stock_unavailable"
    )
    assert held, reply.outcome()


def test_sales_refuses_a_sale_without_an_idempotency_key(
    deployed_stack: Stack, evidence: Evidence, sale: dict
) -> None:
    reply = deployed_stack.edge.json(
        "POST", SALES, sale, token=deployed_stack.token("ADMIN")
    )
    held = evidence.check(
        "sales", "no Idempotency-Key", reply, 400, "idempotency_key_required"
    )
    assert held, reply.outcome()


def test_sales_refuses_a_reused_key_with_another_request(
    deployed_stack: Stack, evidence: Evidence, sale: dict
) -> None:
    if deployed_stack.is_instance:
        evidence.not_run(
            "sales",
            "a reused Idempotency-Key",
            "It needs an accepted sale under that key, which would persist; "
            "the local stack's run covers it (ADR-0039).",
        )
        pytest.skip("an accepted sale persists; local stack only")
    key = str(uuid.uuid4())
    token = deployed_stack.token("ADMIN")
    line = sale["lines"][0] | {"quantity": 1}
    accepted = deployed_stack.edge.json(
        "POST",
        SALES,
        sale | {"lines": [line]},
        token=token,
        headers={"Idempotency-Key": key},
    )
    require(accepted, 201)
    other = sale | {"lines": [line | {"unit_price": "2.00"}]}
    reused = deployed_stack.edge.json(
        "POST", SALES, other, token=token, headers={"Idempotency-Key": key}
    )
    held = evidence.check(
        "sales",
        "the key of an accepted sale, with another request",
        reused,
        409,
        "idempotency_key_reused",
    )
    assert held, reused.outcome()
