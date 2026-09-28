"""The migration matrix (F7-05).

A cross-tabulation of every customer's label between two selected runs —
rows are the earlier run's labels, columns are the later run's, cells are
customer counts. Read-only, gated on segment.read like the rest of the
segmentation surface (ADR-0010).
"""

from __future__ import annotations

from flask import Blueprint, render_template, request

from web.db import get_connection
from web.db.segments import get_label_ordinals, get_run, list_runs
from web.middleware.authz import SEGMENT_READ, requires
from web.services.segment_migration import (
    UnknownRun,
    build_migration_matrix,
    compute_migration,
    order_runs,
)

bp = Blueprint("migration_matrix", __name__, url_prefix="/migration-matrix")

# The newest runs, for the two selector dropdowns. An older run selected by
# id in the URL is added to the options, so it stays selected on resubmit.
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
                earlier_id, later_id = order_runs(connection, run_a_id, run_b_id)
            except UnknownRun:
                error = "One of the selected runs no longer exists."
            else:
                # Show the pair in the order the matrix uses, so the "Earlier
                # run" picker always names the run the rows come from.
                run_a_raw, run_b_raw = str(earlier_id), str(later_id)
                migrations = compute_migration(connection, earlier_id, later_id)
                ordinals = get_label_ordinals(connection)
                matrix = build_migration_matrix(migrations, ordinals)

    listed = {run.run_id for run in runs}
    for raw in dict.fromkeys((run_a_raw, run_b_raw)):
        if raw.isdigit() and int(raw) not in listed:
            selected = get_run(connection, int(raw))
            if selected is not None:
                runs.append(selected)

    return render_template(
        "migration_matrix/index.html",
        runs=runs,
        run_a=run_a_raw,
        run_b=run_b_raw,
        matrix=matrix,
        error=error,
    )
