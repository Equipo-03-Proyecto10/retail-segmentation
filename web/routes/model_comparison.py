"""The model comparison (F9-04).

What a rule-based run and a K-means run assign to the same customers, side by side,
so that choosing a method is an evidenced decision and not a preference. Read-only,
gated on segment.read like the rest of the segmentation surface (ADR-0010); the
permission map (F4-07) assigns the Phase 9 analytics surfaces to it.

The comparison is made on label codes (ADR-0018), by `compare_runs`, which is handed
two runs' rows and the label vocabulary and nothing else. The *kind* of a run is
metadata, and this module uses it for exactly three things: offering the page one
run of each kind, refusing a run in the wrong slot, and naming each run on screen.
It is never what an assignment is read through.
"""

from __future__ import annotations

from flask import Blueprint, Response, abort, render_template, request

from web.db import get_connection
from web.db.model_comparison import list_run_label_rows, list_runs_of_method
from web.db.segments import SegmentationRun, get_label_ordinals, get_run
from web.middleware.authz import SEGMENT_READ, requires
from web.routes.pagination import redirect_last_page
from web.services.catalog import parse_pagination
from web.services.model_comparison import (
    FILTERS,
    compare_runs,
    filter_customers,
    flatten_parameters,
)
from web.services.pagination import page_count

bp = Blueprint("model_comparison", __name__, url_prefix="/model-comparison")

_PER_PAGE = 20
_RUN_OPTIONS = 100
_BIGGEST_RUN_ID = 2**63 - 1

# The two kinds of run the page compares, one of each: (query parameter, kind, name).
_SLOTS = (
    ("rules_run", "RFM_RULES", "rule-based run"),
    ("kmeans_run", "KMEANS", "K-means run"),
)


def _page(status_code: int = 200, **context) -> tuple[str, int]:
    return render_template("model_comparison/index.html", **context), status_code


@bp.get("/")
@requires(SEGMENT_READ)
def index() -> tuple[str, int] | str | Response:
    """Offer one run of each kind, and render the comparison once both are chosen."""
    connection = get_connection()
    options = {
        slot: list_runs_of_method(connection, kind, limit=_RUN_OPTIONS)
        for slot, kind, _name in _SLOTS
    }
    status = request.args.get("status", "all")

    chosen: dict[str, SegmentationRun | None] = {}
    error = None
    for slot, kind, name in _SLOTS:
        raw = request.args.get(slot, "")
        if not raw:
            chosen[slot] = options[slot][0] if options[slot] else None
            continue
        if not (raw.isascii() and raw.isdigit()):
            error, chosen[slot] = "Choose one run from each list.", None
            continue
        run_id = int(raw)
        run = next((r for r in options[slot] if r.run_id == run_id), None)
        if run is None:
            run = get_run(connection, run_id) if run_id <= _BIGGEST_RUN_ID else None
            if run is None:
                abort(404)
            if run.method != kind:
                error, chosen[slot] = f"Run #{run_id} is not a {name}.", None
                continue
            options[slot].append(run)
        chosen[slot] = run

    context = dict(
        rules_options=options["rules_run"],
        kmeans_options=options["kmeans_run"],
        rules=chosen["rules_run"],
        kmeans=chosen["kmeans_run"],
        status=status,
        error=error,
        comparison=None,
    )
    if status not in FILTERS:
        return _page(400, **{**context, "error": "Choose one of the filters listed."})
    if error:
        return _page(400, **context)
    if chosen["rules_run"] is None or chosen["kmeans_run"] is None:
        return _page(**context)

    rules, kmeans = chosen["rules_run"], chosen["kmeans_run"]
    comparison = compare_runs(
        list_run_label_rows(connection, rules.run_id),
        list_run_label_rows(connection, kmeans.run_id),
        get_label_ordinals(connection),
        first_run_id=rules.run_id,
        second_run_id=kmeans.run_id,
    )
    listed = filter_customers(comparison, status)
    page = parse_pagination(request.args.get("page"))
    total_pages = page_count(len(listed), _PER_PAGE)
    if response := redirect_last_page(page, total_pages):
        return response

    return _page(
        **{
            **context,
            "comparison": comparison,
            "customers": listed[(page - 1) * _PER_PAGE : page * _PER_PAGE],
            "total": len(listed),
            "page": page,
            "total_pages": total_pages,
            "rules_parameters": flatten_parameters(rules.parameters),
            "kmeans_parameters": flatten_parameters(kmeans.parameters),
        }
    )
