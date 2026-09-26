"""The migration matrix (F7-05).

A cross-tabulation of every customer's label between two selected runs —
rows are the earlier run's labels, columns are the later run's, cells are
customer counts. Read-only, gated on segment.read like the rest of the
segmentation surface (ADR-0010).
"""

from __future__ import annotations

from flask import Blueprint, render_template, request

from web.db import get_connection
from web.db.segments import get_label_ordinals, list_runs
from web.middleware.authz import SEGMENT_READ, requires
from web.services.segment_migration import (
    UnknownRun,
    build_migration_matrix,
    compute_migration,
)

bp = Blueprint("migration_matrix", __name__, url_prefix="/migration-matrix")

# Every run, for the two selector dropdowns -- a page of 20 (run_history's
# own page size) is plenty to compare against; older runs stay reachable by
# id in the URL even once off this list.
_RUN_OPTIONS = 100


@bp.get("/")
@requires(SEGMENT_READ)
def index() -> str:
    """Offer a pair of runs to compare, and render the matrix once both are
    selected."""
    connection = get_connection()
    runs, _total = list_runs(connection, page=1, per_page=_RUN_OPTIONS)

    run_a_raw = request.args.get("run_a", "")
    run_b_raw = request.args.get("run_b", "")

    matrix = None
    error = None
    if run_a_raw and run_b_raw:
        try:
            run_a_id, run_b_id = int(run_a_raw), int(run_b_raw)
        except ValueError:
            error = "Choose two runs to compare."
        else:
            try:
                migrations = compute_migration(connection, run_a_id, run_b_id)
            except UnknownRun:
                error = "One of the selected runs no longer exists."
            else:
                ordinals = get_label_ordinals(connection)
                matrix = build_migration_matrix(migrations, ordinals)

    return render_template(
        "migration_matrix/index.html",
        runs=runs,
        run_a=run_a_raw,
        run_b=run_b_raw,
        matrix=matrix,
        error=error,
    )
