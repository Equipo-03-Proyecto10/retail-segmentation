"""rfm-engine (F22-04, F22-05, F22-06, RN-65): keeps each customer's R, F and M current.

A sale recorded on `stream:sales.v1.recorded`, and a reversal of one on
`stream:sales.v1.reversed`, each name a transaction. The service finds the customer
of that sale and recomputes that one customer's profile from their net purchases
under the rule set in force, so a duplicate, a late or a reordered delivery gives the
profile one in-order delivery would. A cancelled or returned sale leaves the profile
as if it had never counted, because the net view no longer lists it (ADR-0062). A
change to the captured values puts one profile-changed event in the outbox, in the
same transaction (ADR-0063). The other
operations of contracts/rfm-engine/openapi.yaml are marked planned (ADR-0073) until
the stories after this one register them, except the rule sets (F22-09): an analyst
creates one as a draft, edits it, publishes it, and a published one is never edited,
only versioned (app/rulesets.py). It is ready when PostgreSQL answers and its
own schema is there.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import redis
from flask import Flask, request
from mosaiq_kit import Kit, Page, Representation, configure_logging
from mosaiq_kit.auth import service_redis
from mosaiq_kit.rendering import wire_value
from mosaiq_kit.streams import OutboxRelay, StreamConsumer

from . import ageing, recompute, rulesets
from .config import RECORDED_STREAM, REVERSED_STREAM, Settings

LOGGER = logging.getLogger(__name__)
SUFFIX = "rfm"


def _client(url: str, timeout: float) -> redis.Redis:
    """The restricted client of the consumers and the relay: a blocking XREADGROUP
    outlasts the kit's 200 ms default, which suits only the liveness check."""
    client = service_redis(url, SUFFIX)
    client.connection_pool.connection_kwargs["socket_timeout"] = timeout
    return client


def create_app() -> Flask:
    settings = Settings.from_env()
    app = Flask(__name__, static_folder=None)
    configure_logging()
    holder: dict[str, Kit] = {}

    def schema_ready() -> bool:
        with holder["kit"].db.connection() as connection:
            row = connection.execute(
                "SELECT to_regnamespace(%s) IS NOT NULL", ("rfm",)
            ).fetchone()
        return bool(row and row[0])

    kit = Kit(
        app,
        service="rfm-engine",
        suffix=SUFFIX,
        contracts_dir=Path(__file__).resolve().parent.parent / "contracts",
        ready_checks=[schema_ready],
    )
    holder["kit"] = kit
    app.extensions["mosaiq_kit"] = kit

    @kit.operation("listRfmRuleSets")
    def list_rule_sets() -> Page:
        flag = request.args.get("published")
        with kit.db.connection() as connection:
            items, total = rulesets.listing(
                connection,
                published_only=None if flag is None else flag == "true",
                page=kit.page,
                size=kit.size,
            )
        items = [wire_value(rulesets.compact(item)) for item in items]
        return Page(
            "RfmRuleSets",
            "RfmRuleSet",
            "rule_sets",
            items,
            page=kit.page,
            size=kit.size,
            total=total,
            info_key="page_info",
        )

    def recompute_sale(sale: str, fields: dict[str, str]) -> None:
        if not sale.isdigit():
            LOGGER.warning("Entry without a transaction id: %r", fields)
            return
        with kit.db.transaction() as connection:
            recompute.from_sale(connection, int(sale), settings.rule_set_id)

    def author() -> dict[str, Any]:
        principal = kit.principal
        assert principal is not None
        return principal

    @kit.operation("createRfmRuleSet")
    def create_rule_set() -> Representation:
        definition = rulesets.check(kit.body)
        with kit.db.transaction() as connection:
            rule_set_id = rulesets.create(connection, author(), definition)
            created = rulesets.one(connection, rule_set_id)
        response = Representation("RfmRuleSetDetail", wire_value(created), status=201)
        response.headers = {"Location": f"/v1/rfm-rule-sets/{rule_set_id}"}
        return response

    @kit.operation("getRfmRuleSet")
    def get_rule_set(rule_set_id: int) -> Representation:
        with kit.db.connection() as connection:
            return Representation(
                "RfmRuleSetDetail", wire_value(rulesets.one(connection, rule_set_id))
            )

    @kit.operation("updateRfmRuleSet")
    def update_rule_set(rule_set_id: int) -> Representation:
        definition = rulesets.check(kit.body)
        with kit.db.transaction() as connection:
            rulesets.edit(connection, author(), rule_set_id, definition)
            edited = rulesets.one(connection, rule_set_id)
        return Representation("RfmRuleSetDetail", wire_value(edited))

    @kit.operation("createRfmRuleSetVersion")
    def create_rule_set_version(rule_set_id: int) -> Representation:
        definition = rulesets.check(kit.body)
        with kit.db.transaction() as connection:
            new_id = rulesets.version(connection, author(), rule_set_id, definition)
            created = rulesets.one(connection, new_id)
        response = Representation("RfmRuleSetDetail", wire_value(created), status=201)
        response.headers = {"Location": f"/v1/rfm-rule-sets/{new_id}"}
        return response

    @kit.operation("publishRfmRuleSet")
    def publish_rule_set(rule_set_id: int) -> Representation:
        with kit.db.transaction() as connection:
            rulesets.publish(connection, author(), rule_set_id)
            frozen = rulesets.one(connection, rule_set_id)
        return Representation("RfmRuleSetDetail", wire_value(frozen))

    def handle_sale(fields: dict[str, str]) -> None:
        """One entry of `stream:sales.v1.recorded`: recompute the sale's customer.

        `entity_id` names the sale, never the customer. A failure raises, so the
        kit redelivers, up to five times.
        """
        recompute_sale(fields.get("entity_id", ""), fields)

    def handle_reversal(fields: dict[str, str]) -> None:
        """One entry of `stream:sales.v1.reversed`: recompute the reversed sale's
        customer. `entity_id` is `sale_id:reversal_id`, and the sale is the part
        before the colon. The customer comes from the gross view, which still lists
        a sale that was cancelled in full; the net view no longer does, so the sale
        stops counting and the customer is recomputed without it."""
        sale, _, reversal = fields.get("entity_id", "").partition(":")
        if not reversal.isdigit():
            LOGGER.warning("Reversal entry without a reversal id: %r", fields)
            return
        recompute_sale(sale, fields)

    app.extensions["rfm_handle_sale"] = handle_sale
    app.extensions["rfm_handle_reversal"] = handle_reversal
    if settings.stream_consumer_enabled:
        consumers = []
        for stream, handler in (
            (RECORDED_STREAM, handle_sale),
            (REVERSED_STREAM, handle_reversal),
        ):
            consumer = StreamConsumer(
                _client(settings.redis_state_url, 10), stream, handler
            )
            consumer.start()
            consumers.append(consumer)
        app.extensions["rfm_consumers"] = consumers
    if settings.outbox_relay_enabled and kit.db.dsn:
        relay = OutboxRelay(
            kit.db.dsn,
            SUFFIX,
            ["stream:rfm.v1.profile_changed"],
            _client(settings.redis_state_url, 3),
        )
        relay.start()
        app.extensions["rfm_relay"] = relay
    if settings.pass_enabled and kit.db.dsn:
        daily = ageing.DailyPass(kit.db, settings.rule_set_id, settings.pass_at)
        daily.start()
        app.extensions["rfm_daily_pass"] = daily
    return app
