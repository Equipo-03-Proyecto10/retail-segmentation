"""Model publication domain events, recorded in the change transaction."""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import uuid4

import psycopg
from flask import g
from mosaiq_kit import ApiError

LOGGER = logging.getLogger(__name__)

RECORD = (
    "SELECT created FROM api_audit.record_event_v1("
    "%s, %s, 'SUCCESS', now(), 'USER', %s, %s, %s::smallint,"
    " 'model_version', %s, %s, %s, %s, %s::jsonb)"
)


def publication_changed(
    connection: psycopg.Connection[Any],
    principal: dict[str, Any],
    *,
    action: str,
    version_id: int,
    previous_version_id: int | None,
) -> None:
    """Append the audit fact or fail the publication closed."""
    kind = "MODEL_PUBLISHED" if action == "PUBLISH" else "MODEL_ROLLED_BACK"
    try:
        connection.execute(
            RECORD,
            (
                str(uuid4()),
                kind,
                str(principal["sub"]),
                principal["role"],
                None,
                str(version_id),
                version_id,
                None,
                getattr(g, "correlation_id", None),
                json.dumps(
                    {
                        "previous_version_id": previous_version_id,
                        "reason_code": action,
                    }
                ),
            ),
        )
    except psycopg.Error as error:
        LOGGER.error("model publication audit failed: sqlstate=%s", error.sqlstate)
        raise ApiError(
            503,
            "database_unavailable",
            "The database is unavailable.",
        ) from None
