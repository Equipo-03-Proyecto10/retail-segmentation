"""The campaign and experiment report (F12-04).

Read-only, gated on campaign.read like the experiments it reports on
(docs/analytics-permission-map.md). Filters are optional and combine; a value
the report does not offer is refused, not dropped (RN-45). The export applies
the same filters and carries the data-origin label into the file.
"""

from __future__ import annotations

from flask import Blueprint, Response, render_template, request

from web.db import get_connection
from web.db.experiments import list_campaign_choices
from web.middleware.authz import CAMPAIGN_READ, requires
from web.services import experiment_report as service
from web.services.experiments import DATA_ORIGINS, TARGET_METRICS

bp = Blueprint("experiment_report", __name__, url_prefix="/experiment-report")

_EMPTY = service.ReportPage((), 0, 1, 1)


def _a_page(raw: str | None) -> int:
    try:
        return max(1, int(raw or 1))
    except ValueError:
        return 1


def _context() -> dict:
    return dict(
        campaigns=list_campaign_choices(get_connection()),
        data_origins=DATA_ORIGINS,
        target_metrics=TARGET_METRICS,
        campaign=request.args.get("campaign", ""),
        origin=request.args.get("origin", ""),
    )


@bp.get("/", endpoint="index")
@requires(CAMPAIGN_READ)
def index() -> str | tuple[str, int]:
    context = _context()
    try:
        campaign_id, origin = service.parse_filters(
            context["campaign"],
            context["origin"],
            {choice.campaign_id for choice in context["campaigns"]},
        )
    except service.InvalidFilter as error:
        return (
            render_template(
                "experiment_report/index.html",
                report=_EMPTY,
                error=str(error),
                **context,
            ),
            400,
        )
    report = service.build_report(
        get_connection(),
        campaign_id=campaign_id,
        data_origin=origin,
        page=_a_page(request.args.get("page")),
    )
    return render_template(
        "experiment_report/index.html", report=report, error=None, **context
    )


@bp.get("/export.csv", endpoint="export")
@requires(CAMPAIGN_READ)
def export() -> Response | tuple[str, int]:
    context = _context()
    try:
        campaign_id, origin = service.parse_filters(
            context["campaign"],
            context["origin"],
            {choice.campaign_id for choice in context["campaigns"]},
        )
    except service.InvalidFilter as error:
        return str(error), 400
    body = service.export_csv(
        get_connection(), campaign_id=campaign_id, data_origin=origin
    )
    return Response(
        body,
        mimetype="text/csv",
        headers={"Content-Disposition": 'attachment; filename="experiment-report.csv"'},
    )
