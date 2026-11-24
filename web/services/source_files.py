"""Keeping the source CSV of a sales import as a PRIVATE file (F29-14, ADR-0091).

The import is the business operation; keeping its file is secondary. So the bytes
are written first, the import runs, and only then is the file registered against
the load. If the store is down, or the registration fails, the load stands and is
simply recorded as having no source file; the object, if one was written, is
removed.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from psycopg import Connection

from web.db import sales_loads
from web.db.transactions import atomic
from web.services import files
from web.services.files import (
    PRIVATE,
    SALES_IMPORT_KIND,
    FileStore,
    NewFile,
    StoredObject,
    StoreUnavailable,
)

_log = logging.getLogger(__name__)


def write_source(
    store: FileStore, data: bytes, owner_id: UUID | str | None
) -> StoredObject | None:
    """Write the CSV's bytes to the store, or None when it cannot or need not be
    kept (an empty file, no known uploader, a store that is down)."""
    if not data or owner_id is None:
        return None
    try:
        return files.put_new_file(
            store,
            NewFile(
                kind=SALES_IMPORT_KIND,
                data=data,
                content_type="text/csv",
                owner_id=owner_id,
                privacy=PRIVATE,
                metadata={"kind": SALES_IMPORT_KIND},
            ),
        )
    except StoreUnavailable as error:
        _log.error("source_file_not_kept reason=file_store_unavailable error=%s", error)
        return None


@atomic
def _register(connection: Connection[Any], stored: StoredObject, load_id: int) -> None:
    files.insert_row(connection, stored)
    sales_loads.set_source_file(connection, load_id, stored.file_id)


def attach_source(
    connection: Connection[Any], store: FileStore, stored: StoredObject, load_id: int
) -> bool:
    """Register the written object against its load. False (and the object
    removed) if that fails: the load has already committed and is not undone."""
    try:
        _register(connection, stored, load_id)
    except Exception:
        _log.exception("source_file_not_kept reason=registration load_id=%s", load_id)
        files.remove_object(store, stored.object_path)
        return False
    return True
