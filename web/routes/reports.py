"""The reports index (#344): every analytics report the signed-in profile may
open, and the revenue summary for anyone holding `report.read`.

The index lists a report only when the profile holds the permission that
report is gated on, so it never offers a link that answers 403
(docs/analytics-permission-map.md). It carries no report logic of its own:
each link is the report's own page.
"""

from __future__ import annotations

from dataclasses import dataclass

from flask import Blueprint, current_app, render_template

from web.db import get_connection
from web.db.revenue_summary import read_revenue_summary
from web.middleware.authz import (
    CAMPAIGN_READ,
    REPORT_READ,
    SEGMENT_READ,
    can,
    requires,
)

reports_bp = Blueprint("reports", __name__, url_prefix="/reports")

REVENUE_WINDOW_DAYS = 90


@dataclass(frozen=True)
class ReportLink:
    title: str
    description: str
    endpoint: str
    permission: str


REPORT_GROUPS: tuple[tuple[str, tuple[ReportLink, ...]], ...] = (
    (
        "Segmentation",
        (
            ReportLink(
                "Segmentation dashboard",
                "Segment sizes, the RFM distribution, migration and revenue by label.",
                "segmentation_dashboard.index",
                SEGMENT_READ,
            ),
            ReportLink(
                "Segment history report",
                "Filtered segment history and migration between runs.",
                "segment_history_report.index",
                SEGMENT_READ,
            ),
            ReportLink(
                "Consumption reports",
                "Consumption shifts and recommendations by store, channel, category.",
                "consumption_reports.index",
                SEGMENT_READ,
            ),
        ),
    ),
    (
        "Campaigns and experiments",
        (
            ReportLink(
                "Experiment report",
                "Assignment, exposure, conversion and uplift, with a CSV export.",
                "experiment_report.index",
                CAMPAIGN_READ,
            ),
        ),
    ),
)


def reports_for_current_user() -> list[tuple[str, list[ReportLink]]]:
    """The groups the profile may open, each with only its permitted links."""
    groups = []
    for title, links in REPORT_GROUPS:
        allowed = [link for link in links if can(link.permission)]
        if allowed:
            groups.append((title, allowed))
    return groups


@reports_bp.get("/", endpoint="index")
@requires(REPORT_READ)
def reports_index() -> str:
    config = current_app.config["APP_CONFIG"]
    return render_template(
        "reports/index.html",
        groups=reports_for_current_user(),
        revenue=read_revenue_summary(get_connection(), REVENUE_WINDOW_DAYS),
        is_synthetic=config.data_is_synthetic,
    )
