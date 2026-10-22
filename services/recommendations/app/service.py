"""The Flask application: one read, its cache, and six invalidation consumers."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from flask import Flask, request
from mosaiq_kit import (
    ApiError,
    Kit,
    Representation,
    StreamConsumer,
    configure_logging,
)
from mosaiq_kit.auth import service_redis
from mosaiq_kit.rendering import wire_value
from psycopg import Connection

import redis

from . import rule, store
from .cache import ResultCache, result_key
from .config import DEFAULT_LIMIT, MAX_LIMIT, STREAMS, Settings

LOGGER = logging.getLogger(__name__)
SUFFIX = "recommendations"
_INT = re.compile(r"[0-9]+")


class ViewReads:
    """rule.Reads over one connection, reading the published views."""

    def __init__(self, connection: Connection[Any], label: rule.Label):
        self.connection = connection
        self.label = label

    def current_label(self, customer_id: str) -> rule.Label:
        return self.label

    def purchase_lines(
        self, customer_id: str, since: datetime, until: datetime
    ) -> list[rule.Line]:
        return store.purchase_lines(self.connection, customer_id, since, until)

    def stocked_products(self, store_id: int) -> list[rule.Stocked]:
        return store.stocked_products(self.connection, store_id)

    def segment_buyers(
        self, label_code: str, customer_id: str, since: datetime, until: datetime
    ) -> dict[int, int]:
        return store.segment_buyers(
            self.connection, label_code, customer_id, since, until
        )

    def interest_categories(self, customer_id: str) -> set[int]:
        return store.interest_categories(self.connection, customer_id)

    def category_parents(self) -> dict[int, int | None]:
        return store.category_parents(self.connection)


def _invalid_query(field: str, code: str, message: str) -> ApiError:
    return ApiError(422, "invalid_query", message, [{"field": field, "code": code}])


def _customer_id() -> str:
    value = request.args.get("customer_id")
    if value is None or value == "":
        raise _invalid_query("customer_id", "required", "customer_id is required.")
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        raise _invalid_query(
            "customer_id", "invalid_format", "customer_id must be a UUID."
        ) from None
    return str(parsed)


def _limit() -> int:
    value = request.args.get("limit")
    if value is None:
        return DEFAULT_LIMIT
    message = f"limit must be a whole number from 1 to {MAX_LIMIT}."
    if _INT.fullmatch(value) is None:
        raise _invalid_query("limit", "invalid_type", message)
    if not 1 <= int(value) <= MAX_LIMIT:
        raise _invalid_query("limit", "out_of_range", message)
    return int(value)


def _long_timeout_client(url: str, timeout: float) -> redis.Redis:
    """The consumers' client: a 5 s blocking XREADGROUP outlasts the kit's
    200 ms default, which suits only the liveness check."""
    client = service_redis(url, SUFFIX)
    client.connection_pool.connection_kwargs["socket_timeout"] = timeout
    return client


def create_app() -> Flask:
    settings = Settings.from_env()
    app = Flask(__name__)
    configure_logging()
    holder: dict[str, Kit] = {}

    def database_ready() -> bool:
        with holder["kit"].db.connection() as connection:
            connection.execute(
                "SELECT customer_id FROM api_customers.customer_ref_v1 LIMIT 1"
            )
        return True

    kit = Kit(
        app,
        service="recommendations",
        suffix=SUFFIX,
        contracts_dir=settings.contracts_dir,
        ready_checks=[database_ready],
    )
    holder["kit"] = kit
    cache = ResultCache(
        service_redis(settings.redis_cache_url, SUFFIX)
        if settings.cache_enabled
        else None
    )
    app.extensions["mosaiq_kit"] = kit
    app.extensions["recommendations_cache"] = cache

    @kit.operation("getRecommendations")
    def get_recommendations() -> Representation:
        customer_id = _customer_id()
        limit = _limit()
        with kit.db.connection() as connection:
            if not store.customer_exists(connection, customer_id):
                raise ApiError(
                    422,
                    "unknown_customer",
                    f"Customer {customer_id} does not exist.",
                    [{"field": "customer_id", "code": "unknown_customer"}],
                )
            label = store.current_label(connection, customer_id)
            key = result_key(customer_id, label.run_id, limit)
            cached = cache.get(key)
            if cached is not None:
                return Representation("Recommendation", cached)
            now = datetime.now(UTC)
            result = rule.recommend(
                ViewReads(connection, label), customer_id, now, limit
            )
        data = wire_value(result.as_dict(now))
        cache.put(key, data)
        return Representation("Recommendation", data)

    _start_consumers(app, settings, cache)
    return app


def _start_consumers(app: Flask, settings: Settings, cache: ResultCache) -> None:
    """One consumer per stream, group `recommendations`, each emptying the cache."""
    if not settings.stream_consumer_enabled:
        return

    def handle(_fields: dict[str, str]) -> None:
        # A Redis failure raises, so the kit redelivers, up to five times.
        cache.invalidate()

    consumers = []
    for stream in STREAMS:
        consumer = StreamConsumer(
            _long_timeout_client(settings.redis_state_url, 10), stream, handle
        )
        consumer.start()
        consumers.append(consumer)
    app.extensions["recommendations_consumers"] = consumers
