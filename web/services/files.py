"""The platform's files: a store port, two backends, and the metadata flow.

ADR-0091. The bytes of an uploaded file live in a `FileStore`: a private Cloud
Storage bucket in production, a directory under `UPLOAD_DIR` on a developer's
machine and in tests. PostgreSQL keeps only the `stored_file` row. The web reaches
the store through this port and nothing else, so a route never imports the
Google client and a test never needs the cloud.

Writing is bytes first, then the row, in the caller's transaction: if the store
refuses, no row exists; if the row (or anything the caller does with it before
the commit) fails, the object is deleted. The one case this order cannot undo is
a crash between the two steps, which leaves an object no row names. No reader
sees it, and it costs storage only.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast

from flask import current_app
from psycopg import Connection

from web.config import FILE_STORE_GCS, FILE_STORE_LOCAL, Config
from web.db import stored_files
from web.db.stored_files import StoredFile

_log = logging.getLogger(__name__)

# The extension an object is named with, chosen from the validated content type
# and never from the uploaded file's name. `sql/01_schema.sql` checks the same
# pairing (`stored_file_object_path_check`).
EXTENSION_BY_CONTENT_TYPE = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}

# How long a call to the bucket may take before the user is told it is down.
GCS_TIMEOUT_SECONDS = 15

PUBLIC = "PUBLIC"
PRIVATE = "PRIVATE"


class StoreUnavailable(Exception):
    """The store could not be reached or refused the call. The cause is logged
    where it is raised; callers show a controlled error and leave no row."""


class ObjectMissing(Exception):
    """The store answers, and has no object under that path."""


class FileStore(Protocol):
    """What the web needs of a store. Paths are `<kind>/<file_id>.<ext>`."""

    def put(self, object_path: str, data: bytes, content_type: str) -> None:
        """Write one object. Raises StoreUnavailable."""

    def get(self, object_path: str) -> bytes:
        """Read one object. Raises ObjectMissing or StoreUnavailable."""

    def delete(self, object_path: str) -> None:
        """Remove one object; absent is not an error. Raises StoreUnavailable."""


class LocalFileStore:
    """Objects as files under one directory: the local stack and the tests."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self._root = Path(root)

    def _locate(self, object_path: str) -> Path:
        root = self._root.resolve()
        target = (root / object_path).resolve()
        if root not in target.parents:
            # Never reached with a name the schema accepts; kept so a bug
            # elsewhere cannot write or delete outside the directory.
            raise StoreUnavailable(f"object path {object_path!r} leaves the store")
        return target

    def put(self, object_path: str, data: bytes, content_type: str) -> None:
        target = self._locate(object_path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            # Written beside the target and renamed, so a reader never sees half
            # of an object.
            handle, temporary = tempfile.mkstemp(dir=target.parent, suffix=".part")
            try:
                with os.fdopen(handle, "wb") as stream:
                    stream.write(data)
                os.replace(temporary, target)
            except BaseException:
                Path(temporary).unlink(missing_ok=True)
                raise
        except OSError as error:
            _log.error(
                "file_store_write_failed backend=local path=%r error=%s",
                object_path,
                error,
            )
            raise StoreUnavailable(str(error)) from error

    def get(self, object_path: str) -> bytes:
        target = self._locate(object_path)
        try:
            return target.read_bytes()
        except FileNotFoundError as error:
            raise ObjectMissing(object_path) from error
        except OSError as error:
            _log.error(
                "file_store_read_failed backend=local path=%r error=%s",
                object_path,
                error,
            )
            raise StoreUnavailable(str(error)) from error

    def delete(self, object_path: str) -> None:
        target = self._locate(object_path)
        try:
            target.unlink(missing_ok=True)
        except OSError as error:
            _log.error(
                "file_store_delete_failed backend=local path=%r error=%s",
                object_path,
                error,
            )
            raise StoreUnavailable(str(error)) from error


class GcsFileStore:
    """Objects in one Cloud Storage bucket, through Application Default
    Credentials: on the instance, its attached service account. No key file.

    The `google-cloud-storage` import and the client's construction wait for the
    first call, so the local backend and the tests never load the library and the
    application starts even while the bucket is unreachable. `client` is for
    tests, which hand in an object that records the calls.
    """

    def __init__(self, bucket_name: str, client: Any | None = None) -> None:
        self._bucket_name = bucket_name
        self._client = client

    def _bucket(self) -> Any:
        if self._client is None:
            from google.cloud import storage

            self._client = storage.Client()
        return self._client.bucket(self._bucket_name)

    @staticmethod
    def _is_not_found(error: Exception) -> bool:
        # google.api_core.exceptions.NotFound carries the HTTP status as `code`.
        return getattr(error, "code", None) == 404

    def _unavailable(self, action: str, object_path: str, error: Exception):
        _log.error(
            "file_store_%s_failed backend=gcs bucket=%r path=%r error=%s: %s",
            action,
            self._bucket_name,
            object_path,
            type(error).__name__,
            error,
        )
        return StoreUnavailable(f"{type(error).__name__}: {error}")

    def put(self, object_path: str, data: bytes, content_type: str) -> None:
        try:
            blob = self._bucket().blob(object_path)
            # A name is used once, so the write may be retried safely: it
            # succeeds only if the object does not exist yet.
            blob.upload_from_string(
                data,
                content_type=content_type,
                if_generation_match=0,
                timeout=GCS_TIMEOUT_SECONDS,
            )
        except Exception as error:
            raise self._unavailable("write", object_path, error) from error

    def get(self, object_path: str) -> bytes:
        try:
            blob = self._bucket().blob(object_path)
            return cast(bytes, blob.download_as_bytes(timeout=GCS_TIMEOUT_SECONDS))
        except Exception as error:
            if self._is_not_found(error):
                raise ObjectMissing(object_path) from error
            raise self._unavailable("read", object_path, error) from error

    def delete(self, object_path: str) -> None:
        try:
            self._bucket().blob(object_path).delete(timeout=GCS_TIMEOUT_SECONDS)
        except Exception as error:
            if self._is_not_found(error):
                return
            raise self._unavailable("delete", object_path, error) from error


def build_file_store(config: Config) -> FileStore:
    """The store the configuration names. Nothing is contacted yet."""
    if config.file_store_backend == FILE_STORE_GCS:
        if not config.gcs_bucket:
            raise ValueError("the gcs file store needs a bucket name")
        return GcsFileStore(config.gcs_bucket)
    if config.file_store_backend == FILE_STORE_LOCAL:
        return LocalFileStore(config.upload_dir)
    raise ValueError(f"unknown file store {config.file_store_backend!r}")


_STORE_KEY = "file_store"


def get_file_store() -> FileStore:
    """The current application's store, built on first use. Tests may put a
    store of their own in `app.extensions["file_store"]` first."""
    store = current_app.extensions.get(_STORE_KEY)
    if store is None:
        store = build_file_store(current_app.config["APP_CONFIG"])
        current_app.extensions[_STORE_KEY] = store
    return cast(FileStore, store)


@dataclass(frozen=True)
class NewFile:
    """What a caller has validated and wants kept."""

    kind: str
    data: bytes
    content_type: str
    owner_id: uuid.UUID | str
    privacy: str = PUBLIC
    metadata: dict[str, Any] = field(default_factory=dict)


def public_name(file: StoredFile) -> str:
    """`<file_id>.<ext>`: the name the web's image route answers to, and what
    `product.image_path` holds for a file in the store."""
    extension = EXTENSION_BY_CONTENT_TYPE[file.content_type]
    return f"{file.file_id}{extension}"


def store_new_file(
    connection: Connection, store: FileStore, new: NewFile
) -> StoredFile:
    """Write the bytes, then the metadata row, in the caller's transaction.

    The caller commits (a service wrapped in `@atomic`), or rolls back and calls
    `remove_object`. Raises StoreUnavailable when the store refuses, with no row
    written; on any failure after the object was written, the object is removed
    here before the error continues.
    """
    file_id = uuid.uuid4()
    extension = EXTENSION_BY_CONTENT_TYPE[new.content_type]
    object_path = f"{new.kind}/{file_id}{extension}"
    digest = hashlib.sha256(new.data).hexdigest()

    store.put(object_path, new.data, new.content_type)
    try:
        return stored_files.insert_stored_file(
            connection,
            file_id=file_id,
            object_path=object_path,
            content_type=new.content_type,
            size_bytes=len(new.data),
            sha256=digest,
            privacy=new.privacy,
            owner_user_id=new.owner_id,
            metadata=new.metadata,
        )
    except BaseException:
        connection.rollback()
        remove_object(store, object_path)
        raise


def remove_object(store: FileStore, object_path: str) -> None:
    """Delete an object nobody refers to any more. A failure is logged and
    swallowed: the user's action does not hinge on cleanup, and an object with
    no row costs only storage."""
    try:
        store.delete(object_path)
    except StoreUnavailable:
        _log.error("file_store_orphan_left path=%r", object_path)


def discard_file(connection: Connection, store: FileStore, file_id: uuid.UUID) -> None:
    """Retire a stored file the platform no longer uses, in its own transaction.

    The object is deleted and the row marked `DELETED`. If the store cannot be
    reached the row is marked `PENDING_DELETE` instead, for
    `deploy/migrate-uploads-1189.py --delete-pending` to finish; the user's
    action succeeds either way.
    """
    row = stored_files.get_stored_file(connection, file_id)
    if row is None or row.status == "DELETED":
        connection.commit()
        return
    try:
        store.delete(row.object_path)
    except StoreUnavailable:
        stored_files.mark_pending_delete(connection, file_id)
        _log.error("file_store_delete_deferred file_id=%s", file_id)
    else:
        stored_files.mark_deleted(connection, file_id)
    connection.commit()
