"""Data access for app_session — server-side sessions (ADR-0022, #251).

The signed cookie carries only a session_id. Every request resolves it here,
together with the user's is_active flag and role, so a revoked session, a
deactivated user or a changed role takes effect on the next request.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg import Connection

from web.config import (
    DEFAULT_SESSION_ABSOLUTE_TIMEOUT_SECONDS,
    DEFAULT_SESSION_IDLE_TIMEOUT_SECONDS,
)


@dataclass(frozen=True)
class SessionPrincipal:
    """Who an open session belongs to, as the database says right now."""

    user_id: UUID
    role_id: int
    role_code: str
    name: str


def open_session(connection: Connection[Any], user_id: UUID | str) -> UUID:
    """Record a new session for user_id; the service owns the transaction."""
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO app_session (user_id) VALUES (%s) RETURNING session_id",
            (str(user_id),),
        )
        return cursor.fetchone()[0]


def load_principal(
    connection: Connection[Any],
    session_id: UUID,
    *,
    idle_timeout_seconds: int = DEFAULT_SESSION_IDLE_TIMEOUT_SECONDS,
    absolute_timeout_seconds: int = DEFAULT_SESSION_ABSOLUTE_TIMEOUT_SECONDS,
) -> SessionPrincipal | None:
    """The open session's user, or None if the session is revoked or unknown,
    or its user has been deactivated.

    The two age checks are deliberately in SQL, next to the revoked and active
    checks.  A session that is expired must not be usable for even one request,
    and `last_seen_at` is updated only after the row passes both limits.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT u.user_id, u.role_id, r.code, u.name
            FROM app_session AS s
            JOIN app_user AS u ON u.user_id = s.user_id
            JOIN role AS r ON r.role_id = u.role_id
            WHERE s.session_id = %s
              AND s.revoked_at IS NULL
              AND u.is_active
              AND s.created_at > now() - make_interval(secs => %s)
              AND s.last_seen_at > now() - make_interval(secs => %s)
            FOR UPDATE OF s
            """,
            (session_id, absolute_timeout_seconds, idle_timeout_seconds),
        )
        row = cursor.fetchone()
        if row is not None:
            cursor.execute(
                """
                UPDATE app_session
                   SET last_seen_at = now()
                 WHERE session_id = %s AND revoked_at IS NULL
                """,
                (session_id,),
            )

    return SessionPrincipal(*row) if row else None


def revoke_session(connection: Connection[Any], session_id: UUID) -> None:
    """Close one session; the service owns the transaction."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE app_session SET revoked_at = now()
            WHERE session_id = %s AND revoked_at IS NULL
            """,
            (session_id,),
        )


def revoke_user_sessions(connection: Connection[Any], user_id: UUID | str) -> None:
    """Close every open session of one user; the service owns the transaction."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE app_session SET revoked_at = now()
            WHERE user_id = %s AND revoked_at IS NULL
            """,
            (str(user_id),),
        )
