"""Permission changes recorded as domain events (ADR-0072, F21-25).

Each change of who may do what records one `PERMISSION_CHANGED` event through
`api_audit.record_event_v1`, in the transaction that makes the change, so the
change and its event commit or roll back together. A failure to record rolls
the change back and answers 503 `audit_unavailable`. The event names the
acting administrator, the subject user and the role codes or store ids, never
a password or a token (RN-29; contracts/db/api_audit.record_event.v1.md).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import Any
from uuid import UUID, uuid4

import psycopg
from flask import g
from mosaiq_kit import ApiError

LOGGER = logging.getLogger(__name__)

RECORD = (
    "SELECT created FROM api_audit.record_event_v1("
    "%s, %s, %s, now(), %s, %s, %s, %s::smallint, %s, %s, %s, %s, %s, %s::jsonb)"
)


def unavailable() -> ApiError:
    return ApiError(
        503,
        "audit_unavailable",
        "The permission change could not be recorded, so it was not made.",
    )


def permission_changed(
    connection: psycopg.Connection,
    principal: dict[str, Any],
    subject_user_id: UUID | str,
    change: str,
    *,
    from_role: str | None = None,
    to_role: str | None = None,
    store_ids: Sequence[int] | None = None,
) -> None:
    """Record one PERMISSION_CHANGED in the caller's transaction, or raise 503."""
    details: dict[str, Any] = {"change": change}
    if from_role is not None:
        details["from_role"] = from_role
    if to_role is not None:
        details["to_role"] = to_role
    if store_ids is not None:
        details["store_ids"] = sorted(store_ids)
    try:
        connection.execute(
            RECORD,
            (
                str(uuid4()),
                "PERMISSION_CHANGED",
                "SUCCESS",
                "USER",
                str(principal["sub"]),
                principal.get("role"),
                None,
                "user",
                str(subject_user_id),
                None,
                None,
                getattr(g, "correlation_id", None),
                json.dumps(details),
            ),
        )
    except psycopg.Error as error:
        LOGGER.error("PERMISSION_CHANGED not recorded: sqlstate=%s", error.sqlstate)
        raise unavailable() from None
