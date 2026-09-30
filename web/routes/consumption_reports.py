"""The filtered consumption-shift and recommendation reports (F12-03).

Read-only, gated on segment.read (ADR-0023: STORE_MANAGER, the role this
story is written for, holds it alongside every other Phase 9/10/12 analytics
surface). One filter bar -- store, channel, category, and a period stated as
"as of" plus a number of days -- drives two independent reports below it.
Nothing here reads or is told which segmentation method produced anything.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time

from flask import Blueprint, render_template, request

from web.db import get_connection
from web.db.categories import list_all_categories
from web.db.channels import list_channels
from web.db.stores import list_stores
from web.middleware.authz import SEGMENT_READ, requires
from web.parsing import iso_date, page_number, whole_number
from web.services.consumption_reports import (
    build_recommendation_report,
    build_shift_report,
)
from web.services.consumption_shift import MIN_PURCHASES_PER_PERIOD, InvalidPeriods
from web.services.recommendations import InvalidLimit

bp = Blueprint("consumption_reports", __name__, url_prefix="/consumption-reports")

DEFAULT_WINDOW_DAYS = 180
MAX_WINDOW_DAYS = 3650


def _an_int(raw: str | None) -> int | None:
    """None when unset; the value when it is a whole number; -1 when the
    value given could not be one at all (refused, not silently dropped)."""
    if not raw:
        return None
    value = whole_number(raw)
    return -1 if value is None else value


def _a_date(raw: str | None) -> date | None:
    if not raw:
        return None
    value = iso_date(raw)
    if value is None:
        raise ValueError("Enter a valid date in YYYY-MM-DD format.")
    return value


def _a_window(raw: str | None) -> int:
    if not raw:
        return DEFAULT_WINDOW_DAYS
    days = whole_number(raw, MAX_WINDOW_DAYS)
    if not days:
        raise ValueError(
            f"The window is a whole number of days, 1 to {MAX_WINDOW_DAYS}."
        )
    return days


@bp.get("/")
@requires(SEGMENT_READ)
def index() -> str | tuple[str, int]:
    """Render both reports for the chosen filters."""
    connection = get_connection()

    store_id = _an_int(request.args.get("store"))
    channel_id = _an_int(request.args.get("channel"))
    category_id = _an_int(request.args.get("category"))

    context = dict(
        stores=list_stores(connection, search=None, page=1, per_page=200)[0],
        channels=list_channels(connection, search=None, page=1, per_page=200)[0],
        categories=list_all_categories(connection),
        store=request.args.get("store", ""),
        channel=request.args.get("channel", ""),
        category=request.args.get("category", ""),
        as_of=request.args.get("as_of", ""),
        window_days=request.args.get("window_days", str(DEFAULT_WINDOW_DAYS)),
    )

    if -1 in (store_id, channel_id, category_id):
        return (
            render_template(
                "consumption_reports/index.html",
                shifts=None,
                recommendations=None,
                error="Choose values from the lists.",
                **context,
            ),
            400,
        )

    try:
        as_of_date = _a_date(request.args.get("as_of"))
        as_of = (
            datetime.combine(as_of_date, time(0, 0), tzinfo=UTC)
            if as_of_date
            else datetime.now(UTC)
        )
        window_days = _a_window(request.args.get("window_days"))

        shifts = build_shift_report(
            connection,
            as_of=as_of,
            window_days=window_days,
            store_id=store_id,
            channel_id=channel_id,
            category_id=category_id,
        )
        recommendations = build_recommendation_report(
            connection,
            as_of=as_of,
            window_days=window_days,
            store_id=store_id,
            channel_id=channel_id,
            category_id=category_id,
            page=page_number(request.args.get("page")),
        )
    except (ValueError, InvalidPeriods, InvalidLimit) as error:
        return (
            render_template(
                "consumption_reports/index.html",
                shifts=None,
                recommendations=None,
                error=str(error),
                **context,
            ),
            400,
        )

    return render_template(
        "consumption_reports/index.html",
        shifts=shifts,
        recommendations=recommendations,
        error=None,
        min_purchases=MIN_PURCHASES_PER_PERIOD,
        **context,
    )
