"""Keep the recommendation interactions engagement relays (#1197, ADR-0075, ADR-0090).

The stream carries the fact, not only keys. One row is kept per engagement event
in `recommendations.recommendation_interaction`. Both notices are idempotent and
order-free: a `RECORDED` never undoes a void, and a `VOIDED` that arrives first
stores the fact it repeats, already voided. `CAMPAIGN_EXPOSURE` is experimentation's
and is ignored. A malformed entry raises, so the kit redelivers it and acknowledges
it only after five failed deliveries. Every statement is parameterized.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg import Connection

KINDS = ("RECO_IMPRESSION", "RECO_CLICK")
SMALLINT_MAX = 32_767
INT_MAX = 2_147_483_647

# A recorded event is kept once: a repeat, or an event already voided, changes nothing.
RECORD = (
    "INSERT INTO recommendations.recommendation_interaction"
    " (event_id, kind, occurred_at, store_id, product_id, customer_id,"
    "  label_code, rank, voided)"
    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, false)"
    " ON CONFLICT (event_id) DO NOTHING"
)
# A void keeps the event hidden whichever notice arrived first.
VOID = (
    "INSERT INTO recommendations.recommendation_interaction"
    " (event_id, kind, occurred_at, store_id, product_id, customer_id,"
    "  label_code, rank, voided)"
    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, true)"
    " ON CONFLICT (event_id) DO UPDATE SET voided = true"
)


def _event(value: str, notice: str) -> UUID:
    suffix = ":" + notice.lower()
    if not value.endswith(suffix):
        raise ValueError("event_id does not match notice")
    return UUID(value[: -len(suffix)])


def _instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("occurred_at has no offset")
    return parsed


def _positive(value: str, name: str, ceiling: int) -> int:
    if not value.isascii() or not value.isdigit():
        raise ValueError(f"{name} is not a positive integer")
    number = int(value)
    if not 1 <= number <= ceiling:
        raise ValueError(f"{name} is out of range")
    return number


def apply(connection: Connection[Any], fields: dict[str, str]) -> None:
    """Apply one stream entry inside the caller's transaction."""
    kind = fields.get("type")
    if kind not in KINDS:
        return
    notice = fields.get("notice", "")
    if notice not in {"RECORDED", "VOIDED"}:
        raise ValueError("interaction notice is invalid")
    label = fields["label_code"]
    if not label:
        raise ValueError("label_code is empty")
    customer = fields.get("customer_id")
    parameters = (
        _event(fields.get("event_id", ""), notice),
        kind,
        _instant(fields["occurred_at"]),
        _positive(fields["store_id"], "store_id", SMALLINT_MAX),
        _positive(fields["product_id"], "product_id", INT_MAX),
        str(UUID(customer)) if customer else None,
        label,
        _positive(fields["rank"], "rank", SMALLINT_MAX),
    )
    connection.execute(VOID if notice == "VOIDED" else RECORD, parameters)
