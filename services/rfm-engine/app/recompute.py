"""Recompute one customer's profile, from a sale event or from anything else."""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from psycopg import Connection

from . import profile, store

LOGGER = logging.getLogger(__name__)


class NoRuleSet(RuntimeError):
    """The rule set in force does not exist; the entry is redelivered."""


def customer(
    connection: Connection,
    customer_id: UUID,
    rule_set_id: int,
    as_of: datetime | None = None,
) -> profile.Profile:
    """Write this customer's profile and no other row, in the caller's transaction.

    The result depends on the customer's net purchases, the rule set and `as_of`
    alone, so recomputing twice, or after any order of events, writes what one
    recomputation would. A profile whose captured values did not move leaves the
    outbox untouched, because the trigger compares them (BOUNDARY rfm-engine).
    """
    rules = store.rule_set(connection, rule_set_id)
    if rules is None:
        raise NoRuleSet(f"rule set {rule_set_id} does not exist")
    store.lock(connection, customer_id)
    moment = as_of if as_of is not None else store.now(connection)
    result = profile.build(
        rules,
        store.measures(connection, customer_id, rules.window_days, moment),
        moment,
    )
    store.save(connection, customer_id, result)
    return result


def from_sale(
    connection: Connection,
    transaction_id: int,
    rule_set_id: int,
    as_of: datetime | None = None,
) -> UUID | None:
    """Recompute the customer of one sale. Returns who, or None when the sale has
    no lines yet and so no purchase to count."""
    owner = store.customer_of_sale(connection, transaction_id)
    if owner is None:
        return None
    customer(connection, owner, rule_set_id, as_of)
    return owner
