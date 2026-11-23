"""Promotion reads and draft-only writes (F25-16, ADR-0060, RN-71).

The four floor roles see only active promotions that overlap their current
store assignments. Assignments are read from ``api_users.store_assignment_v1``
inside every request; they are never copied into a token or cache. Promotion
writes lock the campaign row, sharing the lock used by campaign transitions,
so a transition and a write cannot both win on a DRAFT observation.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from mosaiq_kit import ApiError
from psycopg import Connection

from app import rules

FLOOR_ROLES = frozenset(
    {"STORE_MANAGER", "SALES_ASSOCIATE", "LOYALTY_OFFICER", "COMMERCIAL_SUPERVISOR"}
)

ASSIGNED_STORES = (
    "SELECT store_id FROM api_users.store_assignment_v1"
    " WHERE user_id = %s::uuid ORDER BY store_id"
)
LOCK_CAMPAIGN = (
    "SELECT status, starts_on, ends_on FROM public.campaign"
    " WHERE campaign_id = %s FOR UPDATE"
)
GET = """
SELECT p.promotion_id, p.campaign_id, p.name, p.discount_percent,
       p.starts_on, p.ends_on,
       ARRAY(SELECT ps.store_id FROM campaigns.promotion_store AS ps
              WHERE ps.promotion_id = p.promotion_id ORDER BY ps.store_id),
       ARRAY(SELECT pc.channel_id FROM campaigns.promotion_channel AS pc
              WHERE pc.promotion_id = p.promotion_id ORDER BY pc.channel_id),
       c.status = 'ACTIVE' AND CURRENT_DATE BETWEEN p.starts_on AND p.ends_on
  FROM campaigns.promotion AS p
  JOIN public.campaign AS c ON c.campaign_id = p.campaign_id
 WHERE p.promotion_id = %s
"""
LIST = """
SELECT p.promotion_id, p.campaign_id, p.name, p.discount_percent,
       p.starts_on, p.ends_on,
       ARRAY(SELECT ps.store_id FROM campaigns.promotion_store AS ps
              WHERE ps.promotion_id = p.promotion_id ORDER BY ps.store_id),
       ARRAY(SELECT pc.channel_id FROM campaigns.promotion_channel AS pc
              WHERE pc.promotion_id = p.promotion_id ORDER BY pc.channel_id),
       c.status = 'ACTIVE' AND CURRENT_DATE BETWEEN p.starts_on AND p.ends_on
  FROM campaigns.promotion AS p
  JOIN public.campaign AS c ON c.campaign_id = p.campaign_id
 WHERE (%(campaign)s::integer IS NULL OR p.campaign_id = %(campaign)s)
   AND (%(store)s::smallint IS NULL OR EXISTS (
       SELECT FROM campaigns.promotion_store AS ps
        WHERE ps.promotion_id = p.promotion_id AND ps.store_id = %(store)s))
   AND (%(channel)s::smallint IS NULL OR EXISTS (
       SELECT FROM campaigns.promotion_channel AS pc
        WHERE pc.promotion_id = p.promotion_id AND pc.channel_id = %(channel)s))
   AND (%(active)s::boolean IS NULL OR
       (c.status = 'ACTIVE' AND CURRENT_DATE BETWEEN p.starts_on AND p.ends_on)
           = %(active)s)
   AND (%(stores)s::smallint[] IS NULL OR EXISTS (
       SELECT FROM campaigns.promotion_store AS ps
        WHERE ps.promotion_id = p.promotion_id
          AND ps.store_id = ANY(%(stores)s::smallint[])))
 ORDER BY p.promotion_id
 LIMIT %(limit)s OFFSET %(offset)s
"""
COUNT = """
SELECT count(*)
  FROM campaigns.promotion AS p
  JOIN public.campaign AS c ON c.campaign_id = p.campaign_id
 WHERE (%(campaign)s::integer IS NULL OR p.campaign_id = %(campaign)s)
   AND (%(store)s::smallint IS NULL OR EXISTS (
       SELECT FROM campaigns.promotion_store AS ps
        WHERE ps.promotion_id = p.promotion_id AND ps.store_id = %(store)s))
   AND (%(channel)s::smallint IS NULL OR EXISTS (
       SELECT FROM campaigns.promotion_channel AS pc
        WHERE pc.promotion_id = p.promotion_id AND pc.channel_id = %(channel)s))
   AND (%(active)s::boolean IS NULL OR
       (c.status = 'ACTIVE' AND CURRENT_DATE BETWEEN p.starts_on AND p.ends_on)
           = %(active)s)
   AND (%(stores)s::smallint[] IS NULL OR EXISTS (
       SELECT FROM campaigns.promotion_store AS ps
        WHERE ps.promotion_id = p.promotion_id
          AND ps.store_id = ANY(%(stores)s::smallint[])))
"""
INSERT = (
    "INSERT INTO campaigns.promotion"
    " (campaign_id, name, discount_percent, starts_on, ends_on)"
    " VALUES (%s, %s, %s, %s, %s) RETURNING promotion_id"
)
UPDATE = (
    "UPDATE campaigns.promotion SET name = %s, discount_percent = %s,"
    " starts_on = %s, ends_on = %s WHERE promotion_id = %s"
)
DELETE_STORES = "DELETE FROM campaigns.promotion_store WHERE promotion_id = %s"
DELETE_CHANNELS = "DELETE FROM campaigns.promotion_channel WHERE promotion_id = %s"
INSERT_STORE = (
    "INSERT INTO campaigns.promotion_store (promotion_id, store_id) VALUES (%s, %s)"
)
INSERT_CHANNEL = (
    "INSERT INTO campaigns.promotion_channel (promotion_id, channel_id) VALUES (%s, %s)"
)


def confined(principal: dict[str, Any]) -> bool:
    return principal.get("role") in FLOOR_ROLES


def assigned_stores(connection: Connection, principal: dict[str, Any]) -> list[int]:
    return [
        row[0]
        for row in connection.execute(
            ASSIGNED_STORES, (str(principal["sub"]),)
        ).fetchall()
    ]


def forbidden_store() -> ApiError:
    return ApiError(
        403,
        "forbidden_store",
        "The authenticated user is not assigned to the requested store.",
        [{"field": "store_id", "code": "not_assigned"}],
    )


def forbidden_inactive() -> ApiError:
    return ApiError(
        403,
        "forbidden_role",
        "The authenticated role cannot perform this operation.",
    )


def promotion_not_found() -> ApiError:
    return ApiError(
        404,
        "promotion_not_found",
        "No promotion has the requested promotion_id.",
    )


def unknown_campaign() -> ApiError:
    return ApiError(
        422,
        "unknown_campaign",
        "No campaign has the given campaign_id.",
        [{"field": "campaign_id", "code": "unknown_campaign"}],
    )


def promotion_outside_campaign(field: str) -> ApiError:
    return ApiError(
        422,
        "promotion_outside_campaign",
        "The promotion validity must lie within the campaign validity.",
        [{"field": field, "code": "outside_campaign"}],
    )


def unknown_store() -> ApiError:
    return ApiError(
        422,
        "unknown_store",
        "A store id does not exist.",
        [{"field": "store_ids", "code": "unknown_store"}],
    )


def unknown_channel() -> ApiError:
    return ApiError(
        422,
        "unknown_channel",
        "A channel id is not enabled at any listed store.",
        [{"field": "channel_ids", "code": "unknown_channel"}],
    )


def representation(row: tuple[Any, ...]) -> dict[str, Any]:
    (
        promotion_id,
        campaign_id,
        name,
        discount,
        starts,
        ends,
        stores,
        channels,
        active,
    ) = row
    return {
        "promotion_id": promotion_id,
        "campaign_id": campaign_id,
        "name": name,
        "discount_percent": f"{discount:.2f}",
        "starts_on": starts.isoformat(),
        "ends_on": ends.isoformat(),
        "store_ids": list(stores),
        "channel_ids": list(channels),
        "is_active": active,
    }


def get(connection: Connection, promotion_id: int) -> dict[str, Any] | None:
    row = connection.execute(GET, (promotion_id,)).fetchone()
    return representation(row) if row is not None else None


def require(
    connection: Connection,
    promotion_id: int,
    principal: dict[str, Any] | None = None,
) -> dict[str, Any]:
    promotion = get(connection, promotion_id)
    if promotion is None:
        raise promotion_not_found()
    if principal is not None and confined(principal):
        stores = assigned_stores(connection, principal)
        if not promotion["is_active"] or not set(stores).intersection(
            promotion["store_ids"]
        ):
            raise promotion_not_found()
    return promotion


def list_promotions(
    connection: Connection,
    principal: dict[str, Any],
    *,
    campaign_id: int | None,
    store_id: int | None,
    channel_id: int | None,
    active: bool | None,
    page: int,
    size: int,
) -> tuple[list[dict[str, Any]], int]:
    stores: list[int] | None = None
    if confined(principal):
        stores = assigned_stores(connection, principal)
        if active is False:
            raise forbidden_inactive()
        active = True
        if store_id is not None and store_id not in stores:
            raise forbidden_store()
        if store_id is not None:
            stores = [store_id]
    parameters = {
        "campaign": campaign_id,
        "store": store_id,
        "channel": channel_id,
        "active": active,
        "stores": stores,
        "limit": size,
        "offset": (page - 1) * size,
    }
    rows = connection.execute(LIST, parameters).fetchall()
    total = connection.execute(COUNT, parameters).fetchone()[0]
    return [representation(row) for row in rows], total


def _validated(
    connection: Connection,
    campaign_id: int,
    body: dict[str, Any],
) -> tuple[str, Decimal, date, date, list[int], list[int]]:
    campaign = connection.execute(LOCK_CAMPAIGN, (campaign_id,)).fetchone()
    if campaign is None:
        raise unknown_campaign()
    status, campaign_start, campaign_end = campaign
    if status != rules.DRAFT:
        raise rules.campaign_not_draft()
    name = rules.clean_name(body["name"])
    discount = Decimal(body["discount_percent"])
    if discount <= 0:
        raise ApiError(
            422,
            "invalid_body",
            "The request body violates its schema.",
            [{"field": "discount_percent", "code": "exclusive_minimum"}],
        )
    starts_on = rules.parse_date(body["starts_on"], "starts_on")
    ends_on = rules.parse_date(body["ends_on"], "ends_on")
    if ends_on < starts_on:
        raise rules.ends_before_start()
    if starts_on < campaign_start:
        raise promotion_outside_campaign("starts_on")
    if ends_on > campaign_end:
        raise promotion_outside_campaign("ends_on")
    stores = sorted(body["store_ids"])
    channels = sorted(body["channel_ids"])
    directory = connection.execute(
        "SELECT store_id, channel_ids FROM api_stores.store_directory_v2"
        " WHERE store_id = ANY(%s::smallint[]) ORDER BY store_id",
        (stores,),
    ).fetchall()
    if [row[0] for row in directory] != stores:
        raise unknown_store()
    enabled = {channel for row in directory for channel in row[1]}
    if not set(channels).issubset(enabled):
        raise unknown_channel()
    return name, discount, starts_on, ends_on, stores, channels


def _replace_members(
    connection: Connection,
    promotion_id: int,
    stores: list[int],
    channels: list[int],
) -> None:
    connection.execute(DELETE_STORES, (promotion_id,))
    connection.execute(DELETE_CHANNELS, (promotion_id,))
    for store_id in stores:
        connection.execute(INSERT_STORE, (promotion_id, store_id))
    for channel_id in channels:
        connection.execute(INSERT_CHANNEL, (promotion_id, channel_id))


def create(connection: Connection, body: dict[str, Any]) -> dict[str, Any]:
    campaign_id = body["campaign_id"]
    name, discount, starts, ends, stores, channels = _validated(
        connection, campaign_id, body
    )
    promotion_id = connection.execute(
        INSERT, (campaign_id, name, discount, starts, ends)
    ).fetchone()[0]
    _replace_members(connection, promotion_id, stores, channels)
    return require(connection, promotion_id)


def replace(
    connection: Connection, promotion_id: int, body: dict[str, Any]
) -> dict[str, Any]:
    current = connection.execute(
        "SELECT campaign_id FROM campaigns.promotion WHERE promotion_id = %s"
        " FOR UPDATE",
        (promotion_id,),
    ).fetchone()
    if current is None:
        raise promotion_not_found()
    campaign_id = current[0]
    name, discount, starts, ends, stores, channels = _validated(
        connection, campaign_id, body
    )
    connection.execute(UPDATE, (name, discount, starts, ends, promotion_id))
    _replace_members(connection, promotion_id, stores, channels)
    return require(connection, promotion_id)
