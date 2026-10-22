"""The references an event names, checked in PostgreSQL's published views.

svc_engagement may read exactly three views and no table (ADR-0044):
api_stores.store_directory_v1, api_products.product_ref_v1 and
api_customers.customer_ref_v1. Identifiers arrive only as bound parameters.
"""

from __future__ import annotations

from mosaiq_kit import ApiError
from psycopg import Connection

INT32 = (-(2**31), 2**31 - 1)


def _unknown(code: str, field: str, message: str) -> ApiError:
    return ApiError(422, code, message, [{"field": field, "code": code}])


def check_references(
    connection: Connection,
    store_id: int,
    product_id: int,
    customer_id: str | None,
) -> None:
    """Raise 422 unknown_store, unknown_product or unknown_customer, in that order."""

    if (
        not _in_range(store_id)
        or connection.execute(
            "SELECT 1 FROM api_stores.store_directory_v1"
            " WHERE store_id = %s AND is_active",
            (store_id,),
        ).fetchone()
        is None
    ):
        raise _unknown(
            "unknown_store",
            "store_id",
            f"Store {store_id} does not exist or is not active.",
        )
    if (
        not _in_range(product_id)
        or connection.execute(
            "SELECT 1 FROM api_products.product_ref_v1 WHERE product_id = %s",
            (product_id,),
        ).fetchone()
        is None
    ):
        raise _unknown(
            "unknown_product", "product_id", f"Product {product_id} does not exist."
        )
    if (
        customer_id is not None
        and connection.execute(
            "SELECT 1 FROM api_customers.customer_ref_v1 WHERE customer_id = %s::uuid",
            (customer_id,),
        ).fetchone()
        is None
    ):
        raise _unknown(
            "unknown_customer",
            "customer_id",
            f"Customer {customer_id} does not exist.",
        )


def _in_range(value: int) -> bool:
    return INT32[0] <= value <= INT32[1]
