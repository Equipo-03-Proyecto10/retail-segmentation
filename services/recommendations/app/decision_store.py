"""The decision record: written once, read back unchanged, kept 90 days.

recommendations owns the `recommendations` schema (ADR-0046, ADR-0067). Every
statement here names its columns and is parameterized, and the service holds
SELECT, INSERT and DELETE on the four tables and UPDATE of `customer_id` alone
(sql/01_schema.sql). A decision is never updated: the only change it can see is
the customer being removed from it.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from psycopg import Connection

from .config import RETENTION_DAYS
from .decisions import EXCLUSIONS, SIGNALS, Decision, Item, Reason


def record(connection: Connection[Any], decision: Decision) -> bool:
    """Store a decision and everything it holds, or nothing. Recording the same
    `decision_id` again writes nothing and returns False, so a retry or a
    recompute that reuses the id cannot store an answer twice."""
    with connection.transaction():
        inserted = connection.execute(
            "INSERT INTO recommendations.recommendation_decision"
            " (decision_id, customer_id, status, computed_at, run_id,"
            "  model_version_id, store_id, store_source, channel_id)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (decision_id) DO NOTHING RETURNING decision_id",
            (
                decision.decision_id,
                decision.customer_id,
                decision.status,
                decision.computed_at,
                decision.run_id,
                decision.model_version_id,
                decision.store_id,
                decision.store_source,
                decision.channel_id,
            ),
        ).fetchone()
        if inserted is None:
            return False
        with connection.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO recommendations.recommendation_exclusion"
                " (decision_id, reason, product_count) VALUES (%s, %s, %s)",
                [(decision.decision_id, r, n) for r, n in decision.exclusions],
            )
            cursor.executemany(
                "INSERT INTO recommendations.recommendation_item"
                " (decision_id, position, product_id, category_id, kind,"
                "  quantity_on_hand, held_at_store_id, alternative_for,"
                "  unavailable_reason)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        decision.decision_id,
                        i.position,
                        i.product_id,
                        i.category_id,
                        i.kind,
                        i.quantity_on_hand,
                        i.held_at_store_id,
                        i.alternative_for,
                        i.unavailable_reason,
                    )
                    for i in decision.items
                ],
            )
            cursor.executemany(
                "INSERT INTO recommendations.recommendation_reason"
                " (decision_id, position, signal, segment_buyers, category_id,"
                "  share) VALUES (%s, %s, %s, %s, %s, %s)",
                [
                    (
                        decision.decision_id,
                        i.position,
                        r.signal,
                        r.segment_buyers,
                        r.category_id,
                        r.share,
                    )
                    for i in decision.items
                    for r in i.reasons
                ],
            )
    return True


def exists(connection: Connection[Any], decision_id: str) -> bool:
    row = connection.execute(
        "SELECT decision_id FROM recommendations.recommendation_decision"
        " WHERE decision_id = %s",
        (decision_id,),
    ).fetchone()
    return row is not None


def _share(value: Decimal | None) -> Decimal | None:
    """As rule.share() gives it: four decimals at most, no trailing zeros."""
    if value is None:
        return None
    return value.normalize() if value else Decimal(0)


def read(connection: Connection[Any], decision_id: str) -> Decision | None:
    """One decision as it was recorded, or None when the service holds none:
    never recorded, or past its retention."""
    head = connection.execute(
        "SELECT customer_id, status, computed_at, run_id, model_version_id,"
        " store_id, store_source, channel_id"
        " FROM recommendations.recommendation_decision WHERE decision_id = %s",
        (decision_id,),
    ).fetchone()
    if head is None:
        return None
    exclusions = connection.execute(
        "SELECT reason, product_count FROM recommendations.recommendation_exclusion"
        " WHERE decision_id = %s ORDER BY array_position(%s::text[], reason)",
        (decision_id, list(EXCLUSIONS)),
    ).fetchall()
    items = connection.execute(
        "SELECT position, product_id, category_id, kind, quantity_on_hand,"
        " held_at_store_id, alternative_for, unavailable_reason"
        " FROM recommendations.recommendation_item"
        " WHERE decision_id = %s ORDER BY position",
        (decision_id,),
    ).fetchall()
    reasons: dict[int, list[Reason]] = {}
    for position, signal, buyers, category, share in connection.execute(
        "SELECT position, signal, segment_buyers, category_id, share"
        " FROM recommendations.recommendation_reason"
        " WHERE decision_id = %s ORDER BY position,"
        " array_position(%s::text[], signal)",
        (decision_id, list(SIGNALS)),
    ).fetchall():
        reasons.setdefault(position, []).append(
            Reason(signal, buyers, category, _share(share))
        )
    customer, status, computed_at, run, version, store, source, channel = head
    return Decision(
        decision_id=str(decision_id),
        customer_id=str(customer) if customer is not None else None,
        status=status,
        computed_at=computed_at,
        run_id=run,
        model_version_id=version,
        store_id=store,
        store_source=source,
        channel_id=channel,
        exclusions=tuple((reason, count) for reason, count in exclusions),
        items=tuple(
            Item(
                product_id=product,
                category_id=category,
                kind=kind,
                position=position,
                quantity_on_hand=quantity,
                reasons=tuple(reasons.get(position, ())),
                held_at_store_id=held,
                alternative_for=alternative,
                unavailable_reason=unavailable,
            )
            for (
                position,
                product,
                category,
                kind,
                quantity,
                held,
                alternative,
                unavailable,
            ) in items
        ),
    )


def purge_expired(
    connection: Connection[Any], now: datetime, days: int = RETENTION_DAYS
) -> int:
    """Delete the decisions computed more than `days` days before `now`, with
    their exclusions, items and reasons. Returns how many decisions went."""
    with connection.transaction():
        deleted = connection.execute(
            "DELETE FROM recommendations.recommendation_decision"
            " WHERE computed_at < %s",
            (now - timedelta(days=days),),
        ).rowcount
    return deleted


def clear_customer(connection: Connection[Any], customer_id: str) -> int:
    """Anonymization (F21-19): remove the customer from every decision about
    them and leave the figures. Returns how many decisions changed."""
    with connection.transaction():
        changed = connection.execute(
            "UPDATE recommendations.recommendation_decision"
            " SET customer_id = NULL WHERE customer_id = %s",
            (customer_id,),
        ).rowcount
    return changed
