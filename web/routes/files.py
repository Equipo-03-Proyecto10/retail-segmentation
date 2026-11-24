"""Private files, through short-lived signed URLs (F29-14, #1190; ADR-0091).

`GET /files/<id>/download` is where a signed-in user asks for a PRIVATE file. If
their role may read that kind of file, the answer is a URL that works for at most
fifteen minutes (a redirect to it, or JSON when the client asks for JSON); if it
may not, 403 and no URL, and the refusal is recorded. An unknown id is a 404.

`GET /files/<id>/content` is only the local backend's stand-in for the bucket: it
checks the HMAC signature and expiry that `download` put in the URL and serves
the bytes. It does nothing on the `gcs` backend, where Google checks the URL.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    jsonify,
    redirect,
    request,
    session,
    url_for,
)
from flask.typing import ResponseReturnValue

from web.config import FILE_STORE_LOCAL
from web.db import get_connection
from web.db.stored_files import get_stored_file
from web.middleware.authz import (
    current_permissions,
    current_role_code,
    public,
    requires,
)
from web.services.file_urls import (
    ISSUED,
    REFUSED,
    check_local_url,
    download_name,
    issue_url,
    read_permission,
    record_access,
)
from web.services.files import (
    PRIVATE,
    ObjectMissing,
    StoreUnavailable,
    get_file_store,
)

bp = Blueprint("files", __name__, url_prefix="/files")


def _now() -> datetime:
    return datetime.now(UTC)


def _local_url(file_id: UUID, expires: int, signature: str) -> str:
    return url_for(
        "files.content", file_id=file_id, expires=expires, signature=signature
    )


@bp.get("/<uuid:file_id>/download")
@requires()
def download(file_id: UUID) -> ResponseReturnValue:
    """A signed URL for one PRIVATE file, if this user's role may read it."""
    config = current_app.config["APP_CONFIG"]
    connection = get_connection()
    file = get_stored_file(connection, file_id)
    if file is None or file.privacy != PRIVATE or file.status != "STORED":
        # Unknown, public (served by its own route) or removed: the same answer,
        # so the response does not tell a reader which of them it was.
        current_app.logger.info("file_url_not_found file_id=%s", file_id)
        abort(404)

    actor = session["user_id"]
    role = current_role_code() or ""
    now = _now()
    permission = read_permission(file)
    if permission is None or permission not in current_permissions():
        record_access(
            connection,
            file=file,
            actor_id=actor,
            role_code=role,
            outcome=REFUSED,
            occurred_at=now,
            expires_at=None,
        )
        abort(403)

    try:
        signed = issue_url(
            file, get_file_store(), config, now=now, local_url=_local_url
        )
    except StoreUnavailable:
        abort(503)
    record_access(
        connection,
        file=file,
        actor_id=actor,
        role_code=role,
        outcome=ISSUED,
        occurred_at=now,
        expires_at=signed.expires_at,
    )
    if request.accept_mimetypes.best == "application/json":
        return jsonify(
            file_id=str(file.file_id),
            url=signed.url,
            expires_at=signed.expires_at.isoformat(),
        )
    return redirect(signed.url, code=303)


@bp.get("/<uuid:file_id>/content")
@public
def content(file_id: UUID) -> ResponseReturnValue:
    """The local backend's signed download. Refused unless the URL is intact and
    unexpired; the log names the file and the reason, never the query string."""
    config = current_app.config["APP_CONFIG"]
    if config.file_store_backend != FILE_STORE_LOCAL:
        abort(404)
    refusal = check_local_url(
        config.secret_key,
        file_id,
        request.args.get("expires", type=int),
        request.args.get("signature", ""),
        now=_now(),
    )
    if refusal is not None:
        current_app.logger.warning(
            "file_url_refused file_id=%s reason=%s", file_id, refusal
        )
        abort(403)
    file = get_stored_file(get_connection(), file_id)
    if file is None or file.privacy != PRIVATE or file.status != "STORED":
        abort(404)
    try:
        data = get_file_store().get(file.object_path)
    except ObjectMissing:
        current_app.logger.error(
            "file_store_object_missing file_id=%s path=%r", file_id, file.object_path
        )
        abort(404)
    except StoreUnavailable:
        abort(503)
    current_app.logger.info("file_url_used file_id=%s", file_id)
    response = Response(data, mimetype=file.content_type)
    response.headers["Content-Disposition"] = (
        f'attachment; filename="{download_name(file)}"'
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
