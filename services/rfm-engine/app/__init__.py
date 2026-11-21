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
the stories after this one register them, except the verification recalculations
(F22-08, app/verification.py) and the rule sets (F22-09): an analyst
creates one as a draft, edits it, publishes it, and a published one is never edited,
only versioned (app/rulesets.py). It is ready when PostgreSQL answers and its
own schema is there.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any
from uuid import UUID

import redis
from flask import Flask, request
from mosaiq_kit import Kit, Page, Representation, configure_logging
from mosaiq_kit.auth import service_redis
from mosaiq_kit.rendering import wire_value
from mosaiq_kit.streams import OutboxRelay, StreamConsumer

from . import ageing, recompute, rulesets, verification
from .config import CONSENT_STREAM, RECORDED_STREAM, REVERSED_STREAM, Settings

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

    def recompute_sale(
        stream: str, sale: str, fields: dict[str, str], cause: str
    ) -> None:
        if not sale.isdigit():
            LOGGER.warning("Entry of %s without a transaction id: %r", stream, fields)
            return
        with kit.db.transaction() as connection:
            version = recompute.in_force(connection, settings.rule_set_id)
            recompute.from_sale(
                connection,
                int(sale),
                version.rule_set_id,
                applying=recompute.Applying(
                    version, cause, recompute.batch_of(stream, fields)
                ),
            )

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

    verifications: list[threading.Thread] = []
    app.extensions["rfm_verifications"] = verifications

    @kit.operation("createRfmRecalculation")
    def create_recalculation() -> Representation:
        principal = author()
        body = kit.body or {}
        with kit.db.transaction() as connection:
            row = verification.start(
                connection,
                trigger="ON_DEMAND",
                started_by=str(principal["sub"]),
                rule_set_id=body.get("rule_set_id"),
                in_force=settings.rule_set_id,
            )
        thread = threading.Thread(
            target=verification.execute,
            args=(
                kit.db,
                row["recalculation_id"],
                (str(principal["sub"]), str(principal["role"])),
            ),
            name=f"rfm-verification-{row['recalculation_id']}",
            daemon=True,
        )
        verifications.append(thread)
        thread.start()
        response = Representation("RfmRecalculation", wire_value(row), status=201)
        response.headers = {
            "Location": f"/v1/rfm-recalculations/{row['recalculation_id']}"
        }
        return response

    @kit.operation("listRfmRecalculations")
    def list_recalculations() -> Page:
        rule_set = request.args.get("rule_set_id")
        with kit.db.connection() as connection:
            items, total = verification.listing(
                connection,
                state=request.args.get("state"),
                trigger=request.args.get("trigger"),
                rule_set_id=None if rule_set is None else int(rule_set),
                page=kit.page,
                size=kit.size,
            )
        return Page(
            "RfmRecalculations",
            "RfmRecalculation",
            "recalculations",
            [wire_value(item) for item in items],
            page=kit.page,
            size=kit.size,
            total=total,
            info_key="page_info",
        )

    @kit.operation("getRfmRecalculation")
    def get_recalculation(recalculation_id: int) -> Representation:
        with kit.db.connection() as connection:
            row = verification.one(connection, recalculation_id)
        return Representation("RfmRecalculation", wire_value(row))

    @kit.operation("listRfmRecalculationDifferences")
    def list_recalculation_differences(recalculation_id: int) -> Page:
        with kit.db.connection() as connection:
            items, total = verification.differences(
                connection, recalculation_id, page=kit.page, size=kit.size
            )
        return Page(
            "RfmRecalculationDifferences",
            "RfmRecalculationDifference",
            "differences",
            items,
            page=kit.page,
            size=kit.size,
            total=total,
            info_key="page_info",
        )

    def handle_sale(fields: dict[str, str]) -> None:
        """One entry of `stream:sales.v1.recorded`: recompute the sale's customer.

        `entity_id` names the sale, never the customer. A failure raises, so the
        kit redelivers, up to five times.
        """
        recompute_sale(RECORDED_STREAM, fields.get("entity_id", ""), fields, "SALE")

    def handle_reversal(fields: dict[str, str]) -> None:
        """One entry of `stream:sales.v1.reversed`: recompute the reversed sale's
        customer. `entity_id` is `sale_id:reversal_id`, and the sale is the part
        before the colon. The customer comes from the gross view, which still lists
        a sale that was cancelled in full; the net view no longer does, so the sale
        stops counting and the customer is recomputed without it."""
        sale, _, reversal = fields.get("entity_id", "").partition(":")
        if not reversal.isdigit():
            LOGGER.warning(
                "Entry of %s without a reversal id: %r", REVERSED_STREAM, fields
            )
            return
        recompute_sale(REVERSED_STREAM, sale, fields, "REVERSAL")

    def handle_consent(fields: dict[str, str]) -> None:
        """One entry of `stream:preferences.v1.consent_changed`: re-read the
        customer's PROFILING decision and act on what it says now. The entry does not
        say which purpose moved or which way, so a replay or a reordering cannot leave
        the customer on an older decision (ADR-0066 section 2)."""
        try:
            customer_id = UUID(fields.get("entity_id", ""))
        except ValueError:
            LOGGER.warning("Entry of %s without a customer: %r", CONSENT_STREAM, fields)
            return
        with kit.db.transaction() as connection:
            version = recompute.in_force(connection, settings.rule_set_id)
            recompute.customer(
                connection,
                customer_id,
                version.rule_set_id,
                applying=recompute.Applying(
                    version,
                    "CONSENT_GRANTED",
                    recompute.batch_of(CONSENT_STREAM, fields),
                ),
            )

    app.extensions["rfm_handle_consent"] = handle_consent
    app.extensions["rfm_handle_sale"] = handle_sale
    app.extensions["rfm_handle_reversal"] = handle_reversal
    if settings.stream_consumer_enabled:
        consumers = []
        for stream, handler in (
            (RECORDED_STREAM, handle_sale),
            (REVERSED_STREAM, handle_reversal),
            (CONSENT_STREAM, handle_consent),
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
    if kit.db.dsn:
        try:
            verification.reap_orphans(kit.db)
        except Exception:  # noqa: BLE001 - the schema may not be there yet
            LOGGER.exception("Could not look for an unfinished verification")
    if settings.verification_enabled and kit.db.dsn:
        weekly = verification.WeeklyVerification(
            kit.db,
            settings.rule_set_id,
            settings.verification_weekday,
            settings.verification_at,
        )
        weekly.start()
        app.extensions["rfm_weekly_verification"] = weekly
    if settings.pass_enabled and kit.db.dsn:
        daily = ageing.DailyPass(
            kit.db, None, settings.pass_at, default_rule_set_id=settings.rule_set_id
        )
        daily.start()
        app.extensions["rfm_daily_pass"] = daily
    return app
