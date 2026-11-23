"""Assigning an experiment's frame, and reading the assignment back (F25-08).

ADR-0068 §2, §6 and §7, RN-23, RN-42, ADR-0026. The frame was stored with the plan.
Assigning it is one transaction, under a lock on the experiment row, that:

1. refuses an experiment that is already assigned, an UNDERPOWERED plan nobody
   acknowledged, an empty frame, and a frame customer who has since been assigned
   in another experiment whose period overlaps this one's (the rule of §6: the
   first experiment keeps its customers, so this one is refused and the customers
   are counted in the message);
2. deals the frame with `app.verify.deal`, the same function an auditor runs, and
   writes every customer with the one instant of the transaction;
3. reads the rows back and deals them again from what is stored, which is the
   check `recomputation_matches`;
4. stores the sample ratio check and, per arm and measure, the balance check on the
   customers' recency, purchase count and net spend before the frame instant;
5. records who assigned and whether an UNDERPOWERED plan was acknowledged, and adds
   the flag of every check that failed to the experiment's flags.

Nothing in it updates or deletes a row: the schema refuses both from every role
(RN-42). Statements are literal (guards.sh).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
from mosaiq_kit import ApiError
from psycopg import Connection

from app import conversions, experiments, plan, stats, verify

MEASURES = experiments.MEASURES
SAMPLE_RATIO_FAILED = "SAMPLE_RATIO_FAILED"
BALANCE_FLAGGED = "BALANCE_FLAGGED"
RECOMPUTATION_MISMATCH = "RECOMPUTATION_MISMATCH"

LOCK_EXPERIMENT = """
SELECT e.experiment_id, e.starts_on, e.ends_on, e.conversion_window_days,
       p.salt, p.frame_taken_at
  FROM public.experiment AS e
  LEFT JOIN experiments.experiment_plan AS p ON p.experiment_id = e.experiment_id
 WHERE e.experiment_id = %s
   FOR UPDATE OF e
"""
IS_ASSIGNED = conversions.IS_ASSIGNED
FLAGGED = """
SELECT EXISTS (SELECT FROM experiments.experiment_flag
                WHERE experiment_id = %s AND flag = 'UNDERPOWERED')
"""
ARMS = """
SELECT group_id
  FROM public.experiment_group
 WHERE experiment_id = %s
 ORDER BY (kind = 'CONTROL') DESC, group_id
"""
FRAME = """
SELECT customer_id::text
  FROM experiments.experiment_frame_member
 WHERE experiment_id = %s
 ORDER BY customer_id
"""
# A frame customer already assigned in another experiment whose period overlaps this
# one's. A period runs from its start to the later of its end and the last assignment
# plus the window and the settle days (ADR-0068 §6), as the plan reads it.
CONTAMINATED = """
SELECT count(DISTINCT m.customer_id)
  FROM experiments.experiment_frame_member AS m
  JOIN public.experiment AS mine ON mine.experiment_id = m.experiment_id
  JOIN public.experiment_assignment AS a
    ON a.customer_id = m.customer_id AND a.experiment_id <> m.experiment_id
  JOIN public.experiment AS other ON other.experiment_id = a.experiment_id
 WHERE m.experiment_id = %(experiment)s
   AND other.starts_on <= COALESCE(
           mine.ends_on, mine.starts_on + mine.conversion_window_days + %(settle)s)
   AND mine.starts_on <= GREATEST(
           COALESCE(other.ends_on, other.starts_on),
           (SELECT max(x.assigned_at)::date
              FROM public.experiment_assignment AS x
             WHERE x.experiment_id = other.experiment_id)
           + other.conversion_window_days + %(settle)s)
"""
INSERT_ASSIGNMENT = """
INSERT INTO public.experiment_assignment (experiment_id, group_id, customer_id)
VALUES (%s, %s, %s)
"""
STORED = """
SELECT customer_id::text, group_id
  FROM public.experiment_assignment
 WHERE experiment_id = %s
"""
# Per assigned customer, the purchases of the window before the frame instant.
BALANCE = """
SELECT a.group_id,
       count(DISTINCT n.transaction_id),
       COALESCE(sum(n.net_amount), 0)::float8,
       max(n.occurred_at)
  FROM public.experiment AS e
  JOIN public.experiment_assignment AS a ON a.experiment_id = e.experiment_id
  LEFT JOIN api_sales.net_purchase_line_v1 AS n
    ON n.customer_id = a.customer_id
   AND n.occurred_at < %(taken)s
   AND n.occurred_at >= %(taken)s
                        - make_interval(hours => 24 * e.conversion_window_days)
 WHERE e.experiment_id = %(experiment)s
 GROUP BY a.assignment_id, a.group_id
 ORDER BY a.assignment_id
"""
INSERT_RECORD = """
INSERT INTO experiments.experiment_assignment_record
       (experiment_id, assigned_by, acknowledged_underpowered,
        recomputation_matches, sample_ratio_p_value)
VALUES (%s, %s, %s, %s, %s)
"""
INSERT_BALANCE = """
INSERT INTO experiments.experiment_assignment_balance
       (experiment_id, group_id, measure, standardized_mean_difference)
VALUES (%s, %s, %s, %s)
"""
INSERT_FLAG = """
INSERT INTO experiments.experiment_flag (experiment_id, flag)
VALUES (%s, %s)
ON CONFLICT (experiment_id, flag) DO NOTHING
"""

# The page and its count repeat the same filters, each switched off by a NULL.
LIST = """
SELECT a.assignment_id, a.experiment_id, e.campaign_id, a.customer_id, g.group_id,
       g.kind, g.name, p.salt, a.assigned_at, seen.count, seen.first
  FROM public.experiment_assignment AS a
  JOIN public.experiment AS e ON e.experiment_id = a.experiment_id
  JOIN public.experiment_group AS g
    ON g.group_id = a.group_id AND g.experiment_id = a.experiment_id
  LEFT JOIN experiments.experiment_plan AS p ON p.experiment_id = a.experiment_id
  CROSS JOIN LATERAL (
        SELECT count(*) AS count, min(x.exposed_at) AS first
          FROM public.experiment_exposure AS x
         WHERE x.assignment_id = a.assignment_id
           AND NOT EXISTS (
               SELECT FROM experiments.exposure_notice AS v
                WHERE v.source_event_id = x.source_event_id
                  AND v.notice = 'VOIDED')) AS seen
 WHERE (%(experiment)s::bigint IS NULL OR a.experiment_id = %(experiment)s::bigint)
   AND (%(campaign)s::bigint IS NULL OR e.campaign_id = %(campaign)s::bigint)
   AND (%(customer)s::uuid IS NULL OR a.customer_id = %(customer)s::uuid)
   AND (%(arm)s::bigint IS NULL OR a.group_id = %(arm)s::bigint)
   AND (%(kind)s::text IS NULL OR g.kind = %(kind)s::text)
 ORDER BY a.assignment_id
 LIMIT %(limit)s OFFSET %(offset)s
"""
COUNT = """
SELECT count(*)
  FROM public.experiment_assignment AS a
  JOIN public.experiment AS e ON e.experiment_id = a.experiment_id
  JOIN public.experiment_group AS g
    ON g.group_id = a.group_id AND g.experiment_id = a.experiment_id
 WHERE (%(experiment)s::bigint IS NULL OR a.experiment_id = %(experiment)s::bigint)
   AND (%(campaign)s::bigint IS NULL OR e.campaign_id = %(campaign)s::bigint)
   AND (%(customer)s::uuid IS NULL OR a.customer_id = %(customer)s::uuid)
   AND (%(arm)s::bigint IS NULL OR a.group_id = %(arm)s::bigint)
   AND (%(kind)s::text IS NULL OR g.kind = %(kind)s::text)
"""

# A floor read only applies while the campaign's experiment is assigned and its
# primary result has not been stored. Historical bad data may put a customer in
# two such experiments; the contract resolves that deterministically.
CURRENT = """
SELECT a.assignment_id, a.experiment_id, e.campaign_id, a.customer_id, g.group_id,
       g.kind, g.name, p.salt, a.assigned_at, seen.count, seen.first
  FROM public.experiment_assignment AS a
  JOIN public.experiment AS e ON e.experiment_id = a.experiment_id
  JOIN public.experiment_group AS g
    ON g.group_id = a.group_id AND g.experiment_id = a.experiment_id
  LEFT JOIN experiments.experiment_plan AS p ON p.experiment_id = a.experiment_id
  CROSS JOIN LATERAL (
        SELECT count(*) AS count, min(x.exposed_at) AS first
          FROM public.experiment_exposure AS x
         WHERE x.assignment_id = a.assignment_id
           AND NOT EXISTS (
               SELECT FROM experiments.exposure_notice AS v
                WHERE v.source_event_id = x.source_event_id
                  AND v.notice = 'VOIDED')) AS seen
 WHERE a.customer_id = %s::uuid
   AND e.campaign_id::bigint = %s::bigint
   AND NOT EXISTS (
       SELECT FROM experiments.experiment_result AS r
        WHERE r.experiment_id = a.experiment_id)
 ORDER BY a.assigned_at DESC, a.assignment_id
 LIMIT 1
"""


def already_assigned() -> ApiError:
    return ApiError(
        409,
        "already_assigned",
        "The experiment is already assigned; an assignment is written once and "
        "never rewritten.",
    )


def underpowered_not_acknowledged() -> ApiError:
    return ApiError(
        409,
        "underpowered_not_acknowledged",
        "The plan is UNDERPOWERED: its frame cannot supply the customers per arm. "
        "Assign it only with `acknowledge_underpowered` true.",
    )


def empty_frame() -> ApiError:
    return ApiError(
        409,
        "empty_frame",
        "The experiment has no stored frame, so there is nothing to assign.",
    )


def assignment_conflict(count: int) -> ApiError:
    return ApiError(
        409,
        "assignment_conflict",
        f"{count} customer(s) of the frame are assigned in another experiment whose "
        "period overlaps this one's. The experiment planned first keeps them "
        "(ADR-0068), so nothing was assigned.",
    )


def assignment_not_found() -> ApiError:
    return ApiError(
        404,
        "assignment_not_found",
        "The customer holds no arm in a running experiment of this campaign.",
    )


def invalid_current_query(field: str, code: str) -> ApiError:
    return ApiError(
        422,
        "invalid_query",
        "A required query field is missing or malformed.",
        [{"field": field, "code": code}],
    )


def parse_current(args: Any) -> tuple[str, int]:
    customer = args.get("customer_id")
    if customer is None:
        raise invalid_current_query("customer_id", "required")
    try:
        customer = str(UUID(customer))
    except ValueError:
        raise invalid_current_query("customer_id", "invalid_format") from None
    campaign = args.get("campaign_id")
    if campaign is None:
        raise invalid_current_query("campaign_id", "required")
    if (
        not campaign.isascii()
        or not campaign.isdigit()
        or not 1 <= int(campaign) <= 2**63 - 1
    ):
        raise invalid_current_query("campaign_id", "out_of_range")
    return customer, int(campaign)


def balance_rows(
    connection: Connection,
    experiment_id: int,
    arms: list[int],
    taken_at: datetime,
    window: int,
) -> list[dict[str, Any]]:
    """The standardized mean difference of each later arm against `arms[0]`.

    The measures are the customers' recency (days since the last net purchase before
    the frame instant, the window's length when there is none), purchase count and
    net spend over the window before it: R, F and M. A diagnostic only (ADR-0068 §7).
    """
    measures: dict[int, dict[str, list[float]]] = {
        arm: {name: [] for name in MEASURES} for arm in arms
    }
    with connection.cursor() as cursor:
        cursor.execute(BALANCE, {"experiment": experiment_id, "taken": taken_at})
        for group_id, purchases, spend, last in cursor.fetchall():
            recency = (
                float(window)
                if last is None
                else (taken_at - last).total_seconds() / 86400.0
            )
            measures[group_id]["PRE_RECENCY_DAYS"].append(recency)
            measures[group_id]["PRE_PURCHASE_COUNT"].append(float(purchases))
            measures[group_id]["PRE_NET_SPEND"].append(spend)
    reference = arms[0]
    return [
        {
            "group_id": arm,
            "reference_group_id": reference,
            "measure": name,
            "standardized_mean_difference": stats.standardized_mean_difference(
                measures[reference][name], measures[arm][name]
            ),
        }
        for arm in arms[1:]
        for name in MEASURES
    ]


def assign(
    connection: Connection,
    experiment_id: int,
    *,
    actor: str,
    acknowledge_underpowered: bool,
) -> dict[str, Any]:
    """Assign the whole frame once, in the caller's transaction."""
    row = connection.execute(LOCK_EXPERIMENT, (experiment_id,)).fetchone()
    if row is None:
        raise experiments.experiment_not_found()
    _, _, _, window, salt, taken_at = row
    if connection.execute(IS_ASSIGNED, (experiment_id,)).fetchone()[0]:
        raise already_assigned()
    frame = [r[0] for r in connection.execute(FRAME, (experiment_id,)).fetchall()]
    if salt is None or not frame:
        raise empty_frame()
    underpowered = connection.execute(FLAGGED, (experiment_id,)).fetchone()[0]
    if underpowered and not acknowledge_underpowered:
        raise underpowered_not_acknowledged()
    contaminated = connection.execute(
        CONTAMINATED, {"experiment": experiment_id, "settle": plan.SETTLE_DAYS}
    ).fetchone()[0]
    if contaminated:
        raise assignment_conflict(contaminated)

    arms = [r[0] for r in connection.execute(ARMS, (experiment_id,)).fetchall()]
    dealt = verify.deal(salt, experiment_id, arms, frame)
    try:
        with connection.cursor() as cursor:
            cursor.executemany(
                INSERT_ASSIGNMENT,
                [(experiment_id, dealt[c], UUID(c)) for c in sorted(dealt)],
            )
    except psycopg.errors.UniqueViolation:
        # RN-23: the index's refusal, reported and never a 500.
        raise assignment_conflict(0) from None

    # Deal again from what was stored, not from what was just computed.
    stored = {c: g for c, g in connection.execute(STORED, (experiment_id,)).fetchall()}
    matches = stored == verify.deal(salt, experiment_id, arms, stored)
    counts = [sum(1 for g in stored.values() if g == arm) for arm in arms]
    p_value = stats.sample_ratio_p(counts)
    balance = balance_rows(connection, experiment_id, arms, taken_at, window)

    try:
        connection.execute(
            INSERT_RECORD,
            (
                experiment_id,
                actor,
                bool(underpowered and acknowledge_underpowered),
                matches,
                p_value,
            ),
        )
    except psycopg.errors.UniqueViolation:
        raise already_assigned() from None
    for item in balance:
        connection.execute(
            INSERT_BALANCE,
            (
                experiment_id,
                item["group_id"],
                item["measure"],
                item["standardized_mean_difference"],
            ),
        )
    flags = []
    if p_value < stats.SAMPLE_RATIO_THRESHOLD:
        flags.append(SAMPLE_RATIO_FAILED)
    if any(
        abs(i["standardized_mean_difference"]) > stats.BALANCE_THRESHOLD
        for i in balance
    ):
        flags.append(BALANCE_FLAGGED)
    if not matches:
        flags.append(RECOMPUTATION_MISMATCH)
    for flag in flags:
        connection.execute(INSERT_FLAG, (experiment_id, flag))
    return experiments.get_experiment(connection, experiment_id)["assignment"]


def parse_filters(args: Any) -> dict[str, Any]:
    """The five filters: None (off) when absent, 422 `invalid_query` when invalid."""
    values: dict[str, Any] = {}
    for name, key in (
        ("experiment_id", "experiment"),
        ("campaign_id", "campaign"),
        ("arm_id", "arm"),
    ):
        raw = args.get(name)
        if raw is not None:
            if not raw.isascii() or not raw.isdigit() or not 1 <= int(raw) <= 2**63 - 1:
                raise experiments.invalid_query(name, "out_of_range")
            raw = int(raw)
        values[key] = raw
    customer = args.get("customer_id")
    if customer is not None:
        try:
            customer = str(UUID(customer))
        except ValueError:
            raise experiments.invalid_query("customer_id", "invalid_format") from None
    values["customer"] = customer
    kind = args.get("arm_kind")
    if kind is not None and kind not in (plan.CONTROL, plan.TREATMENT):
        raise experiments.invalid_query("arm_kind", "enum")
    values["kind"] = kind
    return values


def list_assignments(
    connection: Connection, filters: dict[str, Any], *, page: int, size: int
) -> tuple[list[dict[str, Any]], int]:
    """One page in `assignment_id` order, each with the key an auditor recomputes."""
    with connection.cursor() as cursor:
        cursor.execute(LIST, filters | {"limit": size, "offset": (page - 1) * size})
        rows = cursor.fetchall()
        cursor.execute(COUNT, filters)
        total = cursor.fetchone()[0]
    return [
        {
            "assignment_id": assignment_id,
            "experiment_id": experiment_id,
            "campaign_id": campaign_id,
            "customer_id": str(customer_id),
            "arm_id": arm_id,
            "arm_kind": kind,
            "arm_name": name,
            "assignment_key": (
                None
                if salt is None
                else verify.assignment_key(salt, experiment_id, str(customer_id))
            ),
            "assigned_at": conversions.instant(assigned_at),
            "exposure_count": 0 if kind == plan.CONTROL else exposures,
            "first_exposed_at": (
                None
                if kind == plan.CONTROL or first is None
                else conversions.instant(first)
            ),
        }
        for (
            assignment_id,
            experiment_id,
            campaign_id,
            customer_id,
            arm_id,
            kind,
            name,
            salt,
            assigned_at,
            exposures,
            first,
        ) in rows
    ], total


def get_current(
    connection: Connection, customer_id: str, campaign_id: int
) -> dict[str, Any]:
    """Read the customer's frozen arm without assigning or recording exposure."""
    row = connection.execute(CURRENT, (customer_id, campaign_id)).fetchone()
    if row is None:
        raise assignment_not_found()
    (
        assignment_id,
        experiment_id,
        found_campaign_id,
        found_customer_id,
        arm_id,
        kind,
        name,
        salt,
        assigned_at,
        exposure_count,
        first_exposed_at,
    ) = row
    return {
        "assignment_id": assignment_id,
        "experiment_id": experiment_id,
        "campaign_id": found_campaign_id,
        "customer_id": str(found_customer_id),
        "arm_id": arm_id,
        "arm_kind": kind,
        "arm_name": name,
        "assignment_key": (
            None
            if salt is None
            else verify.assignment_key(salt, experiment_id, str(found_customer_id))
        ),
        "assigned_at": conversions.instant(assigned_at),
        "exposure_count": 0 if kind == plan.CONTROL else exposure_count,
        "first_exposed_at": (
            None
            if kind == plan.CONTROL or first_exposed_at is None
            else conversions.instant(first_exposed_at)
        ),
    }
