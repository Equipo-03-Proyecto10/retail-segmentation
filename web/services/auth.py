"""Authentication service: password verification and login decisions.

Routes call these functions; they never touch password hashing or the
database directly.

Uses argon2-cffi for password hashing, per web/requirements.txt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from psycopg import Connection

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
