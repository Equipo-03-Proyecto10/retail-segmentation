"""The categories rule: an acyclic tree that is never torn from its users.

The cycle checks, the foreign keys and the delete guard live in the schema;
this module maps each refusal, by constraint name, to the contract's error.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

import psycopg
from flask import Flask, request
from mosaiq_kit import (
    ApiError,
    Kit,
    NoContent,
    OutboxRelay,
    Page,
    Representation,
    StreamConsumer,
    configure_logging,
)
from psycopg import errors

import redis

from .cache import TreeCache

LOGGER = logging.getLogger(__name__)

CONTRACTS_DIR = Path(__file__).resolve().parents[1] / "contracts"
STREAM = "stream:categories.v1.changed"
COLUMNS = "category_id, name, parent_category_id"

NAME_KEY = "category_name_key"
PRIMARY_KEY = "category_pkey"
PARENT_FKEY = "category_parent_category_id_fkey"
PRODUCT_FKEY = "product_category_id_fkey"
INTEREST_GUARD = "category_has_customer_interest"
CYCLE_CHECKS = ("category_not_own_parent", "category_no_cycle")


def not_found() -> ApiError:
    return ApiError(404, "category_not_found", "The category does not exist.")


def refusal(error: psycopg.errors.IntegrityError, *, deleting: bool) -> ApiError | None:
    """The contract's error for a constraint of category, or None."""

    constraint = error.diag.constraint_name
    if isinstance(error, errors.UniqueViolation):
        if constraint == PRIMARY_KEY:
            return ApiError(
                409,
                "category_id_taken",
                "The category id is already used.",
                [{"field": "category_id", "code": "duplicate"}],
            )
        if constraint == NAME_KEY:
            return ApiError(
                409,
                "category_name_taken",
                "The category name is already used.",
                [{"field": "name", "code": "duplicate"}],
            )
    # ON DELETE RESTRICT raises 23001 (restrict_violation); a foreign key that
    # fails on write, and the guard, raise 23503. The constraint name decides.
    if isinstance(error, errors.ForeignKeyViolation | errors.RestrictViolation):
        if constraint == PARENT_FKEY and deleting:
            return ApiError(
                409,
                "category_has_children",
                "The category still has subcategories.",
            )
        if constraint == PARENT_FKEY:
            return ApiError(
                422,
                "unknown_parent_category",
                "The parent category does not exist.",
                [{"field": "parent_category_id", "code": "unknown"}],
            )
        if constraint == PRODUCT_FKEY:
            return ApiError(
                409, "category_has_products", "The category still has products."
            )
        if constraint == INTEREST_GUARD:
            return ApiError(
                409,
                "category_has_customer_interest",
                "A customer names the category as an interest.",
            )
    if isinstance(error, errors.CheckViolation) and constraint in CYCLE_CHECKS:
        return ApiError(
            409,
            "category_cycle",
            "The category cannot be its own parent or ancestor.",
            [{"field": "parent_category_id", "code": "cycle"}],
        )
    return None


def category_json(row: tuple[Any, ...]) -> dict[str, Any]:
    return {"category_id": row[0], "name": row[1], "parent_category_id": row[2]}


def parent_filter() -> int | None:
    raw = request.args.get("parent_id")
    if raw is None:
        return None
    # Strict base-10 digits: int() alone accepts " 1 ", "1_0" and "+1".
    if re.fullmatch(r"-?[0-9]{1,10}", raw) and -(2**31) <= int(raw) < 2**31:
        return int(raw)
    raise ApiError(
        422,
        "invalid_pagination",
        "parent_id must be an integer.",
        [{"field": "parent_id", "code": "invalid_type"}],
    )


def start_background(kit: Kit, cache: TreeCache) -> tuple[OutboxRelay, StreamConsumer]:
    """The owner's relay and its own consumer group (ADR-0044)."""

    url = os.environ["REDIS_STATE_URL"]
    password = os.environ.get("REDIS_CATEGORIES_PASSWORD")

    def client() -> redis.Redis:
        return redis.Redis.from_url(
            url,
            username="svc_categories",
            password=password,
            socket_connect_timeout=2,
            socket_timeout=10,
            decode_responses=True,
        )

    relay = OutboxRelay(kit.db.dsn, "categories", [STREAM], client())
    consumer = StreamConsumer(client(), STREAM, lambda _fields: cache.invalidate())
    relay.start()
    consumer.start()
    return relay, consumer


def create_app() -> Flask:
    app = Flask(__name__)
    configure_logging()
    holder: dict[str, Kit] = {}
    cache = TreeCache.from_environment()

    def schema_ready() -> bool:
        with holder["kit"].db.connection() as connection:
            connection.execute(
                "SELECT category_id FROM api_categories.category_tree_v1 LIMIT %s",
                (1,),
            ).fetchall()
        return True

    kit = Kit(
        app,
        service="categories",
        suffix="categories",
        contracts_dir=CONTRACTS_DIR,
        ready_checks=[schema_ready],
    )
    holder["kit"] = kit
    app.extensions["mosaiq_kit"] = kit
    app.extensions["categories_cache"] = cache

    def page_of(name: str, query: str, params: dict[str, Any]) -> Page:
        offset = (kit.page - 1) * kit.size
        key = f"{name}:{kit.page}:{kit.size}"
        cached = cache.get(key)
        if cached is None:
            with kit.db.connection() as connection:
                total = connection.execute(
                    "SELECT count(*) FROM (" + query + ") AS matched", params
                ).fetchone()[0]
                rows = connection.execute(
                    query + " ORDER BY category_id LIMIT %(limit)s OFFSET %(offset)s",
                    params | {"limit": kit.size, "offset": offset},
                ).fetchall()
            cached = {"items": [category_json(row) for row in rows], "total": total}
            cache.put(key, cached)
        return Page(
            "Categories",
            "Category",
            "categories",
            cached["items"],
            page=kit.page,
            size=kit.size,
            total=cached["total"],
        )

    def existing(category_id: int) -> dict[str, Any]:
        key = f"one:{category_id}"
        cached = cache.get(key)
        if cached is not None:
            return cached
        with kit.db.connection() as connection:
            row = connection.execute(
                "SELECT " + COLUMNS + " FROM public.category WHERE category_id = %s",
                (category_id,),
            ).fetchone()
        if row is None:
            raise not_found()
        cache.put(key, category_json(row))
        return category_json(row)

    @kit.operation("listCategories")
    def list_categories() -> Page:
        parent = parent_filter()
        return page_of(
            f"list:{parent}",
            "SELECT " + COLUMNS + " FROM public.category"
            " WHERE (%(parent)s::smallint IS NULL"
            " OR parent_category_id = %(parent)s::smallint)",
            {"parent": parent},
        )

    @kit.operation("getCategory")
    def get_category(category_id: int) -> Representation:
        return Representation("Category", existing(category_id))

    @kit.operation("listCategoryDescendants")
    def list_descendants(category_id: int) -> Page:
        existing(category_id)
        return page_of(
            f"descendants:{category_id}",
            "WITH RECURSIVE subtree(category_id) AS ("
            " SELECT category_id FROM public.category"
            " WHERE parent_category_id = %(root)s::smallint"
            " UNION"
            " SELECT c.category_id FROM public.category AS c"
            " JOIN subtree AS s ON c.parent_category_id = s.category_id)"
            " SELECT " + COLUMNS + " FROM public.category"
            " WHERE category_id IN (SELECT category_id FROM subtree)",
            {"root": category_id},
        )

    def write(statement: str, params: dict[str, Any], *, deleting: bool = False):
        try:
            with kit.db.transaction() as connection:
                return connection.execute(statement, params).fetchone()
        except psycopg.errors.IntegrityError as error:
            mapped = refusal(error, deleting=deleting)
            if mapped is None:
                raise
            raise mapped from None

    @kit.operation("createCategory")
    def create_category() -> Representation:
        body = kit.body
        try:
            row = write(
                "INSERT INTO public.category (category_id, name, parent_category_id)"
                " VALUES (%(id)s, %(name)s, %(parent)s) RETURNING " + COLUMNS,
                {
                    "id": body["category_id"],
                    "name": body["name"],
                    "parent": body.get("parent_category_id"),
                },
            )
        except ApiError as error:
            if error.code == "category_cycle":
                raise ApiError(
                    422,
                    "invalid_body",
                    "The parsed body violates the request schema.",
                    [{"field": "parent_category_id", "code": "invalid"}],
                ) from None
            raise
        cache.invalidate_quietly()
        category = category_json(row)
        return Representation(
            "Category",
            category,
            status=201,
            headers={"Location": f"/v1/categories/{category['category_id']}"},
        )

    @kit.operation("updateCategory")
    def update_category(category_id: int) -> Representation:
        body = kit.body
        row = write(
            "UPDATE public.category SET"
            " name = CASE WHEN %(set_name)s THEN %(name)s ELSE name END,"
            " parent_category_id = CASE WHEN %(set_parent)s"
            " THEN %(parent)s::smallint ELSE parent_category_id END"
            " WHERE category_id = %(id)s RETURNING " + COLUMNS,
            {
                "id": category_id,
                "set_name": "name" in body,
                "name": body.get("name"),
                "set_parent": "parent_category_id" in body,
                "parent": body.get("parent_category_id"),
            },
        )
        if row is None:
            raise not_found()
        cache.invalidate_quietly()
        return Representation("Category", category_json(row))

    @kit.operation("deleteCategory")
    def delete_category(category_id: int) -> NoContent:
        row = write(
            "DELETE FROM public.category WHERE category_id = %(id)s"
            " RETURNING category_id",
            {"id": category_id},
            deleting=True,
        )
        if row is None:
            raise not_found()
        cache.invalidate_quietly()
        return NoContent()

    if os.environ.get("CATEGORIES_BACKGROUND_WORKERS", "true").lower() != "false":
        app.extensions["categories_workers"] = start_background(kit, cache)
    return app
