"""deploy/migrate-uploads-1189.py moves the first delivery's images into the file
store (F29-13, ADR-0091).

It runs for real against PostgreSQL (on a `RollbackConnection`, so the shared
test database keeps its rows), a temporary `UPLOAD_DIR` and the local backend
standing in for the bucket (the real cloud is not available to the tests): the
dry run, the real run, running it again, resuming after a limit, the files it
cannot move, and the deletes the store could not confirm.
"""

from __future__ import annotations

import hashlib
import importlib.util
import struct
import sys
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from flask.testing import FlaskClient

from tests.rollback_connection import RollbackConnection
from web.app import create_app
from web.config import Config
from web.services.files import LocalFileStore, StoreUnavailable

pytestmark = pytest.mark.postgres

ROOT = Path(__file__).resolve().parents[1]
ADMIN_ID = "11111111-1111-1111-1111-000000000001"


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "migrate_uploads_1189", ROOT / "deploy" / "migrate-uploads-1189.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # a dataclass in the script looks itself up
    spec.loader.exec_module(module)
    return module


migrate = _load_script()


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
JPEG = b"\xff\xd8\xff\xe0 a jpeg from the first delivery"


class RecordingStore(LocalFileStore):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.written: list[str] = []

    def put(self, object_path: str, data: bytes, content_type: str) -> None:
        super().put(object_path, data, content_type)
        self.written.append(object_path)


class DownStore:
    def put(self, object_path: str, data: bytes, content_type: str) -> None:
        raise StoreUnavailable("the bucket is unreachable")

    def get(self, object_path: str) -> bytes:
        raise StoreUnavailable("the bucket is unreachable")

    def delete(self, object_path: str) -> None:
        raise StoreUnavailable("the bucket is unreachable")


@dataclass
class Bench:
    owner: RollbackConnection
    app_url: str
    uploads: Path
    store_root: Path
    store: RecordingStore
    config: Config

    def legacy_product(self, product_id: int, name: str, data: bytes | None) -> None:
        """A product the first delivery left behind: a name and (maybe) a file."""
        self.owner.execute(
            "INSERT INTO product (product_id, sku, name, category_id, list_price,"
            " image_path) VALUES (%s, %s, %s, 1, 1, %s)",
            (product_id, f"MIG-{product_id}", f"Legacy {product_id}", name),
        )
        if data is not None:
            (self.uploads / name).write_bytes(data)

    def product(self, product_id: int) -> tuple:
        return self.owner.execute(
            "SELECT image_path, image_file_id FROM product WHERE product_id = %s",
            (product_id,),
        ).fetchone()

    def rows(self) -> int:
        return self.owner.execute(
            "SELECT count(*) FROM stored_file WHERE object_path = ANY(%s)",
            (self.store.written,),
        ).fetchone()[0]

    def objects(self) -> list[str]:
        return sorted(
            str(p.relative_to(self.store_root))
            for p in self.store_root.rglob("*")
            if p.is_file()
        )

    def environ(self) -> dict[str, str]:
        return {
            "DATABASE_URL": self.app_url,
            "UPLOAD_DIR": str(self.uploads),
            "FLASK_ENV": "development",
        }

    def run(self, *argv: str, store=None) -> int:
        return migrate.main(
            list(argv),
            self.environ(),
            store=store or self.store,
            connect=self.owner.connector,
        )


@pytest.fixture
def bench(app_url: str, tmp_path: Path) -> Iterator[Bench]:
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    store_root = tmp_path / "bucket"
    config = Config(
        secret_key="test",
        environment="testing",
        port=5000,
        log_level="INFO",
        session_cookie_secure=False,
        database_url=app_url,
        upload_dir=str(uploads),
    )
    database = RollbackConnection(app_url)
    try:
        yield Bench(
            database, app_url, uploads, store_root, RecordingStore(store_root), config
        )
    finally:
        database.discard()


def test_a_dry_run_reports_and_writes_nothing_anywhere(bench: Bench, capsys) -> None:
    bench.legacy_product(912001, "0123456789abcdef0123456789abcd01.png", PNG)
    bench.legacy_product(912002, "0123456789abcdef0123456789abcd02.jpg", JPEG)

    status = bench.run("--dry-run")

    out = capsys.readouterr().out
    assert status == 0
    assert "would move 2 image(s); 0 could not be moved" in out
    assert "product 912001: 0123456789abcdef0123456789abcd01.png" in out
    assert bench.product(912001) == ("0123456789abcdef0123456789abcd01.png", None)
    assert bench.product(912002) == ("0123456789abcdef0123456789abcd02.jpg", None)
    assert bench.store.written == [] and bench.objects() == []


def test_a_real_run_moves_each_file_and_points_its_product_at_it(
    bench: Bench, capsys
) -> None:
    bench.legacy_product(912003, "0123456789abcdef0123456789abcd03.png", PNG)
    bench.legacy_product(912004, "0123456789abcdef0123456789abcd04.jpg", JPEG)

    status = bench.run()

    out = capsys.readouterr().out
    assert status == 0 and "moved 2 image(s); 0 could not be moved" in out
    for product_id, data, extension, mime in [
        (912003, PNG, "png", "image/png"),
        (912004, JPEG, "jpg", "image/jpeg"),
    ]:
        name, file_id = bench.product(product_id)
        assert name == f"{file_id}.{extension}"
        row = bench.owner.execute(
            "SELECT object_path, content_type, size_bytes, sha256, privacy, status,"
            " owner_user_id::text, metadata FROM stored_file WHERE file_id = %s",
            (file_id,),
        ).fetchone()
        assert row[:7] == (
            f"product-image/{file_id}.{extension}",
            mime,
            len(data),
            hashlib.sha256(data).hexdigest(),
            "PUBLIC",
            "STORED",
            ADMIN_ID,
        )
        assert row[7]["product_id"] == product_id
        assert row[7]["migrated_from"].endswith(f".{extension}")
        assert (bench.store_root / row[0]).read_bytes() == data
    # Nothing is deleted from UPLOAD_DIR: the operator does that, after looking.
    assert len(list(bench.uploads.iterdir())) == 2
    assert "were not deleted" in out


def test_a_migrated_image_is_served_by_the_web_through_the_store(
    bench: Bench,
) -> None:
    bench.legacy_product(912005, "0123456789abcdef0123456789abcd05.png", PNG)
    bench.run()
    name, _ = bench.product(912005)
    app = create_app(bench.config, database_connector=bench.owner.connector)
    app.extensions["file_store"] = bench.store
    client: FlaskClient = app.test_client()
    with client.session_transaction() as session:
        session.update(user_id=ADMIN_ID, role_code="ADMIN", name="Admin")

    served = client.get(f"/admin/products/image/{name}")

    assert served.status_code == 200 and served.data == PNG
    # The old name no longer exists as a file the route could find by accident.
    (bench.uploads / "0123456789abcdef0123456789abcd05.png").unlink()
    assert client.get(f"/admin/products/image/{name}").data == PNG


def test_running_it_again_moves_nothing_and_changes_nothing(
    bench: Bench, capsys
) -> None:
    bench.legacy_product(912006, "0123456789abcdef0123456789abcd06.png", PNG)
    bench.run()
    first = bench.product(912006)
    objects = bench.objects()
    capsys.readouterr()

    status = bench.run()

    assert status == 0
    assert "moved 0 image(s); 0 could not be moved" in capsys.readouterr().out
    assert bench.product(912006) == first
    assert bench.objects() == objects and bench.rows() == 1


def test_an_interrupted_run_resumes_where_it_stopped(bench: Bench) -> None:
    bench.legacy_product(912007, "0123456789abcdef0123456789abcd07.png", PNG)
    bench.legacy_product(912008, "0123456789abcdef0123456789abcd08.png", PNG)
    bench.legacy_product(912009, "0123456789abcdef0123456789abcd09.png", PNG)

    assert bench.run("--limit", "1") == 0
    assert [bench.product(p)[1] is not None for p in (912007, 912008, 912009)] == [
        True,
        False,
        False,
    ]

    assert bench.run() == 0

    assert all(bench.product(p)[1] is not None for p in (912007, 912008, 912009))
    assert bench.rows() == 3


def test_a_file_it_cannot_move_is_listed_and_the_rest_continue(
    bench: Bench, capsys
) -> None:
    bench.legacy_product(912010, "0123456789abcdef0123456789abcd10.png", PNG)
    bench.legacy_product(912011, "0123456789abcdef0123456789abcd11.png", None)
    bench.legacy_product(912012, "0123456789abcdef0123456789abcd12.png", b"<html>")
    bench.legacy_product(912013, "0123456789abcdef0123456789abcd13.gif", PNG)
    bench.legacy_product(912014, "../../../etc/hostname", None)
    bench.legacy_product(
        912015,
        "0123456789abcdef0123456789abcd15.jpg",
        b"\xff\xd8\xff" + b"x" * 5_300_000,
    )

    status = bench.run()

    out = capsys.readouterr().out
    assert status == 1
    assert "moved 1 image(s); 5 could not be moved" in out
    assert "FAILED product 912011" in out and "missing" in out
    assert "FAILED product 912012" in out and "does not match" in out
    assert "FAILED product 912013" in out and "not an accepted image type" in out
    assert "FAILED product 912014" in out and "leaves the upload directory" in out
    assert "FAILED product 912015" in out and "too large" in out
    assert "keep image_path" in out
    # The good one moved; the others are untouched, to be fixed and run again.
    assert bench.product(912010)[1] is not None
    for product_id in (912011, 912012, 912013, 912014, 912015):
        assert bench.product(product_id)[1] is None
    assert bench.rows() == 1


def test_a_store_that_is_down_moves_nothing_and_leaves_no_row(
    bench: Bench, capsys
) -> None:
    bench.legacy_product(912016, "0123456789abcdef0123456789abcd16.png", PNG)

    status = bench.run(store=DownStore())

    out = capsys.readouterr().out
    assert status == 1
    assert "FAILED product 912016" in out and "the store refused it" in out
    assert bench.product(912016) == ("0123456789abcdef0123456789abcd16.png", None)
    assert (
        bench.owner.execute(
            "SELECT count(*) FROM stored_file WHERE metadata->>'migrated_from' = %s",
            ("0123456789abcdef0123456789abcd16.png",),
        ).fetchone()[0]
        == 0
    )


def test_a_product_that_changed_during_the_move_is_left_alone_and_its_object_removed(
    bench: Bench, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench.legacy_product(912017, "0123456789abcdef0123456789abcd17.png", PNG)
    real_move = migrate._read_original

    def changed_underneath(upload_dir: Path, image_path: str) -> bytes:
        data = real_move(upload_dir, image_path)
        bench.owner.execute(
            "UPDATE product SET image_path = 'another-name.png' WHERE product_id = %s",
            (912017,),
        )
        bench.owner.commit()  # another session's committed change
        return data

    monkeypatch.setattr(migrate, "_read_original", changed_underneath)

    status = bench.run()

    assert status == 1
    assert bench.product(912017) == ("another-name.png", None)
    assert bench.objects() == [] and bench.rows() == 0


def test_the_script_refuses_a_configuration_the_web_would_refuse(capsys) -> None:
    status = migrate.main(
        [], {"DATABASE_URL": "x", "FLASK_ENV": "production", "FLASK_SECRET_KEY": "k"}
    )

    assert status == 2
    assert "configuration refused" in capsys.readouterr().err


def test_exactly_one_administrator_owns_the_migrated_files(bench: Bench) -> None:
    assert migrate._administrator(bench.owner) == UUID(ADMIN_ID)


# ---------- the deletes the store could not confirm ----------


def _pending(bench: Bench) -> UUID:
    file_id = uuid4()
    path = f"product-image/{file_id}.png"
    bench.store.put(path, PNG, "image/png")
    bench.owner.execute(
        "INSERT INTO stored_file (file_id, object_path, content_type, size_bytes,"
        " sha256, privacy, status, owner_user_id)"
        " VALUES (%s, %s, 'image/png', %s, %s, 'PUBLIC', 'PENDING_DELETE', %s)",
        (file_id, path, len(PNG), hashlib.sha256(PNG).hexdigest(), ADMIN_ID),
    )
    return file_id


def test_delete_pending_finishes_the_deletes_the_store_could_not_confirm(
    bench: Bench, capsys
) -> None:
    file_id = _pending(bench)

    status = bench.run("--delete-pending")

    row = bench.owner.execute(
        "SELECT status, deleted_at IS NOT NULL FROM stored_file WHERE file_id = %s",
        (file_id,),
    ).fetchone()
    assert status == 0 and row == ("DELETED", True)
    assert bench.objects() == []
    assert "deleted 1 object(s)" in capsys.readouterr().out


def test_delete_pending_leaves_the_row_pending_while_the_store_is_down(
    bench: Bench, capsys
) -> None:
    file_id = _pending(bench)

    status = bench.run("--delete-pending", store=DownStore())

    row = bench.owner.execute(
        "SELECT status FROM stored_file WHERE file_id = %s", (file_id,)
    ).fetchone()
    assert status == 1 and row == ("PENDING_DELETE",)
    assert f"STILL PENDING {file_id}" in capsys.readouterr().out
    assert len(bench.objects()) == 1


def test_delete_pending_is_not_a_dry_run(bench: Bench) -> None:
    with pytest.raises(SystemExit):
        bench.run("--delete-pending", "--dry-run")


# ---------- one unreadable file does not end the report (#1205) ----------


def test_a_candidate_that_cannot_be_read_is_listed_and_the_rest_continue(
    bench: Bench, capsys
) -> None:
    import os

    bench.legacy_product(912020, "0123456789abcdef0123456789abcd20.png", PNG)
    # A directory where a file should be, and a file nobody may read.
    bench.legacy_product(912021, "0123456789abcdef0123456789abcd21.png", None)
    (bench.uploads / "0123456789abcdef0123456789abcd21.png").mkdir()
    bench.legacy_product(912022, "0123456789abcdef0123456789abcd22.png", PNG)
    unreadable = bench.uploads / "0123456789abcdef0123456789abcd22.png"
    unreadable.chmod(0)
    bench.legacy_product(912023, "0123456789abcdef0123456789abcd23.png", PNG)
    try:
        status = bench.run()
        dry = bench.run("--dry-run")
    finally:
        unreadable.chmod(0o600)

    out = capsys.readouterr().out
    assert status == 1 and dry == 1
    assert "FAILED product 912021" in out and "cannot be read" in out
    if os.geteuid() != 0:  # root reads a mode-0 file
        assert "FAILED product 912022" in out and "Permission denied" in out
    # The readable ones moved in spite of the others.
    assert bench.product(912020)[1] is not None
    assert bench.product(912023)[1] is not None
    assert bench.product(912021)[1] is None


# ---------- a failed move whose cleanup also fails is not forgotten (#1205) ----------


class DeleteDownStore(RecordingStore):
    def delete(self, object_path: str) -> None:
        raise StoreUnavailable("the bucket is unreachable")


def test_a_move_refused_after_the_upload_whose_cleanup_fails_leaves_a_pending_row(
    bench: Bench, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench.legacy_product(912024, "0123456789abcdef0123456789abcd24.png", PNG)
    real_read = migrate._read_original

    def changed_underneath(upload_dir: Path, image_path: str) -> bytes:
        data = real_read(upload_dir, image_path)
        bench.owner.execute(
            "UPDATE product SET image_path = 'another-name.png' WHERE product_id = %s",
            (912024,),
        )
        bench.owner.commit()
        return data

    monkeypatch.setattr(migrate, "_read_original", changed_underneath)
    broken = DeleteDownStore(bench.store_root)

    status = bench.run(store=broken)

    (orphan,) = broken.written
    assert status == 1
    assert (bench.store_root / orphan).exists(), "the object is still in the bucket"
    assert bench.owner.execute(
        "SELECT status, privacy FROM stored_file WHERE object_path = %s", (orphan,)
    ).fetchone() == ("PENDING_DELETE", "PUBLIC")
    # And `--delete-pending` finds and finishes it.
    assert bench.run("--delete-pending") == 0
    assert not (bench.store_root / orphan).exists()
