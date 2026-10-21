"""The run-history view (F7-03).

Every completed segmentation run, and what it produced. Read-only, gated on
segment.read the same as the rest of the segmentation surface (ADR-0010): a
run's result is exactly the substrate segment.read already covers, just
traced back to the run that produced it rather than read as "current".

A run of SEEDED origin is marked Synthetic, on its row in the list and on
its own page (ADR-0052).
"""

from __future__ import annotations

from flask import Blueprint, Response, abort, render_template, request

from web.db import get_connection
from web.db.segments import get_run, list_run_assignments, list_runs
from web.middleware.authz import SEGMENT_READ, requires
from web.routes.pagination import (
    active_filters,
    choice_filter,
    redirect_last_page,
    text_filter,
)
from web.services.catalog import parse_pagination
from web.services.pagination import page_count
from web.services.provenance import runs_synthetic
from web.services.segmentation import METHODS

bp = Blueprint("run_history", __name__, url_prefix="/run-history")

_PER_PAGE = 20


@bp.get("/")
@requires(SEGMENT_READ)
def index() -> str | Response:
    """List completed runs, most recent first, optionally filtered by method
    and by the executing user's name."""
    page = parse_pagination(request.args.get("page"))
    filters = active_filters(method=choice_filter("method", METHODS), q=text_filter())
    rows, total = list_runs(
        get_connection(),
        page=page,
        per_page=_PER_PAGE,
        method=filters.get("method"),
        search=filters.get("q"),
    )
    if response := redirect_last_page(page, page_count(total, _PER_PAGE)):
        return response
    return render_template(
        "run_history/index.html",
        runs=rows,
        page=page,
        total_pages=page_count(total, _PER_PAGE),
        total=total,
        filters=filters,
        methods=METHODS,
        runs_synthetic=runs_synthetic,
        synthetic=runs_synthetic(*rows),
    )


@bp.get("/<int:run_id>")
@requires(SEGMENT_READ)
def detail(run_id: int) -> str | Response:
    """One run's parameters and every customer it scored — unassigned
    customers are shown, not omitted (RN-21)."""
    connection = get_connection()
    run = get_run(connection, run_id)
    if run is None:
        abort(404)

    page = parse_pagination(request.args.get("page"))
    assignments, total = list_run_assignments(
        connection, run_id, page=page, per_page=_PER_PAGE
    )
    if response := redirect_last_page(page, page_count(total, _PER_PAGE)):
        return response
    return render_template(
        "run_history/detail.html",
        run=run,
        synthetic=runs_synthetic(run),
        assignments=assignments,
        page=page,
        total_pages=page_count(total, _PER_PAGE),
        total=total,
    )
