"""Bounded argon2id verification compatible with the frozen web application."""

from __future__ import annotations

import threading

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

# The seed hash is a valid argon2id value made with PasswordHasher defaults. It
# is public test data, not a credential, and gives an unknown email exactly the
# same expensive operation as a known email.
DUMMY_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$BTg0ZDnIiX0UXEv10Pa1Pw$"
    "kPMnEtE7i3rQKZhkDCbDiN3R6FEKkEmsN2CKncGfd1c"
)

_hasher = PasswordHasher()
_verification_slots = threading.BoundedSemaphore(2)


def verify_password(password_hash: str, password: str) -> bool:
    """Verify one password while allowing at most two memory-heavy checks."""

    with _verification_slots:
        try:
            return _hasher.verify(password_hash, password)
        except (VerificationError, InvalidHashError):
            return False
