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
RULES = "RFM_RULES"
WITHDRAWN = "CONSENT_WITHDRAWN"


class NoRuleSet(RuntimeError):
    """The rule set in force does not exist; the entry is redelivered."""


class RuleSetNotPublished(RuntimeError):
    """The rule set in force is still a draft, which an analyst can change. No
    profile is scored under one, so a profile never carries rules that were edited
    after it was written; the entry is redelivered."""


class ProfileRejected(RuntimeError):
    """The segments boundary refused the result: the label is not in the vocabulary
    or the customer does not exist. Nothing is written, the profile included, because
    both commit together or not at all."""


@dataclass(frozen=True, slots=True)
class Version:
    """The published version a change is recorded against
    (api_models.published_version_v1), and the rule set the profile is scored under:
    the one a rules version names, else `RFM_RULE_SET_ID` (ADR-0087). A K-means version
    labels by its centroids, which rfm-engine does not hold, so under one only a
    withdrawal of PROFILING is applied, and its profile is still scored under the
    default rule set."""

    version_id: int
    rule_set_id: int
    window_days: int
    method: str = RULES


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


def effective_batch(applying: Applying, cause: str) -> UUID:
    """The batch a call names. One event or one pass may record changes under more
    than one cause (a customer who withdrew PROFILING inside a pass of ageing ones) or
    more than one version (a pass resumed after a publication), and a run has exactly
    one of each, so each pair is a batch of its own. Still a function of the entry or
    the pass alone, so a redelivery finds the run it wrote (ADR-0087)."""
    return uuid5(_BATCHES, f"{applying.batch_id}:{applying.version.version_id}:{cause}")


def in_force(connection: Connection, default_rule_set_id: int) -> Version | None:
    """The published version, or None when none is. A profile is written either way:
    with None, or under a K-means version, it is scored under `default_rule_set_id`,
    and only what the version can answer is applied (ADR-0087)."""
    found = store.published_version(connection)
    if found is None:
        return None
    version_id, method, rule_set_id, window_days = found
    return Version(
        version_id,
        (
            rule_set_id
            if method == RULES and rule_set_id is not None
            else default_rule_set_id
        ),
        window_days,
        method,
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
            rule_set_id,
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
    rule_set_id: int,
) -> str:
    """Hand the result to the segments boundary. Returns what it did: `opened`,
    `changed`, `unchanged`, or `not_applied` under a version these rules do not
    answer for."""
    version = applying.version
    if version.method != RULES and not withdrawn:
        # A K-means version labels by centroids; these rules are not its answer.
        return "not_applied"
    cause = WITHDRAWN if withdrawn else applying.cause
    try:
        return store.apply_assignment(
            connection,
            effective_batch(applying, cause),
            customer_id,
            result,
            last_purchase_at,
            cause,
            version.version_id,
            version.method,
            window_days if version.method == RULES else version.window_days,
            rule_set_id,
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
