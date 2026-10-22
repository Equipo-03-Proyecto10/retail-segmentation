"""RN-40 recommendation lists, through `recommend` in web/services/recommendations.py.

Every seeded customer, at the seed's anchor, plus three synthetic customers
added inside their own rolled-back case: one with no segment history, one whose
open row is an unassigned result of an added run, and one assigned with no
accepted sale in the window.
"""

from __future__ import annotations

from typing import Any

import psycopg

from tests.golden.cases import Case, Context
from web.services.recommendations import RecommendationResult, recommend

# Synthetic customers: ids no seeded row uses.
NO_HISTORY = "00000000-0000-0000-0000-00000000a001"
UNASSIGNED = "00000000-0000-0000-0000-00000000a002"
NO_SALE = "00000000-0000-0000-0000-00000000a003"


def _normalized(
    result: RecommendationResult, run_id: int | None = None
) -> dict[str, Any]:
    """Everything recommend decided, without the absolute window bounds.

    The window is recorded by its length: the anchor moves with each load, and
    the bounds with it. An added run's id comes from an identity that a
    rolled-back case still advances, so it is replaced by a placeholder.
    """
    message = result.message
    if run_id is not None:
        message = message.replace(f"run {run_id} ", "run <added run> ")
    return {
        "status": result.status.value,
        "message": message,
        "window_days": result.window_days,
        "label_code": result.label_code,
        "store_id": result.store_id,
        "channel_id": result.channel_id,
        "items": [
            {
                "product_id": item.product_id,
                "name": item.name,
                "category_id": item.category_id,
                "category_name": item.category_name,
                "in_stock": item.in_stock,
                "reasons": [
                    {"signal": reason.signal.value, "text": reason.text}
                    for reason in item.reasons
                ],
            }
            for item in result.recommendations
        ],
    }


def _seeded(customer_id: str, context: Context) -> Case:
    def run(connection: psycopg.Connection) -> dict[str, Any]:
        return _normalized(recommend(connection, customer_id, as_of=context.anchor))

    return Case(f"customer-{customer_id}", {"customer_id": customer_id}, run)


_ADD_CUSTOMER = (
    "INSERT INTO customer (customer_id, name, registration_channel_id, registered_on)"
    " VALUES (%s, %s, 1, %s)"
)
_ADD_RUN = (
    "INSERT INTO segmentation_run"
    " (method, window_days, parameters, customer_count, run_at, data_origin)"
    " VALUES ('RFM_RULES', 180, '{\"window_days\": 180}'::jsonb, 1, %s, 'SEEDED')"
    " RETURNING run_id"
)
_ADD_ROW = (
    "INSERT INTO customer_segment_history (customer_id, run_id, label_code, valid_from)"
    " VALUES (%s, %s, %s, %s)"
)


def _synthetic(
    case_id: str, customer_id: str, name: str, label_code: str | None, context: Context
) -> Case:
    def run(connection: psycopg.Connection) -> dict[str, Any]:
        run_id = None
        with connection.cursor() as cursor:
            cursor.execute(
                _ADD_CUSTOMER, (customer_id, name, context.before_anchor(400).date())
            )
            if case_id != "synthetic-no-history":
                cursor.execute(_ADD_RUN, (context.anchor,))
                run_id = cursor.fetchone()[0]
                cursor.execute(
                    _ADD_ROW,
                    (customer_id, run_id, label_code, context.before_anchor(1)),
                )
        result = recommend(connection, customer_id, as_of=context.anchor)
        return _normalized(result, run_id)

    setup = {
        "synthetic-no-history": "a customer with no customer_segment_history row",
        "synthetic-unassigned": (
            "a customer whose open row, from an added run, has a NULL label_code"
        ),
        "synthetic-no-sale": (
            "a customer assigned LOYAL by an added run, with no accepted sale"
        ),
    }[case_id]
    # recommendations-service splits the web's no_segment in two, read from
    # api_segments.current_label_v1.state (F14-22).
    differs = {
        "synthetic-no-history": (
            "recommendations-service reports no_open_assignment where the web "
            "reports no_segment: the customer has no open assignment."
        ),
        "synthetic-unassigned": (
            "recommendations-service reports unassigned_result where the web "
            "reports no_segment: the open assignment is the unassigned result."
        ),
    }.get(case_id)
    return Case(case_id, {"customer_id": customer_id, "setup": setup}, run, differs)


def build(connection: psycopg.Connection, context: Context) -> list[Case]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT customer_id::text FROM customer ORDER BY customer_id")
        seeded = [row[0] for row in cursor.fetchall()]
    return [_seeded(customer_id, context) for customer_id in seeded] + [
        _synthetic(
            "synthetic-no-history", NO_HISTORY, "Golden: no history", None, context
        ),
        _synthetic(
            "synthetic-unassigned", UNASSIGNED, "Golden: unassigned", None, context
        ),
        _synthetic("synthetic-no-sale", NO_SALE, "Golden: no sale", "LOYAL", context),
    ]


def as_of(context: Context) -> dict[str, Any]:
    return {"anchor": context.anchor.isoformat()}
