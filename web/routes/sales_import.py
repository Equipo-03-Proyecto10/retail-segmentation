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

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
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
_FILENAME_MAX = 255


def _render_index(page: int, *, error: str | None = None) -> str | Response:
    """The upload form above the real upload history, also when a submitted
    file was refused -- the history does not vanish because of one bad file."""
    loads, total = list_sales_loads(get_connection(), page=page, per_page=_PER_PAGE)
    total_pages = page_count(total, _PER_PAGE)
    if error is None and (response := redirect_last_page(page, total_pages)):
        return response
    return render_template(
        "sales_import/index.html",
        loads=loads,
        page=page,
        total_pages=total_pages,
        total=total,
        contract_version=CONTRACT_VERSION,
        error=error,
    )


def _refuse(error: str) -> tuple[str | Response, int]:
    return _render_index(1, error=error), 400


@bp.get("/")
@requires(SALES_INGEST_EXECUTE)
def index() -> str | Response:
    """The upload form, and a history of previous attempts."""
    return _render_index(parse_pagination(request.args.get("page")))


@bp.post("/")
@requires(SALES_INGEST_EXECUTE)
def upload() -> Response | tuple[str | Response, int]:
    """Accept one CSV file, load it, and redirect to the stored report."""
    file = request.files.get("file")
    if file is None or not file.filename:
        return _refuse("Choose a CSV file to upload.")

    # sales_load.filename is VARCHAR(255), and the client's name is only
    # displayed: the file itself is saved under a fixed name, so a name too
    # long for the filesystem cannot turn into a 500.
    filename = secure_filename(file.filename)[:_FILENAME_MAX] or "upload.csv"
    max_bytes = current_app.config["APP_CONFIG"].max_upload_bytes

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir) / "upload.csv"
        file.save(tmp_path)
        # MAX_CONTENT_LENGTH leaves FORM_OVERHEAD_BYTES of room for the rest
        # of the form, so a file just over MAX_UPLOAD_BYTES still arrives.
        if tmp_path.stat().st_size > max_bytes:
            return _refuse(
                f"The file is larger than the {max_bytes / (1024 * 1024):.0f} MB "
                "upload limit. Split it and upload each part."
            )
        try:
            _, load_id = load_and_record_sales_csv(
                get_connection(),
                tmp_path,
                filename=filename,
                contract_version=CONTRACT_VERSION,
                loaded_by=session.get("user_id"),
            )
        except UnsupportedContractVersion as error:
            return _refuse(f"The file was not loaded: {error}.")

    # Redirected rather than rendered, unlike segment_run: reloading a
    # rendered report would post the file again, recording a second load
    # whose every row is rejected as a duplicate of the first.
    return redirect(url_for("sales_import.detail", load_id=load_id), code=303)


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
