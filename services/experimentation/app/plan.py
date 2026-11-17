"""Planning an experiment: the frame, the baseline, the size and the salt (F25-09).

ADR-0068. An experiment is planned and frozen before anyone is assigned, in one
transaction: its arms, the frame taken at one instant `T`, the baseline estimated
from the frame's own net sales, the customers each arm needs, and a salt drawn
once after the frame is fixed. Nothing is editable afterwards.

The frame is built from one SQL statement (`FRAME`), so every published object it
reads (campaign, segment, consent, restriction, sales, other experiments) is read
at the same statement snapshot, and `T` is the PostgreSQL clock read in that
statement. The plan serializes with every other plan, and with the web's creation
of an experiment, on the advisory lock 222001 taken first (contracts/db/
advisory-locks.md), so a plan always sees the frames of the plans that committed
before it: the first experiment planned keeps its customers (rule 6).

A customer is removed from the frame for the first reason in `REASONS` that
applies, so the counts add up: `size + sum(counts) = holders of the label`.
Statements are literal (guards.sh).
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from mosaiq_kit import ApiError
from psycopg import Connection

from app import sizing

# Serializes the allocation of experiment and group ids, as the web does
# (web/db/experiments.py), and the frames of overlapping experiments.
CREATE_LOCK_KEY = 222_001

SETTLE_DAYS = 7
CONTROL = "CONTROL"
TREATMENT = "TREATMENT"
CONVERSION = sizing.CONVERSION
REVENUE_PER_CUSTOMER = sizing.REVENUE_PER_CUSTOMER
UNDERPOWERED = "UNDERPOWERED"

# The order a removed customer is attributed in. ANONYMIZED and INACTIVE are
# decided by customers-service, which publishes neither yet (api_customers.
# customer_ref_v1 carries an id and a registration date), so no customer is
# removed for them today. CAMPAIGN_ELIGIBILITY is the campaign's own rule
# (registration cutoff, purchase rule); the contract's list of reasons is open.
REASONS = (
    "CAMPAIGN_EXCLUSION",
    "CAMPAIGN_ELIGIBILITY",
    "NO_PERSONALIZATION_CONSENT",
    "NO_MARKETING_CONTACT_CONSENT",
    "CHANNEL_RESTRICTED",
    "OVERLAPPING_EXPERIMENT",
)

LOCK = "SELECT pg_advisory_xact_lock(%s)"

TARGET = """
SELECT campaign_id, label_code, status
  FROM api_campaigns.campaign_target_v1
 WHERE campaign_id = %s
"""

# One row per customer who holds the campaign's target label, or one row of nulls
# when nobody does, so `taken_at` and the campaign's rules are always returned.
# Every published object is read here and nowhere else in the plan. The baseline
# period is `[T - window, T)`; the eligibility window of a purchase rule ends at T too.
FRAME = """
WITH clock AS (SELECT clock_timestamp() AS taken_at),
target AS (
    SELECT * FROM api_campaigns.campaign_target_v1 WHERE campaign_id = %(campaign)s
),
holders AS (
    SELECT c.customer_id, c.registered_on
      FROM api_customers.customer_ref_v1 AS c
      JOIN api_segments.current_label_v1 AS l ON l.customer_id = c.customer_id
      JOIN target AS tg ON l.state = 'assigned' AND l.label_code = tg.label_code
),
baseline AS (
    SELECT n.customer_id,
           count(DISTINCT n.transaction_id) AS purchases,
           sum(n.net_amount) AS amount
      FROM api_sales.net_purchase_line_v1 AS n
      CROSS JOIN clock
     WHERE n.occurred_at >= clock.taken_at - make_interval(hours => 24 * %(window)s)
       AND n.occurred_at < clock.taken_at
     GROUP BY n.customer_id
),
rule_window AS (
    SELECT n.customer_id, count(DISTINCT n.transaction_id) AS purchases
      FROM api_sales.net_purchase_line_v1 AS n
      CROSS JOIN clock
      CROSS JOIN target AS tg
     WHERE tg.min_purchases IS NOT NULL
       AND n.occurred_at >= clock.taken_at
                            - make_interval(hours => 24 * tg.purchase_window_days)
       AND n.occurred_at < clock.taken_at
     GROUP BY n.customer_id
),
other_periods AS (
    SELECT e.experiment_id, e.starts_on,
           GREATEST(
               COALESCE(e.ends_on, e.starts_on),
               COALESCE((SELECT max(a.assigned_at)::date
                           FROM public.experiment_assignment AS a
                          WHERE a.experiment_id = e.experiment_id)
                        + e.conversion_window_days + %(settle)s, e.starts_on)
           ) AS ends_on
      FROM public.experiment AS e
),
overlapping AS (
    SELECT DISTINCT m.customer_id
      FROM (SELECT f.experiment_id, f.customer_id
              FROM experiments.experiment_frame_member AS f
            UNION
            SELECT a.experiment_id, a.customer_id
              FROM public.experiment_assignment AS a) AS m
      JOIN other_periods AS o ON o.experiment_id = m.experiment_id
     WHERE o.starts_on <= %(ends_on)s AND %(starts_on)s <= o.ends_on
),
channels AS (SELECT count(*) AS total FROM api_sales.channel_v1)
SELECT clock.taken_at, tg.label_code, tg.status,
       tg.registered_on_or_before, tg.min_purchases, tg.purchase_window_days,
       COALESCE((SELECT array_agg(x.customer_id ORDER BY x.customer_id)
                   FROM api_campaigns.campaign_excluded_customer_v1 AS x
                  WHERE x.campaign_id = tg.campaign_id), '{}'::uuid[])
           AS excluded_customers,
       COALESCE((SELECT array_agg(x.label_code ORDER BY x.label_code)
                   FROM api_campaigns.campaign_excluded_label_v1 AS x
                  WHERE x.campaign_id = tg.campaign_id), '{}'::text[])
           AS excluded_labels,
       h.customer_id,
       h.registered_on,
       EXISTS (SELECT FROM api_campaigns.campaign_excluded_customer_v1 AS x
                WHERE x.campaign_id = tg.campaign_id
                  AND x.customer_id = h.customer_id)
           OR EXISTS (SELECT FROM api_campaigns.campaign_excluded_label_v1 AS x
                       JOIN api_segments.current_label_v1 AS l
                         ON l.label_code = x.label_code
                      WHERE x.campaign_id = tg.campaign_id
                        AND l.customer_id = h.customer_id) AS excluded,
       (tg.registered_on_or_before IS NOT NULL
        AND h.registered_on > tg.registered_on_or_before)
           OR (tg.min_purchases IS NOT NULL
               AND COALESCE(rw.purchases, 0) < tg.min_purchases) AS not_eligible,
       COALESCE(personalization.decision = 'GRANTED', false) AS personalization,
       COALESCE(marketing.decision = 'GRANTED', false) AS marketing_contact,
       (channels.total > 0
        AND (SELECT count(*)
               FROM api_preferences.restriction_v1 AS r
              WHERE r.customer_id = h.customer_id AND r.kind = 'CHANNEL')
            >= channels.total) AS channel_restricted,
       (ov.customer_id IS NOT NULL) AS overlapping,
       COALESCE(b.purchases, 0) AS baseline_purchases,
       COALESCE(b.amount, 0)::numeric(14,2) AS baseline_amount
  FROM clock
  CROSS JOIN target AS tg
  CROSS JOIN channels
  LEFT JOIN holders AS h ON true
  LEFT JOIN api_preferences.consent_state_v1 AS personalization
         ON personalization.customer_id = h.customer_id
        AND personalization.purpose = 'PERSONALIZATION'
  LEFT JOIN api_preferences.consent_state_v1 AS marketing
         ON marketing.customer_id = h.customer_id
        AND marketing.purpose = 'MARKETING_CONTACT'
  LEFT JOIN baseline AS b ON b.customer_id = h.customer_id
  LEFT JOIN rule_window AS rw ON rw.customer_id = h.customer_id
  LEFT JOIN overlapping AS ov ON ov.customer_id = h.customer_id
 ORDER BY h.customer_id
"""

INSERT_EXPERIMENT = """
INSERT INTO public.experiment (experiment_id, name, campaign_id, target_metric,
                               starts_on, ends_on, conversion_window_days, data_origin)
SELECT COALESCE(max(experiment_id), 0) + 1, %s, %s, %s, %s, %s, %s, %s
  FROM public.experiment
RETURNING experiment_id
"""
LAST_GROUP = "SELECT COALESCE(max(group_id), 0) FROM public.experiment_group"
INSERT_GROUP = """
INSERT INTO public.experiment_group (group_id, experiment_id, kind, name,
                                     treatment_description)
VALUES (%s, %s, %s, %s, %s)
"""
INSERT_PLAN = """
INSERT INTO experiments.experiment_plan (
    experiment_id, alpha, comparison_alpha, power, minimum_detectable_effect,
    baseline, baseline_std_dev, required_per_arm, smallest_detectable_effect,
    settle_days, frame_taken_at, frame_size, frame_digest, label_code,
    model_version_id, salt, planned_at, planned_by)
VALUES (%(experiment)s, %(alpha)s, %(comparison_alpha)s, %(power)s, %(mde)s,
        %(baseline)s, %(std_dev)s, %(required)s, %(smallest)s,
        %(settle)s, %(taken_at)s, %(size)s, %(digest)s, %(label)s,
        NULL, %(salt)s, %(taken_at)s, %(actor)s)
"""
INSERT_MEMBER = """
INSERT INTO experiments.experiment_frame_member (experiment_id, customer_id)
VALUES (%s, %s)
"""
INSERT_EXCLUSION = """
INSERT INTO experiments.experiment_frame_exclusion (experiment_id, reason, removed)
VALUES (%s, %s, %s)
"""
INSERT_FLAG = """
INSERT INTO experiments.experiment_flag (experiment_id, flag) VALUES (%s, %s)
"""


def campaign_closed() -> ApiError:
    return ApiError(
        409,
        "campaign_closed",
        "An experiment cannot be attached to a campaign that is finished or cancelled.",
    )


def invalid_body(field: str, code: str) -> ApiError:
    return ApiError(
        422,
        "invalid_body",
        "The request body breaks a rule of the plan.",
        [{"field": field, "code": code}],
    )


def check_arms(arms: list[dict[str, Any]]) -> None:
    """At most one CONTROL and at least one TREATMENT; with no control, exactly two."""
    controls = sum(1 for arm in arms if arm["kind"] == CONTROL)
    treatments = len(arms) - controls
    if controls > 1:
        raise invalid_body("arms", "control_count")
    if treatments < 1 or (controls == 0 and treatments != 2):
        raise invalid_body("arms", "treatment_count")


def ordered_arms(arms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The deal order: the control first, then the treatments as they were given."""
    return [a for a in arms if a["kind"] == CONTROL] + [
        a for a in arms if a["kind"] == TREATMENT
    ]


def estimator_of(metric: str) -> str:
    return (
        "DIFFERENCE_OF_PROPORTIONS"
        if metric == CONVERSION
        else "DIFFERENCE_OF_MEANS"
    )


def secondary_of(metric: str) -> str:
    return REVENUE_PER_CUSTOMER if metric == CONVERSION else CONVERSION


def compare_count(arms: list[dict[str, Any]]) -> int:
    """How many comparisons the alpha is split over: each treatment against the control,
    or the one comparison of two offers."""
    controls = sum(1 for arm in arms if arm["kind"] == CONTROL)
    return len(arms) - controls if controls else 1


def exclusions_of(rows: list[tuple[Any, ...]]) -> tuple[list[UUID], dict[str, int]]:
    """The frame, and what each reason removed, from the statement's rows.

    Row columns 9 onward are those of `FRAME`: customer, registered_on, excluded,
    not_eligible, personalization, marketing_contact, channel_restricted,
    overlapping, baseline_purchases, baseline_amount.
    """
    frame: list[UUID] = []
    removed = dict.fromkeys(REASONS, 0)
    for row in rows:
        customer = row[8]
        if customer is None:
            continue
        excluded, not_eligible, personalization, marketing, restricted, overlap = row[
            10:16
        ]
        if excluded:
            reason = "CAMPAIGN_EXCLUSION"
        elif not_eligible:
            reason = "CAMPAIGN_ELIGIBILITY"
        elif not personalization:
            reason = "NO_PERSONALIZATION_CONSENT"
        elif not marketing:
            reason = "NO_MARKETING_CONTACT_CONSENT"
        elif restricted:
            reason = "CHANNEL_RESTRICTED"
        elif overlap:
            reason = "OVERLAPPING_EXPERIMENT"
        else:
            frame.append(customer)
            continue
        removed[reason] += 1
    return frame, {reason: count for reason, count in removed.items() if count}


def baseline_of(
    metric: str, rows: list[tuple[Any, ...]], frame: list[UUID]
) -> tuple[float, float | None]:
    """The metric among the frame's own customers over the period before `T`.

    CONVERSION: the share of the frame with a net purchase in the period.
    REVENUE_PER_CUSTOMER: the mean net spend per frame customer, zero for one who
    bought nothing, and its sample standard deviation (n - 1; zero below two
    customers). An empty frame has a baseline of zero.
    """
    members = set(frame)
    purchases: list[int] = []
    amounts: list[float] = []
    for row in rows:
        if row[8] in members:
            purchases.append(row[16])
            amounts.append(float(row[17]))
    size = len(purchases)
    if size == 0:
        return 0.0, (0.0 if metric == REVENUE_PER_CUSTOMER else None)
    if metric == CONVERSION:
        return sum(1 for count in purchases if count > 0) / size, None
    mean = sum(amounts) / size
    if size < 2:
        return mean, 0.0
    variance = sum((value - mean) ** 2 for value in amounts) / (size - 1)
    return mean, variance**0.5


def digest_of(
    *,
    campaign_id: int,
    label_code: str,
    taken_at: datetime,
    rules: dict[str, Any],
    excluded_customers: list[UUID],
    excluded_labels: list[str],
    frame: list[UUID],
) -> str:
    """SHA-256 over the sorted customer ids and the criteria, as canonical JSON.

    The document is `{"criteria": {...}, "customers": [...]}` with sorted keys and
    no spaces, in UTF-8; `customers` are the frame's ids in ascending order as
    lowercase hyphenated text. The criteria are the campaign and its label, the
    instant `T` as UTC ISO text, the campaign's eligibility rule and the
    campaign's excluded customers and labels, sorted.
    """
    document = {
        "criteria": {
            "campaign_id": campaign_id,
            "excluded_customers": sorted(str(c) for c in excluded_customers),
            "excluded_labels": sorted(excluded_labels),
            "label_code": label_code,
            "rules": rules,
            "taken_at": instant(taken_at),
        },
        "customers": sorted(str(customer) for customer in frame),
    }
    text = json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def instant(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def plan(
    connection: Connection,
    body: dict[str, Any],
    *,
    actor: str,
) -> int:
    """Plan and freeze one experiment in the caller's transaction; return its id."""
    metric = body["primary_metric"]
    effect = float(body["minimum_detectable_effect"])
    alpha = float(body.get("alpha", 0.05))
    power = float(body.get("power", 0.80))
    window = int(body["conversion_window_days"])
    arms = ordered_arms(body["arms"])
    check_arms(body["arms"])
    if metric == CONVERSION and effect >= 1.0:
        raise invalid_body("minimum_detectable_effect", "out_of_range")
    starts_on = date.fromisoformat(str(body["starts_on"]))
    ends_on = starts_on + timedelta(days=window + SETTLE_DAYS)
    campaign_id = int(body["campaign_id"])

    # Every plan, and every experiment the web creates, takes this lock first.
    connection.execute(LOCK, (CREATE_LOCK_KEY,))
    target = connection.execute(TARGET, (campaign_id,)).fetchone()
    if target is None:
        raise invalid_body("campaign_id", "unknown_campaign")
    if target[2] in ("FINISHED", "CANCELLED"):
        raise campaign_closed()

    rows = connection.execute(
        FRAME,
        {
            "campaign": campaign_id,
            "window": window,
            "settle": SETTLE_DAYS,
            "starts_on": starts_on,
            "ends_on": ends_on,
        },
    ).fetchall()
    taken_at: datetime = rows[0][0]
    label_code: str = rows[0][1]
    registered_on_or_before, min_purchases, purchase_window = rows[0][3:6]
    excluded_customers: list[UUID] = list(rows[0][6])
    excluded_labels: list[str] = list(rows[0][7])
    frame, removed = exclusions_of(rows)

    baseline, std_dev = baseline_of(metric, rows, frame)
    if metric == CONVERSION and baseline + effect >= 1.0:
        raise invalid_body("minimum_detectable_effect", "out_of_range")
    comparisons = compare_count(body["arms"])
    alpha_each = sizing.comparison_alpha(alpha, comparisons)
    required = sizing.required_per_arm(
        metric,
        baseline=baseline,
        std_dev=std_dev,
        effect=effect,
        alpha_each=alpha_each,
        power=power,
    )
    available = len(frame) // len(arms)
    underpowered = available < required
    smallest = sizing.smallest_detectable_effect(
        metric,
        available_per_arm=available,
        baseline=baseline,
        std_dev=std_dev,
        alpha_each=alpha_each,
        power=power,
    )
    digest = digest_of(
        campaign_id=campaign_id,
        label_code=label_code,
        taken_at=taken_at,
        rules={
            "min_purchases": min_purchases,
            "purchase_window_days": purchase_window,
            "registered_on_or_before": (
                registered_on_or_before.isoformat() if registered_on_or_before else None
            ),
        },
        excluded_customers=excluded_customers,
        excluded_labels=excluded_labels,
        frame=frame,
    )
    # Drawn once, after the frame is fixed, and published with the plan.
    salt = secrets.token_bytes(32).hex()

    experiment_id = connection.execute(
        INSERT_EXPERIMENT,
        (
            body["name"],
            campaign_id,
            metric,
            starts_on,
            ends_on,
            window,
            body.get("data_origin", "OBSERVED"),
        ),
    ).fetchone()[0]
    last_group = connection.execute(LAST_GROUP).fetchone()[0]
    with connection.cursor() as cursor:
        cursor.executemany(
            INSERT_GROUP,
            [
                (
                    last_group + position,
                    experiment_id,
                    arm["kind"],
                    arm["name"],
                    arm["treatment_description"],
                )
                for position, arm in enumerate(arms, start=1)
            ],
        )
        cursor.execute(
            INSERT_PLAN,
            {
                "experiment": experiment_id,
                "alpha": Decimal(str(alpha)),
                "comparison_alpha": alpha_each,
                "power": Decimal(str(power)),
                "mde": Decimal(str(body["minimum_detectable_effect"])),
                "baseline": baseline,
                "std_dev": std_dev,
                "required": required,
                "smallest": smallest,
                "settle": SETTLE_DAYS,
                "taken_at": taken_at,
                "size": len(frame),
                "digest": digest,
                "label": label_code,
                "salt": salt,
                "actor": actor,
            },
        )
        cursor.executemany(INSERT_MEMBER, [(experiment_id, c) for c in frame])
        cursor.executemany(
            INSERT_EXCLUSION,
            [(experiment_id, reason, count) for reason, count in removed.items()],
        )
        if underpowered:
            cursor.execute(INSERT_FLAG, (experiment_id, UNDERPOWERED))
    return experiment_id
