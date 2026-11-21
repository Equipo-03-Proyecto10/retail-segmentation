"""Sale attribution: one holder per sale, kept apart from effect (F25-12).

ADR-0069. A sale is held by at most one campaign: the latest exposure before the
sale among the experiments that have a campaign, a tie going to the lower
assignment id. The rule lives in one place, the SQL function
`experiments.attribute_experiment`, which this module only calls; the unique key
on the sale is what refuses a second holder. A holder is never reassigned or
reopened, so a reversal changes nothing stored: `counts` and `net_amount` are
read at request time from the net view (ADR-0062), and every figure carries the
`ATTRIBUTION` label because attribution is commercial, never causal.
Statements are literal (guards.sh).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from mosaiq_kit import ApiError
from psycopg import Connection

from app import conversions

LABEL = "ATTRIBUTION"

RUN = (
    "SELECT attributed, already_attributed, pending"
    " FROM experiments.attribute_experiment(%s)"
)

# One attribution joined to what it is derived from, each row read as of one
# instant. The sale's own time comes from the gross view, so a reversed sale still
# has one; the net amount is zero once no unit is left. The three statements share
# the same joins and are written out in full (guards.sh).
LIST = """
SELECT s.attribution_id, s.sale_id, e.experiment_id, e.campaign_id, s.assignment_id,
       a.customer_id, a.group_id, x.exposed_at, sale.sold_at,
       a.assigned_at + make_interval(hours => 24 * e.conversion_window_days),
       net.net, s.attributed_at
  FROM experiments.sale_attribution AS s
  JOIN public.experiment_assignment AS a ON a.assignment_id = s.assignment_id
  JOIN public.experiment AS e ON e.experiment_id = a.experiment_id
  JOIN public.experiment_exposure AS x ON x.exposure_id = s.exposure_id
  CROSS JOIN LATERAL (
        SELECT min(p.occurred_at) AS sold_at
          FROM api_sales.purchase_line_v1 AS p
         WHERE p.transaction_id = s.sale_id) AS sale
  CROSS JOIN LATERAL (
        SELECT COALESCE(sum(n.net_amount), 0)::numeric(12,2) AS net
          FROM api_sales.net_purchase_line_v1 AS n
         WHERE n.transaction_id = s.sale_id) AS net
 WHERE (%(experiment)s::bigint IS NULL OR e.experiment_id = %(experiment)s::bigint)
   AND (%(campaign)s::bigint IS NULL OR e.campaign_id = %(campaign)s::bigint)
   AND (%(customer)s::uuid IS NULL OR a.customer_id = %(customer)s::uuid)
   AND (%(sale)s::bigint IS NULL OR s.sale_id = %(sale)s::bigint)
   AND (%(counts)s::boolean IS NULL OR (net.net > 0) = %(counts)s::boolean)
 ORDER BY s.attribution_id
 LIMIT %(limit)s OFFSET %(offset)s
"""
COUNT = """
SELECT count(*)
  FROM experiments.sale_attribution AS s
  JOIN public.experiment_assignment AS a ON a.assignment_id = s.assignment_id
  JOIN public.experiment AS e ON e.experiment_id = a.experiment_id
  CROSS JOIN LATERAL (
        SELECT COALESCE(sum(n.net_amount), 0)::numeric(12,2) AS net
          FROM api_sales.net_purchase_line_v1 AS n
         WHERE n.transaction_id = s.sale_id) AS net
 WHERE (%(experiment)s::bigint IS NULL OR e.experiment_id = %(experiment)s::bigint)
   AND (%(campaign)s::bigint IS NULL OR e.campaign_id = %(campaign)s::bigint)
   AND (%(customer)s::uuid IS NULL OR a.customer_id = %(customer)s::uuid)
   AND (%(sale)s::bigint IS NULL OR s.sale_id = %(sale)s::bigint)
   AND (%(counts)s::boolean IS NULL OR (net.net > 0) = %(counts)s::boolean)
"""
GET = """
SELECT s.attribution_id, s.sale_id, e.experiment_id, e.campaign_id, s.assignment_id,
       a.customer_id, a.group_id, x.exposed_at, sale.sold_at,
       a.assigned_at + make_interval(hours => 24 * e.conversion_window_days),
       net.net, s.attributed_at
  FROM experiments.sale_attribution AS s
  JOIN public.experiment_assignment AS a ON a.assignment_id = s.assignment_id
  JOIN public.experiment AS e ON e.experiment_id = a.experiment_id
  JOIN public.experiment_exposure AS x ON x.exposure_id = s.exposure_id
  CROSS JOIN LATERAL (
        SELECT min(p.occurred_at) AS sold_at
          FROM api_sales.purchase_line_v1 AS p
         WHERE p.transaction_id = s.sale_id) AS sale
  CROSS JOIN LATERAL (
        SELECT COALESCE(sum(n.net_amount), 0)::numeric(12,2) AS net
          FROM api_sales.net_purchase_line_v1 AS n
         WHERE n.transaction_id = s.sale_id) AS net
 WHERE s.attribution_id = %(attribution)s
"""

# What a result reports for one experiment's treatment arm: the sales it holds,
# read net as of now. `held` counts every row, so a sale since fully reversed
# still shows that the arm held it, while sales and revenue count only what is
# left (ADR-0062).
SUMMARY = """
SELECT count(*), count(*) FILTER (WHERE net.net > 0),
       count(DISTINCT a.customer_id) FILTER (WHERE net.net > 0),
       COALESCE(sum(net.net), 0)::numeric(12,2)
  FROM experiments.sale_attribution AS s
  JOIN public.experiment_assignment AS a ON a.assignment_id = s.assignment_id
  CROSS JOIN LATERAL (
        SELECT COALESCE(sum(n.net_amount), 0)::numeric(12,2) AS net
          FROM api_sales.net_purchase_line_v1 AS n
         WHERE n.transaction_id = s.sale_id) AS net
 WHERE a.experiment_id = %(experiment)s AND a.group_id = %(arm)s
"""
# Treatment-arm conversions whose sale this experiment's campaign does not hold:
# held by another campaign, or not held at all (no exposure before the sale).
UNATTRIBUTED = """
SELECT count(DISTINCT c.transaction_id)
  FROM public.experiment_conversion AS c
  JOIN public.experiment_assignment AS a ON a.assignment_id = c.assignment_id
  JOIN public.experiment_group AS g
    ON g.group_id = a.group_id AND g.experiment_id = a.experiment_id
 WHERE a.experiment_id = %(experiment)s AND g.kind = 'TREATMENT'
   AND NOT EXISTS (
        SELECT FROM experiments.sale_attribution AS s
          JOIN public.experiment_assignment AS holder
            ON holder.assignment_id = s.assignment_id
         WHERE s.sale_id = c.transaction_id
           AND holder.experiment_id = %(experiment)s)
"""
NOW = "SELECT clock_timestamp()"


def not_found() -> ApiError:
    return ApiError(
        404, "sale_attribution_not_found", "The sale attribution does not exist."
    )


def evaluate(connection: Connection, experiment_id: int) -> dict[str, Any]:
    """Hold the sales whose windows have closed and say how many were added."""
    conversions.require_experiment(connection, experiment_id)
    if not connection.execute(conversions.IS_ASSIGNED, (experiment_id,)).fetchone()[0]:
        raise conversions.not_assigned()
    attributed, already, pending = connection.execute(RUN, (experiment_id,)).fetchone()
    as_of = conversions.instant(connection.execute(NOW).fetchone()[0])
    return {
        "experiment_id": experiment_id,
        "attributed": attributed,
        "already_attributed": already,
        "pending": pending,
        "label": LABEL,
        "as_of": as_of,
    }


def parse_filters(args: Any) -> dict[str, Any]:
    """Five filters: None (off) when absent, 422 `invalid_query` when invalid."""
    values: dict[str, Any] = conversions.parse_filters(
        {"customer_id": args.get("customer_id"), "counts": args.get("counts")}
    )
    values.pop("arm")
    for name, key in (
        ("experiment_id", "experiment"),
        ("campaign_id", "campaign"),
        ("sale_id", "sale"),
    ):
        raw = args.get(name)
        if raw is not None:
            if not raw.isascii() or not raw.isdigit() or not 1 <= int(raw) <= 2**63 - 1:
                raise conversions.invalid_query(name, "out_of_range")
            raw = int(raw)
        values[key] = raw
    return values


def _row(row: tuple[Any, ...], as_of: str) -> dict[str, Any]:
    (
        attribution_id,
        sale_id,
        experiment_id,
        campaign_id,
        assignment_id,
        customer_id,
        arm_id,
        exposed_at,
        sold_at,
        closes,
        net,
        attributed_at,
    ) = row
    return {
        "attribution_id": attribution_id,
        "sale_id": sale_id,
        "experiment_id": experiment_id,
        "campaign_id": campaign_id,
        "assignment_id": assignment_id,
        "customer_id": str(customer_id),
        "arm_id": arm_id,
        "exposed_at": conversions.instant(exposed_at),
        "sold_at": conversions.instant(sold_at),
        "window_closes_at": conversions.instant(closes),
        "net_amount": conversions.money(net),
        "counts": net > 0,
        "attributed_at": conversions.instant(attributed_at),
        "label": LABEL,
        "as_of": as_of,
    }


def list_attributions(
    connection: Connection, filters: dict[str, Any], *, page: int, size: int
) -> tuple[list[dict[str, Any]], int]:
    as_of = conversions.instant(connection.execute(NOW).fetchone()[0])
    with connection.cursor() as cursor:
        cursor.execute(LIST, filters | {"limit": size, "offset": (page - 1) * size})
        rows = cursor.fetchall()
        cursor.execute(COUNT, filters)
        total = cursor.fetchone()[0]
    return [_row(row, as_of) for row in rows], total


def get_attribution(connection: Connection, attribution_id: int) -> dict[str, Any]:
    as_of = conversions.instant(connection.execute(NOW).fetchone()[0])
    with connection.cursor() as cursor:
        cursor.execute(GET, {"attribution": attribution_id})
        row = cursor.fetchone()
    if row is None:
        raise not_found()
    return _row(row, as_of)


def result_section(
    connection: Connection, experiment: dict[str, Any]
) -> dict[str, Any] | None:
    """The `attribution` section of a result, read now: per treatment arm, the sales
    held and their net revenue, apart from the effect the comparison reports
    (ADR-0069). None when the experiment has no campaign or holds nothing yet."""
    if experiment["campaign_id"] is None:
        return None
    experiment_id = experiment["experiment_id"]
    as_of = conversions.instant(connection.execute(NOW).fetchone()[0])
    arms = []
    held = sales = 0
    revenue = Decimal(0)
    with connection.cursor() as cursor:
        for arm in experiment["arms"]:
            if arm["kind"] != "TREATMENT":
                continue
            cursor.execute(SUMMARY, {"experiment": experiment_id, "arm": arm["arm_id"]})
            rows, count, customers, net = cursor.fetchone()
            held += rows
            sales += count
            revenue += net
            arms.append(
                {
                    "arm_id": arm["arm_id"],
                    "attributed_sales": count,
                    "attributed_customers": customers,
                    "attributed_revenue": conversions.money(net),
                }
            )
        cursor.execute(UNATTRIBUTED, {"experiment": experiment_id})
        (unattributed,) = cursor.fetchone()
    if held == 0:
        return None
    return {
        "label": LABEL,
        "window_days": experiment["design"]["conversion_window_days"],
        "arms": arms,
        "attributed_sales": sales,
        "attributed_revenue": conversions.money(revenue),
        "unattributed_conversions": unattributed,
        "as_of": as_of,
    }
