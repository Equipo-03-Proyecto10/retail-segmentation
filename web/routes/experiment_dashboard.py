"""The experiment dashboard (AN-03): one experiment's conversion rate per arm,
its difference from the baseline arm with the 95% interval, and conversions over
time within the conversion window.

Read-only, gated on campaign.read like the experiments it charts
(docs/analytics-permission-map.md). Server-rendered exactly as the segmentation
dashboard is: the data is computed here and embedded in the page as inert JSON,
never fetched from an endpoint, and Highcharts is served from this
application's own static files because application pages carry a same-origin
script-src (deploy/nginx/mosaiq.conf).
"""

from __future__ import annotations

import json
from typing import Any

from flask import Blueprint, abort, render_template, request

from web.db import get_connection
from web.db.experiment_report import list_report_experiments
from web.middleware.authz import CAMPAIGN_READ, requires
from web.parsing import INT_MAX, whole_number
from web.services import experiment_dashboard as service
from web.services.experiments import ExperimentNotFound

bp = Blueprint("experiment_dashboard", __name__, url_prefix="/experiment-dashboard")

# The newest experiments, for the picker. One selected by id in the URL is added
# to the options, so it stays selected on resubmit.
_PICKER_OPTIONS = 100


def _embed(payload: dict) -> str:
    """Serialize for a `<script type="application/json">` block: inert data,
    never an executable script, so the page's same-origin script-src never has
    to allow it. `<` is escaped so no value -- an experiment or arm name is user
    input -- can close the tag early."""
    return json.dumps(payload).replace("<", "\\u003c")


def _points(rate: float | None) -> float | None:
    return None if rate is None else round(rate * 100, 4)


def chart_payload(dashboard: service.ExperimentDashboard) -> dict[str, Any]:
    """Every chart's data. Rates and differences are in percent and percentage
    points; an arm with nothing to divide by is null, never zero."""
    experiment = dashboard.experiment
    arms = dashboard.arms
    difference: dict[str, Any] | None = None
    if dashboard.baseline is not None:
        difference = {
            "baseline": {
                "kind": dashboard.baseline.kind,
                "name": dashboard.baseline.name,
            },
            "categories": [d.name for d in dashboard.differences],
            "points": [_points(d.comparison.uplift) for d in dashboard.differences],
            "low": [_points(d.comparison.ci_low) for d in dashboard.differences],
            "high": [_points(d.comparison.ci_high) for d in dashboard.differences],
        }
    over_time = None
    if dashboard.over_time is not None:
        over_time = {
            "window_days": dashboard.over_time.window_days,
            "drawn_days": dashboard.over_time.drawn_days,
            "series": [
                {
                    "name": arm.name,
                    "kind": arm.kind,
                    "data": [_points(share) for share in shares],
                }
                for arm, shares in dashboard.over_time.series
            ],
        }
    return {
        "experiment": {
            "id": experiment.experiment_id,
            "name": experiment.name,
            "data_origin": experiment.data_origin,
            "synthetic": dashboard.label is not None,
            "label": dashboard.label,
        },
        "rates": {
            "categories": [arm.name for arm in arms],
            "kinds": [arm.kind for arm in arms],
            "assigned": _points_list(arm.rate_assigned for arm in arms),
            "exposed": _points_list(
                arm.rate_exposed if arm.kind == "TREATMENT" else None for arm in arms
            ),
        },
        "difference": difference,
        "over_time": over_time,
    }


def _points_list(rates) -> list[float | None]:
    return [_points(rate) for rate in rates]


def _picker(connection):
    found, _total = list_report_experiments(
        connection, campaign_id=None, data_origin=None, limit=_PICKER_OPTIONS, offset=0
    )
    return found


@bp.get("/")
@requires(CAMPAIGN_READ)
def index():
    """Render the dashboard for the experiment named in `?experiment=`, or the
    newest one that has assignments. An id that is not a whole number is a 400;
    one that does not exist is a 404; no experiment at all is a 200 that says
    so."""
    connection = get_connection()
    raw = request.args.get("experiment", "")
    selected: int | None = None
    if raw:
        selected = whole_number(raw, INT_MAX)
        if selected is None:
            return (
                render_template(
                    "experiment_dashboard/index.html",
                    dashboard=None,
                    experiments=_picker(connection),
                    error="Choose an experiment from the list.",
                ),
                400,
            )

    options = _picker(connection)
    if selected is None and options:
        selected = next((e.experiment_id for e in options if e.assignments), None)
        if selected is None:
            selected = options[0].experiment_id
    if selected is None:
        return render_template(
            "experiment_dashboard/index.html",
            dashboard=None,
            experiments=[],
            error=None,
        )

    try:
        dashboard = service.build_dashboard(connection, selected)
    except ExperimentNotFound:
        abort(404)
    if dashboard.experiment.experiment_id not in {e.experiment_id for e in options}:
        options.append(dashboard.experiment)
    return render_template(
        "experiment_dashboard/index.html",
        dashboard=dashboard,
        experiments=options,
        error=None,
        chart_data=_embed(chart_payload(dashboard)),
    )
