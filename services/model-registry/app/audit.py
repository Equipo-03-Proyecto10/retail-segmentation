"""Model publication domain events, recorded in the change transaction."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

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


DRIFT_RECORD = (
    "SELECT created FROM api_audit.record_event_v1("
    "%s, 'CALCULATION_RUN', 'SUCCESS', now(), 'SYSTEM', NULL, NULL, NULL,"
    " 'calculation', %s, %s, NULL, %s, %s::jsonb)"
)


def drift_run(
    connection: psycopg.Connection[Any],
    *,
    event_id: UUID,
    version_id: int,
    window: tuple[datetime, datetime],
    input_count: int,
    output_count: int,
    digest: str | None,
    status: str,
    correlation_id: str | None,
) -> None:
    """Append the drift run's audit fact in the run's own transaction, or fail
    the run closed: a report is never committed without its event."""
    details: dict[str, Any] = {
        "calculation": "DRIFT",
        "status": status,
        "input_count": input_count,
        "output_count": output_count,
        "window_from": window[0].astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_to": window[1].astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if digest is not None:
        details["parameters_digest"] = digest
    try:
        connection.execute(
            DRIFT_RECORD,
            (
                str(event_id),
                f"drift-{version_id}-{window[1].astimezone(UTC):%Y%m%d}",
                version_id,
                correlation_id,
                json.dumps(details),
            ),
        )
    except psycopg.Error as error:
        LOGGER.error("drift run audit failed: sqlstate=%s", error.sqlstate)
        raise
