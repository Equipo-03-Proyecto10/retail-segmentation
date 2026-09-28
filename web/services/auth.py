"""Authentication service: password verification and login decisions.

Routes call these functions; they never touch password hashing or the
database directly.

Uses argon2-cffi for password hashing, per web/requirements.txt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from psycopg import Connection

from web.db.sessions import (
    SessionPrincipal,
    load_principal,
    open_session,
    revoke_session,
)
from web.db.transactions import atomic
from web.db.users import AppUser, get_user_by_email

_hasher = PasswordHasher()
_logger = logging.getLogger(__name__)
# Public dummy credential: only used to spend the same Argon2 work on a miss.
_DUMMY_HASH = _hasher.hash("dummy credential for failed authentication")


@dataclass(frozen=True)
class LoginResult:
    success: bool
    user: AppUser | None = None


def hash_password(plain_password: str) -> str:
    """Return a secure Argon2 hash for storing a new or updated password."""
    return _hasher.hash(plain_password)


def authenticate(
    connection: Connection,
    email: str,
    plain_password: str,
) -> LoginResult:
    """Verify credentials without revealing which field was wrong.

    Returns a LoginResult with success=False for: unknown email, wrong
    password, a deactivated account, or an unusable hash. The caller shows the
    same generic message in every failure case.
    """
    user = get_user_by_email(connection, email)

    active = user is not None and user.is_active

    try:
        _hasher.verify(user.password_hash if active else _DUMMY_HASH, plain_password)
    except VerifyMismatchError:
        return LoginResult(success=False)
    except (InvalidHashError, VerificationError):
        _logger.warning("login_refused reason=unusable_hash email=%r", email[:254])
        return LoginResult(success=False)

    if not active:
        return LoginResult(success=False)

    return LoginResult(success=True, user=user)


# ---------- server-side sessions (ADR-0022, #251) ----------


@atomic
def start_session(connection: Connection, user_id: UUID | str) -> str:
    """Open a server-side session for a user who just authenticated.

    The returned id is all the cookie needs to carry: everything else about
    the visitor is re-read from the database on each request.
    """
    return str(open_session(connection, user_id))


@atomic
def end_session(connection: Connection, session_id: str | None) -> None:
    """Revoke the session server-side (RF-02), so a copy of the cookie taken
    before signing out no longer authenticates anyone."""
    parsed = _session_uuid(session_id)
    if parsed is not None:
        revoke_session(connection, parsed)


def current_principal(
    connection: Connection, session_id: str | None
) -> SessionPrincipal | None:
    """Who the session belongs to right now, or None when it is unknown,
    revoked, or its user has been deactivated (RF-09)."""
    parsed = _session_uuid(session_id)
    if parsed is None:
        return None
    return load_principal(connection, parsed)


def _session_uuid(session_id: str | None) -> UUID | None:
    if not session_id:
        return None
    try:
        return UUID(str(session_id))
    except ValueError:
        return None
