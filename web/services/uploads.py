"""Image uploads for product records (F3-07, #67; the file store, F29-13, #1189).

An uploaded file is validated by content type, size and signature, and its bytes
are written to the file store (ADR-0091) under a generated name; the database
keeps a `stored_file` row with the metadata, never the bytes (RF-08). The
uploaded file's own name is ignored.

Files that predate the file store sit under `Config.upload_dir` and are named in
`product.image_path` alone; `delete_legacy_image` still removes one of those.
"""

from __future__ import annotations

import logging
import os
import struct
import uuid
import zlib
from dataclasses import dataclass
from pathlib import Path

from psycopg import Connection
from werkzeug.datastructures import FileStorage

from web.config import Config
from web.services.files import (
    PUBLIC,
    FileStore,
    NewFile,
    StoredObject,
    StoreUnavailable,
    discard_file,
    get_file_store,
    put_new_file,
    remove_object,
)

PRODUCT_IMAGE_KIND = "product-image"
# The types a product image may have; the file store also holds text/csv.
IMAGE_CONTENT_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})

# A product image is shown as a thumbnail and a detail picture; no camera
# produces a side longer than this. A header that declares more is refused
# before anything decodes it, so a later resize step cannot be made to
# allocate gigapixels from a few hundred bytes (#595).
MAX_IMAGE_SIDE_PIXELS = 8192

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class UploadRejected(Exception):
    """A file was refused: wrong type, or over the size limit."""


class UploadUnavailable(Exception):
    """The file store could not take the file. Nothing was saved."""


@dataclass(frozen=True)
class ValidatedImage:
    data: bytes
    content_type: str


def _validate(file: FileStorage, config: Config) -> ValidatedImage:
    """Return the validated bytes and their content type, or raise
    UploadRejected."""
    content_type = (file.mimetype or "").lower()
    if (
        content_type not in config.allowed_image_types
        or content_type not in IMAGE_CONTENT_TYPES
    ):
        raise UploadRejected(
            f"'{content_type or 'unknown'}' is not an accepted image type. "
            f"Allowed types: {', '.join(sorted(config.allowed_image_types))}."
        )

    # FileStorage has no reliable pre-read size on every WSGI server, so the
    # stream is measured directly and rewound — the caller still needs to
    # read it afterwards to write the file.
    file.stream.seek(0, os.SEEK_END)
    size = file.stream.tell()
    file.stream.seek(0)

    if size == 0:
        raise UploadRejected("The image file is empty.")

    if size > config.max_upload_bytes:
        limit_mb = config.max_upload_bytes / (1024 * 1024)
        raise UploadRejected(
            f"File is too large ({size / (1024 * 1024):.1f} MB). "
            f"The limit is {limit_mb:.0f} MB."
        )

    header = file.stream.read(12)
    file.stream.seek(0)
    signatures = {
        "image/jpeg": header.startswith(b"\xff\xd8\xff"),
        "image/png": header.startswith(_PNG_SIGNATURE),
        "image/webp": header.startswith(b"RIFF") and header[8:12] == b"WEBP",
    }
    if not signatures[content_type]:
        raise UploadRejected("The file content does not match the selected image type.")

    data = file.stream.read()
    file.stream.seek(0)
    if content_type == "image/png":
        _check_png(data)

    return ValidatedImage(data=data, content_type=content_type)


def _check_png(data: bytes) -> None:
    """Walk a PNG's chunks without decoding pixels (#595).

    The file must start with IHDR, declare a size within
    MAX_IMAGE_SIDE_PIXELS, carry a correct CRC on every chunk, and end at
    IEND: anything appended after it (an HTML page, a script) is refused.
    """
    malformed = UploadRejected("The PNG file is damaged or incomplete.")
    position = len(_PNG_SIGNATURE)
    first = True
    while True:
        if position + 8 > len(data):
            raise malformed
        length, kind = struct.unpack(">I4s", data[position : position + 8])
        body_start = position + 8
        body_end = body_start + length
        if body_end + 4 > len(data):
            raise malformed
        body = data[body_start:body_end]
        (crc,) = struct.unpack(">I", data[body_end : body_end + 4])
        if zlib.crc32(kind + body) != crc:
            raise malformed
        position = body_end + 4

        if first:
            if kind != b"IHDR" or length != 13:
                raise malformed
            width, height = struct.unpack(">II", body[:8])
            if not (0 < width <= MAX_IMAGE_SIDE_PIXELS) or not (
                0 < height <= MAX_IMAGE_SIDE_PIXELS
            ):
                raise UploadRejected(
                    f"The image is {width}×{height} pixels. Each side must be "
                    f"{MAX_IMAGE_SIDE_PIXELS} pixels or fewer."
                )
            first = False
        elif kind == b"IEND":
            if position != len(data):
                raise UploadRejected(
                    "The PNG file has extra data after its end. "
                    "Export the image again and upload that file."
                )
            return


def save_product_image(
    file: FileStorage,
    config: Config,
    *,
    owner_id: uuid.UUID | str,
    store: FileStore | None = None,
) -> StoredObject:
    """Validate an uploaded product image and write its bytes to the file store.

    Raises UploadRejected on a disallowed type, an oversized file, or a PNG that
    is damaged, too large in pixels, or carries data after its end, and
    UploadUnavailable when the store cannot take the file; in both cases nothing
    is stored. On success the object is in the store and its row is not yet
    written: the catalog service inserts the row in the product's own transaction
    (`create_product(image=...)`), and the caller calls `abandon_product_image` if
    that write fails. The original filename is ignored.
    """
    log = logging.getLogger(__name__)
    try:
        image = _validate(file, config)
    except UploadRejected as error:
        log.info("upload_refused reason=%r", str(error))
        raise
    try:
        return put_new_file(
            store or get_file_store(),
            NewFile(
                kind=PRODUCT_IMAGE_KIND,
                data=image.data,
                content_type=image.content_type,
                owner_id=owner_id,
                privacy=PUBLIC,
                metadata={"kind": PRODUCT_IMAGE_KIND},
            ),
        )
    except StoreUnavailable as error:
        log.error("upload_failed reason=file_store_unavailable error=%s", error)
        raise UploadUnavailable(
            "The image could not be saved because the file store is not "
            "reachable. Nothing was changed; try again in a few minutes."
        ) from error


def abandon_product_image(
    saved: StoredObject, *, store: FileStore | None = None
) -> None:
    """Remove the object of an upload whose transaction did not commit."""
    remove_object(store or get_file_store(), saved.object_path)


def retire_product_image(
    connection: Connection,
    *,
    image_file_id: uuid.UUID | None,
    image_path: str | None,
    config: Config,
    store: FileStore | None = None,
) -> None:
    """Remove the picture a product no longer has, after its change committed.

    A file in the store is marked `DELETED` (or `PENDING_DELETE` when the store
    is unreachable); a file that predates the store is deleted from the upload
    directory. Neither can fail the user's action.
    """
    if image_file_id is not None:
        discard_file(connection, store or get_file_store(), image_file_id)
    elif image_path:
        delete_legacy_image(image_path, config)


def delete_legacy_image(relative_path: str, config: Config) -> None:
    """Remove a file that predates the file store from `UPLOAD_DIR`, if it still
    exists.

    Silent on a missing file: replacing an image whose file was already lost
    should not itself become an error.
    """
    if not relative_path:
        return
    root = Path(config.upload_dir).resolve()
    target = (root / relative_path).resolve()
    if target.parent != root:
        return
    target.unlink(missing_ok=True)
