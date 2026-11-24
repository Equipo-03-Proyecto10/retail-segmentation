"""Uploading, reading, replacing and deleting a product image through the web's
own routes, with the file store behind them and a real PostgreSQL (F29-13,
ADR-0091).

The local backend runs for real on a temporary directory, and the same flows run
against a fake GCS client (the real cloud is not available to the tests). The
failure orders are the point: a store that is down leaves no row, and a row that
is refused leaves no object.

Every test owns one product id and the objects its store was ever asked to
write; the fixture deletes exactly those afterwards, so the shared test database
keeps its rows.
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

import psycopg
import pytest
from flask import Flask
from flask.testing import FlaskClient

from web.app import create_app
from web.config import Config
from web.db import stored_files
from web.services.files import (
    GcsFileStore,
    LocalFileStore,
    StoreUnavailable,
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
    owner: psycopg.Connection
    root: Path
    product_ids: list[int] = field(default_factory=list)
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
        self.product_ids.append(product_id)
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
def stack(app_url: str, owner_url: str, tmp_path: Path) -> Iterator[Stack]:
    config = Config(
        secret_key="test",
        environment="testing",
        port=5000,
        log_level="INFO",
        session_cookie_secure=False,
        database_url=app_url,
        upload_dir=str(tmp_path),
    )
    app = create_app(config)
    store = RecordingLocalStore(tmp_path)
    app.extensions["file_store"] = store
    app.config["PROPAGATE_EXCEPTIONS"] = False
    client = app.test_client()
    with client.session_transaction() as session:
        session.update(user_id=ADMIN_ID, role_code="ADMIN", name="Admin")
    with psycopg.connect(owner_url, autocommit=True) as owner:
        state = Stack(app, client, store, owner, tmp_path)
        try:
            yield state
        finally:
            owner.execute(
                "DELETE FROM product WHERE product_id = ANY(%s)", (state.product_ids,)
            )
            owner.execute(
                "DELETE FROM stored_file WHERE object_path = ANY(%s)", (state.paths(),)
            )


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

    assert response.status_code == 500
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


@pytest.mark.parametrize(
    "change",
    [
        "UPDATE stored_file SET privacy = 'PRIVATE' WHERE file_id = %s",
        "UPDATE stored_file SET status = 'PENDING_DELETE' WHERE file_id = %s",
    ],
    ids=["private", "pending-delete"],
)
def test_a_file_that_is_not_public_and_stored_is_not_served(
    stack: Stack, change: str
) -> None:
    stack.create(911020, _image(PNG))
    name, file_id = stack.product(911020)
    stack.owner.execute(change, (file_id,))

    assert stack.client.get(f"/admin/products/image/{name}").status_code == 404


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
        "the-private-bucket", client=client
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
    stack.app.extensions["file_store"] = GcsFileStore("b", client=client)

    response = stack.create(911025, _image(PNG))

    assert response.status_code == 503
    assert stack.product(911025) is None
    assert client.objects == {}
    attempted = [c[1] for c in client.calls if c[0] == "upload_from_string"]
    assert len(attempted) == 1 and stack.rows_for(attempted) == 0
