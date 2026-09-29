"""The sales CSV import page (F8-01, #334).

Parsing, contract checks and reconciliation are tested in
tests/test_sales_csv.py, row validation in tests/test_ingestion_service.py.
What is covered here is the page: that an upload ends on the stored report
rather than on a page a reload would post again, that a refused file is
refused in words above the real history, that a file over MAX_UPLOAD_BYTES is
refused even inside the form overhead MAX_CONTENT_LENGTH allows, that a stored
report and its CSV download survive the request that made them, and that the
client's file name cannot turn into a 500. The database is a mock and the
data-access functions are replaced, so each test states what the page was
given.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from unittest.mock import MagicMock, Mock
from uuid import UUID

import pytest
from flask import Flask
from flask.testing import FlaskClient

from web.app import create_app
from web.config import Config
from web.db.sales_loads import SalesLoad, SalesLoadRejectionRow
from web.services.sales_csv import LoadReport, UnsupportedContractVersion

_URL = "/admin/sales-import/"
_ADMIN_ID = "11111111-1111-1111-1111-000000000001"
_MAX_UPLOAD_BYTES = 1024

_LOAD = SalesLoad(
    load_id=7,
    filename="september.csv",
    contract_version=1,
    received_count=3,
    accepted_count=1,
    rejected_count=2,
    loaded_by=UUID(_ADMIN_ID),
    loaded_by_name="Ada Admin",
    loaded_at=datetime(2026, 9, 29, 15, 30, tzinfo=UTC),
)
_REJECTIONS = [
    SalesLoadRejectionRow(3, "unknown product"),
    SalesLoadRejectionRow(4, "quantity must be positive"),
]


@pytest.fixture
def app() -> Flask:
    application = create_app(
        Config(
            secret_key="test",
            environment="testing",
            port=5000,
            log_level="INFO",
            session_cookie_secure=False,
            database_url="unused-by-test",
            max_upload_bytes=_MAX_UPLOAD_BYTES,
        ),
        database_connector=Mock(return_value=MagicMock()),
    )
    application.config["PROPAGATE_EXCEPTIONS"] = False
    return application


@pytest.fixture
def pages(monkeypatch: pytest.MonkeyPatch) -> dict[str, Mock]:
    fakes = {
        "list_sales_loads": Mock(return_value=([_LOAD], 1)),
        "get_sales_load": Mock(return_value=_LOAD),
        "list_sales_load_rejections": Mock(return_value=_REJECTIONS),
        "load_and_record_sales_csv": Mock(
            return_value=(LoadReport(3, 1, 2, ()), _LOAD.load_id)
        ),
    }
    for name, fake in fakes.items():
        monkeypatch.setattr(f"web.routes.sales_import.{name}", fake)
    return fakes


def _admin(app: Flask) -> FlaskClient:
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = _ADMIN_ID
        flask_session["role_code"] = "ADMIN"
        flask_session["name"] = "Ada Admin"
    return client


def _upload(client: FlaskClient, content: bytes, filename: str = "september.csv"):
    return client.post(
        _URL,
        data={"file": (io.BytesIO(content), filename)},
        content_type="multipart/form-data",
    )


def _body(response) -> str:
    return response.get_data(as_text=True)


# ---------- uploading ----------


def test_an_upload_ends_on_its_stored_report_not_on_a_page_a_reload_reposts(
    app, pages
) -> None:
    response = _upload(_admin(app), b"transaction_id,...\n")

    assert response.status_code == 303
    assert response.headers["Location"] == "/admin/sales-import/7"


def test_the_upload_is_loaded_under_the_contract_and_credited_to_the_admin(
    app, pages
) -> None:
    _upload(_admin(app), b"transaction_id,...\n")

    kwargs = pages["load_and_record_sales_csv"].call_args.kwargs
    assert kwargs["filename"] == "september.csv"
    assert kwargs["contract_version"] == 1
    assert kwargs["loaded_by"] == _ADMIN_ID


def test_submitting_without_a_file_is_refused_above_the_real_history(
    app, pages
) -> None:
    response = _admin(app).post(_URL, data={}, content_type="multipart/form-data")

    assert response.status_code == 400
    assert "Choose a CSV file to upload." in _body(response)
    assert "september.csv" in _body(response)
    assert "No uploads yet" not in _body(response)
    pages["load_and_record_sales_csv"].assert_not_called()


def test_a_file_off_the_contract_is_refused_in_words(app, pages) -> None:
    pages["load_and_record_sales_csv"].side_effect = UnsupportedContractVersion(
        "the header does not match contract version 1"
    )

    response = _upload(_admin(app), b"wrong,header\n")

    assert response.status_code == 400
    assert (
        "The file was not loaded: the header does not match contract version 1."
        in _body(response)
    )
    assert "september.csv" in _body(response)


def test_a_file_over_the_upload_limit_is_refused_before_any_row_is_read(
    app, pages
) -> None:
    """MAX_CONTENT_LENGTH is MAX_UPLOAD_BYTES plus FORM_OVERHEAD_BYTES, so a
    file a few bytes over the limit still reaches the view; it is refused
    here instead of loaded."""
    response = _upload(_admin(app), b"x" * (_MAX_UPLOAD_BYTES + 1))

    assert response.status_code == 400
    assert "larger than the" in _body(response)
    pages["load_and_record_sales_csv"].assert_not_called()


def test_a_file_at_the_upload_limit_is_loaded(app, pages) -> None:
    response = _upload(_admin(app), b"x" * _MAX_UPLOAD_BYTES)

    assert response.status_code == 303
    pages["load_and_record_sales_csv"].assert_called_once()


def test_a_file_name_too_long_for_the_disk_or_the_column_is_cut_not_a_500(
    app, pages
) -> None:
    response = _upload(_admin(app), b"transaction_id,...\n", "a" * 400 + ".csv")

    assert response.status_code == 303
    assert len(pages["load_and_record_sales_csv"].call_args.kwargs["filename"]) == 255


def test_a_file_name_is_sanitized_before_it_is_stored(app, pages) -> None:
    _upload(_admin(app), b"transaction_id,...\n", "../../etc/sales march.csv")

    stored = pages["load_and_record_sales_csv"].call_args.kwargs["filename"]
    assert stored == "etc_sales_march.csv"


# ---------- the stored report ----------


def test_the_history_lists_every_previous_load(app, pages) -> None:
    response = _admin(app).get(_URL)

    assert response.status_code == 200
    assert "september.csv" in _body(response)
    assert 'href="/admin/sales-import/7"' in _body(response)


def test_a_stored_report_reconciles_and_lists_every_rejected_line(app, pages) -> None:
    response = _admin(app).get("/admin/sales-import/7")

    body = _body(response)
    assert response.status_code == 200
    assert "unknown product" in body and "quantity must be positive" in body
    assert 'href="/admin/sales-import/7/rejections.csv"' in body
    pages["list_sales_load_rejections"].assert_called_once_with(
        pages["get_sales_load"].call_args.args[0], 7
    )


def test_an_unknown_load_is_a_404(app, pages) -> None:
    pages["get_sales_load"].return_value = None

    client = _admin(app)

    assert client.get("/admin/sales-import/999").status_code == 404
    assert client.get("/admin/sales-import/999/rejections.csv").status_code == 404


def test_the_rejections_download_as_csv_with_their_file_line_numbers(
    app, pages
) -> None:
    response = _admin(app).get("/admin/sales-import/7/rejections.csv")

    assert response.status_code == 200
    assert response.mimetype == "text/csv"
    assert (
        response.headers["Content-Disposition"]
        == 'attachment; filename="sales-load-7-rejections.csv"'
    )
    assert list(csv.reader(io.StringIO(_body(response)))) == [
        ["line_number", "reason"],
        ["3", "unknown product"],
        ["4", "quantity must be positive"],
    ]


# ---------- who may use it ----------


@pytest.mark.parametrize("role_code", ["ANALYST", "MARKETING", "AUDITOR"])
def test_a_profile_without_sales_ingest_is_refused_and_nothing_is_loaded(
    app, pages, role_code
) -> None:
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = _ADMIN_ID
        flask_session["role_code"] = role_code

    response = _upload(client, b"transaction_id,...\n")

    assert response.status_code == 403
    pages["load_and_record_sales_csv"].assert_not_called()
