"""argon2id hashing, the same algorithm and parameters the web verifies with.

`PasswordHasher()`'s defaults are what web/services/auth.py uses, so a hash
written here signs in through the web and through auth-service alike. Each hash
takes 64 MiB; the container has 224 MiB and four request threads, so hashing
and verifying run one at a time.
"""

from __future__ import annotations

import threading

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()
_lock = threading.Lock()


def hash_password(password: str) -> str:
    with _lock:
        return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    with _lock:
        try:
            return _hasher.verify(password_hash, password)
        except (VerificationError, InvalidHashError):
            return False
