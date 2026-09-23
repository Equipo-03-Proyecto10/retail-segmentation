"""Permission-gated destinations for planned interface sections.

The permission matrix already names reports, while the current delivery defines
no workflow for that section. This route keeps navigation complete without
inventing business behavior ahead of the agreed scope.
"""

from flask import Blueprint, render_template

from web.middleware.authz import REPORT_READ, requires

reports_bp = Blueprint("reports", __name__, url_prefix="/reports")


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
