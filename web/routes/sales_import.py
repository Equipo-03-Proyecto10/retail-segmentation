"""Sales CSV import for the administrator (F8-01, #334).

An HTML upload page, not an HTTP ingestion endpoint -- ADR-0020 rules out the
latter but the CSV view it names is exactly this: a page that validates the
contract version and header, then hands rows to the same row-level ingestion
service tests/test_sales_csv.py has exercised since #210.
"""

from __future__ import annotations

import csv
import io
import tempfile
from pathlib import Path

from flask import Blueprint, Response, abort, render_template, request, session
from werkzeug.utils import secure_filename

from web.db import get_connection
from web.db.sales_loads import (
    get_sales_load,
    list_sales_load_rejections,
    list_sales_loads,
)
from web.middleware.authz import SALES_INGEST_EXECUTE, requires
from web.routes.pagination import redirect_last_page
from web.services.catalog import parse_pagination
from web.services.pagination import page_count
from web.services.sales_csv import (
    CONTRACT_VERSION,
    UnsupportedContractVersion,
    load_and_record_sales_csv,
)

bp = Blueprint("sales_import", __name__, url_prefix="/admin/sales-import")

_PER_PAGE = 20


@bp.get("/")
@requires(SALES_INGEST_EXECUTE)
def index() -> str | Response:
    """The upload form, and a history of previous attempts."""
    connection = get_connection()
    page = parse_pagination(request.args.get("page"))
    loads, total = list_sales_loads(connection, page=page, per_page=_PER_PAGE)
    if response := redirect_last_page(page, page_count(total, _PER_PAGE)):
        return response
    return render_template(
        "sales_import/index.html",
        loads=loads,
        page=page,
        total_pages=page_count(total, _PER_PAGE),
        total=total,
        contract_version=CONTRACT_VERSION,
    )


@bp.post("/")
@requires(SALES_INGEST_EXECUTE)
def upload() -> str | Response:
    """Accept one CSV file, load it, and show the resulting report."""
    file = request.files.get("file")
    if file is None or not file.filename:
        return (
            render_template(
                "sales_import/index.html",
                loads=[],
                page=1,
                total_pages=1,
                total=0,
                contract_version=CONTRACT_VERSION,
                error="Choose a CSV file to upload.",
            ),
            400,
        )

    filename = secure_filename(file.filename) or "upload.csv"
    connection = get_connection()

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir) / filename
        file.save(tmp_path)
        try:
            report, load_id = load_and_record_sales_csv(
                connection,
                tmp_path,
                filename=filename,
                contract_version=CONTRACT_VERSION,
                loaded_by=session.get("user_id"),
            )
        except UnsupportedContractVersion as error:
            return (
                render_template(
                    "sales_import/index.html",
                    loads=[],
                    page=1,
                    total_pages=1,
                    total=0,
                    contract_version=CONTRACT_VERSION,
                    error=str(error),
                ),
                400,
            )

    return render_template(
        "sales_import/report.html",
        load=get_sales_load(connection, load_id),
        rejections=list_sales_load_rejections(connection, load_id),
    )


@bp.get("/<int:load_id>")
@requires(SALES_INGEST_EXECUTE)
def detail(load_id: int) -> str:
    """Retrieve a past load's report (AC 3): it survives the page that made
    it."""
    connection = get_connection()
    load = get_sales_load(connection, load_id)
    if load is None:
        abort(404)
    rejections = list_sales_load_rejections(connection, load_id)
    return render_template("sales_import/report.html", load=load, rejections=rejections)


@bp.get("/<int:load_id>/rejections.csv")
@requires(SALES_INGEST_EXECUTE)
def download_rejections(load_id: int) -> Response:
    """The same rejection report, as a downloadable CSV (AC 3)."""
    connection = get_connection()
    load = get_sales_load(connection, load_id)
    if load is None:
        abort(404)
    rejections = list_sales_load_rejections(connection, load_id)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["line_number", "reason"])
    for rejection in rejections:
        writer.writerow([rejection.line_number, rejection.reason])

    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": (
                f'attachment; filename="sales-load-{load_id}-rejections.csv"'
            )
        },
    )
