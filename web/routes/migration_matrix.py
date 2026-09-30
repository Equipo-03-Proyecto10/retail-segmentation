"""The migration matrix (F7-05).

A cross-tabulation of every customer's label between two selected runs —
rows are the earlier run's labels, columns are the later run's, cells are
customer counts. Read-only, gated on segment.read like the rest of the
segmentation surface (ADR-0010).
"""

from __future__ import annotations

from flask import Blueprint, render_template, request

from web.db import get_connection
from web.db.consumption_reports import list_customer_names
from web.db.segments import get_label_ordinals, get_run, list_runs
from web.middleware.authz import SEGMENT_READ, requires
from web.parsing import BIGINT_MAX, whole_number
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


def _query_value(name: str) -> tuple[str, bool]:
    """Return one query value and whether the parameter was repeated."""
    values = request.args.getlist(name)
    if len(values) > 1:
        return "", True
    return (values[0], False) if values else ("", False)


def _state_value(raw: str, states: list[str], *, from_state: bool) -> str | None:
    """Validate a state filter against the matrix vocabulary.

    The aliases keep the two absence states explicit in URLs while allowing
    the product wording ``NEW`` for a customer absent from the earlier run.
    """
    if not raw:
        return None
    if raw in states:
        return raw

    aliases = {
        "unassigned": "Unassigned",
        "new": "Not in earlier run",
        "absent from earlier run": "Not in earlier run",
        "absent from later run": "Not in later run",
    }
    alias = aliases.get(raw.strip().casefold())
    if alias in states and (from_state or alias != "Not in earlier run"):
        return alias
    return None


@bp.get("/")
@requires(SEGMENT_READ)
def index() -> str | tuple[str, int]:
    """Offer a pair of runs to compare, and render the matrix once both are
    selected."""
    connection = get_connection()
    runs, _total = list_runs(connection, page=1, per_page=_RUN_OPTIONS)

    run_a_raw, run_a_repeated = _query_value("run_a")
    run_b_raw, run_b_repeated = _query_value("run_b")
    from_state_raw, from_state_repeated = _query_value("from_state")
    to_state_raw, to_state_repeated = _query_value("to_state")
    before_raw, before_repeated = _query_value("before")
    after_raw, after_repeated = _query_value("after")
    if before_raw and from_state_raw and before_raw != from_state_raw:
        from_state_repeated = True
    if after_raw and to_state_raw and after_raw != to_state_raw:
        to_state_repeated = True
    from_state_raw = before_raw or from_state_raw
    to_state_raw = after_raw or to_state_raw

    matrix = None
    error = None
    filter_error = None
    selected_from = None
    selected_to = None
    selected_migrations = ()
    customer_names = {}
    if run_a_repeated or run_b_repeated:
        error = "Choose each run only once."
    elif run_a_raw and run_b_raw:
        run_a_id = whole_number(run_a_raw, BIGINT_MAX)
        run_b_id = whole_number(run_b_raw, BIGINT_MAX)
        if run_a_id is None or run_b_id is None:
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

                if (
                    from_state_repeated
                    or to_state_repeated
                    or before_repeated
                    or after_repeated
                ):
                    filter_error = "Choose each cell state only once."
                else:
                    selected_from = _state_value(
                        from_state_raw, matrix.row_labels, from_state=True
                    )
                    selected_to = _state_value(
                        to_state_raw, matrix.column_labels, from_state=False
                    )
                    if from_state_raw and selected_from is None:
                        filter_error = "That before state is not in this matrix."
                    elif to_state_raw and selected_to is None:
                        filter_error = "That after state is not in this matrix."
                    elif selected_from or selected_to:
                        selected_migrations = tuple(
                            migration
                            for row, columns in matrix.cell_migrations.items()
                            if selected_from is None or row == selected_from
                            for column, members in columns.items()
                            if selected_to is None or column == selected_to
                            for migration in members
                        )
                        customer_names = list_customer_names(
                            connection,
                            [
                                migration.customer_id
                                for migration in selected_migrations
                            ],
                        )
    elif (
        from_state_raw
        or to_state_raw
        or from_state_repeated
        or to_state_repeated
        or before_repeated
        or after_repeated
    ):
        filter_error = "Choose two runs before filtering matrix cells."

    listed = {run.run_id for run in runs}
    for raw in dict.fromkeys((run_a_raw, run_b_raw)):
        run_id = whole_number(raw, BIGINT_MAX)
        if run_id is not None and run_id not in listed:
            selected = get_run(connection, run_id)
            if selected is not None:
                runs.append(selected)

    page = render_template(
        "migration_matrix/index.html",
        runs=runs,
        run_a=run_a_raw,
        run_b=run_b_raw,
        matrix=matrix,
        error=error,
        filter_error=filter_error,
        selected_from=selected_from,
        selected_to=selected_to,
        selected_migrations=selected_migrations,
        customer_names=customer_names,
    )
    return (page, 400) if filter_error else page
