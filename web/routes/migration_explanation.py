"""Per-customer migration explanation (F7-06).

Why one customer moved (or didn't) between two runs — the raw R/F/M values
and scores from both runs, their deltas, and which component moved most.
Everything comes from the stored history row (ADR-0017); nothing here
recomputes a score. Read-only, gated on segment.read like the rest of the
segmentation surface (ADR-0010).
"""

from __future__ import annotations

from flask import Blueprint, abort, render_template, request

from web.db import get_connection
from web.db.segments import get_customer_assignment_for_run, get_run
from web.middleware.authz import SEGMENT_READ, requires
from web.services.segment_migration import UnknownRun, explain_migration, order_runs

bp = Blueprint("migration_explanation", __name__, url_prefix="/migration-explanation")


@bp.get("/")
@requires(SEGMENT_READ)
def index() -> str:
    """Explain one customer's movement between two runs, given as query
    parameters run_a, run_b and customer_id (typically reached from a link
    on the migration matrix or a customer's detail page)."""
    connection = get_connection()

    run_a_raw = request.args.get("run_a", "")
    run_b_raw = request.args.get("run_b", "")
    customer_id = request.args.get("customer_id", "")

    if not (run_a_raw.isdigit() and run_b_raw.isdigit() and customer_id):
        abort(404)

    try:
        earlier_id, later_id = order_runs(connection, int(run_a_raw), int(run_b_raw))
    except UnknownRun:
        abort(404)

    earlier_run = get_run(connection, earlier_id)
    later_run = get_run(connection, later_id)

    assignment_before = get_customer_assignment_for_run(
        connection, earlier_id, customer_id
    )
    assignment_after = get_customer_assignment_for_run(
        connection, later_id, customer_id
    )
    if assignment_before is None and assignment_after is None:
        abort(404)

    explanation = explain_migration(assignment_before, assignment_after)
    customer_name = (
        assignment_before.customer_name
        if assignment_before
        else assignment_after.customer_name
    )

    return render_template(
        "migration_explanation/index.html",
        earlier_run=earlier_run,
        later_run=later_run,
        customer_id=customer_id,
        customer_name=customer_name,
        explanation=explanation,
    )
