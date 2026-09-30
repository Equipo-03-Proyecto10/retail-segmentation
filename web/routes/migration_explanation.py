"""Per-customer migration explanation (F7-06, #338).

Why one customer moved (or didn't) between two runs — one sentence per
measure with its raw values and a changed/stable judgement (RN-50), then the
scores from both runs and their deltas. Everything comes from the stored
history row (ADR-0017); nothing here recomputes a score. Read-only, gated on
segment.read like the rest of the segmentation surface (ADR-0010).

Reached from a customer listed in a migration matrix cell (#339) and from a
change on the customer's segment timeline (#337).
"""

from __future__ import annotations

import uuid

from flask import Blueprint, abort, render_template, request

from web.db import get_connection
from web.db.segments import get_customer_assignment_for_run, get_run
from web.middleware.authz import SEGMENT_READ, requires
from web.parsing import BIGINT_MAX, whole_number
from web.services.segment_migration import (
    UnknownRun,
    describe_migration,
    explain_migration,
    order_runs,
)

bp = Blueprint("migration_explanation", __name__, url_prefix="/migration-explanation")


@bp.get("/")
@requires(SEGMENT_READ)
def index() -> str:
    """Explain one customer's movement between two runs, given as query
    parameters run_a, run_b and customer_id, in either order."""
    connection = get_connection()

    run_a_raw = request.args.get("run_a", "")
    run_b_raw = request.args.get("run_b", "")
    customer_id = request.args.get("customer_id", "")

    run_a_id = whole_number(run_a_raw, BIGINT_MAX)
    run_b_id = whole_number(run_b_raw, BIGINT_MAX)
    if run_a_id is None or run_b_id is None or not customer_id:
        abort(404)

    try:
        # `UUID()` also takes `urn:uuid:…`, braces and bare hex; PostgreSQL
        # takes only some of those, so the id is used in its one canonical form.
        customer_id = str(uuid.UUID(customer_id))
    except ValueError:
        abort(404)

    try:
        earlier_id, later_id = order_runs(connection, run_a_id, run_b_id)
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
        narrative=describe_migration(
            explanation,
            run_at_before=earlier_run.run_at,
            run_at_after=later_run.run_at,
        ),
    )
