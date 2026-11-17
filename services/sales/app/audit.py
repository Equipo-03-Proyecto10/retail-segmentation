"""Logged reads of one customer's sales history (ADR-0072, F21-25).

`GET /v1/sales?customer_id=` names one customer, so it records a
`HISTORY_READ` domain event through `api_audit.record_event_v1` in the
transaction of the read, before any row is returned. A 403 on the same route
records one with outcome `REFUSED`. When the event cannot be recorded the read
fails closed with 503 `audit_unavailable`: the log must not hold only the
reads that happened to be logged. The event names who, which customer and
when; never what was read (contracts/db/api_audit.record_event.v1.md).
"""

from __future__ import annotations

import functools
import json
import logging
from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

import psycopg
from flask import Flask, g, request
from mosaiq_kit import ApiError, Kit

LOGGER = logging.getLogger(__name__)

RECORD = (
    "SELECT created FROM api_audit.record_event_v1("
    "%s, %s, %s, now(), %s, %s, %s, %s::smallint, %s, %s, %s, %s, %s, %s::jsonb)"
)


def unavailable() -> ApiError:
    return ApiError(
        503,
        "audit_unavailable",
        "The read could not be recorded, so it was not served.",
    )


def record_history_read(
    connection: psycopg.Connection,
    principal: dict[str, Any],
    customer_id: str,
    *,
    outcome: str = "SUCCESS",
) -> None:
    """Record one HISTORY_READ of a customer's sales, or raise 503."""
    try:
        connection.execute(
            RECORD,
            (
                str(uuid4()),
                "HISTORY_READ",
                outcome,
                "USER",
                str(principal["sub"]),
                principal.get("role"),
                None,
                "customer",
                customer_id,
                None,
                None,
                getattr(g, "correlation_id", None),
                json.dumps({"history": "SALES"}),
            ),
        )
    except psycopg.Error as error:
        LOGGER.error("HISTORY_READ not recorded: sqlstate=%s", error.sqlstate)
        raise unavailable() from None


def named_customer() -> str | None:
    """The customer a list names, or None when it names none or a malformed one."""
    raw = request.args.get("customer_id")
    if raw is None:
        return None
    try:
        return str(UUID(raw))
    except ValueError:
        return None


def log_refusals(app: Flask, kit: Kit, operation_id: str) -> None:
    """Record a 403 on a logged read as a REFUSED HISTORY_READ.

    The kit refuses the role before the handler runs, so the operation's view
    is wrapped: the principal is already verified when the 403 is raised. If
    the refusal cannot be recorded the answer is 503, not 403.
    """
    endpoint = "mosaiq_operation_" + operation_id
    view: Callable[..., Any] = app.view_functions[endpoint]

    @functools.wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        try:
            return view(*args, **kwargs)
        except ApiError as error:
            principal = getattr(g, "principal", None)
            customer_id = named_customer()
            if error.status == 403 and principal and customer_id:
                with kit.db.transaction() as connection:
                    record_history_read(
                        connection, principal, customer_id, outcome="REFUSED"
                    )
            raise

    app.view_functions[endpoint] = wrapped
