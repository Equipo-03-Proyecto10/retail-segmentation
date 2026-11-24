"""Data access for stored_file: the metadata of a file kept in the file store.

Never the bytes (ADR-0091). Write functions leave the transaction to the caller.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg import Connection
from psycopg.types.json import Jsonb


@dataclass(frozen=True)
class StoredFile:
    file_id: uuid.UUID
    object_path: str
    content_type: str
    size_bytes: int
    sha256: str
    privacy: str
    status: str
    owner_user_id: uuid.UUID
    created_at: datetime
    metadata: dict[str, Any]


def insert_stored_file(
    connection: Connection,
    *,
    file_id: uuid.UUID,
    object_path: str,
    content_type: str,
    size_bytes: int,
    sha256: str,
    privacy: str,
    owner_user_id: uuid.UUID | str,
    metadata: dict[str, Any],
) -> StoredFile:
    """Insert one `STORED` row and return it."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO stored_file
                (file_id, object_path, content_type, size_bytes, sha256,
                 privacy, owner_user_id, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING file_id, object_path, content_type, size_bytes, sha256,
                      privacy, status, owner_user_id, created_at, metadata
            """,
            (
                file_id,
                object_path,
                content_type,
                size_bytes,
                sha256,
                privacy,
                owner_user_id,
                Jsonb(metadata),
            ),
        )
        return StoredFile(*cursor.fetchone())


def get_stored_file(connection: Connection, file_id: uuid.UUID) -> StoredFile | None:
    """One row by id, or None."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT file_id, object_path, content_type, size_bytes, sha256,
                   privacy, status, owner_user_id, created_at, metadata
            FROM stored_file WHERE file_id = %s
            """,
            (file_id,),
        )
        row = cursor.fetchone()
    return StoredFile(*row) if row else None


def mark_deleted(connection: Connection, file_id: uuid.UUID) -> None:
    """The object is gone: record it, keeping the row."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE stored_file
            SET status = 'DELETED', deleted_at = now()
            WHERE file_id = %s AND status <> 'DELETED'
            """,
            (file_id,),
        )


def mark_pending_delete(connection: Connection, file_id: uuid.UUID) -> None:
    """The store could not be reached; the delete is for a later run."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE stored_file SET status = 'PENDING_DELETE'
            WHERE file_id = %s AND status = 'STORED'
            """,
            (file_id,),
        )
