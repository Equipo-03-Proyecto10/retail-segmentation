"""campaigns-service: the campaign state machine (F14-28, RN-15 to RN-49).

The five operations of contracts/campaigns/openapi.yaml on mosaiq-kit. Every
write runs in `kit.db.transaction()`, which names the caller to fn_audit, so
the campaign's own audit trigger writes one attributed `audit_log` row per
statement. Nothing is cached: a move made through the web's page shows on the
next request. The activation rule that needs the web-only experiment tables is
`trg_campaign_activation_guard`, which fires for this service's sessions only
(E-13); the rest is `app/rules.py`, pinned to the web by golden fixtures.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from flask import Flask, request
from mosaiq_kit import ApiError, Kit, Page, Representation, configure_logging
from psycopg.errors import CheckViolation, ForeignKeyViolation, UniqueViolation

from app import rules


@contextmanager
def refusals() -> Iterator[None]:
    """Database refusals in the rule's words, mapped by constraint name."""
    try:
        yield
    except (CheckViolation, ForeignKeyViolation) as error:
        raise rules.translate(error) from None


def create_app() -> Flask:
    app = Flask(__name__, static_folder=None)
    configure_logging()
    holder: dict[str, Kit] = {}

    def schema_ready() -> bool:
        with holder["kit"].db.connection() as connection:
            row = connection.execute(
                "SELECT to_regclass(%s) IS NOT NULL AND EXISTS ("
                " SELECT FROM pg_trigger WHERE tgname = %s AND NOT tgisinternal)",
                ("public.campaign", "trg_campaign_activation_guard"),
            ).fetchone()
        return bool(row and row[0])

    kit = Kit(
        app,
        service="campaigns",
        suffix="campaigns",
        contracts_dir=Path(__file__).resolve().parent.parent / "contracts",
        ready_checks=[schema_ready],
    )
    holder["kit"] = kit
    app.extensions["mosaiq_kit"] = kit

    @kit.operation("listCampaigns")
    def list_campaigns() -> Page:
        with kit.db.connection() as connection:
            campaigns, total = rules.list_campaigns(
                connection,
                status=request.args.get("status"),
                label_code=request.args.get("label_code"),
                page=kit.page,
                size=kit.size,
            )
        return Page(
            "Campaigns",
            "Campaign",
            "campaigns",
            campaigns,
            page=kit.page,
            size=kit.size,
            total=total,
            info_key="page_info",
        )

    @kit.operation("createCampaign")
    def create_campaign() -> Representation:
        body = kit.body
        name = rules.clean_name(body["name"])
        starts_on = rules.parse_date(body["starts_on"], "starts_on")
        ends_on = rules.parse_date(body["ends_on"], "ends_on")
        for _ in range(rules.CREATE_ATTEMPTS):
            try:
                with refusals(), kit.db.transaction() as connection:
                    created = rules.create_campaign_once(
                        connection,
                        name=name,
                        label_code=body["label_code"],
                        starts_on=starts_on,
                        ends_on=ends_on,
                    )
            except UniqueViolation as error:
                if not rules.is_id_collision(error):
                    raise
                continue
            response = Representation("Campaign", created, status=201)
            response.headers = {"Location": f"/v1/campaigns/{created['campaign_id']}"}
            return response
        raise ApiError(
            503,
            "database_unavailable",
            "A campaign id could not be allocated; retry the request.",
        )

    @kit.operation("getCampaign")
    def get_campaign(campaign_id: int) -> Representation:
        with kit.db.connection() as connection:
            return Representation(
                "Campaign", rules.require_campaign(connection, campaign_id)
            )

    @kit.operation("updateCampaign")
    def update_campaign(campaign_id: int) -> Representation:
        changes: dict[str, Any] = dict(kit.body)
        with refusals(), kit.db.transaction() as connection:
            updated = rules.update_draft(connection, campaign_id, changes)
        return Representation("Campaign", updated)

    @kit.operation("transitionCampaign")
    def transition_campaign(campaign_id: int) -> Representation:
        body = kit.body
        with refusals(), kit.db.transaction() as connection:
            moved = rules.transition(
                connection,
                campaign_id,
                to=body["to"],
                expected_status=body["expected_status"],
            )
        return Representation("Campaign", moved)

    return app
