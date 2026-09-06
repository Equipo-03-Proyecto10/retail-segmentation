"""Authentication service: password verification and login decisions.

Routes call these functions; they never touch password hashing or the
database directly.

Uses argon2-cffi for password hashing, per web/requirements.txt.
"""

from __future__ import annotations

from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from psycopg import Connection

from web.db.users import AppUser, get_user_by_email

_hasher = PasswordHasher()


@dataclass(frozen=True)
class LoginResult:
    success: bool
    user: AppUser | None = None


def hash_password(plain_password: str) -> str:
    """Return a secure Argon2 hash for storing a new or updated password."""
    return _hasher.hash(plain_password)


def authenticate(connection: Connection, email: str, plain_password: str) -> LoginResult:
    """Verify credentials without revealing which field was wrong.

    Returns a LoginResult with success=False for: unknown email, wrong
    password, or a deactivated account. The caller shows the same generic
    message in every failure case.
    """
    user = get_user_by_email(connection, email)

    if user is None or not user.is_active:
        return LoginResult(success=False)

    try:
        _hasher.verify(user.password_hash, plain_password)
    except VerifyMismatchError:
        return LoginResult(success=False)

    return LoginResult(success=True, user=user)