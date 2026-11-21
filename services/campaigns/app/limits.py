"""What a campaign may reach and spend (F25-04, ADR-0045, ADR-0046).

Four resources of one campaign, each written whole while the campaign is DRAFT:
the audience cap, the budget, the eligibility rules and the exclusions. The
audience itself is computed, never stored: both audience operations are one
statement as of one instant, so `audience_size` is the same number in both
(contracts/campaigns/openapi.yaml). Every function takes the request's
connection and raises `ApiError`.

The tables are `campaigns.campaign_*` and hold only what the web's `campaign`
table has no column for, so a write of the web's cannot lose them. Every write
first locks the campaign's row, the lock the web's own writes take, so a move
to ACTIVE made through the web and a replacement made here queue: the
replacement then sees the new status and is refused.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from mosaiq_kit import ApiError
from psycopg import Connection
from psycopg.errors import ForeignKeyViolation

from app import rules

# The order of precedence of the contract's `AudienceExclusionReason`.
REASONS = (
    "not_in_customer_reference",
    "personalization_withdrawn",
    "marketing_contact_withdrawn",
    "excluded_customer",
    "excluded_label",
    "registered_after_cutoff",
    "too_few_purchases",
    "over_max_customers",
)
MAX_EXCLUDED_CUSTOMERS = 1000
MAX_EXCLUDED_LABELS = 20

# Whole statements, never built from pieces: every value is bound.
NOW = "SELECT clock_timestamp()"
CAMPAIGN_LABEL = "SELECT label_code FROM public.campaign WHERE campaign_id = %s"
LOCK_DRAFT = "SELECT status FROM public.campaign WHERE campaign_id = %s FOR UPDATE"
GET_AUDIENCE = (
    "SELECT max_customers FROM campaigns.campaign_audience WHERE campaign_id = %s"
)
DELETE_AUDIENCE = "DELETE FROM campaigns.campaign_audience WHERE campaign_id = %s"
PUT_AUDIENCE = (
    "INSERT INTO campaigns.campaign_audience (campaign_id, max_customers)"
    " VALUES (%s, %s)"
)
GET_BUDGET = (
    "SELECT total_amount, per_customer_cap FROM campaigns.campaign_budget"
    " WHERE campaign_id = %s"
)
DELETE_BUDGET = "DELETE FROM campaigns.campaign_budget WHERE campaign_id = %s"
PUT_BUDGET = (
    "INSERT INTO campaigns.campaign_budget (campaign_id, total_amount,"
    " per_customer_cap) VALUES (%s, %s, %s)"
)
SPENDING = """
WITH customer_spending AS (
    SELECT line.customer_id,
           sum((line.list_price - line.unit_price) * line.net_quantity)
               AS spent_amount
      FROM api_sales.promotion_line_v1 AS line
      JOIN campaigns.promotion AS promotion
        ON promotion.promotion_id = line.promotion_id
     WHERE promotion.campaign_id = %(campaign)s
     GROUP BY line.customer_id
), summary AS (
    SELECT coalesce(sum(spent_amount), 0) AS total_spent,
           count(*) AS customer_total,
           count(*) FILTER (WHERE spent_amount >= (
               SELECT per_customer_cap FROM campaigns.campaign_budget
                WHERE campaign_id = %(campaign)s
           )) AS customers_at_cap
      FROM customer_spending
), customer_page AS (
    SELECT customer_id, spent_amount
      FROM customer_spending
     ORDER BY customer_id
     LIMIT %(size)s OFFSET %(offset)s
)
SELECT statement_timestamp(), budget.total_amount, budget.per_customer_cap,
       summary.total_spent, summary.customer_total, summary.customers_at_cap,
       customer_page.customer_id, customer_page.spent_amount
  FROM public.campaign AS campaign
  LEFT JOIN campaigns.campaign_budget AS budget
    ON budget.campaign_id = campaign.campaign_id
 CROSS JOIN summary
  LEFT JOIN customer_page ON true
 WHERE campaign.campaign_id = %(campaign)s
 ORDER BY customer_page.customer_id
"""
GET_ELIGIBILITY = (
    "SELECT registered_on_or_before, min_purchases, purchase_window_days"
    " FROM campaigns.campaign_eligibility WHERE campaign_id = %s"
)
DELETE_ELIGIBILITY = "DELETE FROM campaigns.campaign_eligibility WHERE campaign_id = %s"
PUT_ELIGIBILITY = (
    "INSERT INTO campaigns.campaign_eligibility (campaign_id,"
    " registered_on_or_before, min_purchases, purchase_window_days)"
    " VALUES (%s, %s, %s, %s)"
)
GET_EXCLUDED_CUSTOMERS = (
    "SELECT customer_id FROM campaigns.campaign_excluded_customer"
    " WHERE campaign_id = %s ORDER BY customer_id"
)
GET_EXCLUDED_LABELS = (
    "SELECT label_code FROM campaigns.campaign_excluded_label"
    " WHERE campaign_id = %s ORDER BY label_code"
)
DELETE_EXCLUDED_CUSTOMERS = (
    "DELETE FROM campaigns.campaign_excluded_customer WHERE campaign_id = %s"
)
DELETE_EXCLUDED_LABELS = (
    "DELETE FROM campaigns.campaign_excluded_label WHERE campaign_id = %s"
)
PUT_EXCLUDED_CUSTOMER = (
    "INSERT INTO campaigns.campaign_excluded_customer (campaign_id, customer_id)"
    " VALUES (%s, %s)"
)
PUT_EXCLUDED_LABEL = (
    "INSERT INTO campaigns.campaign_excluded_label (campaign_id, label_code)"
    " VALUES (%s, %s)"
)

# One pass over the customers who hold the campaign's label. Each is judged once
# and counted under the first reason that applies, in the contract's order, so the
# counts add up to the holders. The cap removes the last customers by id once every
# earlier reason is applied. An anonymized customer keeps its row in the customer
# reference with `anonymized` true (ADR-0066 section 3, F21-19): it is counted under
# the first reason and is never in an audience, whatever consent rows it still holds.
# Consent: a purpose is granted only when its row says GRANTED; no row, a
# WITHDRAWN row and a view that cannot be read are all not granted (ADR-0066
# section 2). A purchase is a sale with a net amount above zero
# (ADR-0062), read from the net view and never from gross lines.
AUDIENCE = """
WITH holders AS (
    SELECT l.customer_id
      FROM api_segments.current_label_v1 AS l
     WHERE l.state = 'assigned' AND l.label_code = %(label)s
), judged AS (
    SELECT h.customer_id,
           (r.customer_id IS NOT NULL AND NOT r.anonymized) AS in_reference,
           EXISTS (SELECT FROM api_preferences.consent_state_v1 AS c
                    WHERE c.customer_id = h.customer_id
                      AND c.purpose = 'PERSONALIZATION'
                      AND c.decision = 'GRANTED') AS personalization,
           EXISTS (SELECT FROM api_preferences.consent_state_v1 AS c
                    WHERE c.customer_id = h.customer_id
                      AND c.purpose = 'MARKETING_CONTACT'
                      AND c.decision = 'GRANTED') AS marketing_contact,
           EXISTS (SELECT FROM campaigns.campaign_excluded_customer AS x
                    WHERE x.campaign_id = %(campaign)s
                      AND x.customer_id = h.customer_id) AS excluded_customer,
           EXISTS (SELECT FROM campaigns.campaign_excluded_label AS x
                    WHERE x.campaign_id = %(campaign)s
                      AND x.label_code = %(label)s) AS excluded_label,
           COALESCE(r.registered_on > %(cutoff)s::date, false) AS registered_after,
           CASE WHEN %(minimum)s::int IS NULL THEN false
                ELSE COALESCE((
                    SELECT count(*) FROM (
                        SELECT n.transaction_id
                          FROM api_sales.net_purchase_line_v1 AS n
                         WHERE n.customer_id = h.customer_id
                           AND n.occurred_at <= %(as_of)s::timestamptz
                           AND n.occurred_at >= %(as_of)s::timestamptz
                               - make_interval(days => %(window)s::int)
                         GROUP BY n.transaction_id
                        HAVING sum(n.net_amount) > 0) AS sales), 0) < %(minimum)s::int
           END AS too_few
      FROM holders AS h
      LEFT JOIN api_customers.customer_ref_v1 AS r ON r.customer_id = h.customer_id
), first_reason AS (
    SELECT customer_id,
           CASE WHEN NOT in_reference THEN 1
                WHEN NOT personalization THEN 2
                WHEN NOT marketing_contact THEN 3
                WHEN excluded_customer THEN 4
                WHEN excluded_label THEN 5
                WHEN registered_after THEN 6
                WHEN too_few THEN 7
           END AS reason
      FROM judged
), final AS (
    SELECT CASE WHEN reason IS NULL AND %(cap)s::int IS NOT NULL
                     AND row_number() OVER (PARTITION BY (reason IS NULL)
                                            ORDER BY customer_id) > %(cap)s::int
                THEN 8 ELSE reason END AS reason
      FROM first_reason
)
SELECT count(*),
       count(*) FILTER (WHERE reason = 1), count(*) FILTER (WHERE reason = 2),
       count(*) FILTER (WHERE reason = 3), count(*) FILTER (WHERE reason = 4),
       count(*) FILTER (WHERE reason = 5), count(*) FILTER (WHERE reason = 6),
       count(*) FILTER (WHERE reason = 7), count(*) FILTER (WHERE reason = 8),
       count(*) FILTER (WHERE reason IS NULL)
  FROM final
"""


def invalid_body(field: str, code: str) -> ApiError:
    return ApiError(
        422,
        "invalid_body",
        "The request body violates its schema.",
        [{"field": field, "code": code}],
    )


def label_of(connection: Connection, campaign_id: int) -> str:
    row = connection.execute(CAMPAIGN_LABEL, (campaign_id,)).fetchone()
    if row is None:
        raise rules.campaign_not_found()
    return str(row[0])


def lock_draft(connection: Connection, campaign_id: int) -> None:
    """Take the campaign's row, then insist it is a DRAFT.

    The same row lock the web's own writes take, so the status read here is the
    status the replacement is written under.
    """
    row = connection.execute(LOCK_DRAFT, (campaign_id,)).fetchone()
    if row is None:
        raise rules.campaign_not_found()
    if row[0] != rules.DRAFT:
        raise rules.campaign_not_draft()


def money(value: Decimal | None) -> str | None:
    return None if value is None else f"{value:.2f}"


def instant(value: datetime) -> str:
    """The wire form of a moment: UTC, with a Z."""
    return value.isoformat().replace("+00:00", "Z")


# --- Audience ---------------------------------------------------------------


def cap_of(connection: Connection, campaign_id: int) -> int | None:
    row = connection.execute(GET_AUDIENCE, (campaign_id,)).fetchone()
    return None if row is None else int(row[0])


def now(connection: Connection) -> datetime:
    row = connection.execute(NOW).fetchone()
    assert row is not None
    return row[0]


def audience_counts(
    connection: Connection, campaign_id: int, as_of: datetime
) -> dict[str, Any]:
    """Who holds the label and who is left out, counted by the first reason."""
    label = label_of(connection, campaign_id)
    rule = connection.execute(GET_ELIGIBILITY, (campaign_id,)).fetchone()
    cutoff, minimum, window = rule if rule else (None, None, None)
    row = connection.execute(
        AUDIENCE,
        {
            "campaign": campaign_id,
            "label": label,
            "as_of": as_of,
            "cutoff": cutoff,
            "minimum": minimum,
            "window": window,
            "cap": cap_of(connection, campaign_id),
        },
    ).fetchone()
    assert row is not None
    return {
        "campaign_id": campaign_id,
        "label_code": label,
        "label_holders": int(row[0]),
        "reasons": [
            {"code": code, "excluded": int(row[position])}
            for position, code in enumerate(REASONS, start=1)
        ],
        "audience_size": int(row[9]),
        "as_of": instant(as_of),
    }


def get_audience(
    connection: Connection, campaign_id: int, as_of: datetime | None = None
) -> dict[str, Any]:
    moment = as_of if as_of is not None else now(connection)
    counted = audience_counts(connection, campaign_id, moment)
    body: dict[str, Any] = {
        "campaign_id": campaign_id,
        "label_code": counted["label_code"],
        "audience_size": counted["audience_size"],
        "as_of": counted["as_of"],
    }
    cap = cap_of(connection, campaign_id)
    if cap is not None:
        body["max_customers"] = cap
    return body


def get_breakdown(
    connection: Connection, campaign_id: int, as_of: datetime | None = None
) -> dict[str, Any]:
    moment = as_of if as_of is not None else now(connection)
    return audience_counts(connection, campaign_id, moment)


def put_audience(
    connection: Connection, campaign_id: int, body: dict[str, Any]
) -> dict[str, Any]:
    lock_draft(connection, campaign_id)
    connection.execute(DELETE_AUDIENCE, (campaign_id,))
    if "max_customers" in body:
        connection.execute(PUT_AUDIENCE, (campaign_id, body["max_customers"]))
    return get_audience(connection, campaign_id)


# --- Budget -----------------------------------------------------------------


def get_budget(connection: Connection, campaign_id: int) -> dict[str, Any]:
    label_of(connection, campaign_id)
    body: dict[str, Any] = {"campaign_id": campaign_id}
    row = connection.execute(GET_BUDGET, (campaign_id,)).fetchone()
    if row is not None:
        for field, value in zip(
            ("total_amount", "per_customer_cap"), row, strict=False
        ):
            if value is not None:
                body[field] = money(value)
    return body


def get_budget_spending(
    connection: Connection,
    campaign_id: int,
    *,
    page: int,
    size: int,
) -> dict[str, Any]:
    """Report discounts from this campaign's promotions, net of reversals."""
    rows = connection.execute(
        SPENDING,
        {
            "campaign": campaign_id,
            "size": size,
            "offset": (page - 1) * size,
        },
    ).fetchall()
    if not rows:
        raise rules.campaign_not_found()

    computed_at, total_budget, cap, total_spent, customer_total, at_cap, _, _ = rows[0]
    body: dict[str, Any] = {
        "campaign_id": campaign_id,
        "computed_at": instant(computed_at),
    }
    if total_budget is not None:
        body["total_budget"] = money(total_budget)
    body["total_spent"] = money(total_spent)
    body["total_reached"] = total_budget is not None and total_spent >= total_budget

    customers: list[dict[str, Any]] = []
    total = 0
    if cap is not None:
        body["per_customer_cap"] = money(cap)
        body["customers_at_cap"] = int(at_cap)
        customers = [
            {
                "customer_id": str(customer_id),
                "spent_amount": money(spent_amount),
                "cap_reached": spent_amount >= cap,
            }
            for *_, customer_id, spent_amount in rows
            if customer_id is not None
        ]
        total = int(customer_total)
    body["customers"] = customers
    body["page_info"] = {"page": page, "size": size, "total": total}
    return body


def put_budget(
    connection: Connection, campaign_id: int, body: dict[str, Any]
) -> dict[str, Any]:
    total = Decimal(body["total_amount"]) if "total_amount" in body else None
    cap = Decimal(body["per_customer_cap"]) if "per_customer_cap" in body else None
    if total is not None and cap is not None and cap > total:
        raise invalid_body("per_customer_cap", "exceeds_total_amount")
    lock_draft(connection, campaign_id)
    connection.execute(DELETE_BUDGET, (campaign_id,))
    if total is not None or cap is not None:
        connection.execute(PUT_BUDGET, (campaign_id, total, cap))
    return get_budget(connection, campaign_id)


# --- Eligibility rules ------------------------------------------------------


def get_eligibility(connection: Connection, campaign_id: int) -> dict[str, Any]:
    label_of(connection, campaign_id)
    body: dict[str, Any] = {"campaign_id": campaign_id}
    row = connection.execute(GET_ELIGIBILITY, (campaign_id,)).fetchone()
    if row is not None:
        registered, minimum, window = row
        if registered is not None:
            body["registered_on_or_before"] = registered.isoformat()
        if minimum is not None:
            body["min_purchases"] = minimum
            body["purchase_window_days"] = window
    return body


def put_eligibility(
    connection: Connection, campaign_id: int, body: dict[str, Any]
) -> dict[str, Any]:
    if ("min_purchases" in body) != ("purchase_window_days" in body):
        field = "purchase_window_days" if "min_purchases" in body else "min_purchases"
        raise invalid_body(field, "required_together")
    registered: date | None = None
    if "registered_on_or_before" in body:
        registered = rules.parse_date(
            body["registered_on_or_before"], "registered_on_or_before"
        )
    lock_draft(connection, campaign_id)
    connection.execute(DELETE_ELIGIBILITY, (campaign_id,))
    if body:
        connection.execute(
            PUT_ELIGIBILITY,
            (
                campaign_id,
                registered,
                body.get("min_purchases"),
                body.get("purchase_window_days"),
            ),
        )
    return get_eligibility(connection, campaign_id)


# --- Exclusions -------------------------------------------------------------


def get_exclusions(connection: Connection, campaign_id: int) -> dict[str, Any]:
    label_of(connection, campaign_id)
    customers = connection.execute(GET_EXCLUDED_CUSTOMERS, (campaign_id,)).fetchall()
    labels = connection.execute(GET_EXCLUDED_LABELS, (campaign_id,)).fetchall()
    return {
        "campaign_id": campaign_id,
        "customer_ids": [str(row[0]) for row in customers],
        "label_codes": [row[0] for row in labels],
    }


def unknown_customer() -> ApiError:
    return ApiError(
        422,
        "unknown_customer",
        "A customer id is absent from the customer reference.",
        [{"field": "customer_ids", "code": "unknown"}],
    )


def unknown_label() -> ApiError:
    return ApiError(
        422,
        "unknown_label",
        "A label code is absent from the segment label vocabulary.",
        [{"field": "label_codes", "code": "unknown"}],
    )


def put_exclusions(
    connection: Connection, campaign_id: int, body: dict[str, Any]
) -> dict[str, Any]:
    customers = list(
        dict.fromkeys(str(c).lower() for c in body.get("customer_ids", []))
    )
    labels = list(dict.fromkeys(body.get("label_codes", [])))
    if len(customers) > MAX_EXCLUDED_CUSTOMERS:
        raise invalid_body("customer_ids", "too_many")
    if len(labels) > MAX_EXCLUDED_LABELS:
        raise invalid_body("label_codes", "too_many")
    lock_draft(connection, campaign_id)
    connection.execute(DELETE_EXCLUDED_CUSTOMERS, (campaign_id,))
    connection.execute(DELETE_EXCLUDED_LABELS, (campaign_id,))
    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.executemany(
                    PUT_EXCLUDED_CUSTOMER, [(campaign_id, c) for c in customers]
                )
    except ForeignKeyViolation as error:
        constraint = getattr(getattr(error, "diag", None), "constraint_name", None)
        if constraint != "campaign_excluded_customer_customer_id_fkey":
            raise
        raise unknown_customer() from None
    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.executemany(
                    PUT_EXCLUDED_LABEL, [(campaign_id, label) for label in labels]
                )
    except ForeignKeyViolation as error:
        constraint = getattr(getattr(error, "diag", None), "constraint_name", None)
        if constraint != "campaign_excluded_label_label_code_fkey":
            raise
        raise unknown_label() from None
    return get_exclusions(connection, campaign_id)
