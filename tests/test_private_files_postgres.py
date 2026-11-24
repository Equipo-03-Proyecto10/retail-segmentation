"""Private files through signed URLs, on the web's own routes and a real
PostgreSQL (F29-14, ADR-0091).

A sales import keeps its CSV as a PRIVATE stored file. An administrator asks for
it and gets a URL that works for a short time; an analyst is refused with no URL
and the refusal is recorded; an unknown id is a 404; a URL that is tampered with
or expired is refused. The local backend stands in for the bucket, and runs for
real. The web runs on a `RollbackConnection`, so nothing a test writes outlives it.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import psycopg
import pytest
from flask import Flask
from flask.testing import FlaskClient

from tests.rollback_connection import RollbackConnection
from tests.test_file_store_postgres import DownStore, RecordingLocalStore
from web.app import create_app
from web.config import Config
from web.db import stored_files
from web.services.files import PRIVATE, NewFile, insert_row, put_new_file

pytestmark = pytest.mark.postgres

ADMIN_ID = "11111111-1111-1111-1111-000000000001"
ANALYST_ID = "11111111-1111-1111-1111-000000000002"
HEADER = (
    "transaction_id,customer_id,store_id,channel_id,occurred_at,"
    "product_id,quantity,unit_price"
)
ROW = "T-1,00000000-0000-0000-0000-000000000000,1,1,2026-01-01T00:00:00Z,1,1,1.00"
CSV = f"{HEADER}\n{ROW}\n".encode()


@dataclass
class Bench:
    app: Flask
    db: RollbackConnection
    store: RecordingLocalStore
    root: Path

    def client(self, user_id: str, role: str) -> FlaskClient:
        client = self.app.test_client()
        with client.session_transaction() as session:
            session.update(user_id=user_id, role_code=role, name=role.title())
        return client

    def private_file(
        self,
        *,
        kind: str = "sales-import",
        data: bytes = CSV,
        privacy: str = PRIVATE,
    ) -> UUID:
        """A file written in the state wanted. A row's privacy and identity never
        change after it is written (ADR-0091), so a test asks for the state."""
        stored = put_new_file(
            self.store,
            NewFile(
                kind=kind,
                data=data,
                content_type="text/csv",
                owner_id=ADMIN_ID,
                privacy=privacy,
            ),
        )
        insert_row(self.db, stored)
        return stored.file_id

    def accesses(self, file_id: UUID) -> list[tuple]:
        return self.db.execute(
            "SELECT actor_user_id::text, role_code, outcome, expires_at,"
            " expires_at - occurred_at FROM file_access WHERE file_id = %s"
            " ORDER BY access_id",
            (file_id,),
        ).fetchall()


@pytest.fixture
def bench(app_url: str, tmp_path: Path) -> Iterator[Bench]:
    config = Config(
        secret_key="a-test-secret",
        environment="testing",
        port=5000,
        log_level="INFO",
        session_cookie_secure=False,
        database_url=app_url,
        upload_dir=str(tmp_path),
        signed_url_ttl_seconds=600,
    )
    database = RollbackConnection(app_url)
    app = create_app(config, database_connector=database.connector)
    app.config["PROPAGATE_EXCEPTIONS"] = False
    store = RecordingLocalStore(tmp_path)
    app.extensions["file_store"] = store
    try:
        yield Bench(app, database, store, tmp_path)
    finally:
        database.discard()


def _path_and_query(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.path}?{parts.query}"


# ---------- an allowed role gets a URL that downloads ----------


def test_an_administrator_gets_a_short_lived_url_that_downloads_the_file(
    bench: Bench,
) -> None:
    file_id = bench.private_file()
    admin = bench.client(ADMIN_ID, "ADMIN")

    asked = admin.get(f"/files/{file_id}/download")

    assert asked.status_code == 303
    url = asked.headers["Location"]
    assert url.startswith(f"/files/{file_id}/content?")
    assert set(parse_qs(urlsplit(url).query)) == {"expires", "signature"}
    # A browser holding only the URL (no cookie) downloads the bytes.
    anonymous = bench.app.test_client()
    got = anonymous.get(url)
    assert got.status_code == 200
    assert got.data == CSV
    assert got.mimetype == "text/csv"
    assert got.headers["Content-Disposition"] == (
        f'attachment; filename="sales-import-{file_id}.csv"'
    )
    assert got.headers["X-Content-Type-Options"] == "nosniff"
    assert got.headers["Cache-Control"] == "no-store"


def test_the_answer_can_be_json_with_the_url_and_its_expiry(bench: Bench) -> None:
    file_id = bench.private_file()

    asked = bench.client(ADMIN_ID, "ADMIN").get(
        f"/files/{file_id}/download", headers={"Accept": "application/json"}
    )

    body = asked.get_json()
    assert asked.status_code == 200
    assert body["file_id"] == str(file_id)
    assert body["url"].startswith(f"/files/{file_id}/content?")
    assert body["expires_at"].endswith("+00:00")
    assert bench.app.test_client().get(body["url"]).data == CSV


def test_an_issued_url_is_recorded_with_an_expiry_within_fifteen_minutes(
    bench: Bench,
) -> None:
    file_id = bench.private_file()
    bench.client(ADMIN_ID, "ADMIN").get(f"/files/{file_id}/download")

    ((actor, role, outcome, expires_at, lifetime),) = bench.accesses(file_id)

    assert (actor, role, outcome) == (ADMIN_ID, "ADMIN", "ISSUED")
    assert expires_at is not None
    assert 0 < lifetime.total_seconds() <= 900
    assert lifetime.total_seconds() == 600


def test_the_log_names_the_file_the_actor_and_the_expiry_never_the_signature(
    bench: Bench, caplog: pytest.LogCaptureFixture
) -> None:
    file_id = bench.private_file()
    admin = bench.client(ADMIN_ID, "ADMIN")

    with caplog.at_level(logging.DEBUG):
        asked = admin.get(f"/files/{file_id}/download")
        url = asked.headers["Location"]
        bench.app.test_client().get(url)

    signature = parse_qs(urlsplit(url).query)["signature"][0]
    assert f"file_id={file_id}" in caplog.text
    assert f"actor={ADMIN_ID}" in caplog.text
    assert "expires_at=20" in caplog.text
    assert "outcome=ISSUED" in caplog.text
    assert signature not in caplog.text
    assert "signature=" not in caplog.text and "expires=" not in caplog.text


# ---------- a role that may not ----------


@pytest.mark.parametrize(
    "role", ["ANALYST", "STORE_MANAGER", "MARKETING", "INVENTORY_PLANNER", "AUDITOR"]
)
def test_a_role_that_may_not_read_it_gets_403_no_url_and_a_recorded_refusal(
    bench: Bench, role: str, caplog: pytest.LogCaptureFixture
) -> None:
    file_id = bench.private_file()

    with caplog.at_level(logging.INFO):
        refused = bench.client(ANALYST_ID, role).get(f"/files/{file_id}/download")

    assert refused.status_code == 403
    assert "Location" not in refused.headers
    assert b"signature" not in refused.data and b"expires" not in refused.data
    assert bench.accesses(file_id) == [(ANALYST_ID, role, "REFUSED", None, None)]
    assert f"outcome=REFUSED file_id={file_id}" in caplog.text


def test_a_refusal_is_not_an_issue_and_leaves_the_file_unreadable(
    bench: Bench,
) -> None:
    file_id = bench.private_file()
    analyst = bench.client(ANALYST_ID, "ANALYST")

    analyst.get(f"/files/{file_id}/download")
    asked_json = analyst.get(
        f"/files/{file_id}/download", headers={"Accept": "application/json"}
    )

    assert asked_json.status_code == 403
    assert [row[2] for row in bench.accesses(file_id)] == ["REFUSED", "REFUSED"]
    # And there is no other way in: the content route needs a signature.
    assert analyst.get(f"/files/{file_id}/content").status_code == 403


def test_a_kind_nobody_may_read_is_refused_even_to_the_administrator(
    bench: Bench,
) -> None:
    file_id = bench.private_file(kind="mystery")

    refused = bench.client(ADMIN_ID, "ADMIN").get(f"/files/{file_id}/download")

    assert refused.status_code == 403
    assert bench.accesses(file_id)[0][2] == "REFUSED"


def test_an_anonymous_visitor_is_sent_to_sign_in(bench: Bench) -> None:
    file_id = bench.private_file()

    asked = bench.app.test_client().get(f"/files/{file_id}/download")

    assert asked.status_code == 302 and "/login" in asked.headers["Location"]
    assert bench.accesses(file_id) == []


# ---------- an id that is not a private, stored file ----------


def test_an_unknown_id_is_a_404_in_the_common_error_page_and_issues_nothing(
    bench: Bench,
) -> None:
    missing = uuid4()

    answer = bench.client(ADMIN_ID, "ADMIN").get(f"/files/{missing}/download")

    assert answer.status_code == 404
    assert "Not Found" in answer.get_data(as_text=True)
    assert "Location" not in answer.headers
    assert (
        bench.db.execute(
            "SELECT count(*) FROM file_access WHERE file_id = %s",
            (missing,),
        ).fetchone()[0]
        == 0
    )


def test_a_removed_or_public_file_is_a_404_not_a_url(bench: Bench) -> None:
    removed = bench.private_file()
    stored_files.mark_deleted(bench.db, removed)  # the one move the web may make
    public = bench.private_file(privacy="PUBLIC")
    admin = bench.client(ADMIN_ID, "ADMIN")

    assert admin.get(f"/files/{removed}/download").status_code == 404
    assert admin.get(f"/files/{public}/download").status_code == 404


def test_a_malformed_id_is_a_404(bench: Bench) -> None:
    assert (
        bench.client(ADMIN_ID, "ADMIN").get("/files/not-a-uuid/download").status_code
        == 404
    )


# ---------- the URL itself ----------


def test_a_tampered_url_is_refused_and_logged_without_its_query(
    bench: Bench, caplog: pytest.LogCaptureFixture
) -> None:
    file_id = bench.private_file()
    other = bench.private_file(data=b"another,file\n")
    url = (
        bench.client(ADMIN_ID, "ADMIN")
        .get(f"/files/{file_id}/download")
        .headers["Location"]
    )
    query = parse_qs(urlsplit(url).query)
    anonymous = bench.app.test_client()

    with caplog.at_level(logging.INFO):
        later = anonymous.get(
            f"/files/{file_id}/content?expires={int(query['expires'][0]) + 3600}"
            f"&signature={query['signature'][0]}"
        )
        another_file = anonymous.get(
            f"/files/{other}/content?expires={query['expires'][0]}"
            f"&signature={query['signature'][0]}"
        )
        unsigned = anonymous.get(f"/files/{file_id}/content")
        flipped = anonymous.get(
            f"/files/{file_id}/content?expires={query['expires'][0]}"
            f"&signature={'0' * 64}"
        )

    assert [r.status_code for r in (later, another_file, unsigned, flipped)] == [
        403
    ] * 4
    assert caplog.text.count("file_url_refused") == 4
    assert query["signature"][0] not in caplog.text
    assert "reason=signature" in caplog.text and "reason=malformed" in caplog.text
    # The real one still works afterwards.
    assert anonymous.get(url).status_code == 200


def test_an_expired_url_is_refused(
    bench: Bench, caplog: pytest.LogCaptureFixture
) -> None:
    bench.app.config["APP_CONFIG"] = _short(bench.app.config["APP_CONFIG"])
    file_id = bench.private_file()
    url = (
        bench.client(ADMIN_ID, "ADMIN")
        .get(f"/files/{file_id}/download")
        .headers["Location"]
    )
    anonymous = bench.app.test_client()
    assert anonymous.get(url).status_code == 200

    time.sleep(3.2)
    with caplog.at_level(logging.INFO):
        late = anonymous.get(url)

    assert late.status_code == 403
    assert "file_url_refused" in caplog.text and "reason=expired" in caplog.text
    assert b"T-1" not in late.data  # none of the file's content


def _short(config: Config) -> Config:
    from dataclasses import replace

    return replace(config, signed_url_ttl_seconds=2)


def test_the_content_route_serves_nothing_that_is_not_private(bench: Bench) -> None:
    from web.services.file_urls import signature

    file_id = bench.private_file(privacy="PUBLIC")
    expires = int(time.time()) + 300
    sig = signature("a-test-secret", file_id, expires)

    answer = bench.app.test_client().get(
        f"/files/{file_id}/content?expires={expires}&signature={sig}"
    )

    assert answer.status_code == 404


def test_a_store_that_is_down_issues_no_url_on_the_gcs_backend_path(
    bench: Bench,
) -> None:
    # The signer raising StoreUnavailable is a 503 and no row says ISSUED.
    class SigningDown(DownStore):
        def signed_url(self, *args, **kwargs):
            from web.services.files import StoreUnavailable

            raise StoreUnavailable("signBlob refused")

    file_id = bench.private_file()
    bench.app.extensions["file_store"] = SigningDown()

    answer = bench.client(ADMIN_ID, "ADMIN").get(f"/files/{file_id}/download")

    assert answer.status_code == 503
    assert bench.accesses(file_id) == []


def test_on_the_gcs_backend_the_local_content_route_does_not_exist(
    bench: Bench,
) -> None:
    from dataclasses import replace

    file_id = bench.private_file()
    bench.app.config["APP_CONFIG"] = replace(
        bench.app.config["APP_CONFIG"], file_store_backend="gcs", gcs_bucket="b"
    )

    answer = bench.app.test_client().get(
        f"/files/{file_id}/content?expires=1&signature=x"
    )

    assert answer.status_code == 404


# ---------- where the file comes from: a sales import ----------


def test_a_sales_import_keeps_its_csv_as_a_private_file_the_report_links_to(
    bench: Bench,
) -> None:
    admin = bench.client(ADMIN_ID, "ADMIN")

    posted = admin.post(
        "/admin/sales-import/",
        data={"file": (BytesIO(CSV), "../../my sales.csv")},
        content_type="multipart/form-data",
    )

    assert posted.status_code == 303
    load_id = int(posted.headers["Location"].rstrip("/").split("/")[-1])
    ((file_id, path, privacy, status, size, owner, meta),) = bench.db.execute(
        "SELECT f.file_id, f.object_path, f.privacy, f.status, f.size_bytes,"
        " f.owner_user_id::text, f.metadata"
        " FROM sales_load l JOIN stored_file f ON f.file_id = l.source_file_id"
        " WHERE l.load_id = %s",
        (load_id,),
    ).fetchall()
    assert path == f"sales-import/{file_id}.csv"
    assert (privacy, status, size, owner) == ("PRIVATE", "STORED", len(CSV), ADMIN_ID)
    assert "my sales" not in path
    assert (bench.root / path).read_bytes() == CSV
    page = admin.get(posted.headers["Location"]).get_data(as_text=True)
    assert f"/files/{file_id}/download" in page
    assert "works for 10 minutes" in page
    # The administrator who made the load can read it back.
    url = admin.get(f"/files/{file_id}/download").headers["Location"]
    assert bench.app.test_client().get(url).data == CSV


def test_a_store_that_is_down_does_not_stop_the_import(
    bench: Bench, caplog: pytest.LogCaptureFixture
) -> None:
    bench.app.extensions["file_store"] = DownStore()
    admin = bench.client(ADMIN_ID, "ADMIN")

    with caplog.at_level(logging.ERROR):
        posted = admin.post(
            "/admin/sales-import/",
            data={"file": (BytesIO(CSV), "s.csv")},
            content_type="multipart/form-data",
        )

    assert posted.status_code == 303
    page = admin.get(posted.headers["Location"]).get_data(as_text=True)
    assert "could not be kept for download" in page
    assert "Not kept" in page
    assert "source_file_not_kept reason=file_store_unavailable" in caplog.text
    load_id = int(posted.headers["Location"].rstrip("/").split("/")[-1])
    assert bench.db.execute(
        "SELECT source_file_id FROM sales_load WHERE load_id = %s", (load_id,)
    ).fetchone() == (None,)


def test_a_refused_csv_leaves_no_file_behind(bench: Bench) -> None:
    admin = bench.client(ADMIN_ID, "ADMIN")

    posted = admin.post(
        "/admin/sales-import/",
        data={"file": (BytesIO(b"not,the,header\n1,2,3\n"), "bad.csv")},
        content_type="multipart/form-data",
    )

    assert posted.status_code == 400
    assert bench.store.written
    assert not [p for p in bench.root.rglob("*") if p.is_file()]


def test_a_failed_registration_removes_the_object_and_keeps_the_load(
    bench: Bench, monkeypatch: pytest.MonkeyPatch
) -> None:
    from web.db import sales_loads

    def refused(*args, **kwargs):
        raise psycopg.errors.CheckViolation("refused")

    monkeypatch.setattr(sales_loads, "set_source_file", refused)
    admin = bench.client(ADMIN_ID, "ADMIN")

    posted = admin.post(
        "/admin/sales-import/",
        data={"file": (BytesIO(CSV), "s.csv")},
        content_type="multipart/form-data",
    )

    assert posted.status_code == 303
    assert not [p for p in bench.root.rglob("*") if p.is_file()]
    load_id = int(posted.headers["Location"].rstrip("/").split("/")[-1])
    assert bench.db.execute(
        "SELECT source_file_id FROM sales_load WHERE load_id = %s", (load_id,)
    ).fetchone() == (None,)
    assert (
        bench.db.execute(
            "SELECT count(*) FROM stored_file WHERE object_path = ANY(%s)",
            (bench.store.written,),
        ).fetchone()[0]
        == 0
    )


def test_the_seeded_history_cannot_be_downloaded(bench: Bench) -> None:
    # The seed's sales-load files are DELETED history: asking for one is a 404.
    seeded = bench.db.execute(
        "SELECT source_file_id FROM sales_load WHERE source_file_id IS NOT NULL"
        " ORDER BY load_id LIMIT 1"
    ).fetchone()[0]

    assert (
        bench.client(ADMIN_ID, "ADMIN").get(f"/files/{seeded}/download").status_code
        == 404
    )
    assert re.match(r"^[0-9a-f-]{36}$", str(seeded))
