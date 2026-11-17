"""Store narrowing of the four floor roles (ADR-0060, F19-07).

A user with one of the four floor roles works only in the stores assigned to
them, and a role that is not a floor role is never confined: `ADMIN`,
`ANALYST`, `MARKETING` and `AUDITOR` are not assigned to stores, and the ones
that read sales read every store.

The assignments are read from `api_users.store_assignment_v1` inside the
request that needs them, with a parameterised statement, and are kept neither
in the process, nor in Redis, nor in the token (ADR-0036, ADR-0044). A change
that commits is therefore seen by the next request. A floor user with no row
is assigned to no store and can work in none.
"""

from __future__ import annotations

from typing import Any

import psycopg
from mosaiq_kit import ApiError

FLOOR_ROLES = frozenset(
    {"STORE_MANAGER", "SALES_ASSOCIATE", "LOYALTY_OFFICER", "COMMERCIAL_SUPERVISOR"}
)

ASSIGNED = (
    "SELECT store_id FROM api_users.store_assignment_v1"
    " WHERE user_id = %s::uuid ORDER BY store_id"
)


def confined(principal: dict[str, Any]) -> bool:
    """Whether the caller's role is one that works only in assigned stores."""
    return principal.get("role") in FLOOR_ROLES


def assigned_stores(
    connection: psycopg.Connection, principal: dict[str, Any]
) -> list[int]:
    """The caller's stores as of this statement."""
    return [row[0] for row in connection.execute(ASSIGNED, (str(principal["sub"]),))]


def forbidden_store(what: str | None = None) -> ApiError:
    """403 `forbidden_store`: no data, and nothing about the row except that it
    is somewhere else. `what` names the sale or reversal a lookup by id found."""
    if what is None:
        return ApiError(
            403,
            "forbidden_store",
            "The caller is not assigned to this store.",
            [{"field": "store_id", "code": "other_store"}],
        )
    return ApiError(
        403,
        "forbidden_store",
        f"The {what} belongs to a store the caller is not assigned to.",
        [{"field": f"{what}_id", "code": "other_store"}],
    )


def require_store(
    connection: psycopg.Connection,
    principal: dict[str, Any],
    store_id: int,
    *,
    what: str | None = None,
) -> None:
    """Refuse a store the caller is confined away from."""
    if confined(principal) and store_id not in assigned_stores(connection, principal):
        raise forbidden_store(what)


def readable_stores(
    connection: psycopg.Connection,
    principal: dict[str, Any],
    store_id: int | None,
) -> list[int] | None:
    """The stores a list may cover: None for every store, else the caller's.

    A store named by a confined caller must be one of theirs (403). With none
    named, the list covers the caller's stores, which is empty for a floor
    user with no assignment.
    """
    if not confined(principal):
        return None
    stores = assigned_stores(connection, principal)
    if store_id is None:
        return stores
    if store_id not in stores:
        raise forbidden_store()
    return [store_id]
