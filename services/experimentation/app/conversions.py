"""Conversions: a qualifying sale inside a fixed window, read net (F25-11).

RN-27, ADR-0019, ADR-0050, ADR-0069. A conversion links an assignment to a sale
of the assigned customer made at or after the assignment instant and before it
plus the experiment's conversion window. Evaluating records the sales that are
not recorded yet and nothing else: no recorded row is ever changed or deleted,
and `UNIQUE (assignment_id, transaction_id)` makes a second evaluation add only
what the first one had not seen.

A reversal never touches a conversion. The row stays; whether it counts is read
at request time from the net view (ADR-0062): it counts while its sale has a
unit left. A sale that was fully reversed has no row in the net view, so the gross
view still gives its date and origin. Statements are literal (guards.sh).
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from mosaiq_kit import ApiError
from psycopg import Connection

FIND_EXPERIMENT = "SELECT experiment_id FROM public.experiment WHERE experiment_id = %s"
IS_ASSIGNED = (
    "SELECT EXISTS (SELECT FROM public.experiment_assignment WHERE experiment_id = %s)"
)
NOW = "SELECT clock_timestamp()"

# Each qualifying sale once: a sale with several lines is several rows of the net
# view, and every row it has has a unit left. The window is a number of 24-hour
# days after the assignment instant, so a change of time zone cannot move it.
EVALUATE = """
INSERT INTO public.experiment_conversion (assignment_id, transaction_id, converted_at)
SELECT a.assignment_id, s.transaction_id, %(at)s::timestamptz
  FROM public.experiment AS e
  JOIN public.experiment_assignment AS a ON a.experiment_id = e.experiment_id
  JOIN (SELECT DISTINCT n.transaction_id, n.customer_id, n.occurred_at
          FROM api_sales.net_purchase_line_v1 AS n) AS s
    ON s.customer_id = a.customer_id
 WHERE e.experiment_id = %(experiment)s
   AND s.occurred_at >= a.assigned_at
   AND s.occurred_at < a.assigned_at
                       + make_interval(hours => 24 * e.conversion_window_days)
 ORDER BY a.assignment_id, s.transaction_id
ON CONFLICT (assignment_id, transaction_id) DO NOTHING
"""
COUNT_RECORDED = """
SELECT count(*)
  FROM public.experiment_conversion AS c
  JOIN public.experiment_assignment AS a ON a.assignment_id = c.assignment_id
 WHERE a.experiment_id = %s
"""

# The page and its count repeat the same filters, each switched off by a NULL.
LIST = """
SELECT c.conversion_id, a.group_id, a.customer_id, c.transaction_id,
       sale.sold_at, c.converted_at, sale.origin, net.net
  FROM public.experiment_conversion AS c
  JOIN public.experiment_assignment AS a ON a.assignment_id = c.assignment_id
  CROSS JOIN LATERAL (
        SELECT min(p.occurred_at) AS sold_at, min(p.data_origin) AS origin
          FROM api_sales.purchase_line_v1 AS p
         WHERE p.transaction_id = c.transaction_id) AS sale
  CROSS JOIN LATERAL (
        SELECT COALESCE(sum(n.net_amount), 0)::numeric(12,2) AS net
          FROM api_sales.net_purchase_line_v1 AS n
         WHERE n.transaction_id = c.transaction_id) AS net
 WHERE a.experiment_id = %(experiment)s
   AND (%(customer)s::uuid IS NULL OR a.customer_id = %(customer)s::uuid)
   AND (%(arm)s::bigint IS NULL OR a.group_id = %(arm)s::bigint)
   AND (%(counts)s::boolean IS NULL OR (net.net > 0) = %(counts)s::boolean)
 ORDER BY c.conversion_id
 LIMIT %(limit)s OFFSET %(offset)s
"""
COUNT = """
SELECT count(*)
  FROM public.experiment_conversion AS c
  JOIN public.experiment_assignment AS a ON a.assignment_id = c.assignment_id
  CROSS JOIN LATERAL (
        SELECT COALESCE(sum(n.net_amount), 0)::numeric(12,2) AS net
          FROM api_sales.net_purchase_line_v1 AS n
         WHERE n.transaction_id = c.transaction_id) AS net
 WHERE a.experiment_id = %(experiment)s
   AND (%(customer)s::uuid IS NULL OR a.customer_id = %(customer)s::uuid)
   AND (%(arm)s::bigint IS NULL OR a.group_id = %(arm)s::bigint)
   AND (%(counts)s::boolean IS NULL OR (net.net > 0) = %(counts)s::boolean)
"""

# Intent to treat, per arm, over every assigned customer. A customer is converted
# while a recorded conversion of theirs has a sale with a unit left; otherwise
# pending until their own window closes, and not converted after (ADR-0069).
OUTCOMES = """
SELECT g.group_id, g.kind, g.name,
       count(*),
       count(*) FILTER (WHERE seen.converted),
       count(*) FILTER (WHERE NOT seen.converted AND a.assigned_at
              + make_interval(hours => 24 * e.conversion_window_days) > %(as_of)s),
       count(*) FILTER (WHERE NOT seen.converted AND a.assigned_at
              + make_interval(hours => 24 * e.conversion_window_days) <= %(as_of)s)
  FROM public.experiment AS e
  JOIN public.experiment_assignment AS a ON a.experiment_id = e.experiment_id
  JOIN public.experiment_group AS g
    ON g.group_id = a.group_id AND g.experiment_id = a.experiment_id
  CROSS JOIN LATERAL (
        SELECT EXISTS (
            SELECT FROM public.experiment_conversion AS c
             WHERE c.assignment_id = a.assignment_id
               AND EXISTS (SELECT FROM api_sales.net_purchase_line_v1 AS n
                            WHERE n.transaction_id = c.transaction_id)
        ) AS converted) AS seen
 WHERE e.experiment_id = %(experiment)s
 GROUP BY g.group_id, g.kind, g.name
 ORDER BY g.group_id
"""


def experiment_not_found() -> ApiError:
    return ApiError(404, "experiment_not_found", "The experiment does not exist.")


def not_assigned() -> ApiError:
    return ApiError(
        409,
        "experiment_not_assigned",
        "The experiment is not assigned, so it has nothing to evaluate or report.",
    )


def invalid_query(field: str, code: str) -> ApiError:
    return ApiError(
        422,
        "invalid_query",
        "A query parameter is outside what the operation accepts.",
        [{"field": field, "code": code}],
    )


def require_experiment(connection: Connection, experiment_id: int) -> None:
    if connection.execute(FIND_EXPERIMENT, (experiment_id,)).fetchone() is None:
        raise experiment_not_found()


def money(value: Decimal) -> str:
    return f"{value:.2f}"


def instant(value: datetime) -> str:
    """An instant in UTC, whatever offset the session's TimeZone read it with."""
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def evaluate(connection: Connection, experiment_id: int) -> dict[str, Any]:
    """Record the qualifying sales that are not recorded yet, and say how many.

    Two evaluations at once are safe without a lock: the unique key makes the
    second insert of a sale do nothing, so each sale is recorded by one of them.
    """
    require_experiment(connection, experiment_id)
    if not connection.execute(IS_ASSIGNED, (experiment_id,)).fetchone()[0]:
        raise not_assigned()
    at = connection.execute(NOW).fetchone()[0]
    recorded = connection.execute(
        EVALUATE, {"at": at, "experiment": experiment_id}
    ).rowcount
    (total,) = connection.execute(COUNT_RECORDED, (experiment_id,)).fetchone()
    return {
        "experiment_id": experiment_id,
        "evaluated_at": instant(at),
        "recorded": recorded,
        "already_recorded": total - recorded,
    }


def parse_filters(args: Any) -> dict[str, Any]:
    """The three filters: None (off) when absent, 422 `invalid_query` when invalid."""
    customer = args.get("customer_id")
    if customer is not None:
        try:
            customer = str(UUID(customer))
        except ValueError:
            raise invalid_query("customer_id", "invalid_format") from None
    arm = args.get("arm_id")
    if arm is not None:
        if not arm.isascii() or not arm.isdigit() or not 1 <= int(arm) <= 2**63 - 1:
            raise invalid_query("arm_id", "out_of_range")
        arm = int(arm)
    counts = args.get("counts")
    if counts is not None:
        if counts not in ("true", "false"):
            raise invalid_query("counts", "invalid_type")
        counts = counts == "true"
    return {"customer": customer, "arm": arm, "counts": counts}


def list_conversions(
    connection: Connection,
    experiment_id: int,
    filters: dict[str, Any],
    *,
    page: int,
    size: int,
) -> tuple[list[dict[str, Any]], int]:
    """One page in conversion_id order, each row read as of one instant."""
    require_experiment(connection, experiment_id)
    values = filters | {"experiment": experiment_id}
    as_of = instant(connection.execute(NOW).fetchone()[0])
    with connection.cursor() as cursor:
        cursor.execute(LIST, values | {"limit": size, "offset": (page - 1) * size})
        rows = cursor.fetchall()
        cursor.execute(COUNT, values)
        total = cursor.fetchone()[0]
    return [
        {
            "conversion_id": conversion_id,
            "experiment_id": experiment_id,
            "arm_id": arm_id,
            "customer_id": str(customer_id),
            "sale_id": sale_id,
            "sold_at": instant(sold_at),
            "converted_at": instant(converted_at),
            "sale_data_origin": origin,
            "counts": net > 0,
            "net_amount": money(net),
            "as_of": as_of,
        }
        for (
            conversion_id,
            arm_id,
            customer_id,
            sale_id,
            sold_at,
            converted_at,
            origin,
            net,
        ) in rows
    ], total


def arm_outcomes(
    connection: Connection, experiment_id: int, as_of: datetime | None = None
) -> list[dict[str, Any]]:
    """Per arm, converted, pending and not converted: the counts a result reports.

    Not an operation yet: getExperimentResult (F25-13) reads it. `converted +
    pending + not_converted` is `analysed`, which here is every assigned customer.
    """
    require_experiment(connection, experiment_id)
    moment = as_of or connection.execute(NOW).fetchone()[0]
    with connection.cursor() as cursor:
        cursor.execute(OUTCOMES, {"experiment": experiment_id, "as_of": moment})
        rows = cursor.fetchall()
    return [
        {
            "arm_id": arm_id,
            "kind": kind,
            "name": name,
            "assigned": assigned,
            "analysed": assigned,
            "converted": converted,
            "pending": pending,
            "not_converted": not_converted,
        }
        for arm_id, kind, name, assigned, converted, pending, not_converted in rows
    ]
