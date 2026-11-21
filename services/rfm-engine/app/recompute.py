"""Recompute one customer's profile, from a sale event or from anything else."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

import psycopg.errors
from psycopg import Connection

from . import profile, store

LOGGER = logging.getLogger(__name__)
_BATCHES = uuid5(NAMESPACE_URL, "https://mosaiq.example/rfm-engine/batch")


class NoRuleSet(RuntimeError):
    """The rule set in force does not exist; the entry is redelivered."""


class RuleSetNotPublished(RuntimeError):
    """The rule set in force is still a draft, which an analyst can change. No
    profile is scored under one, so a profile never carries rules that were edited
    after it was written; the entry is redelivered."""


class NoPublishedVersion(RuntimeError):
    """No rules version is published, so there is nothing to score under and nothing
    to record an assignment against (ADR-0063); the entry is redelivered."""


class ProfileRejected(RuntimeError):
    """The segments boundary refused the result: the label is not in the vocabulary
    or the customer does not exist. Nothing is written, the profile included, because
    both commit together or not at all."""


@dataclass(frozen=True, slots=True)
class Version:
    """The version a change is scored under (api_models.published_version_v1)."""

    version_id: int
    rule_set_id: int
    window_days: int


@dataclass(frozen=True, slots=True)
class Applying:
    """What it takes to apply the result to the segment history: the version it was
    scored under, what moved the customer, and the batch the change belongs to (one
    event, or one pass). Without it a profile is written and no assignment changes."""

    version: Version
    cause: str
    batch_id: UUID


def batch_of(stream: str, fields: dict[str, str]) -> UUID:
    """The batch of changes one stream entry makes: the same entry, redelivered, names
    the same batch and so finds the run it already wrote (RN-65)."""
    entry = fields.get("event_id") or fields.get("entity_id", "")
    return uuid5(_BATCHES, f"{stream}:{entry}")


def pass_batch(pass_run_id: int) -> UUID:
    """The batch of one scheduled pass: all its changes are one run."""
    return uuid5(_BATCHES, f"pass:{pass_run_id}")


def in_force(connection: Connection, default_rule_set_id: int) -> Version:
    """The published rules version, or the reason there is none. A version that names
    no rule set (one derived from a web run) is scored under `default_rule_set_id`."""
    found = store.published_rule_version(connection)
    if found is None:
        raise NoPublishedVersion("no rules version is published")
    version_id, rule_set_id, window_days = found
    return Version(
        version_id,
        rule_set_id if rule_set_id is not None else default_rule_set_id,
        window_days,
    )


def require_published(connection: Connection, rule_set_id: int) -> profile.RuleSet:
    """The rule set, or the reason a profile cannot be scored under it."""
    rules = store.rule_set(connection, rule_set_id)
    if rules is None:
        raise NoRuleSet(f"rule set {rule_set_id} does not exist")
    if not rules.published:
        raise RuleSetNotPublished(f"rule set {rule_set_id} is not published")
    return rules


def customer(
    connection: Connection,
    customer_id: UUID,
    rule_set_id: int,
    as_of: datetime | None = None,
    applying: Applying | None = None,
) -> profile.Profile:
    """Write this customer's profile and no other row, in the caller's transaction,
    and with `applying` apply the result to their segment history in the same one.

    The result depends on the customer's net purchases, the rule set and `as_of`
    alone, so recomputing twice, or after any order of events, writes what one
    recomputation would. A profile whose captured values did not move leaves the
    outbox untouched, because the trigger compares them (BOUNDARY rfm-engine). A
    customer who withdrew PROFILING is not measured at all: they get the unassigned
    result, whatever moved them (ADR-0066 section 2).
    """
    rules = require_published(connection, rule_set_id)
    store.lock(connection, customer_id)
    moment = as_of if as_of is not None else store.now(connection)
    withdrawn = store.profiling_withdrawn(connection, customer_id)
    measures = (
        profile.Measures(0, Decimal("0.00"), None, None)
        if withdrawn
        else store.measures(connection, customer_id, rules.window_days, moment)
    )
    result = profile.build(rules, measures, moment)
    store.save(connection, customer_id, result)
    if applying is not None:
        apply(
            connection,
            customer_id,
            result,
            measures.last_at,
            applying,
            withdrawn,
            rules.window_days,
        )
    return result


def apply(
    connection: Connection,
    customer_id: UUID,
    result: profile.Profile,
    last_purchase_at: datetime | None,
    applying: Applying,
    withdrawn: bool,
    window_days: int,
) -> str:
    """Hand the result to the segments boundary. Returns what it did."""
    cause = "CONSENT_WITHDRAWN" if withdrawn else applying.cause
    try:
        return store.apply_assignment(
            connection,
            applying.batch_id,
            customer_id,
            result,
            last_purchase_at,
            cause,
            applying.version.version_id,
            window_days,
        )
    except psycopg.errors.CheckViolation as refusal:
        name = refusal.diag.constraint_name
        if name in ("unknown_label", "unknown_customer"):
            LOGGER.warning("Result of %s rejected: %s", customer_id, name)
            raise ProfileRejected(f"{customer_id}: {name}") from refusal
        raise


def from_sale(
    connection: Connection,
    transaction_id: int,
    rule_set_id: int,
    as_of: datetime | None = None,
    applying: Applying | None = None,
) -> UUID | None:
    """Recompute the customer of one sale. Returns who, or None when the sale has
    no lines yet and so no purchase to count."""
    owner = store.customer_of_sale(connection, transaction_id)
    if owner is None:
        return None
    customer(connection, owner, rule_set_id, as_of, applying)
    return owner
