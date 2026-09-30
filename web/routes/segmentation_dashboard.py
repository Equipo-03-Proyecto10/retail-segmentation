"""The segmentation dashboard (F12-01): segment sizes, the RFM distribution,
migration flow against the previous run, and revenue by label, for one run.

Read-only, gated on segment.read like the rest of the segmentation surface
(ADR-0010). Server-rendered: the four charts' data is computed here and
embedded in the page as JSON text (docs/roadmap.md's "dashboards constraint"),
never fetched afterwards from an endpoint, and the page loads Highcharts from
this application's own static files rather than a CDN -- application pages
carry a same-origin-only script-src (deploy/nginx/mosaiq.conf), the CDN
allowance in docs/design-system/charts/ does not extend here, and a page that
assumed otherwise would render as this application's fonts once did before
they were vendored for the same reason.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from flask import Blueprint, Response, abort, current_app, render_template, request

from web.db import get_connection
from web.db.segments import get_run, list_runs
from web.middleware.authz import SEGMENT_READ, requires
from web.parsing import BIGINT_MAX, whole_number
from web.services.segmentation_dashboard import (
    Dashboard,
    NoRuns,
    build_dashboard,
    build_kpis,
)

bp = Blueprint("segmentation_dashboard", __name__, url_prefix="/segmentation-dashboard")

# The newest runs, for the run picker. An older run selected by id in the URL
# is added to the options, the same pattern migration_matrix and
# model_comparison already use, so it stays selected on resubmit.
_RUN_OPTIONS = 100


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def _embed(payload: dict) -> str:
    """Serialize for a `<script type="application/json">` block: inert data,
    never an executable script, so the page's strict same-origin script-src
    (no 'unsafe-inline') never has to allow it. `<` is escaped so no value
    -- none here is arbitrary text, but the defence costs nothing -- can
    close the tag early."""
    return json.dumps(payload, default=_json_default).replace("<", "\\u003c")


def _run_summary(run) -> dict:
    return {
        "run_id": run.run_id,
        "method": run.method,
        "run_at": run.run_at.isoformat(),
        "window_days": run.window_days,
        "customer_count": run.customer_count,
    }


def _chart_payload(dashboard: Dashboard) -> dict:
    """Every chart's data, in the shape docs/design-system/charts/index.html's
    worked examples already use: a column chart's [{name, y}], a heatmap's
    [x, y, value] triples over quintile categories, a sankey's keyed
    [from, to, weight] rows with distinct before/after node ids."""
    sizes = [
        {"name": size.label or "Unassigned", "y": size.count}
        for size in dashboard.sizes
    ]
    revenue = [
        {"name": item.label, "y": float(item.total)} for item in dashboard.revenue
    ]
    heatmap = [
        [cell.frequency_bin - 1, cell.recency_bin - 1, cell.count]
        for cell in dashboard.heatmap
    ]

    migration: dict | None = None
    if dashboard.migration is not None:
        nodes = {}
        for link in dashboard.migration.links:
            nodes[f"{link.source} (before)"] = link.source
            nodes[f"{link.target} (after)"] = link.target
        migration = {
            "nodes": [{"id": node_id} for node_id in nodes],
            "data": [
                [f"{link.source} (before)", f"{link.target} (after)", link.weight]
                for link in dashboard.migration.links
            ],
            "new_to_population": dashboard.migration.new_to_population,
            "left_the_population": dashboard.migration.left_the_population,
        }

    return {
        "sizes": sizes,
        "revenue": revenue,
        "heatmap": heatmap,
        "migration": migration,
    }


@bp.get("/")
@requires(SEGMENT_READ)
def index() -> Response | tuple[Response, int]:
    """Render the dashboard for one run: the run named in `?run=`, or the
    newest run there is. A run id that is not a whole number is a 400; one
    that does not exist is a 404; no run at all yet is a 200 that says so.
    """
    connection = get_connection()
    config = current_app.config["APP_CONFIG"]

    raw_run = request.args.get("run", "")
    run_id: int | None = None
    if raw_run:
        run_id = whole_number(raw_run, BIGINT_MAX)
        if run_id is None:
            return (
                render_template(
                    "segmentation_dashboard/index.html",
                    dashboard=None,
                    runs=[],
                    error="Choose a run from the list.",
                    is_synthetic=config.data_is_synthetic,
                ),
                400,
            )
        if get_run(connection, run_id) is None:
            abort(404)

    try:
        dashboard = build_dashboard(connection, run_id)
    except NoRuns:
        return render_template(
            "segmentation_dashboard/index.html",
            dashboard=None,
            runs=[],
            error=None,
            is_synthetic=config.data_is_synthetic,
        )

    runs, _total = list_runs(connection, page=1, per_page=_RUN_OPTIONS)
    if dashboard.run.run_id not in {run.run_id for run in runs}:
        runs.append(dashboard.run)

    payload = {
        "run": _run_summary(dashboard.run),
        "previous_run": (
            _run_summary(dashboard.previous_run) if dashboard.previous_run else None
        ),
        **_chart_payload(dashboard),
    }

    return render_template(
        "segmentation_dashboard/index.html",
        dashboard=dashboard,
        runs=runs,
        error=None,
        is_synthetic=config.data_is_synthetic,
        chart_data=_embed(payload),
        kpis=build_kpis(connection, dashboard.run, datetime.now(UTC)),
    )
