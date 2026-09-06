"""Image uploads for product records (F3-07, #67).

An uploaded file is validated by content type and size, written under
Config.upload_dir with a generated name, and only its relative path is ever
handed back — the database stores that path, never the bytes (RF-08).
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass

from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from web.config import Config

# Maps an accepted MIME type to the extension the stored file gets, so the
# extension on disk always matches what the browser actually sent — never
# trusted from the client's original filename alone.
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
    if content_type not in config.allowed_image_types:
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

    if size > config.max_upload_bytes:
        limit_mb = config.max_upload_bytes / (1024 * 1024)
        raise UploadRejected(
            f"File is too large ({size / (1024 * 1024):.1f} MB). "
            f"The limit is {limit_mb:.0f} MB."
        )

    return _EXTENSION_BY_TYPE[content_type]


def save_product_image(file: FileStorage, config: Config) -> SavedUpload:
    """Validate and store an uploaded product image.

    Raises UploadRejected on a disallowed type or an oversized file. The
    original filename is never trusted for anything but its extension hint —
    secure_filename is applied, and the actual name written to disk is a
    fresh UUID either way, so no two uploads can collide.
    """
    extension = _validate(file, config)

    os.makedirs(config.upload_dir, exist_ok=True)

    # secure_filename strips path separators and odd characters from
    # whatever the browser sent — defence in depth, since the stored name
    # below never uses this value directly.
    secure_filename(file.filename or "upload")
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
    absolute_path = os.path.join(config.upload_dir, relative_path)
    if os.path.exists(absolute_path):
        os.remove(absolute_path)
