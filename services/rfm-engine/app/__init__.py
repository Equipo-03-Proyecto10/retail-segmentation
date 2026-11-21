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
the stories after this one register them. It is ready when PostgreSQL answers and its
own schema is there.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import redis
from flask import Flask
from mosaiq_kit import Kit, Page, configure_logging
from mosaiq_kit.auth import service_redis
from mosaiq_kit.rendering import wire_value
from mosaiq_kit.streams import OutboxRelay, StreamConsumer

from . import recompute
from .config import RECORDED_STREAM, REVERSED_STREAM, Settings

LOGGER = logging.getLogger(__name__)
SUFFIX = "rfm"


def _client(url: str, timeout: float) -> redis.Redis:
    """The restricted client of the consumers and the relay: a blocking XREADGROUP
    outlasts the kit's 200 ms default, which suits only the liveness check."""
    client = service_redis(url, SUFFIX)
    client.connection_pool.connection_kwargs["socket_timeout"] = timeout
    return client


def _rule_sets(kit: Kit) -> tuple[list[dict[str, Any]], int]:
    """The rule sets, newest first. `published` is false for every one: it is true
    only while model-registry's published version points at the rule set, and that
    view is not published yet."""
    offset = (kit.page - 1) * kit.size
    with kit.db.connection() as connection:
        total = connection.execute("SELECT count(*) FROM rfm.rule_set").fetchone()
        rows = connection.execute(
            "SELECT rule_set_id, name, window_days, recency_boundaries,"
            " frequency_boundaries, monetary_boundaries, fallback_label_code,"
            " created_at, created_by FROM rfm.rule_set"
            " ORDER BY rule_set_id DESC LIMIT %s OFFSET %s",
            (kit.size, offset),
        ).fetchall()
        bands = connection.execute(
            "SELECT rule_set_id, label_code, r_min, r_max, f_min, f_max, m_min, m_max"
            " FROM rfm.rule_set_band WHERE rule_set_id = ANY(%s)"
            " ORDER BY rule_set_id, band_no",
            ([row[0] for row in rows],),
        ).fetchall()
    items = [
        {
            "rule_set_id": row[0],
            "name": row[1],
            "window_days": row[2],
            "recency_boundaries": list(row[3]),
            "frequency_boundaries": list(row[4]),
            "monetary_boundaries": [f"{value:.2f}" for value in row[5]],
            "bands": [
                {
                    "label_code": band[1],
                    "r_min": band[2],
                    "r_max": band[3],
                    "f_min": band[4],
                    "f_max": band[5],
                    "m_min": band[6],
                    "m_max": band[7],
                }
                for band in bands
                if band[0] == row[0]
            ],
            "fallback_label_code": row[6],
            "published": False,
            "created_at": row[7],
            "created_by": str(row[8]) if row[8] else None,
        }
        for row in rows
    ]
    return [wire_value(item) for item in items], int(total[0] if total else 0)


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
        items, total = _rule_sets(kit)
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
    return app
