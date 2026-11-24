"""Move the product images under UPLOAD_DIR into the file store (F29-13, #1189).

ADR-0091. Before the file store, an uploaded product image was a file under
UPLOAD_DIR named by `product.image_path`. This script uploads each such file to
the configured store, writes its `stored_file` row, and points the product at it
(`image_file_id`, and `image_path` set to the new public name, which is what
products-service publishes as `photo_url`), one product per transaction.

* Idempotent and resumable. A product that already has `image_file_id` is not
  selected again; run it as often as needed, and after an interruption.
* `--dry-run` reads the files and the database, writes nothing anywhere, and
  prints what a real run would do.
* It never deletes an original. The operator removes them once the report is
  clean and the images show in the catalog.
* A file it cannot move (missing, outside the directory, not an accepted image,
  over the limit, the store down) is listed with its reason, the rest continue,
  and the exit status is 1: the gap is recorded, not hidden.
* The migrated rows are attributed to the administrator, because the old upload
  did not record who made it.
* `--delete-pending` is the other job: it finishes the deletes the store could
  not confirm (`stored_file.status = 'PENDING_DELETE'`) and marks the rows
  `DELETED`.

Every statement is parameterized. It reads the web's environment (DATABASE_URL,
FILE_STORE_BACKEND, GCS_BUCKET, UPLOAD_DIR), so run it with the web's virtualenv
and `/etc/mosaiq/mosaiq.env`, from the checkout; see deploy/README.md.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
from psycopg import Connection
from werkzeug.datastructures import FileStorage

# Run from the checkout, `python deploy/migrate-uploads-1189.py`: the script's own
# folder is what Python puts on the path, so put the checkout's root there too.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from web.config import Config, ConfigurationError, load_dotenv_file  # noqa: E402
from web.services import uploads  # noqa: E402
from web.services.files import (  # noqa: E402
    PUBLIC,
    FileStore,
    NewFile,
    StoreUnavailable,
    build_file_store,
    discard_file,
    insert_row,
    put_new_file,
    remove_object,
)

_CONTENT_TYPE_BY_SUFFIX = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}

log = logging.getLogger("migrate-uploads-1189")


@dataclass
class Report:
    """What a run did, product by product."""

    moved: list[tuple[int, str, str]] = field(default_factory=list)
    would_move: list[tuple[int, str]] = field(default_factory=list)
    failed: list[tuple[int, str, str]] = field(default_factory=list)
    deleted: list[UUID] = field(default_factory=list)
    still_pending: list[UUID] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.failed and not self.still_pending


def _administrator(connection: Connection) -> UUID:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT u.user_id
            FROM app_user AS u JOIN role AS r ON r.role_id = u.role_id
            WHERE r.code = %s
            """,
            ("ADMIN",),
        )
        rows = cursor.fetchall()
    if len(rows) != 1:
        raise RuntimeError(
            f"expected exactly one administrator to own the files, found {len(rows)}"
        )
    return rows[0][0]


def _pending_images(connection: Connection) -> list[tuple[int, str]]:
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT product_id, image_path FROM product
            WHERE image_path IS NOT NULL AND image_file_id IS NULL
            ORDER BY product_id
            """)
        return [(row[0], row[1]) for row in cursor.fetchall()]


def _read_original(upload_dir: Path, image_path: str) -> bytes:
    root = upload_dir.resolve()
    target = (root / image_path).resolve()
    if target.parent != root:
        raise ValueError("the name leaves the upload directory")
    try:
        return target.read_bytes()
    except FileNotFoundError as error:
        raise ValueError("the file is missing from the upload directory") from error


def _validated(data: bytes, image_path: str, config: Config) -> str:
    """The content type, from the extension, confirmed by the same checks an
    upload passes (signature, size limit, PNG structure)."""
    content_type = _CONTENT_TYPE_BY_SUFFIX.get(Path(image_path).suffix.lower())
    if content_type is None:
        raise ValueError("the extension is not an accepted image type")
    try:
        uploads._validate(
            FileStorage(BytesIO(data), filename=image_path, content_type=content_type),
            config,
        )
    except uploads.UploadRejected as error:
        raise ValueError(str(error)) from error
    return content_type


def _move_one(
    connection: Connection,
    store: FileStore,
    config: Config,
    owner_id: UUID,
    product_id: int,
    image_path: str,
) -> str:
    """Move one product's picture; returns its new public name. Raises
    ValueError with the reason when it cannot be moved. The product's row is
    committed only when the object and the row are both in place."""
    data = _read_original(Path(config.upload_dir), image_path)
    content_type = _validated(data, image_path, config)
    try:
        stored = put_new_file(
            store,
            NewFile(
                kind=uploads.PRODUCT_IMAGE_KIND,
                data=data,
                content_type=content_type,
                owner_id=owner_id,
                privacy=PUBLIC,
                metadata={
                    "kind": uploads.PRODUCT_IMAGE_KIND,
                    "product_id": product_id,
                    "migrated_from": image_path,
                },
            ),
        )
    except StoreUnavailable as error:
        raise ValueError(f"the store refused it: {error}") from error

    name = stored.public_name
    try:
        insert_row(connection, stored)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE product SET image_path = %s, image_file_id = %s
                WHERE product_id = %s AND image_path = %s AND image_file_id IS NULL
                """,
                (name, stored.file_id, product_id, image_path),
            )
            if cursor.rowcount != 1:
                raise ValueError("the product changed while it was being moved")
        connection.commit()
    except BaseException as error:
        connection.rollback()
        remove_object(store, stored.object_path)
        if isinstance(error, ValueError):
            raise
        if isinstance(error, psycopg.Error):
            raise ValueError(f"the row or the product was refused: {error}") from error
        raise
    return name


def migrate_images(
    connection: Connection,
    store: FileStore,
    config: Config,
    *,
    dry_run: bool,
    limit: int | None = None,
) -> Report:
    """Move every product image that is still a bare file under UPLOAD_DIR."""
    report = Report()
    owner_id = _administrator(connection)
    connection.commit()
    pending = _pending_images(connection)
    connection.commit()
    if limit is not None:
        pending = pending[:limit]
    for product_id, image_path in pending:
        try:
            if dry_run:
                data = _read_original(Path(config.upload_dir), image_path)
                _validated(data, image_path, config)
                report.would_move.append((product_id, image_path))
            else:
                name = _move_one(
                    connection, store, config, owner_id, product_id, image_path
                )
                report.moved.append((product_id, image_path, name))
        except ValueError as error:
            report.failed.append((product_id, image_path, str(error)))
    return report


def delete_pending(connection: Connection, store: FileStore) -> Report:
    """Finish the deletes the store could not confirm."""
    report = Report()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT file_id FROM stored_file WHERE status = %s ORDER BY created_at",
            ("PENDING_DELETE",),
        )
        file_ids = [row[0] for row in cursor.fetchall()]
    connection.commit()
    for file_id in file_ids:
        discard_file(connection, store, file_id)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT status FROM stored_file WHERE file_id = %s", (file_id,)
            )
            status = cursor.fetchone()[0]
        connection.commit()
        (report.deleted if status == "DELETED" else report.still_pending).append(
            file_id
        )
    return report


def _print(report: Report, *, dry_run: bool, deleting: bool) -> None:
    if deleting:
        print(f"deleted {len(report.deleted)} object(s) marked PENDING_DELETE")
        for file_id in report.still_pending:
            print(f"STILL PENDING {file_id}: the store refused the delete again")
        return
    verb = "would move" if dry_run else "moved"
    done = report.would_move if dry_run else report.moved
    print(f"{verb} {len(done)} image(s); {len(report.failed)} could not be moved")
    for item in done:
        print(f"  product {item[0]}: {item[1]}" + ("" if dry_run else f" -> {item[2]}"))
    for product_id, image_path, reason in report.failed:
        print(f"FAILED product {product_id} ({image_path}): {reason}")
    if report.failed:
        print(
            "The files above stay under UPLOAD_DIR and their products keep "
            "image_path; fix the cause and run this again."
        )
    elif not dry_run and report.moved:
        print(
            "The originals under UPLOAD_DIR were not deleted; check the catalog, "
            "then remove them."
        )


def main(
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
    *,
    store: FileStore | None = None,
    connect: Any = psycopg.connect,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--dry-run", action="store_true", help="report only; write nothing"
    )
    parser.add_argument(
        "--delete-pending",
        action="store_true",
        help="finish deletes the store could not confirm, instead of migrating",
    )
    parser.add_argument("--limit", type=int, help="move at most this many images")
    args = parser.parse_args(argv)
    if args.delete_pending and (args.dry_run or args.limit is not None):
        parser.error("--delete-pending takes neither --dry-run nor --limit")

    if environ is None:
        load_dotenv_file()
    try:
        config = Config.from_env(environ)
    except ConfigurationError as error:
        print(f"configuration refused: {error}", file=sys.stderr)
        return 2
    store = store or build_file_store(config)

    with connect(config.database_url) as connection:
        if args.delete_pending:
            report = delete_pending(connection, store)
        else:
            report = migrate_images(
                connection, store, config, dry_run=args.dry_run, limit=args.limit
            )
    _print(report, dry_run=args.dry_run, deleting=args.delete_pending)
    return 0 if report.clean else 1


if __name__ == "__main__":
    sys.exit(main())
