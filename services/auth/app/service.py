"""The authentication rule, independent of Flask transport details."""

from __future__ import annotations

import secrets
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import jwt
from mosaiq_kit import ApiError

from app.keys import SigningMaterial
from app.passwords import DUMMY_HASH, verify_password
from app.store import (
    SessionStore,
    is_expired,
    password_fingerprint,
    refresh_fingerprint,
)

AUDIENCE = "mosaiq-api"


def invalid_credentials() -> ApiError:
    return ApiError(401, "invalid_credentials", "The email or password is invalid.")


def invalid_refresh() -> ApiError:
    return ApiError(401, "invalid_refresh", "The refresh token is invalid.")


def session_revoked() -> ApiError:
    return ApiError(401, "session_revoked", "The session is no longer live.")


def session_expired() -> ApiError:
    return ApiError(401, "session_expired", "The session reached its absolute end.")


def refresh_reused() -> ApiError:
    return ApiError(
        401,
        "refresh_reused",
        "The refresh token was already used; the session was revoked.",
    )


class AuthService:
    """Authenticate credentials and own token/session transitions."""

    def __init__(
        self,
        *,
        database: Any,
        store: SessionStore,
        signing: SigningMaterial,
        issuer: str,
        access_ttl: int,
        session_ttl: int,
    ):
        self.database = database
        self.store = store
        self.signing = signing
        self.issuer = issuer
        self.access_ttl = access_ttl
        self.session_ttl = session_ttl

    def credential_by_email(self, email: str) -> dict[str, Any] | None:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT user_id::text, password_hash, is_active, role_code "
                "FROM api_users.credential_v1 WHERE email_lower = %s",
                (email.lower(),),
            ).fetchone()
        if row is None:
            return None
        return {
            "user_id": row[0],
            "password_hash": row[1],
            "is_active": row[2],
            "role_code": row[3],
        }

    def credential_by_user(self, user_id: str) -> dict[str, Any] | None:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT user_id::text, password_hash, is_active, role_code "
                "FROM api_users.credential_v1 WHERE user_id = %s",
                (user_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "user_id": row[0],
            "password_hash": row[1],
            "is_active": row[2],
            "role_code": row[3],
        }

    def credentials_by_user(self, user_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not user_ids:
            return {}
        with self.database.connection() as connection:
            rows = connection.execute(
                "SELECT user_id::text, password_hash, is_active, role_code "
                "FROM api_users.credential_v1 WHERE user_id = ANY(%s::uuid[])",
                (user_ids,),
            ).fetchall()
        return {
            row[0]: {
                "user_id": row[0],
                "password_hash": row[1],
                "is_active": row[2],
                "role_code": row[3],
            }
            for row in rows
        }

    @staticmethod
    def credential_matches_session(
        credential: dict[str, Any] | None, session: dict[str, str]
    ) -> bool:
        return bool(
            credential
            and credential["is_active"]
            and credential["role_code"] == session.get("role")
            and password_fingerprint(credential["password_hash"])
            == session.get("password_fingerprint")
        )

    def sign_in(self, email: str, password: str) -> dict[str, Any]:
        credential = self.credential_by_email(email)
        password_hash = credential["password_hash"] if credential else DUMMY_HASH
        password_valid = verify_password(password_hash, password)
        if not credential or not password_valid or not credential["is_active"]:
            raise invalid_credentials()

        now = int(time.time())
        sid = str(uuid.uuid4())
        expires_at = now + self.session_ttl
        refresh_token = secrets.token_urlsafe(32)
        self.store.create_session(
            sid=sid,
            user_id=credential["user_id"],
            role=credential["role_code"],
            expires_at=expires_at,
            password_hash=credential["password_hash"],
            refresh_hash=refresh_fingerprint(refresh_token),
        )
        return self._token_result(
            user_id=credential["user_id"],
            sid=sid,
            role=credential["role_code"],
            session_expires_at=expires_at,
            refresh_token=refresh_token,
        )

    def refresh(self, refresh_token: str) -> dict[str, Any]:
        old_hash = refresh_fingerprint(refresh_token)
        entry = self.store.get_refresh(old_hash)
        if not entry:
            raise invalid_refresh()
        sid = entry.get("sid", "")
        if entry.get("state") != "current":
            if sid:
                self.store.revoke_session(sid)
            raise refresh_reused()

        session = self.store.get_session(sid)
        if not session or is_expired(session):
            if sid:
                self.store.revoke_session(sid)
            raise session_expired()

        # This database read and every account check deliberately precede the
        # WATCH/MULTI/EXEC transition, leaving the old token current on failure.
        credential = self.credential_by_user(session["user_id"])
        if not self.credential_matches_session(credential, session):
            self.store.revoke_session(sid)
            raise session_revoked()

        new_token = secrets.token_urlsafe(32)
        outcome = self.store.rotate_refresh(
            old_hash=old_hash,
            new_hash=refresh_fingerprint(new_token),
            sid=sid,
            expires_at=int(session["expires_at"]),
        )
        if outcome == "rotated":
            self.store.revoke_session(sid)
            raise refresh_reused()
        if outcome == "missing":
            if is_expired(session):
                raise session_expired()
            raise invalid_refresh()
        return self._token_result(
            user_id=session["user_id"],
            sid=sid,
            role=session["role"],
            session_expires_at=int(session["expires_at"]),
            refresh_token=new_token,
        )

    def _token_result(
        self,
        *,
        user_id: str,
        sid: str,
        role: str,
        session_expires_at: int,
        refresh_token: str,
    ) -> dict[str, Any]:
        now = int(time.time())
        token_expires_at = min(now + self.access_ttl, session_expires_at)
        claims = {
            "iss": self.issuer,
            "aud": AUDIENCE,
            "sub": user_id,
            "sid": sid,
            "role": role,
            "jti": str(uuid.uuid4()),
            "iat": now,
            "exp": token_expires_at,
        }
        access_token = jwt.encode(
            claims,
            self.signing.private_key,
            algorithm="RS256",
            headers={"kid": self.signing.kid},
        )
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "Bearer",
            # v1's result contract fixes this advertised normal lifetime at 600;
            # the signed exp is shortened at the absolute session boundary.
            "expires_in": self.access_ttl,
            "expires_at": datetime.fromtimestamp(token_expires_at, UTC),
        }
