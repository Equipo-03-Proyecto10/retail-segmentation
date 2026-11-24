"""Uploading, reading, replacing and deleting a product image through the web's
own routes, with the file store behind them and a real PostgreSQL (F29-13,
ADR-0091).

The local backend runs for real on a temporary directory, and the same flows run
against a fake GCS client (the real cloud is not available to the tests). The
failure orders are the point: a store that is down leaves no row, and a row that
is refused leaves no object.

The web runs on a `RollbackConnection`: one real connection in one transaction
that is never committed, so nothing a test writes (products, files, the outbox
events their triggers capture) outlives it, and the shared test database keeps
its rows.
"""

from __future__ import annotations

import hashlib
import logging
import re
import struct
import zlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock

import pytest
from flask import Flask
from flask.testing import FlaskClient

from tests.rollback_connection import RollbackConnection
from web.app import create_app
from web.config import Config
from web.db import stored_files
from web.services.files import (
    GcsFileStore,
    LocalFileStore,
    NewFile,
    StoreUnavailable,
    insert_row,
    put_new_file,
)

pytestmark = pytest.mark.postgres

ADMIN_ID = "11111111-1111-1111-1111-000000000001"
NAME = re.compile(r"^[0-9a-f-]{36}\.(jpg|png|webp)$")


def _chunk(kind: bytes, body: bytes) -> bytes:
    return (
        struct.pack(">I", len(body))
        + kind
        + body
        + struct.pack(">I", zlib.crc32(kind + body))
    )


PNG = (
    b"\x89PNG\r\n\x1a\n"
    + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0))
    + _chunk(b"IDAT", zlib.compress(b"\x00\x00"))
    + _chunk(b"IEND", b"")
)
JPEG = b"\xff\xd8\xff\xe0 a jpeg for the test"
WEBP = b"RIFF\x04\x00\x00\x00WEBPa webp for the test"


class RecordingLocalStore(LocalFileStore):
    """The local backend, remembering every path it was asked to write."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.attempted: list[str] = []
        self.written: list[str] = []

    def put(self, object_path: str, data: bytes, content_type: str) -> None:
        self.attempted.append(object_path)
        super().put(object_path, data, content_type)
        self.written.append(object_path)


class DownStore:
    """A store that refuses everything, as an unreachable bucket does."""

    def __init__(self) -> None:
        self.attempted: list[str] = []

    def put(self, object_path: str, data: bytes, content_type: str) -> None:
        self.attempted.append(object_path)
        raise StoreUnavailable("the bucket is unreachable")

    def get(self, object_path: str) -> bytes:
        raise StoreUnavailable("the bucket is unreachable")

    def delete(self, object_path: str) -> None:
        raise StoreUnavailable("the bucket is unreachable")


@dataclass
class Stack:
    app: Flask
    client: FlaskClient
    store: RecordingLocalStore
    owner: RollbackConnection
    root: Path
    extra_paths: list[str] = field(default_factory=list)

    def product(self, product_id: int) -> tuple | None:
        return self.owner.execute(
            "SELECT image_path, image_file_id FROM product WHERE product_id = %s",
            (product_id,),
        ).fetchone()

    def file(self, file_id) -> tuple | None:
        return self.owner.execute(
            "SELECT object_path, content_type, size_bytes, sha256, privacy, status,"
            " owner_user_id::text, deleted_at, metadata"
            " FROM stored_file WHERE file_id = %s",
            (file_id,),
        ).fetchone()

    def rows_for(self, paths: list[str]) -> int:
        return self.owner.execute(
            "SELECT count(*) FROM stored_file WHERE object_path = ANY(%s)", (paths,)
        ).fetchone()[0]

    def files_of_test(self) -> int:
        return self.rows_for(self.paths())

    def paths(self) -> list[str]:
        return [*self.store.written, *self.extra_paths]

    def on_disk(self) -> list[str]:
        return sorted(
            str(p.relative_to(self.root)) for p in self.root.rglob("*") if p.is_file()
        )

    def create(self, product_id: int, image=None, *, sku: str | None = None):
        data = {
            "product_id": str(product_id),
            "sku": sku or f"SKU-1189-{product_id}",
            "name": f"File store product {product_id}",
            "category_id": "1",
            "list_price": "10.00",
        }
        if image is not None:
            data["image"] = image
        return self.client.post(
            "/admin/products/new", data=data, content_type="multipart/form-data"
        )

    def edit(self, product_id: int, image=None):
        data = {
            "sku": f"SKU-1189-{product_id}",
            "name": f"File store product {product_id}",
            "category_id": "1",
            "list_price": "10.00",
            "is_active": "on",
        }
        if image is not None:
            data["image"] = image
        return self.client.post(
            f"/admin/products/{product_id}/edit",
            data=data,
            content_type="multipart/form-data",
        )


@pytest.fixture
def stack(app_url: str, tmp_path: Path) -> Iterator[Stack]:
    config = Config(
        secret_key="test",
        environment="testing",
        port=5000,
        log_level="INFO",
        session_cookie_secure=False,
        database_url=app_url,
        upload_dir=str(tmp_path),
    )
    database = RollbackConnection(app_url)
    app = create_app(config, database_connector=database.connector)
    store = RecordingLocalStore(tmp_path)
    app.extensions["file_store"] = store
    app.config["PROPAGATE_EXCEPTIONS"] = False
    client = app.test_client()
    with client.session_transaction() as session:
        session.update(user_id=ADMIN_ID, role_code="ADMIN", name="Admin")
    try:
        yield Stack(app, client, store, database, tmp_path)
    finally:
        database.discard()


def _image(data: bytes, name: str = "My Holiday Photo.png", mime="image/png"):
    return (BytesIO(data), name, mime)


# ---------- the local backend, end to end ----------


@pytest.mark.parametrize(
    "data,mime,extension",
    [
        (PNG, "image/png", "png"),
        (JPEG, "image/jpeg", "jpg"),
        (WEBP, "image/webp", "webp"),
    ],
)
def test_an_uploaded_image_is_stored_with_its_metadata_and_served_back(
    stack: Stack, data: bytes, mime: str, extension: str
) -> None:
    response = stack.create(911001, _image(data, "../My Holiday Photo.x", mime))

    assert response.status_code == 302
    name, file_id = stack.product(911001)
    assert NAME.match(name) and name.endswith(f".{extension}")
    object_path, content_type, size, sha256, privacy, status, owner, deleted, meta = (
        stack.file(file_id)
    )
    # The object is named after the file's own id: never the uploaded name.
    assert object_path == f"product-image/{file_id}.{extension}"
    assert "Holiday" not in object_path
    assert (content_type, size, privacy, status, owner, deleted) == (
        mime,
        len(data),
        "PUBLIC",
        "STORED",
        ADMIN_ID,
        None,
    )
    assert sha256 == hashlib.sha256(data).hexdigest()
    assert meta == {"kind": "product-image"}
    # The bytes are in the store, in one place, and not loose in UPLOAD_DIR.
    assert stack.on_disk() == [object_path]
    assert (stack.root / object_path).read_bytes() == data

    served = stack.client.get(f"/admin/products/image/{name}")
    assert served.status_code == 200
    assert served.data == data
    assert served.mimetype == mime
    assert served.headers["X-Content-Type-Options"] == "nosniff"


def test_a_stored_image_is_cacheable_by_the_browser_only_and_revalidates(
    stack: Stack,
) -> None:
    stack.create(911002, _image(PNG))
    name, _ = stack.product(911002)

    served = stack.client.get(f"/admin/products/image/{name}")
    tag = served.headers["ETag"]

    assert tag == f'"{hashlib.sha256(PNG).hexdigest()}"'
    # `private`: the route needs a signed-in reader, so no shared cache may keep
    # it; `immutable`: a name is never reused for other bytes.
    assert served.headers["Cache-Control"] == "private, max-age=86400, immutable"
    assert "Pragma" not in served.headers

    again = stack.client.get(
        f"/admin/products/image/{name}", headers={"If-None-Match": tag}
    )
    assert again.status_code == 304
    assert again.data == b""


def test_a_revalidation_does_not_touch_the_store(stack: Stack) -> None:
    stack.create(911003, _image(PNG))
    name, _ = stack.product(911003)
    tag = stack.client.get(f"/admin/products/image/{name}").headers["ETag"]
    stack.app.extensions["file_store"] = DownStore()

    again = stack.client.get(
        f"/admin/products/image/{name}", headers={"If-None-Match": tag}
    )

    assert again.status_code == 304


def test_the_catalog_and_the_form_show_the_stored_image(stack: Stack) -> None:
    stack.create(911004, _image(PNG))
    name, _ = stack.product(911004)

    detail = stack.client.get("/catalog/products/911004")
    form = stack.client.get("/admin/products/911004/edit")

    assert f"/admin/products/image/{name}" in detail.get_data(as_text=True)
    assert f"/admin/products/image/{name}" in form.get_data(as_text=True)


def test_a_product_without_an_image_has_no_file(stack: Stack) -> None:
    assert stack.create(911005).status_code == 302

    assert stack.product(911005) == (None, None)
    assert stack.on_disk() == []


@pytest.mark.parametrize(
    "image",
    [
        _image(b"GIF89a....", "animated.gif", "image/gif"),
        _image(b"%PDF-1.4", "a.pdf", "application/pdf"),
        _image(b"", "empty.png", "image/png"),
        _image(b"\xff\xd8\xff" + b"x" * (5 * 1024 * 1024), "big.jpg", "image/jpeg"),
    ],
    ids=["gif", "pdf", "empty", "one-byte-over-5MB"],
)
def test_a_refused_file_writes_neither_object_nor_rows(stack: Stack, image) -> None:
    response = stack.create(911006, image)

    assert response.status_code == 400
    assert stack.product(911006) is None
    assert stack.on_disk() == []
    assert stack.files_of_test() == 0


def test_a_request_far_over_the_limit_is_refused_before_anything_is_written(
    stack: Stack,
) -> None:
    six_megabytes = b"\xff\xd8\xff" + b"x" * (6 * 1024 * 1024)

    response = stack.create(911007, _image(six_megabytes, "six.jpg", "image/jpeg"))

    assert response.status_code == 413
    assert stack.product(911007) is None
    assert stack.on_disk() == []


def test_replacing_the_image_retires_the_old_file_and_keeps_its_row(
    stack: Stack,
) -> None:
    stack.create(911008, _image(PNG))
    old_name, old_id = stack.product(911008)

    assert stack.edit(911008, _image(JPEG, "new.jpg", "image/jpeg")).status_code == 302

    new_name, new_id = stack.product(911008)
    assert new_id != old_id and new_name != old_name
    old = stack.file(old_id)
    assert old[5] == "DELETED" and old[7] is not None
    assert stack.file(new_id)[5] == "STORED"
    assert stack.on_disk() == [stack.file(new_id)[0]]
    assert stack.client.get(f"/admin/products/image/{old_name}").status_code == 404
    assert stack.client.get(f"/admin/products/image/{new_name}").data == JPEG


def test_editing_a_product_without_a_new_image_keeps_the_image(stack: Stack) -> None:
    stack.create(911009, _image(PNG))
    before = stack.product(911009)

    assert stack.edit(911009).status_code == 302

    assert stack.product(911009) == before
    assert len(stack.on_disk()) == 1


def test_deleting_the_product_retires_its_file(stack: Stack) -> None:
    stack.create(911010, _image(PNG))
    _, file_id = stack.product(911010)

    response = stack.client.post(
        "/admin/products/911010/delete", data={"confirm": "yes"}
    )

    assert response.status_code == 302
    assert stack.product(911010) is None
    row = stack.file(file_id)
    assert row[5] == "DELETED" and row[7] is not None
    assert stack.on_disk() == []


# ---------- the store is down ----------


def test_a_store_that_is_down_refuses_the_upload_and_leaves_no_row(
    stack: Stack, caplog: pytest.LogCaptureFixture
) -> None:
    down = DownStore()
    stack.app.extensions["file_store"] = down

    with caplog.at_level(logging.ERROR):
        response = stack.create(911011, _image(PNG))

    assert response.status_code == 503
    assert "file store is not reachable" in response.get_data(as_text=True)
    assert "Nothing was changed" in response.get_data(as_text=True)
    assert stack.product(911011) is None
    assert len(down.attempted) == 1 and stack.rows_for(down.attempted) == 0
    assert "upload_failed reason=file_store_unavailable" in caplog.text


def test_an_unwritable_local_store_is_a_controlled_error_and_leaves_no_row(
    stack: Stack, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # The directory the store writes under is a file: the real backend fails.
    blocker = tmp_path / "blocked"
    blocker.write_bytes(b"not a directory")
    blocked = RecordingLocalStore(blocker)
    stack.app.extensions["file_store"] = blocked

    with caplog.at_level(logging.ERROR):
        response = stack.create(911012, _image(PNG))

    assert response.status_code == 503
    assert stack.product(911012) is None
    assert len(blocked.attempted) == 1 and stack.rows_for(blocked.attempted) == 0
    assert "file_store_write_failed backend=local" in caplog.text
    assert "upload_failed" in caplog.text


def test_a_store_that_is_down_refuses_an_edit_and_changes_nothing(
    stack: Stack,
) -> None:
    stack.create(911013, _image(PNG))
    before = stack.product(911013)
    stack.app.extensions["file_store"] = DownStore()

    response = stack.edit(911013, _image(JPEG, "new.jpg", "image/jpeg"))

    assert response.status_code == 503
    assert stack.product(911013) == before


def test_reading_while_the_store_is_down_is_a_503_and_the_page_still_renders(
    stack: Stack,
) -> None:
    stack.create(911014, _image(PNG))
    name, _ = stack.product(911014)
    stack.app.extensions["file_store"] = DownStore()

    assert stack.client.get(f"/admin/products/image/{name}").status_code == 503
    assert stack.client.get("/catalog/products/911014").status_code == 200


def test_deleting_while_the_store_is_down_defers_the_object_not_the_user(
    stack: Stack, caplog: pytest.LogCaptureFixture
) -> None:
    stack.create(911015, _image(PNG))
    _, file_id = stack.product(911015)
    stack.app.extensions["file_store"] = DownStore()

    with caplog.at_level(logging.ERROR):
        response = stack.client.post(
            "/admin/products/911015/delete", data={"confirm": "yes"}
        )

    assert response.status_code == 302
    assert stack.product(911015) is None
    assert stack.file(file_id)[5] == "PENDING_DELETE"
    assert len(stack.on_disk()) == 1
    assert "file_store_delete_deferred" in caplog.text


# ---------- the row is refused ----------


def test_a_refused_product_leaves_no_object_and_no_row(stack: Stack) -> None:
    stack.create(911016, _image(PNG), sku="SKU-1189-911016")
    kept = stack.on_disk()

    # The same SKU: the product is refused after the object was written.
    response = stack.create(
        911017, _image(JPEG, "b.jpg", "image/jpeg"), sku="SKU-1189-911016"
    )

    assert response.status_code == 409
    assert stack.product(911017) is None
    assert stack.on_disk() == kept
    # Written, then rolled back with the product: two writes, one row.
    assert len(stack.store.written) == 2 and stack.files_of_test() == 1


def test_a_refused_metadata_row_removes_the_object(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = stored_files.insert_stored_file

    def refused(connection, **fields):
        # The database itself refuses this name: a user's file name, not an id.
        return real(connection, **{**fields, "object_path": "../escape.jpg"})

    monkeypatch.setattr(stored_files, "insert_stored_file", refused)

    response = stack.create(911018, _image(PNG))

    # The product's write is refused as a whole: a controlled 409 on the form.
    assert response.status_code == 409
    assert stack.product(911018) is None
    assert len(stack.store.written) == 1
    assert stack.on_disk() == []
    assert stack.files_of_test() == 0


def test_a_failure_after_the_row_removes_the_object_too(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    from web.routes import admin

    def broken(*args, **kwargs):
        raise RuntimeError("the database went away")

    monkeypatch.setattr(admin, "create_product", broken)

    response = stack.create(911019, _image(PNG))

    assert response.status_code == 500
    assert stack.product(911019) is None
    assert stack.on_disk() == []
    assert stack.files_of_test() == 0


# ---------- reading: only a stored, public file is served ----------


def test_a_file_that_is_pending_delete_is_not_served(stack: Stack) -> None:
    stack.create(911020, _image(PNG))
    name, file_id = stack.product(911020)
    stack.owner.execute(
        "UPDATE stored_file SET status = 'PENDING_DELETE' WHERE file_id = %s",
        (file_id,),
    )

    assert stack.client.get(f"/admin/products/image/{name}").status_code == 404


def test_a_private_file_is_not_served_by_the_public_image_route(stack: Stack) -> None:
    # A row's privacy cannot be changed after it is written, so the file is
    # written PRIVATE and a product is pointed at it.
    stored = put_new_file(
        stack.store,
        NewFile(
            kind="product-image",
            data=PNG,
            content_type="image/png",
            owner_id=ADMIN_ID,
            privacy="PRIVATE",
        ),
    )
    stack.extra_paths.append(stored.object_path)
    insert_row(stack.owner, stored)
    stack.owner.execute(
        "INSERT INTO product (product_id, sku, name, category_id, list_price,"
        " image_path, image_file_id) VALUES (911060, 'SKU-1189-911060', 'Private', 1,"
        " 1, %s, %s)",
        (stored.public_name, stored.file_id),
    )

    answer = stack.client.get(f"/admin/products/image/{stored.public_name}")

    assert answer.status_code == 404


def test_an_object_missing_from_the_store_is_a_404_and_is_logged(
    stack: Stack, caplog: pytest.LogCaptureFixture
) -> None:
    stack.create(911021, _image(PNG))
    name, file_id = stack.product(911021)
    (stack.root / stack.file(file_id)[0]).unlink()

    with caplog.at_level(logging.ERROR):
        response = stack.client.get(f"/admin/products/image/{name}")

    assert response.status_code == 404
    assert "file_store_object_missing" in caplog.text


def test_an_unknown_or_mismatched_name_is_a_404(stack: Stack) -> None:
    stack.create(911022, _image(PNG))
    name, file_id = stack.product(911022)
    wrong_extension = f"{file_id}.webp"
    unknown = "00000000-0000-4000-8000-00000000dead.png"

    assert (
        stack.client.get(f"/admin/products/image/{wrong_extension}").status_code == 404
    )
    assert stack.client.get(f"/admin/products/image/{unknown}").status_code == 404
    assert stack.client.get(f"/admin/products/image/{name}").status_code == 200


def test_a_reader_without_catalog_access_is_refused(stack: Stack) -> None:
    stack.create(911023, _image(PNG))
    name, _ = stack.product(911023)
    with stack.client.session_transaction() as session:
        session.update(user_id=ADMIN_ID, role_code="CUSTOMER", name="Customer")

    assert stack.client.get(f"/admin/products/image/{name}").status_code == 403


# ---------- the GCS backend behind the same routes ----------


def test_the_gcs_backend_serves_the_whole_flow_with_a_fake_client(
    stack: Stack,
) -> None:
    from tests.test_file_store import FakeClient

    client = FakeClient()
    stack.app.extensions["file_store"] = GcsFileStore(
        "the-private-bucket", client=client, retry_policy=lambda: "retry"
    )

    assert stack.create(911024, _image(PNG)).status_code == 302
    name, file_id = stack.product(911024)
    object_path = stack.file(file_id)[0]
    stack.extra_paths.append(object_path)

    # The bytes went to the bucket, under the generated name, with their type,
    # and nothing was written to UPLOAD_DIR.
    assert client.objects == {object_path: PNG}
    upload = next(c for c in client.calls if c[0] == "upload_from_string")
    assert upload[3]["content_type"] == "image/png"
    assert ("bucket", "the-private-bucket") in client.calls
    assert stack.on_disk() == []

    assert stack.client.get(f"/admin/products/image/{name}").data == PNG

    assert stack.edit(911024, _image(JPEG, "n.jpg", "image/jpeg")).status_code == 302
    assert object_path not in client.objects
    assert len(client.objects) == 1
    assert stack.file(file_id)[5] == "DELETED"


def test_a_gcs_bucket_that_is_down_leaves_no_row(stack: Stack) -> None:
    from tests.test_file_store import FakeClient

    client = FakeClient()
    client.failures["upload_from_string"] = ConnectionError("unreachable")
    stack.app.extensions["file_store"] = GcsFileStore(
        "b", client=client, retry_policy=lambda: "retry"
    )

    response = stack.create(911025, _image(PNG))

    assert response.status_code == 503
    assert stack.product(911025) is None
    assert client.objects == {}
    attempted = [c[1] for c in client.calls if c[0] == "upload_from_string"]
    assert len(attempted) == 1 and stack.rows_for(attempted) == 0


# ---------- two writers: what an edit retires is what it displaced (#1205) ----------


def _status(stack: Stack, file_id) -> str:
    return stack.file(file_id)[5]


def test_an_edit_retires_the_picture_it_replaced_not_the_one_its_page_read(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    from web.db.products import get_product
    from web.routes import admin

    stack.create(911030, _image(PNG))  # A
    _, a_id = stack.product(911030)
    page_read = get_product(stack.owner, 911030)  # the page of an edit that began on A
    assert stack.edit(911030, _image(JPEG, "b.jpg", "image/jpeg")).status_code == 302
    b_name, b_id = stack.product(911030)  # another edit won: B
    monkeypatch.setattr(admin, "get_product", Mock(return_value=page_read))

    assert stack.edit(911030, _image(WEBP, "c.webp", "image/webp")).status_code == 302

    _, c_id = stack.product(911030)
    assert [_status(stack, f) for f in (a_id, b_id, c_id)] == [
        "DELETED",
        "DELETED",
        "STORED",
    ], "B was displaced by C and must be retired, whatever the page had read"
    assert stack.on_disk() == [stack.file(c_id)[0]]
    assert stack.client.get(f"/admin/products/image/{b_name}").status_code == 404


def test_an_edit_of_a_product_deleted_meanwhile_is_refused_and_keeps_nothing(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    from web.db.products import get_product
    from web.routes import admin

    stack.create(911031, _image(PNG))
    page_read = get_product(stack.owner, 911031)
    stack.client.post("/admin/products/911031/delete", data={"confirm": "yes"})
    assert stack.product(911031) is None
    monkeypatch.setattr(admin, "get_product", Mock(return_value=page_read))
    written = len(stack.store.written)

    response = stack.edit(911031, _image(JPEG, "new.jpg", "image/jpeg"))

    assert response.status_code == 409
    assert "no longer exists" in response.get_data(as_text=True)
    new_path = stack.store.written[written]
    assert stack.rows_for([new_path]) == 0, "no row for a picture nobody has"
    assert stack.on_disk() == [], "and no object"


def test_a_delete_retires_the_picture_the_product_held_when_it_went(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    from web.db.products import get_product
    from web.routes import admin

    stack.create(911032, _image(PNG))  # A
    _, a_id = stack.product(911032)
    page_read = get_product(stack.owner, 911032)
    stack.edit(911032, _image(JPEG, "b.jpg", "image/jpeg"))  # B
    _, b_id = stack.product(911032)
    monkeypatch.setattr(admin, "get_product", Mock(return_value=page_read))

    response = stack.client.post(
        "/admin/products/911032/delete", data={"confirm": "yes"}
    )

    assert response.status_code == 302
    assert (_status(stack, a_id), _status(stack, b_id)) == ("DELETED", "DELETED")
    assert stack.on_disk() == []


# ---------- a delete is recorded before it is done (#1205) ----------


def test_a_failure_recording_the_delete_leaves_the_row_pending_and_the_user_unharmed(
    stack: Stack, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from web.db import stored_files

    stack.create(911033, _image(PNG))
    _, file_id = stack.product(911033)
    path = stack.file(file_id)[0]

    def refused(*args, **kwargs):
        raise RuntimeError("the database went away")

    monkeypatch.setattr(stored_files, "mark_deleted", refused)
    with caplog.at_level(logging.ERROR):
        response = stack.client.post(
            "/admin/products/911033/delete", data={"confirm": "yes"}
        )

    assert response.status_code == 302, "the user's action does not fail"
    assert stack.product(911033) is None
    assert not (stack.root / path).exists(), "the object is gone"
    assert _status(stack, file_id) == "PENDING_DELETE", "and the row says so"
    assert "file_store_delete_record_deferred" in caplog.text

    # What `--delete-pending` does afterwards: the object is already gone, and the
    # row is finished.
    monkeypatch.undo()
    from web.services.files import discard_file

    discard_file(stack.owner, stack.store, file_id)
    assert _status(stack, file_id) == "DELETED"


# ---------- a cleanup that cannot reach the store is not forgotten (#1205) ----------


class DeleteDownStore(RecordingLocalStore):
    """Writes and reads, but cannot delete: the bucket failed between the two."""

    def delete(self, object_path: str) -> None:
        raise StoreUnavailable("the bucket is unreachable")


def _swap_in_a_store_that_cannot_delete(stack: Stack) -> DeleteDownStore:
    broken = DeleteDownStore(stack.root)
    stack.app.extensions["file_store"] = broken
    return broken


def _pending(stack: Stack, path: str) -> tuple | None:
    return stack.owner.execute(
        "SELECT status, privacy, owner_user_id::text, size_bytes FROM stored_file"
        " WHERE object_path = %s",
        (path,),
    ).fetchone()


def test_a_refused_create_whose_cleanup_fails_leaves_a_pending_delete_row(
    stack: Stack, caplog: pytest.LogCaptureFixture
) -> None:
    stack.create(911034, _image(PNG), sku="SKU-1189-DUP")
    broken = _swap_in_a_store_that_cannot_delete(stack)

    with caplog.at_level(logging.ERROR):
        response = stack.create(
            911035, _image(JPEG, "b.jpg", "image/jpeg"), sku="SKU-1189-DUP"
        )

    assert response.status_code == 409
    assert stack.product(911035) is None
    (orphan,) = broken.written
    assert (stack.root / orphan).exists(), "the object could not be removed"
    assert _pending(stack, orphan) == ("PENDING_DELETE", "PUBLIC", ADMIN_ID, len(JPEG))
    assert "file_store_orphan_delete_deferred" in caplog.text
    # The cleanup command can now find it, and finish.
    from web.services.files import discard_file

    stack.app.extensions["file_store"] = stack.store
    row = stack.owner.execute(
        "SELECT file_id FROM stored_file WHERE object_path = %s", (orphan,)
    ).fetchone()
    discard_file(stack.owner, stack.store, row[0])
    assert not (stack.root / orphan).exists()
    assert _pending(stack, orphan)[0] == "DELETED"


def test_a_refused_edit_whose_cleanup_fails_leaves_a_pending_delete_row(
    stack: Stack,
) -> None:
    stack.create(911036, _image(PNG), sku="SKU-1189-ONE")
    stack.create(911037, _image(PNG), sku="SKU-1189-TWO")
    broken = _swap_in_a_store_that_cannot_delete(stack)
    data = {
        "sku": "SKU-1189-ONE",  # taken by the other product
        "name": "File store product 911037",
        "category_id": "1",
        "list_price": "10.00",
        "is_active": "on",
        "image": _image(JPEG, "b.jpg", "image/jpeg"),
    }

    response = stack.client.post(
        "/admin/products/911037/edit", data=data, content_type="multipart/form-data"
    )

    assert response.status_code == 409
    (orphan,) = broken.written[-1:]
    assert _pending(stack, orphan)[0] == "PENDING_DELETE"


def test_a_cleanup_that_works_leaves_no_row(stack: Stack) -> None:
    stack.create(911038, _image(PNG), sku="SKU-1189-DUP2")

    response = stack.create(
        911039, _image(JPEG, "b.jpg", "image/jpeg"), sku="SKU-1189-DUP2"
    )

    assert response.status_code == 409
    assert stack.rows_for(stack.store.written[1:]) == 0
