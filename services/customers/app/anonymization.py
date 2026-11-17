"""Anonymize a customer in place (ADR-0066 section 3, F21-19).

A customer is never deleted: sales, R, F and M, segment history and the audit
trail hang on the key (RN-08). Anonymization therefore removes the identity and
keeps the row. One transaction does all of it, so the identity is gone, the
cards are revoked, the audit entries are redacted and the two domain events are
recorded together, or none of it happens:

1. the customer row is locked and read; one that is already anonymized is
   answered as it was and nothing is written, recorded or announced;
2. `name` becomes the placeholder, `email`, `phone` and the account link become
   null and `anonymized_at` is set (the schema's CHECK refuses a marked row that
   still holds an identity, and its trigger refuses any later change);
3. every active loyalty card is revoked by the actor;
4. `customers.redact_audit_v1` removes the identity from the customer's audit
   entries, as a definer that may rewrite those two payloads and nothing else;
5. `CUSTOMER_ANONYMIZED` and `AUDIT_PAYLOAD_REDACTED` are recorded through
   `api_audit.record_event_v1` naming the administrator (ADR-0072). If either
   cannot be recorded the operation fails closed with 503 `audit_unavailable`
   and the transaction rolls back: the log must not hold only the anonymizations
   that happened to be logged.

The trigger of the customer table writes the outbox row of
`stream:customers.v1.anonymized` in the same transaction.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import uuid4

import psycopg
from flask import g
from mosaiq_kit import ApiError

LOGGER = logging.getLogger(__name__)

PLACEHOLDER = "Anonymized customer"
REASONS = ("SUBJECT_REQUEST", "RETENTION_EXPIRED")
# A card revoked by anonymization says why in the vocabulary the card already has.
CARD_REASON = {"SUBJECT_REQUEST": "CUSTOMER_REQUEST", "RETENTION_EXPIRED": "OTHER"}

LOCK = "SELECT anonymized_at FROM public.customer WHERE customer_id = %s FOR UPDATE"
ANONYMIZE = (
    "UPDATE public.customer SET name = %s, email = NULL, phone = NULL,"
    " user_id = NULL, anonymized_at = now()"
    " WHERE customer_id = %s AND anonymized_at IS NULL RETURNING anonymized_at"
)
REVOKE_CARDS = (
    "UPDATE customers.loyalty_card SET status = 'REVOKED',"
    " revoked_at = CURRENT_TIMESTAMP, revoked_by = %s, revocation_reason = %s"
    " WHERE customer_id = %s AND status = 'ACTIVE'"
)
REDACT = "SELECT customers.redact_audit_v1(%s)"
RECORD = (
    "SELECT created FROM api_audit.record_event_v1("
    "%s, %s, %s, now(), %s, %s, %s, %s::smallint, %s, %s, %s, %s, %s, %s::jsonb)"
)


def unavailable() -> ApiError:
    return ApiError(
        503,
        "audit_unavailable",
        "The anonymization could not be recorded, so it was not done.",
    )


def record(
    connection: psycopg.Connection,
    principal: dict[str, Any],
    customer_id: str,
    action: str,
    reason_code: str,
) -> None:
    """Record one CRITICAL_ACTION about the customer, or raise 503."""

    try:
        connection.execute(
            RECORD,
            (
                str(uuid4()),
                "CRITICAL_ACTION",
                "SUCCESS",
                "USER",
                str(principal["sub"]),
                principal.get("role"),
                None,
                "customer",
                customer_id,
                None,
                None,
                getattr(g, "correlation_id", None),
                json.dumps({"action": action, "reason_code": reason_code}),
            ),
        )
    except psycopg.Error as error:
        LOGGER.error("%s not recorded: sqlstate=%s", action, error.sqlstate)
        raise unavailable() from None


def anonymize(
    connection: psycopg.Connection,
    principal: dict[str, Any],
    customer_id: str,
    reason: str,
) -> tuple[str, Any]:
    """Anonymize inside the caller's transaction.

    Returns `("ANONYMIZED" | "ALREADY_ANONYMIZED", anonymized_at)`, or raises
    the 404 of an unknown customer through the returned `None` row.
    """

    row = connection.execute(LOCK, (customer_id,)).fetchone()
    if row is None:
        raise ApiError(
            404,
            "customer_not_found",
            "The customer does not exist.",
            [{"field": "customer_id", "code": "not_found"}],
        )
    if row[0] is not None:
        return "ALREADY_ANONYMIZED", row[0]
    anonymized_at = connection.execute(
        ANONYMIZE, (PLACEHOLDER, customer_id)
    ).fetchone()[0]
    connection.execute(
        REVOKE_CARDS, (str(principal["sub"]), CARD_REASON[reason], customer_id)
    )
    connection.execute(REDACT, (customer_id,))
    record(connection, principal, customer_id, "CUSTOMER_ANONYMIZED", reason)
    record(
        connection, principal, customer_id, "AUDIT_PAYLOAD_REDACTED", "ANONYMIZATION"
    )
    return "ANONYMIZED", anonymized_at
