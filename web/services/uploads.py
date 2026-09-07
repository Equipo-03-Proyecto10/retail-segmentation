"""Image uploads for product records (F3-07, #67).

An uploaded file is validated by content type and size, written under
Config.upload_dir with a generated name, and only its relative path is ever
handed back — the database stores that path, never the bytes (RF-08).
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from werkzeug.datastructures import FileStorage

from web.config import Config

# Maps an accepted MIME type to the extension the stored file gets, so the
# extension on disk matches the declared type and the checked file signature.
_EXTENSION_BY_TYPE = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


class UploadRejected(Exception):
    """A file was refused: wrong type, or over the size limit."""


@dataclass(frozen=True)
class SavedUpload:
    """Where an accepted upload ended up. `relative_path` is what the
    database stores in product.image_path."""

    relative_path: str
    absolute_path: str


def _validate(file: FileStorage, config: Config) -> str:
    """Return the extension to save with, or raise UploadRejected."""
    content_type = (file.mimetype or "").lower()
    if (
        content_type not in config.allowed_image_types
        or content_type not in _EXTENSION_BY_TYPE
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
        "image/png": header.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/webp": header.startswith(b"RIFF") and header[8:12] == b"WEBP",
    }
    if not signatures[content_type]:
        raise UploadRejected("The file content does not match the selected image type.")

    return _EXTENSION_BY_TYPE[content_type]


def save_product_image(file: FileStorage, config: Config) -> SavedUpload:
    """Validate and store an uploaded product image.

    Raises UploadRejected on a disallowed type or an oversized file. The
    original filename is ignored; a generated name prevents collisions.
    """
    extension = _validate(file, config)
    os.makedirs(config.upload_dir, exist_ok=True)

    stored_name = f"{uuid.uuid4().hex}{extension}"
    absolute_path = os.path.join(config.upload_dir, stored_name)

    file.save(absolute_path)

    return SavedUpload(relative_path=stored_name, absolute_path=absolute_path)


def delete_product_image(relative_path: str, config: Config) -> None:
    """Remove a previously stored image, if it still exists.

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
