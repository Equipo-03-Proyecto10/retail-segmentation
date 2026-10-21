"""The Flask application: routes, error mapping, cache, relay and consumer."""

from __future__ import annotations

import logging
import re
from typing import Any

import psycopg
from flask import Flask, request
from mosaiq_kit import (
    ApiError,
    Kit,
    OutboxRelay,
    Page,
    Representation,
    StreamConsumer,
    configure_logging,
)
from mosaiq_kit.auth import service_redis

import redis

from . import store
from .cache import ReadCache
from .config import CHANGED_STREAM, MAX_BATCH_IDS, Settings

LOGGER = logging.getLogger(__name__)
SUFFIX = "products"
_IDS = re.compile(r"[1-9][0-9]*(,[1-9][0-9]*)*")
_INT = re.compile(r"[0-9]+")


def _long_timeout_client(url: str, timeout: float) -> redis.Redis:
    """A signed-in client for the relay and the consumer.

    The kit's default client times out after 200 ms, which suits a liveness
    check but not WAITAOF (up to 2 s) or a 5 s blocking XREADGROUP.
    """

    client = service_redis(url, SUFFIX)
    client.connection_pool.connection_kwargs["socket_timeout"] = timeout
    return client


def _invalid_query(field: str, code: str) -> ApiError:
    return ApiError(
        422,
        "invalid_query",
        "A query parameter is outside its allowed range.",
        [{"field": field, "code": code}],
    )


def _text_filter(name: str, maximum: int) -> str | None:
    value = request.args.get(name)
    if value is None:
        return None
    if not 1 <= len(value) <= maximum:
        raise _invalid_query(name, "too_short" if not value else "too_long")
    return value


def _category_filter() -> int | None:
    value = request.args.get("category_id")
    if value is None:
        return None
    if _INT.fullmatch(value) is None:
        raise _invalid_query("category_id", "invalid_type")
    if not 1 <= int(value) <= 32767:
        raise _invalid_query("category_id", "out_of_range")
    return int(value)


def _active_filter() -> bool | None:
    value = request.args.get("active")
    if value is None:
        return None
    if value not in {"true", "false"}:
        raise _invalid_query("active", "invalid_type")
    return value == "true"


def _ids_filter() -> list[int] | None:
    value = request.args.get("ids")
    if value is None:
        return None
    if _IDS.fullmatch(value) is None:
        raise _invalid_query("ids", "invalid_format")
    ids = [int(item) for item in value.split(",")]
    if len(ids) > MAX_BATCH_IDS:
        raise ApiError(
            422,
            "too_many_ids",
            "At most 50 product ids may be requested.",
            [{"field": "ids", "code": "max_items"}],
        )
    if any(item > 2147483647 for item in ids):
        raise _invalid_query("ids", "out_of_range")
    return sorted(set(ids))


def _whole_pattern_match(body: dict[str, Any], *fields: str) -> None:
    """Refuse what the contract's patterns refuse, including a final newline.

    The kit validates `pattern` with Python's `re`, where `$` also matches just
    before a final newline, so "1.00\n" passed the ListPrice pattern (#594).
    The contract's pattern means the whole value.
    """
    for field in fields:
        value = body.get(field)
        if isinstance(value, str) and value.endswith("\n"):
            raise ApiError(
                422,
                "invalid_body",
                "The request body violates the service schema.",
                [{"field": field, "code": "invalid_format"}],
            )


def _constraint(error: psycopg.errors.Diagnostic | Any) -> str:
    return getattr(getattr(error, "diag", None), "constraint_name", None) or ""


def _map_write_error(error: psycopg.Error) -> ApiError:
    """Translate the schema's refusals into the contract's codes."""

    constraint = _constraint(error)
    if isinstance(error, psycopg.errors.UniqueViolation):
        if constraint == "product_sku_key":
            return ApiError(
                409,
                "sku_taken",
                "The SKU is already assigned to a product.",
                [{"field": "sku", "code": "duplicate"}],
            )
        if constraint == "product_pkey":
            return ApiError(
                409,
                "product_id_taken",
                "The product_id is already assigned to a product.",
                [{"field": "product_id", "code": "duplicate"}],
            )
    if isinstance(error, psycopg.errors.ForeignKeyViolation):
        if constraint == "product_category_id_fkey":
            return ApiError(
                422,
                "unknown_category",
                "The category_id does not identify an existing category.",
                [{"field": "category_id", "code": "unknown"}],
            )
    if isinstance(error, psycopg.errors.CheckViolation):
        if constraint == "product_list_price_check":
            return ApiError(
                422,
                "invalid_body",
                "The request body violates the product schema.",
                [{"field": "list_price", "code": "out_of_range"}],
            )
    raise error


def create_app() -> Flask:
    settings = Settings.from_env()
    app = Flask(__name__)
    configure_logging()
    holder: dict[str, Kit] = {}

    def database_ready() -> bool:
        with holder["kit"].db.connection() as connection:
            connection.execute(
                "SELECT product_id FROM api_products.product_ref_v1 LIMIT 1"
            )
        return True

    kit = Kit(
        app,
        service="products",
        suffix=SUFFIX,
        contracts_dir=settings.contracts_dir,
        ready_checks=[database_ready],
    )
    holder["kit"] = kit
    # RATE_LIMIT_ENABLED is read by the kit itself (default true).

    cache = ReadCache(
        service_redis(settings.redis_cache_url, SUFFIX)
        if settings.cache_enabled
        else None
    )
    app.extensions["products_cache"] = cache
    app.extensions["mosaiq_kit"] = kit

    def invalidate() -> None:
        try:
            cache.invalidate()
        except redis.RedisError as error:
            LOGGER.warning("Cache invalidation failed: %r", error)

    @kit.operation("listProducts")
    def list_products() -> Page:
        filters = {
            "q": _text_filter("q", 150),
            "sku": _text_filter("sku", 40),
            "category_id": _category_filter(),
            "active": _active_filter(),
            "ids": _ids_filter(),
        }
        generation = cache.generation()
        name = cache.list_key({**filters, "page": kit.page, "size": kit.size})
        cached = cache.get(generation, name)
        if cached is None:
            with kit.db.connection() as connection:
                items, total = store.list_products(
                    connection,
                    **filters,
                    limit=kit.size,
                    offset=(kit.page - 1) * kit.size,
                )
            cached = {"items": items, "total": total}
            cache.put(generation, name, cached)
        return Page(
            "Products",
            "Product",
            "products",
            cached["items"],
            page=kit.page,
            size=kit.size,
            total=cached["total"],
        )

    @kit.operation("getProduct")
    def get_product(product_id: int) -> Representation:
        generation = cache.generation()
        name = cache.item_key(product_id)
        product = cache.get(generation, name)
        if product is None:
            with kit.db.connection() as connection:
                product = store.get_product(connection, product_id)
            if product is None:
                raise _not_found()
            cache.put(generation, name, product)
        return Representation("Product", product)

    @kit.operation("createProduct")
    def create_product() -> Representation:
        body = kit.body
        _whole_pattern_match(body, "list_price")
        try:
            with kit.db.transaction() as connection:
                product = store.create_product(connection, body)
        except psycopg.errors.IntegrityError as error:
            raise _map_write_error(error) from None
        invalidate()
        return Representation(
            "Product",
            product,
            status=201,
            headers={"Location": f"/v1/products/{product['product_id']}"},
        )

    @kit.operation("updateProduct")
    def update_product(product_id: int) -> Representation:
        _whole_pattern_match(kit.body, "list_price")
        try:
            with kit.db.transaction() as connection:
                product = store.update_product(connection, product_id, kit.body)
        except psycopg.errors.IntegrityError as error:
            raise _map_write_error(error) from None
        if product is None:
            raise _not_found()
        invalidate()
        return Representation("Product", product)

    _start_workers(app, kit, settings, cache)
    return app


def _not_found() -> ApiError:
    return ApiError(
        404, "product_not_found", "No product has the requested product_id."
    )


def _start_workers(app: Flask, kit: Kit, settings: Settings, cache: ReadCache) -> None:
    """The outbox relay and the invalidation consumer, one thread each."""

    if settings.relay_enabled and kit.db.dsn:
        relay = OutboxRelay(
            kit.db.dsn,
            SUFFIX,
            [CHANGED_STREAM],
            _long_timeout_client(settings.redis_state_url, 5),
        )
        relay.start()
        app.extensions["products_relay"] = relay

    if settings.stream_consumer_enabled:

        def handle(_fields: dict[str, str]) -> None:
            # Any change makes every cached read stale; a Redis failure raises
            # so the kit redelivers, up to five times.
            if cache.enabled:
                cache.invalidate()

        consumer = StreamConsumer(
            _long_timeout_client(settings.redis_state_url, 10),
            CHANGED_STREAM,
            handle,
        )
        consumer.start()
        app.extensions["products_consumer"] = consumer
