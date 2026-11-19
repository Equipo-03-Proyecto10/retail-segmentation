"""Promotions and valid discounts on a phone sale (F25-05, #836).

Each acceptance criterion of #836 has its own section below, run against the real
PostgreSQL and Redis of the suite. The product, the store's commercial rule and
the promotions are written as their owners would write them, through the owner
connection, and `api_campaigns.active_promotion_v1`,
`api_stores.store_directory_v2` and `api_products.product_ref_v1` are read by
the service as `svc_sales`, as in production.
"""

from __future__ import annotations

import copy
import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import psycopg
import pytest

from tests.conftest import CATALOG_PRICE, idempotency_headers, sale_xml

LIST_PRICE = "100.00"
CAMPAIGN = 9_836_001


@pytest.fixture
def shop(owner: psycopg.Connection, world: dict, role_subjects: dict):
    """One product at 100.00 in the world's store, a store cap of 25.00, and an
    ACTIVE campaign valid around today. Everything is put back afterwards."""
    product = world["products"][0]
    store = world["store"]
    other_store = owner.execute(
        "SELECT min(store_id) FROM public.store WHERE store_id <> %s", (store,)
    ).fetchone()[0]
    other_channel = owner.execute(
        "SELECT min(channel_id) FROM public.channel WHERE channel_id <> %s",
        (world["channel"],),
    ).fetchone()[0]
    rule = owner.execute(
        "SELECT max_discount_percent, set_by, set_at FROM stores.store_commercial_rule"
        " WHERE store_id = %s",
        (store,),
    ).fetchone()
    today = owner.execute("SELECT CURRENT_DATE").fetchone()[0]
    owner.execute(
        "UPDATE public.product SET list_price = %s WHERE product_id = %s",
        (LIST_PRICE, product),
    )
    _set_cap(owner, store, "25.00", role_subjects["ADMIN"])
    owner.execute(
        "INSERT INTO public.campaign"
        " (campaign_id, name, label_code, starts_on, ends_on, status)"
        " VALUES (%s, 'F25-05 test', 'CHAMPION', %s, %s, 'ACTIVE')",
        (CAMPAIGN, today - timedelta(days=20), today + timedelta(days=20)),
    )
    try:
        yield {
            **world,
            "product": product,
            "other_store": other_store,
            "other_channel": other_channel,
            "today": today,
            "admin": role_subjects["ADMIN"],
        }
    finally:
        owner.execute(
            "DELETE FROM campaigns.promotion WHERE campaign_id = %s", (CAMPAIGN,)
        )
        owner.execute("DELETE FROM public.campaign WHERE campaign_id = %s", (CAMPAIGN,))
        owner.execute(
            "UPDATE public.product SET list_price = %s WHERE product_id = %s",
            (CATALOG_PRICE, product),
        )
        owner.execute(
            "DELETE FROM stores.store_commercial_rule WHERE store_id = %s", (store,)
        )
        if rule is not None:
            owner.execute(
                "INSERT INTO stores.store_commercial_rule"
                " (store_id, max_discount_percent, set_by, set_at)"
                " VALUES (%s, %s, %s, %s)",
                (store, *rule),
            )


def _set_cap(owner, store: int, percent: str | None, admin: str) -> None:
    owner.execute(
        "INSERT INTO stores.store_commercial_rule (store_id, max_discount_percent,"
        " set_by) VALUES (%s, %s, %s) ON CONFLICT (store_id) DO UPDATE"
        " SET max_discount_percent = EXCLUDED.max_discount_percent",
        (store, percent, admin),
    )


def promotion(
    owner,
    shop: dict,
    percent: str = "20.00",
    *,
    stores: list[int] | None = None,
    channels: list[int] | None = None,
    starts: date | None = None,
    ends: date | None = None,
) -> int:
    promotion_id = owner.execute(
        "INSERT INTO campaigns.promotion"
        " (campaign_id, name, discount_percent, starts_on, ends_on)"
        " VALUES (%s, 'Test', %s, %s, %s) RETURNING promotion_id",
        (
            CAMPAIGN,
            percent,
            starts or shop["today"] - timedelta(days=10),
            ends or shop["today"] + timedelta(days=10),
        ),
    ).fetchone()[0]
    for store in stores or [shop["store"]]:
        owner.execute(
            "INSERT INTO campaigns.promotion_store (promotion_id, store_id)"
            " VALUES (%s, %s)",
            (promotion_id, store),
        )
    for channel in channels or [shop["channel"]]:
        owner.execute(
            "INSERT INTO campaigns.promotion_channel (promotion_id, channel_id)"
            " VALUES (%s, %s)",
            (promotion_id, channel),
        )
    return promotion_id


def body(shop: dict, price: str, promotion_id: int | None = None, **changes) -> dict:
    line = {"product_id": shop["product"], "quantity": 2, "unit_price": price}
    if promotion_id is not None:
        line["promotion_id"] = promotion_id
    value = {
        "customer_id": shop["customer"],
        "store_id": shop["store"],
        "channel_id": shop["channel"],
        "occurred_at": (datetime.now(UTC) - timedelta(minutes=5)).isoformat(),
        "lines": [line],
    }
    value.update(changes)
    return value


def recorded(owner, key: str) -> dict:
    """What the database holds for this key: headers, priced lines, stock."""
    source = "svc-sales:" + key
    return {
        "sales": owner.execute(
            "SELECT count(*) FROM public.transaction WHERE source_transaction_id = %s",
            (source,),
        ).fetchone()[0],
        "prices": owner.execute(
            "SELECT l.product_id, l.unit_price::text, p.list_price::text,"
            " p.promotion_id FROM public.transaction AS t"
            " JOIN public.transaction_line AS l USING (transaction_id)"
            " LEFT JOIN sales.sale_line_price AS p USING (transaction_id, product_id)"
            " WHERE t.source_transaction_id = %s ORDER BY l.product_id",
            (source,),
        ).fetchall(),
    }


def on_hand(owner, shop: dict) -> int:
    return owner.execute(
        "SELECT quantity_on_hand FROM public.inventory"
        " WHERE store_id = %s AND product_id = %s",
        (shop["store"], shop["product"]),
    ).fetchone()[0]


def post(client, token, value: dict, key: str, *, xml: bool = False):
    headers = idempotency_headers(token("STORE_MANAGER"), key)
    if xml:
        return client.post(
            "/v1/sales",
            data=sale_xml(value),
            headers={
                **headers,
                "Content-Type": "application/xml",
                "Accept": "application/json",
            },
        )
    return client.post("/v1/sales", json=value, headers=headers)


# Criterion 1. An active promotion for this store, channel and date: the charged
# price follows it, and the line records the promotion.


@pytest.mark.parametrize("xml", [False, True], ids=["json", "xml"])
def test_an_active_promotion_is_charged_and_recorded_on_the_line(
    client, token, owner, shop, xml
):
    promotion_id = promotion(owner, shop, "20.00")
    key = str(uuid.uuid4())
    before = on_hand(owner, shop)

    response = post(client, token, body(shop, "80.00", promotion_id), key, xml=xml)

    assert response.status_code == 201, response.get_json()
    sale = response.get_json()
    assert sale["lines"] == [
        {"product_id": shop["product"], "quantity": 2, "unit_price": "80.00"}
    ]
    assert sale["total"] == "160.00"
    assert recorded(owner, key) == {
        "sales": 1,
        "prices": [(shop["product"], "80.00", LIST_PRICE, promotion_id)],
    }
    assert on_hand(owner, shop) == before - 2


def test_the_lowest_accepted_price_is_rounded_half_up_to_the_cent(
    client, token, owner, shop
):
    # 19.99 less 15% is 16.9915: 16.99 is the lowest price, 16.98 is below it.
    owner.execute(
        "UPDATE public.product SET list_price = %s WHERE product_id = %s",
        ("19.99", shop["product"]),
    )
    promotion_id = promotion(owner, shop, "15.00")
    refused = post(client, token, body(shop, "16.98", promotion_id), str(uuid.uuid4()))
    assert refused.get_json()["code"] == "discount_above_promotion"
    key = str(uuid.uuid4())
    accepted = post(client, token, body(shop, "16.99", promotion_id), key)
    assert accepted.status_code == 201, accepted.get_json()
    assert recorded(owner, key)["prices"] == [
        (shop["product"], "16.99", "19.99", promotion_id)
    ]


def test_a_promotion_on_one_line_leaves_the_other_lines_at_list_price(
    client, token, owner, shop
):
    second = shop["products"][1]
    promotion_id = promotion(owner, shop, "20.00")
    value = body(shop, "85.00", promotion_id)
    value["lines"].append({"product_id": second, "quantity": 1, "unit_price": "1.00"})
    key = str(uuid.uuid4())

    response = post(client, token, value, key)

    assert response.status_code == 201, response.get_json()
    assert recorded(owner, key)["prices"] == sorted(
        [
            (shop["product"], "85.00", LIST_PRICE, promotion_id),
            (second, "1.00", CATALOG_PRICE, None),
        ]
    )


# Criterion 2. A discount with no valid promotion, or above the store's
# commercial rule, is refused with a code the client can show, and nothing is
# recorded.


def _not_applicable_cases(shop: dict, owner) -> dict:
    today = shop["today"]
    return {
        "unknown promotion": 987_654_321,
        "another store": promotion(owner, shop, stores=[shop["other_store"]]),
        "another channel": promotion(owner, shop, channels=[shop["other_channel"]]),
        "ended": promotion(
            owner,
            shop,
            starts=today - timedelta(days=15),
            ends=today - timedelta(days=1),
        ),
        "not yet begun": promotion(
            owner,
            shop,
            starts=today + timedelta(days=1),
            ends=today + timedelta(days=15),
        ),
    }


@pytest.mark.parametrize(
    "case",
    ["unknown promotion", "another store", "another channel", "ended", "not yet begun"],
)
def test_a_promotion_that_does_not_apply_is_refused_and_nothing_is_recorded(
    client, token, owner, shop, case
):
    promotion_id = _not_applicable_cases(shop, owner)[case]
    key = str(uuid.uuid4())
    before = on_hand(owner, shop)

    response = post(client, token, body(shop, "80.00", promotion_id), key)

    assert response.status_code == 422
    assert response.get_json()["code"] == "promotion_not_applicable"
    assert response.get_json()["details"] == [
        {"field": "lines[0].promotion_id", "code": "not_applicable"}
    ]
    assert recorded(owner, key) == {"sales": 0, "prices": []}
    assert on_hand(owner, shop) == before


@pytest.mark.parametrize("status", ["DRAFT", "FINISHED", "CANCELLED"])
def test_a_promotion_of_a_campaign_that_is_not_active_is_refused(
    client, token, owner, shop, status
):
    promotion_id = promotion(owner, shop)
    owner.execute(
        "UPDATE public.campaign SET status = %s WHERE campaign_id = %s",
        (status, CAMPAIGN),
    )
    key = str(uuid.uuid4())

    response = post(client, token, body(shop, "80.00", promotion_id), key)

    assert response.get_json()["code"] == "promotion_not_applicable"
    assert recorded(owner, key) == {"sales": 0, "prices": []}


def test_a_sale_dated_outside_the_promotions_validity_is_refused(
    client, token, owner, shop
):
    # Active today, but the sale is dated the day before it began.
    today = shop["today"]
    promotion_id = promotion(owner, shop, starts=today, ends=today + timedelta(days=5))
    yesterday = datetime.combine(today - timedelta(days=1), time(12), tzinfo=UTC)
    key = str(uuid.uuid4())

    response = post(
        client,
        token,
        body(shop, "80.00", promotion_id, occurred_at=yesterday.isoformat()),
        key,
    )

    assert response.get_json()["code"] == "promotion_not_applicable"
    assert recorded(owner, key) == {"sales": 0, "prices": []}


@pytest.mark.parametrize("xml", [False, True], ids=["json", "xml"])
def test_a_discount_without_a_promotion_is_refused_and_nothing_is_recorded(
    client, token, owner, shop, xml
):
    key = str(uuid.uuid4())
    before = on_hand(owner, shop)

    response = post(client, token, body(shop, "99.99"), key, xml=xml)

    assert response.status_code == 422
    assert response.get_json()["code"] == "discount_without_promotion"
    assert response.get_json()["details"] == [
        {"field": "lines[0].unit_price", "code": "below_list_price"}
    ]
    assert recorded(owner, key) == {"sales": 0, "prices": []}
    assert on_hand(owner, shop) == before


def test_a_discount_above_the_promotion_is_refused_and_nothing_is_recorded(
    client, token, owner, shop
):
    promotion_id = promotion(owner, shop, "20.00")
    key = str(uuid.uuid4())
    before = on_hand(owner, shop)

    response = post(client, token, body(shop, "79.99", promotion_id), key)

    assert response.status_code == 422
    assert response.get_json()["code"] == "discount_above_promotion"
    assert response.get_json()["details"] == [
        {"field": "lines[0].unit_price", "code": "below_promotion"}
    ]
    assert recorded(owner, key) == {"sales": 0, "prices": []}
    assert on_hand(owner, shop) == before


def test_a_discount_above_the_stores_rule_is_refused_even_within_the_promotion(
    client, token, owner, shop
):
    # The promotion allows 40%; the store allows 25%, so 75.00 is the floor.
    promotion_id = promotion(owner, shop, "40.00")
    key = str(uuid.uuid4())
    before = on_hand(owner, shop)

    response = post(client, token, body(shop, "74.99", promotion_id), key)

    assert response.status_code == 422
    assert response.get_json()["code"] == "discount_above_store_cap"
    assert response.get_json()["details"] == [
        {"field": "lines[0].unit_price", "code": "below_store_cap"}
    ]
    assert recorded(owner, key) == {"sales": 0, "prices": []}
    assert on_hand(owner, shop) == before
    accepted = post(client, token, body(shop, "75.00", promotion_id), str(uuid.uuid4()))
    assert accepted.status_code == 201, accepted.get_json()


def test_a_store_without_a_cap_allows_the_whole_promotion(client, token, owner, shop):
    _set_cap(owner, shop["store"], None, shop["admin"])
    promotion_id = promotion(owner, shop, "40.00")

    response = post(client, token, body(shop, "60.00", promotion_id), str(uuid.uuid4()))

    assert response.status_code == 201, response.get_json()


def test_a_store_cap_of_zero_refuses_every_discount(client, token, owner, shop):
    _set_cap(owner, shop["store"], "0.00", shop["admin"])
    promotion_id = promotion(owner, shop, "10.00")

    response = post(client, token, body(shop, "99.99", promotion_id), str(uuid.uuid4()))

    assert response.get_json()["code"] == "discount_above_store_cap"


def test_one_refused_line_refuses_the_whole_sale(client, token, owner, shop):
    second = shop["products"][1]
    value = body(shop, LIST_PRICE)
    value["lines"].append({"product_id": second, "quantity": 1, "unit_price": "1.00"})
    owner.execute(
        "UPDATE public.product SET list_price = %s WHERE product_id = %s",
        ("2.00", second),
    )
    key = str(uuid.uuid4())
    try:
        response = post(client, token, value, key)
    finally:
        owner.execute(
            "UPDATE public.product SET list_price = %s WHERE product_id = %s",
            (CATALOG_PRICE, second),
        )

    assert response.get_json()["code"] == "discount_without_promotion"
    assert response.get_json()["details"][0]["field"] == "lines[1].unit_price"
    assert recorded(owner, key) == {"sales": 0, "prices": []}


def test_an_unknown_channel_keeps_its_own_code_before_any_discount_rule(
    client, token, owner, shop
):
    response = post(
        client, token, body(shop, "50.00", channel_id=32000), str(uuid.uuid4())
    )
    assert response.get_json()["code"] == "unknown_channel"


# Criterion 3. RN-13 stores what was charged, not the list price; when a discount
# applies, the list price at sale time is kept, so discounts can be reported.


def test_the_list_price_at_sale_time_is_kept_after_the_catalog_changes(
    client, token, owner, shop
):
    promotion_id = promotion(owner, shop, "20.00")
    discounted, full = str(uuid.uuid4()), str(uuid.uuid4())
    assert (
        post(client, token, body(shop, "80.00", promotion_id), discounted).status_code
        == 201
    )
    assert post(client, token, body(shop, LIST_PRICE), full).status_code == 201

    owner.execute(
        "UPDATE public.product SET list_price = %s WHERE product_id = %s",
        ("130.00", shop["product"]),
    )

    assert recorded(owner, discounted)["prices"] == [
        (shop["product"], "80.00", LIST_PRICE, promotion_id)
    ]
    assert recorded(owner, full)["prices"] == [
        (shop["product"], LIST_PRICE, LIST_PRICE, None)
    ]
    # The discount granted is reported from what was kept, not from today's 130.00.
    granted = owner.execute(
        "SELECT sum((p.list_price - l.unit_price) * l.quantity)"
        " FROM public.transaction AS t"
        " JOIN public.transaction_line AS l USING (transaction_id)"
        " JOIN sales.sale_line_price AS p USING (transaction_id, product_id)"
        " WHERE t.source_transaction_id = ANY(%s)",
        (["svc-sales:" + discounted, "svc-sales:" + full],),
    ).fetchone()[0]
    assert granted == Decimal("40.00")


def test_the_kept_list_price_cannot_be_rewritten_by_the_service(owner, service_url):
    with psycopg.connect(service_url) as connection:
        for statement in (
            "UPDATE sales.sale_line_price SET list_price = 1",
            "DELETE FROM sales.sale_line_price",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(statement)
            connection.rollback()


# Criterion 4. A replay of the same Idempotency-Key creates no second sale.


def test_a_replayed_promotion_sale_creates_no_second_sale(client, token, owner, shop):
    promotion_id = promotion(owner, shop, "20.00")
    key = str(uuid.uuid4())
    value = body(shop, "80.00", promotion_id)
    before = on_hand(owner, shop)

    first = post(client, token, value, key)
    replay = post(client, token, copy.deepcopy(value), key)

    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.headers["Idempotent-Replayed"] == "true"
    assert replay.get_json() == first.get_json()
    assert recorded(owner, key) == {
        "sales": 1,
        "prices": [(shop["product"], "80.00", LIST_PRICE, promotion_id)],
    }
    assert on_hand(owner, shop) == before - 2


def test_a_replay_after_the_promotion_ended_still_answers_the_original(
    client, token, owner, shop
):
    promotion_id = promotion(owner, shop, "20.00")
    key = str(uuid.uuid4())
    value = body(shop, "80.00", promotion_id)
    assert post(client, token, value, key).status_code == 201
    owner.execute(
        "UPDATE public.campaign SET status = 'FINISHED' WHERE campaign_id = %s",
        (CAMPAIGN,),
    )

    replay = post(client, token, value, key)

    assert replay.status_code == 200
    assert recorded(owner, key)["sales"] == 1


def test_the_same_key_with_another_promotion_is_a_conflict(client, token, owner, shop):
    first_promotion = promotion(owner, shop, "20.00")
    second_promotion = promotion(owner, shop, "25.00")
    key = str(uuid.uuid4())
    assert (
        post(client, token, body(shop, "80.00", first_promotion), key).status_code
        == 201
    )

    response = post(client, token, body(shop, "80.00", second_promotion), key)

    assert response.status_code == 409
    assert response.get_json()["code"] == "idempotency_key_reused"
    assert recorded(owner, key)["prices"] == [
        (shop["product"], "80.00", LIST_PRICE, first_promotion)
    ]


def test_a_request_without_a_promotion_hashes_as_before_1_9_0():
    """A phone's queued retry from before this change must still replay."""
    import hashlib
    import json

    from app.service import _canonical

    occurred = datetime(2026, 10, 15, 20, 32, 10, tzinfo=UTC)
    lines = [
        {
            "product_id": 7,
            "quantity": 2,
            "unit_price": Decimal("12.5"),
            "promotion_id": None,
        }
    ]
    request = {
        "customer_id": "7B3AD781-C5DE-4F40-AE50-1FA825CE2B25",
        "store_id": 3,
        "channel_id": 2,
    }
    before = {
        "sub": "u",
        "customer_id": "7b3ad781-c5de-4f40-ae50-1fa825ce2b25",
        "store_id": 3,
        "channel_id": 2,
        "occurred_at": "2026-10-15T20:32:10+00:00",
        "lines": [{"product_id": 7, "quantity": 2, "unit_price": "12.50"}],
    }
    expected = hashlib.sha256(
        json.dumps(before, sort_keys=True, separators=(",", ":")).encode()
    ).digest()
    assert _canonical(request, occurred, lines, "u") == expected
    lines[0]["promotion_id"] = 41
    assert _canonical(request, occurred, lines, "u") != expected
