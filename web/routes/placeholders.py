"""Permission-gated destinations for planned interface sections.

The permission matrix already names campaigns and reports, while the current
delivery defines no workflow for either section. These routes keep navigation
complete without inventing business behavior ahead of the agreed scope.
"""

from flask import Blueprint, render_template

from web.middleware.authz import CAMPAIGN_READ, REPORT_READ, requires

campaigns_bp = Blueprint("campaigns", __name__, url_prefix="/campaigns")
reports_bp = Blueprint("reports", __name__, url_prefix="/reports")


@campaigns_bp.get("/", endpoint="index")
@requires(CAMPAIGN_READ)
def campaigns_index() -> str:
    """Explain the current delivery boundary for campaign workflows."""
    return render_template(
        "status/coming_soon.html",
        page_title="Campaigns",
        description=(
            "Campaign planning and management do not have a workflow in this "
            "delivery."
        ),
        scope_note=(
            "The data model and permission rules are in place. A later story must "
            "define the user workflow before this section can create or change "
            "campaigns."
        ),
    )


@reports_bp.get("/", endpoint="index")
@requires(REPORT_READ)
def reports_index() -> str:
    """Explain that analytical reports remain on the roadmap."""
    return render_template(
        "status/coming_soon.html",
        page_title="Reports",
        description="Analytical reports and dashboards are still being built.",
        scope_note=(
            "Dashboards, RFM distributions and segment migration reports are deferred "
            "in docs/roadmap.md and are not part of the current delivery."
        ),
    )
