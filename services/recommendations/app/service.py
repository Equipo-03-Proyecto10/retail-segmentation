"""The Flask application: `/v1` and `/v2` reads, their cache, and eight
invalidation consumers."""

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
from psycopg import Connection, errors

import redis

from . import decision_store, decisions, retention, rule, rule_v2, store
from .cache import ResultCache, result_key, result_key_v2
from .config import ASSIGNMENT_STREAM, DEFAULT_LIMIT, MAX_LIMIT, STREAMS, Settings

LOGGER = logging.getLogger(__name__)
SUFFIX = "recommendations"
_INT = re.compile(r"[0-9]+")
INT32_MAX = 2_147_483_647
# The roles that work only in the stores assigned to them and may name only
# those as the reference store (ADR-0060, the contract's getRecommendationsV2).
FLOOR_ROLES = frozenset({"SALES_ASSOCIATE", "STORE_MANAGER", "LOYALTY_OFFICER"})


_UNREADABLE = (
    errors.InsufficientPrivilege,
    errors.UndefinedTable,
    errors.UndefinedColumn,
)


def _unreadable(error: Exception) -> rule.Unreadable:
    LOGGER.warning("A consent view could not be read: %r", error)
    return rule.Unreadable(str(error))


class ViewReads:
    """rule.Reads over one connection, reading the published views."""

    def __init__(self, connection: Connection[Any], label: rule.Label):
        self.connection = connection
        self.label = label

    def personalization_granted(self, customer_id: str) -> bool:
        try:
            return store.personalization_granted(self.connection, customer_id)
        except _UNREADABLE as error:
            self.connection.rollback()
            raise _unreadable(error) from error

    def restricted_categories(self, customer_id: str) -> frozenset[int]:
        try:
            return store.restricted_categories(self.connection, customer_id)
        except _UNREADABLE as error:
            self.connection.rollback()
            raise _unreadable(error) from error

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

    def model_version(self, run_id: int) -> int | None:
        return store.model_version(self.connection, run_id)

    def products(self) -> list[rule_v2.Product]:
        return store.products(self.connection)

    def stores(self) -> dict[int, rule_v2.Store]:
        return store.stores(self.connection)

    def here(self, store_id: int, channel_id: int | None) -> dict[int, rule_v2.Here]:
        return store.here(self.connection, store_id, channel_id)

    def selling_elsewhere(
        self, product_ids: list[int], store_id: int, channel_id: int | None
    ) -> dict[int, dict[int, int]]:
        return store.selling_elsewhere(
            self.connection, product_ids, store_id, channel_id
        )

    def substitutes(self, product_ids: list[int]) -> dict[int, list[int]]:
        return store.substitutes(self.connection, product_ids)


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
    normalized = value.lstrip("0") or "0"
    if len(normalized) > len(str(MAX_LIMIT)) or not 1 <= int(normalized) <= MAX_LIMIT:
        raise _invalid_query("limit", "out_of_range", message)
    return int(normalized)


def _positive_id(name: str) -> int | None:
    """An optional positive int32 query parameter."""
    value = request.args.get(name)
    if value is None:
        return None
    message = f"{name} must be a positive whole number."
    if _INT.fullmatch(value) is None:
        raise _invalid_query(name, "invalid_type", message)
    normalized = value.lstrip("0") or "0"
    if len(normalized) > len(str(INT32_MAX)) or not 1 <= int(normalized) <= INT32_MAX:
        raise _invalid_query(name, "out_of_range", message)
    return int(normalized)


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
            reads = ViewReads(connection, rule.Label("no_open_assignment", None, None))
            allowed = rule.eligibility(reads, customer_id)
            if not allowed.personalization:
                # Never cached: the next read looks at consent again. The answer
                # is still recorded, with its status and no items or signals
                # (ADR-0067, Consent); /v1 can only send it as none_match.
                now = datetime.now(UTC)
                withdrawn = rule.withdrawn(customer_id, now)
                decision_store.record(
                    connection,
                    decisions.withdrawn(
                        customer_id, decision_id=str(uuid.uuid4()), computed_at=now
                    ),
                )
                return Representation(
                    "Recommendation", wire_value(withdrawn.as_dict(now))
                )
            reads.label = store.current_label(connection, customer_id)
            key = result_key(customer_id, reads.label.run_id, limit, allowed.restricted)
            generation = cache.generation()
            cached = cache.get(key, generation)
            if cached is not None:
                # Nothing is written: the answer is the one already recorded.
                LOGGER.info("Served cached decision %s", cached.decision_id)
                return Representation("Recommendation", cached.value)
            now = datetime.now(UTC)
            result = rule.recommend(reads, customer_id, now, limit, allowed.restricted)
            # The record comes first and is committed with this connection, so
            # an answer is never cached, or sent, without its decision.
            decision = decisions.from_v1(
                result, decision_id=str(uuid.uuid4()), computed_at=now
            )
            decision_store.record(connection, decision)
        data = wire_value(result.as_dict(now))
        cache.put(key, data, generation, decision.decision_id)
        return Representation("Recommendation", data)

    @kit.operation("getRecommendationsV2")
    def get_recommendations_v2() -> Representation:
        # Refusals in the contract's order (ADR-0084): a malformed parameter,
        # then a floor role's store, then what does not exist.
        customer_id = _customer_id()
        limit = _limit()
        store_id = _positive_id("store_id")
        channel_id = _positive_id("channel_id")
        principal = kit.principal or {}
        with kit.db.connection() as connection:
            reference = None
            if store_id is not None:
                if principal.get("role") in FLOOR_ROLES and (
                    store_id
                    not in store.assigned_stores(connection, str(principal["sub"]))
                ):
                    raise ApiError(
                        403,
                        "forbidden_store",
                        "The caller is not assigned to this store.",
                        [{"field": "store_id", "code": "other_store"}],
                    )
                reference = store.store(connection, store_id)
                if reference is None:
                    raise ApiError(
                        422,
                        "unknown_store",
                        f"Store {store_id} does not exist.",
                        [{"field": "store_id", "code": "unknown_store"}],
                    )
            if channel_id is not None and not store.channel_exists(
                connection, channel_id
            ):
                raise ApiError(
                    422,
                    "unknown_channel",
                    f"Channel {channel_id} does not exist.",
                    [{"field": "channel_id", "code": "unknown_channel"}],
                )
            if not store.customer_exists(connection, customer_id):
                raise ApiError(
                    422,
                    "unknown_customer",
                    f"Customer {customer_id} does not exist.",
                    [{"field": "customer_id", "code": "unknown_customer"}],
                )
            reads = ViewReads(connection, rule.Label("no_open_assignment", None, None))
            allowed = rule.eligibility(reads, customer_id)
            now = datetime.now(UTC)
            if not allowed.personalization:
                # Never cached, and nothing of the customer's is read; the
                # answer is still recorded (ADR-0067, Consent).
                result = rule_v2.withdrawn(
                    customer_id, now, store=reference, channel_id=channel_id
                )
                decision = result.decision(str(uuid.uuid4()), now)
                decision_store.record(connection, decision)
                return Representation(
                    "Recommendation", wire_value(result.as_dict(decision))
                )
            reads.label = store.current_label(connection, customer_id)
            version = (
                store.model_version(connection, reads.label.run_id)
                if reads.label.run_id is not None
                else None
            )
            key = result_key_v2(
                customer_id,
                reads.label.run_id,
                version,
                limit,
                store_id,
                channel_id,
                allowed.restricted,
            )
            generation = cache.generation()
            cached = cache.get(key, generation)
            if cached is not None:
                # Nothing is written: the answer is the one already recorded.
                LOGGER.info("Served cached decision %s", cached.decision_id)
                return Representation("Recommendation", cached.value)
            result = rule_v2.recommend(
                reads,
                customer_id,
                now,
                limit,
                allowed.restricted,
                store=reference,
                channel_id=channel_id,
            )
            # Recorded and committed before it is cached or sent (ADR-0067).
            decision = result.decision(str(uuid.uuid4()), now)
            decision_store.record(connection, decision)
        data = wire_value(result.as_dict(decision))
        cache.put(key, data, generation, decision.decision_id)
        return Representation("Recommendation", data)

    _start_consumers(app, settings, cache)
    if settings.retention_enabled:
        retention.start(app, kit)
    return app


def _start_consumers(app: Flask, settings: Settings, cache: ResultCache) -> None:
    """One consumer per stream, group `recommendations`, each emptying the cache (a
    changed assignment empties only that customer's results)."""
    if not settings.stream_consumer_enabled:
        return

    def handle(_fields: dict[str, str]) -> None:
        # A Redis failure raises, so the kit redelivers, up to five times.
        cache.invalidate()

    def handle_assignment(fields: dict[str, str]) -> None:
        """A customer's label changed: only their results are stale. An entry that
        names no customer cannot be narrowed, so it empties the cache as any other."""
        customer_id = fields.get("entity_id")
        if customer_id:
            cache.invalidate_customer(customer_id)
        else:
            cache.invalidate()

    consumers = []
    for stream in STREAMS:
        consumer = StreamConsumer(
            _long_timeout_client(settings.redis_state_url, 10),
            stream,
            handle_assignment if stream == ASSIGNMENT_STREAM else handle,
        )
        consumer.start()
        consumers.append(consumer)
    app.extensions["recommendations_consumers"] = consumers
