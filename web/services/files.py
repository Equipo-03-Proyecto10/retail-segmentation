"""The platform's files: a store port, two backends, and the metadata flow.

ADR-0091. The bytes of an uploaded file live in a `FileStore`: a private Cloud
Storage bucket in production, a directory under `UPLOAD_DIR` on a developer's
machine and in tests. PostgreSQL keeps only the `stored_file` row. The web reaches
the store through this port and nothing else, so a route never imports the
Google client and a test never needs the cloud.

Writing is bytes first (`put_new_file`), then the row, inserted by the service
that owns the transaction in the same unit of work as whatever refers to the file:
if the store refuses, no row exists; if the row or that unit of work fails, the
caller removes the object (`abandon_object`). The one case this order cannot undo
is a crash between the two steps, which leaves an object no row names. No reader
sees it, and it costs storage only.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Protocol, cast

from flask import current_app
from psycopg import Connection

from web.config import FILE_STORE_GCS, FILE_STORE_LOCAL, Config
from web.db import stored_files
from web.db.stored_files import StoredFile
from web.db.transactions import atomic

_log = logging.getLogger(__name__)

# The extension an object is named with, chosen from the validated content type
# and never from the uploaded file's name. `sql/01_schema.sql` checks the same
# pairing (`stored_file_object_path_check`).
EXTENSION_BY_CONTENT_TYPE = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "text/csv": ".csv",
}

# How long a call to the bucket may take before the user is told it is down. Two
# numbers, because the client's `timeout` bounds one HTTP attempt, and its retry
# policy (a 120-second deadline by default, the same as gunicorn's worker timeout
# and twice NGINX's) bounds the operation: the deadline is what keeps a retryable
# outage from outliving the proxy and the worker (ADR-0091).
GCS_TIMEOUT_SECONDS = 10
GCS_DEADLINE_SECONDS = 20

PUBLIC = "PUBLIC"
PRIVATE = "PRIVATE"
# The kinds a PRIVATE file can have today, and the folder (first path segment)
# each lives in. See web/services/file_urls.py for who may read each.
SALES_IMPORT_KIND = "sales-import"


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

    def __init__(
        self,
        bucket_name: str,
        client: Any | None = None,
        retry_policy: Callable[[], Any] | None = None,
        signer_identity: Callable[[], tuple[str, str]] | None = None,
    ) -> None:
        self._bucket_name = bucket_name
        self._client = client
        self._retry_policy = retry_policy
        self._identity = signer_identity

    def _retry(self) -> Any:
        """The retry policy of every call: the client's own, with its whole-operation
        deadline cut to GCS_DEADLINE_SECONDS. Tests hand in `retry_policy`."""
        if self._retry_policy is not None:
            return self._retry_policy()
        from google.cloud.storage.retry import DEFAULT_RETRY

        return DEFAULT_RETRY.with_deadline(GCS_DEADLINE_SECONDS)

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
                retry=self._retry(),
            )
        except Exception as error:
            raise self._unavailable("write", object_path, error) from error

    def get(self, object_path: str) -> bytes:
        try:
            blob = self._bucket().blob(object_path)
            return cast(
                bytes,
                blob.download_as_bytes(
                    timeout=GCS_TIMEOUT_SECONDS, retry=self._retry()
                ),
            )
        except Exception as error:
            if self._is_not_found(error):
                raise ObjectMissing(object_path) from error
            raise self._unavailable("read", object_path, error) from error

    def _signer_identity(self) -> tuple[str, str]:
        """The attached service account and a fresh access token for it.

        Compute Engine credentials hold no private key, so the library signs by
        calling IAM `signBlob` as this account (it needs
        `roles/iam.serviceAccountTokenCreator` on itself, ADR-0091). Tests hand in
        `signer_identity` instead."""
        if self._identity is not None:
            return self._identity()
        import google.auth
        from google.auth.transport.requests import Request

        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        credentials.refresh(Request())
        return credentials.service_account_email, credentials.token

    def signed_url(
        self, object_path: str, *, expires_in: int, download_name: str
    ) -> str:
        """A V4 URL that lets whoever holds it GET this one object for
        `expires_in` seconds, as a download. The object stays private: no ACL is
        set and no public access is granted. The URL is returned and never
        logged."""
        try:
            email, token = self._signer_identity()
            return str(
                self._bucket()
                .blob(object_path)
                .generate_signed_url(
                    version="v4",
                    expiration=timedelta(seconds=expires_in),
                    method="GET",
                    response_disposition=f'attachment; filename="{download_name}"',
                    service_account_email=email,
                    access_token=token,
                )
            )
        except Exception as error:
            raise self._unavailable("sign", object_path, error) from error

    def delete(self, object_path: str) -> None:
        try:
            self._bucket().blob(object_path).delete(
                timeout=GCS_TIMEOUT_SECONDS, retry=self._retry()
            )
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


@dataclass(frozen=True)
class StoredObject:
    """An object already written to the store, whose row is not yet written.

    The row is inserted by the service that owns the transaction (for a product
    image, the catalog's), in the same unit of work as whatever refers to it.
    """

    file_id: uuid.UUID
    object_path: str
    content_type: str
    size_bytes: int
    sha256: str
    privacy: str
    owner_id: uuid.UUID | str
    metadata: dict[str, Any]

    @property
    def public_name(self) -> str:
        """`<file_id>.<ext>`: the name the web's image route answers to, and what
        `product.image_path` holds for a file in the store."""
        return f"{self.file_id}{EXTENSION_BY_CONTENT_TYPE[self.content_type]}"


def public_name(file: StoredFile) -> str:
    """The same name, for a row read back from the database."""
    return f"{file.file_id}{EXTENSION_BY_CONTENT_TYPE[file.content_type]}"


def put_new_file(store: FileStore, new: NewFile) -> StoredObject:
    """Write the bytes of a new file to the store, under a generated name.

    This is the first half of keeping a file: bytes first, then the row. It
    raises StoreUnavailable when the store refuses, with nothing written and no
    row to undo. After it returns, whoever holds the `StoredObject` must either
    commit its row (`stored_files.insert_stored_file`, in the unit of work that
    uses it) or call `abandon_object` if that unit of work fails.
    """
    file_id = uuid.uuid4()
    extension = EXTENSION_BY_CONTENT_TYPE[new.content_type]
    object_path = f"{new.kind}/{file_id}{extension}"
    store.put(object_path, new.data, new.content_type)
    return StoredObject(
        file_id=file_id,
        object_path=object_path,
        content_type=new.content_type,
        size_bytes=len(new.data),
        sha256=hashlib.sha256(new.data).hexdigest(),
        privacy=new.privacy,
        owner_id=new.owner_id,
        metadata=new.metadata,
    )


def insert_row(connection: Connection, stored: StoredObject) -> StoredFile:
    """The metadata row of an object already in the store, in the caller's
    transaction."""
    return stored_files.insert_stored_file(
        connection,
        file_id=stored.file_id,
        object_path=stored.object_path,
        content_type=stored.content_type,
        size_bytes=stored.size_bytes,
        sha256=stored.sha256,
        privacy=stored.privacy,
        owner_user_id=stored.owner_id,
        metadata=stored.metadata,
    )


def remove_object(store: FileStore, object_path: str) -> None:
    """Delete an object nobody refers to any more. A failure is logged and
    swallowed: the user's action does not hinge on cleanup, and an object with
    no row costs only storage."""
    try:
        store.delete(object_path)
    except StoreUnavailable:
        _log.error("file_store_orphan_left path=%r", object_path)


@atomic
def _mark_pending(connection: Connection, file_id: uuid.UUID) -> StoredFile | None:
    """Commit the intent to delete, before the store is touched."""
    row = stored_files.get_stored_file(connection, file_id)
    if row is not None and row.status == "STORED":
        stored_files.mark_pending_delete(connection, file_id)
    return row


@atomic
def _mark_deleted(connection: Connection, file_id: uuid.UUID) -> None:
    stored_files.mark_deleted(connection, file_id)


def discard_file(connection: Connection, store: FileStore, file_id: uuid.UUID) -> None:
    """Retire a stored file the platform no longer uses.

    The row is committed `PENDING_DELETE` first, then the object is deleted, then
    the row is committed `DELETED`: three steps, each its own transaction, so that
    whichever fails or is interrupted leaves a row that says what is still to do
    (`deploy/migrate-uploads-1189.py --delete-pending` finishes it) and never one
    that says `STORED` over an object that is gone. The user's action succeeds in
    every case, and a file already `DELETED` or unknown is left alone.
    """
    row = _mark_pending(connection, file_id)
    if row is None or row.status == "DELETED":
        return
    try:
        store.delete(row.object_path)
    except StoreUnavailable:
        _log.error("file_store_delete_deferred file_id=%s", file_id)
        return
    try:
        _mark_deleted(connection, file_id)
    except Exception:
        _log.exception("file_store_delete_record_deferred file_id=%s", file_id)


@atomic
def _record_orphan(connection: Connection, stored: StoredObject) -> None:
    insert_row(connection, stored)
    stored_files.mark_pending_delete(connection, stored.file_id)


def abandon_object(
    connection: Connection, store: FileStore, stored: StoredObject
) -> None:
    """Remove an object whose write was refused after it reached the store.

    Called after the refused transaction has rolled back. If the store cannot be
    reached either, the object would have no row and nothing could ever find it, so
    its metadata is committed in a fresh transaction as `PENDING_DELETE`, for
    `--delete-pending` to retry. Neither failure reaches the user."""
    try:
        store.delete(stored.object_path)
    except StoreUnavailable:
        try:
            _record_orphan(connection, stored)
            _log.error("file_store_orphan_delete_deferred file_id=%s", stored.file_id)
        except Exception:
            _log.exception("file_store_orphan_left path=%r", stored.object_path)
