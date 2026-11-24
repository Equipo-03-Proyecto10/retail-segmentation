"""Short-lived URLs for PRIVATE files (F29-14, #1190; ADR-0091).

A PRIVATE file is never served by an open route. A user whose role may read it
asks the web for it, and the web answers with a URL that works for at most fifteen
minutes: a V4 signed URL to the bucket on the `gcs` backend (the object itself
stays private), and an HMAC-signed URL to a route of this application on the
`local` backend, so the local stack and the tests behave the same.

Who may read a file is decided here, by its kind, from the web's own permission
model. Every request, granted or refused, leaves a `file_access` row; the log
line holds the file id, the actor and the expiry, never the URL or its signature.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from psycopg import Connection

from web.config import MAX_SIGNED_URL_TTL_SECONDS, Config
from web.db import file_access
from web.db.stored_files import StoredFile
from web.db.transactions import atomic
from web.middleware.authz import SALES_INGEST_EXECUTE
from web.services.files import (
    EXTENSION_BY_CONTENT_TYPE,
    PRIVATE,
    SALES_IMPORT_KIND,
    FileStore,
    StoreUnavailable,
)

_log = logging.getLogger(__name__)

ISSUED = "ISSUED"
REFUSED = "REFUSED"

# Which permission reads each kind of PRIVATE file. A kind that is not here has
# no reader: it is refused to everybody, the safe direction.
READ_PERMISSION_BY_KIND = {SALES_IMPORT_KIND: SALES_INGEST_EXECUTE}

_DOMAIN = b"mosaiq-private-file-url-v1"


@dataclass(frozen=True)
class SignedUrl:
    url: str
    expires_at: datetime

    def __repr__(self) -> str:
        # Never put the URL, which carries the signature, in a log or a traceback.
        return f"SignedUrl(expires_at={self.expires_at.isoformat()})"


def kind_of(file: StoredFile) -> str:
    """The folder an object lives in: `sales-import` in `sales-import/<id>.csv`."""
    return file.object_path.split("/", 1)[0]


def read_permission(file: StoredFile) -> str | None:
    return READ_PERMISSION_BY_KIND.get(kind_of(file))


def ttl_seconds(config: Config) -> int:
    """The lifetime of a URL: configured, but between 1 and 900 seconds."""
    return max(1, min(config.signed_url_ttl_seconds, MAX_SIGNED_URL_TTL_SECONDS))


def download_name(file: StoredFile) -> str:
    """The name the browser saves the file under: never the uploaded file's."""
    return (
        f"{kind_of(file)}-{file.file_id}{EXTENSION_BY_CONTENT_TYPE[file.content_type]}"
    )


# ---------- the local backend's signature ----------


def signature(secret: str, file_id: uuid.UUID, expires: int) -> str:
    message = _DOMAIN + b"|" + f"{file_id}|{expires}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def check_local_url(
    secret: str, file_id: uuid.UUID, expires: int | None, given: str, *, now: datetime
) -> str | None:
    """None when the URL is good; otherwise why it is refused. The signature is
    checked before the expiry so a forged URL is never told how late it is."""
    if expires is None or not given:
        return "malformed"
    if not hmac.compare_digest(signature(secret, file_id, expires), given):
        return "signature"
    if now.timestamp() >= expires:
        return "expired"
    return None


def issue_url(
    file: StoredFile,
    store: FileStore,
    config: Config,
    *,
    now: datetime,
    local_url: Callable[[uuid.UUID, int, str], str],
) -> SignedUrl:
    """A URL for one stored file, valid `ttl_seconds` from `now`.

    Raises StoreUnavailable if the bucket's signer cannot be reached."""
    lifetime = ttl_seconds(config)
    expires_at = now + timedelta(seconds=lifetime)
    sign = getattr(store, "signed_url", None)
    if sign is not None:
        url = sign(
            file.object_path, expires_in=lifetime, download_name=download_name(file)
        )
    else:
        expires = int(expires_at.timestamp())
        url = local_url(
            file.file_id, expires, signature(config.secret_key, file.file_id, expires)
        )
    return SignedUrl(url=url, expires_at=expires_at)


@atomic
def record_access(
    connection: Connection,
    *,
    file: StoredFile,
    actor_id: uuid.UUID | str,
    role_code: str,
    outcome: str,
    occurred_at: datetime,
    expires_at: datetime | None,
) -> None:
    """One `file_access` row, committed on its own."""
    file_access.insert_file_access(
        connection,
        file_id=file.file_id,
        actor_user_id=actor_id,
        role_code=role_code,
        outcome=outcome,
        occurred_at=occurred_at,
        expires_at=expires_at,
    )
    _log.info(
        "file_access outcome=%s file_id=%s actor=%s role=%s expires_at=%s",
        outcome,
        file.file_id,
        actor_id,
        role_code,
        expires_at.isoformat() if expires_at else "-",
    )


__all__ = [
    "ISSUED",
    "PRIVATE",
    "REFUSED",
    "SignedUrl",
    "StoreUnavailable",
    "check_local_url",
    "download_name",
    "issue_url",
    "kind_of",
    "read_permission",
    "record_access",
    "signature",
    "ttl_seconds",
]
