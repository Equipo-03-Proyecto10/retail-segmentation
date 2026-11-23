"""The four promotion operations against the real schema (F25-16)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import psycopg
import pytest
from lxml import etree

from tests.conftest import ADMIN, ANALYST, AUDITOR, MARKETING

pytestmark = pytest.mark.postgres

ROLES = {
    "ADMIN": ADMIN,
    "ANALYST": ANALYST,
    "STORE_MANAGER": "11111111-1111-1111-1111-000000000003",
    "MARKETING": MARKETING,
    "INVENTORY_PLANNER": "11111111-1111-1111-1111-000000000005",
    "AUDITOR": AUDITOR,
    "CUSTOMER": "11111111-1111-1111-1111-000000000007",
    "SALES_ASSOCIATE": "11111111-1111-1111-1111-000000000031",
    "LOYALTY_OFFICER": "11111111-1111-1111-1111-000000000032",
    "COMMERCIAL_SUPERVISOR": "11111111-1111-1111-1111-000000000033",
}
READERS = {
    "ADMIN",
    "ANALYST",
    "STORE_MANAGER",
    "MARKETING",
    "AUDITOR",
    "SALES_ASSOCIATE",
    "LOYALTY_OFFICER",
    "COMMERCIAL_SUPERVISOR",
}
FLOOR = {
    "STORE_MANAGER",
    "SALES_ASSOCIATE",
    "LOYALTY_OFFICER",
    "COMMERCIAL_SUPERVISOR",
}


def caller(role: str) -> tuple[str, str]:
    return ROLES[role], role


def code(reply: Any) -> tuple[int, str | None]:
    return reply.status_code, (reply.get_json(silent=True) or {}).get("code")


def campaign(
    owner: psycopg.Connection,
    campaign_id: int,
    *,
    status: str = "DRAFT",
    start: date | None = None,
    end: date | None = None,
) -> None:
    today = date.today()
    owner.execute(
        "INSERT INTO public.campaign"
        " (campaign_id, name, label_code, starts_on, ends_on, status)"
        " VALUES (%s, %s, 'LOYAL', %s, %s, %s)",
        (
            campaign_id,
            f"Promotion test {campaign_id}",
            start or today - timedelta(days=10),
            end or today + timedelta(days=10),
            status,
        ),
    )


def valid_places(owner: psycopg.Connection) -> tuple[list[int], list[int]]:
    rows = owner.execute(
        "SELECT store_id, channel_ids FROM api_stores.store_directory_v2"
        " WHERE cardinality(channel_ids) > 0 ORDER BY store_id"
    ).fetchall()
    assert rows
    return [rows[0][0]], [rows[0][1][0]]


def body(
    owner: psycopg.Connection,
    campaign_id: int,
    *,
    stores: list[int] | None = None,
    channels: list[int] | None = None,
) -> dict[str, Any]:
    if stores is None or channels is None:
        found_stores, found_channels = valid_places(owner)
        stores = stores or found_stores
        channels = channels or found_channels
    today = date.today()
    return {
        "campaign_id": campaign_id,
        "name": "API promotion",
        "discount_percent": "12.50",
        "starts_on": (today - timedelta(days=2)).isoformat(),
        "ends_on": (today + timedelta(days=2)).isoformat(),
        "store_ids": stores,
        "channel_ids": channels,
    }


def create(
    api: Any, owner: psycopg.Connection, campaign_id: int, **changes: Any
) -> Any:
    payload = body(owner, campaign_id) | changes
    return api("POST", "/v1/promotions", json_body=payload)


def floor_places(owner: psycopg.Connection) -> tuple[list[int], list[int]]:
    stores = [
        owner.execute(
            "SELECT min(store_id) FROM api_users.store_assignment_v1"
            " WHERE user_id = %s",
            (ROLES[role],),
        ).fetchone()[0]
        for role in sorted(FLOOR)
    ]
    assert all(store is not None for store in stores)
    rows = owner.execute(
        "SELECT store_id, channel_ids FROM api_stores.store_directory_v2"
        " WHERE store_id = ANY(%s::smallint[])",
        (stores,),
    ).fetchall()
    channels = sorted({channel for row in rows for channel in row[1]})
    assert channels
    return sorted(set(stores)), [channels[0]]


def test_create_read_replace_and_list_json(api: Any, owner: psycopg.Connection) -> None:
    campaign(owner, 11_640_001)
    created = create(api, owner, 11_640_001)
    assert created.status_code == 201
    promotion = created.get_json()
    promotion_id = promotion["promotion_id"]
    assert created.headers["Location"] == f"/v1/promotions/{promotion_id}"
    assert promotion == body(owner, 11_640_001) | {
        "promotion_id": promotion_id,
        "is_active": False,
    }

    assert api("GET", f"/v1/promotions/{promotion_id}").get_json() == promotion
    listed = api(
        "GET", "/v1/promotions?campaign_id=11640001&active=false&size=1"
    ).get_json()
    assert listed["promotions"] == [promotion]
    assert listed["page_info"] == {"page": 1, "size": 1, "total": 1}

    replacement = {
        key: value
        for key, value in body(owner, 11_640_001).items()
        if key != "campaign_id"
    } | {"name": "Replaced promotion", "discount_percent": "20.00"}
    replaced = api("PUT", f"/v1/promotions/{promotion_id}", json_body=replacement)
    assert replaced.status_code == 200
    assert replaced.get_json() == promotion | replacement


def test_every_role_is_allowed_or_refused_exactly_as_the_contract(
    api: Any, owner: psycopg.Connection
) -> None:
    campaign(owner, 11_640_002)
    stores, channels = floor_places(owner)
    promotion = create(
        api,
        owner,
        11_640_002,
        store_ids=stores,
        channel_ids=channels,
    ).get_json()
    owner.execute(
        "UPDATE public.campaign SET status = 'ACTIVE' WHERE campaign_id = %s",
        (11_640_002,),
    )

    for role in sorted(ROLES):
        listing = api("GET", "/v1/promotions?active=true", as_user=caller(role))
        reading = api(
            "GET", f"/v1/promotions/{promotion['promotion_id']}", as_user=caller(role)
        )
        expected = 200 if role in READERS else 403
        assert listing.status_code == reading.status_code == expected, role

        write = api(
            "PUT",
            f"/v1/promotions/{promotion['promotion_id']}",
            as_user=caller(role),
            json_body={
                key: value
                for key, value in body(owner, 11_640_002).items()
                if key != "campaign_id"
            },
        )
        expected_write = 409 if role in {"ADMIN", "MARKETING"} else 403
        assert write.status_code == expected_write, role
        create_attempt = api(
            "POST",
            "/v1/promotions",
            as_user=caller(role),
            json_body=body(owner, 11_640_002),
        )
        assert create_attempt.status_code == expected_write, role


@pytest.mark.parametrize("role", sorted(FLOOR))
def test_floor_roles_are_active_only_and_scoped_per_request(
    api: Any, owner: psycopg.Connection, role: str
) -> None:
    campaign(owner, 11_640_003)
    assigned = owner.execute(
        "SELECT store_id FROM api_users.store_assignment_v1 WHERE user_id = %s"
        " ORDER BY store_id LIMIT 1",
        (ROLES[role],),
    ).fetchone()[0]
    channel = owner.execute(
        "SELECT channel_ids[1] FROM api_stores.store_directory_v2 WHERE store_id = %s",
        (assigned,),
    ).fetchone()[0]
    assert channel is not None
    promotion = create(
        api,
        owner,
        11_640_003,
        store_ids=[assigned],
        channel_ids=[channel],
    ).get_json()

    # A DRAFT campaign is not disclosed through either floor read.
    assert (
        api("GET", "/v1/promotions", as_user=caller(role)).get_json()["promotions"]
        == []
    )
    assert code(
        api(
            "GET",
            f"/v1/promotions/{promotion['promotion_id']}",
            as_user=caller(role),
        )
    ) == (404, "promotion_not_found")

    owner.execute(
        "UPDATE public.campaign SET status = 'ACTIVE' WHERE campaign_id = %s",
        (11_640_003,),
    )
    shown = api("GET", f"/v1/promotions?store_id={assigned}", as_user=caller(role))
    assert [item["promotion_id"] for item in shown.get_json()["promotions"]] == [
        promotion["promotion_id"]
    ]
    assert code(api("GET", "/v1/promotions?active=false", as_user=caller(role))) == (
        403,
        "forbidden_role",
    )

    unassigned = owner.execute(
        "SELECT store_id FROM public.store WHERE store_id NOT IN"
        " (SELECT store_id FROM api_users.store_assignment_v1 WHERE user_id = %s)"
        " ORDER BY store_id LIMIT 1",
        (ROLES[role],),
    ).fetchone()[0]
    assert code(
        api("GET", f"/v1/promotions?store_id={unassigned}", as_user=caller(role))
    ) == (403, "forbidden_store")

    # Assignment is not cached: removing it changes the very next request.
    owner.execute(
        "DELETE FROM users.store_assignment WHERE user_id = %s AND store_id = %s",
        (ROLES[role], assigned),
    )
    assert code(
        api("GET", f"/v1/promotions?store_id={assigned}", as_user=caller(role))
    ) == (403, "forbidden_store")


def test_validation_refusals_and_atomic_replace(
    api: Any, owner: psycopg.Connection
) -> None:
    campaign(owner, 11_640_004)
    original = create(api, owner, 11_640_004).get_json()
    promotion_id = original["promotion_id"]
    good = body(owner, 11_640_004)
    replace = {key: value for key, value in good.items() if key != "campaign_id"}

    cases = [
        (good | {"promotion_id": 1}, "invalid_body"),
        (good | {"discount_percent": "0.00"}, "invalid_body"),
        (good | {"ends_on": "2000-01-01"}, "ends_before_start"),
        (good | {"starts_on": "1999-01-01"}, "promotion_outside_campaign"),
        (good | {"campaign_id": 2_147_000_000}, "unknown_campaign"),
        (good | {"store_ids": [32767]}, "unknown_store"),
        (good | {"channel_ids": [32767]}, "unknown_channel"),
    ]
    for payload, expected in cases:
        assert code(api("POST", "/v1/promotions", json_body=payload))[1] == expected

    assert code(
        api(
            "PUT",
            f"/v1/promotions/{promotion_id}",
            json_body=replace | {"campaign_id": 11_640_004},
        )
    ) == (422, "invalid_body")
    assert api("GET", f"/v1/promotions/{promotion_id}").get_json() == original
    assert code(api("PUT", "/v1/promotions/2147000000", json_body=replace)) == (
        404,
        "promotion_not_found",
    )

    owner.execute(
        "UPDATE public.campaign SET status = 'CANCELLED' WHERE campaign_id = %s",
        (11_640_004,),
    )
    assert code(api("PUT", f"/v1/promotions/{promotion_id}", json_body=replace)) == (
        409,
        "campaign_not_draft",
    )


def test_every_operation_answers_xml_that_validates_against_the_xsd(
    api: Any, service: Any, owner: psycopg.Connection
) -> None:
    campaign(owner, 11_640_005)
    stores, channels = valid_places(owner)
    today = date.today()
    xml = (
        '<CreatePromotionRequest xmlns="https://mosaiq.maxthecoder.online/xml/'
        'campaigns/v1"><campaign_id>11640005</campaign_id><name>XML promotion</name>'
        "<discount_percent>10.00</discount_percent>"
        f"<starts_on>{today - timedelta(days=1)}</starts_on>"
        f"<ends_on>{today + timedelta(days=1)}</ends_on>"
        f"<store_ids>{stores[0]}</store_ids><channel_ids>{channels[0]}</channel_ids>"
        "</CreatePromotionRequest>"
    )
    created = api(
        "POST",
        "/v1/promotions",
        data=xml,
        content_type="application/xml",
        headers={"Accept": "application/xml"},
    )
    assert created.status_code == 201
    schema = service.extensions["mosaiq_kit"].contract.xml_schema
    schema.assertValid(etree.fromstring(created.data))
    promotion_id = int(
        etree.fromstring(created.data).xpath(
            "string(c:promotion_id)",
            namespaces={"c": "https://mosaiq.maxthecoder.online/xml/campaigns/v1"},
        )
    )

    read = api(
        "GET",
        f"/v1/promotions/{promotion_id}",
        headers={"Accept": "application/xml"},
    )
    listed = api("GET", "/v1/promotions", headers={"Accept": "application/xml"})
    for reply in (read, listed):
        assert reply.status_code == 200
        schema.assertValid(etree.fromstring(reply.data))

    replacement = xml.replace(
        "CreatePromotionRequest", "ReplacePromotionRequest"
    ).replace("<campaign_id>11640005</campaign_id>", "")
    replaced = api(
        "PUT",
        f"/v1/promotions/{promotion_id}",
        data=replacement,
        content_type="application/xml",
        headers={"Accept": "application/xml"},
    )
    assert replaced.status_code == 200
    schema.assertValid(etree.fromstring(replaced.data))


def test_api_created_promotion_is_the_sales_service_view_row(
    api: Any,
    owner: psycopg.Connection,
    database: dict[str, str],
) -> None:
    campaign(owner, 11_640_006)
    created = create(api, owner, 11_640_006).get_json()
    owner.execute(
        "UPDATE public.campaign SET status = 'ACTIVE' WHERE campaign_id = %s",
        (11_640_006,),
    )
    with psycopg.connect(database["service"]) as service_connection:
        row = service_connection.execute(
            "SELECT campaign_id, store_id, channel_id, discount_percent::text,"
            " starts_on::text, ends_on::text"
            " FROM api_campaigns.active_promotion_v1 WHERE promotion_id = %s",
            (created["promotion_id"],),
        ).fetchone()
    expected = (
        created["campaign_id"],
        created["store_ids"][0],
        created["channel_ids"][0],
        created["discount_percent"],
        created["starts_on"],
        created["ends_on"],
    )
    assert row == expected

    # F25-05's accepted database contract uses the sale-time window view so an
    # offline sale is judged on its own date, not the request's CURRENT_DATE.
    with psycopg.connect(database["sales"]) as sales:
        row = sales.execute(
            "SELECT campaign_id, store_id, channel_id, discount_percent::text,"
            " starts_on::text, ends_on::text"
            " FROM api_campaigns.promotion_window_v1 WHERE promotion_id = %s",
            (created["promotion_id"],),
        ).fetchone()
    assert row == expected
