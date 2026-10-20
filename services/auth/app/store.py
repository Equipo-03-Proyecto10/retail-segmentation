"""Redis-owned session state, refresh rotation and scan-free revocation."""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Iterable
from typing import Any

import redis
from mosaiq_kit import ApiError

LOGGER = logging.getLogger(__name__)
ACTIVE_USERS_KEY = "auth:session_users"
# Refresh entries and their family outlive the session by this much, so a
# refresh presented after the absolute end still finds its entry and answers
# session_expired rather than invalid_refresh. The session hash and the
# liveness key still end exactly at the absolute end.
EXPIRED_REFRESH_GRACE_SECONDS = 86_400


def refresh_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def password_fingerprint(password_hash: str) -> str:
    return hashlib.sha256(password_hash.encode("utf-8")).hexdigest()


def auth_unavailable() -> ApiError:
    return ApiError(
        503, "auth_unavailable", "Session state is temporarily unavailable."
    )


class SessionStore:
    """Own the private Redis representation of auth sessions."""

    def __init__(self, client: redis.Redis):
        self.redis = client

    @staticmethod
    def session_key(sid: str) -> str:
        return "auth:session:" + sid

    @staticmethod
    def user_key(user_id: str) -> str:
        return "auth:user_sids:" + user_id

    @staticmethod
    def refresh_key(fingerprint: str) -> str:
        return "auth:refresh:" + fingerprint

    @staticmethod
    def family_key(sid: str) -> str:
        return "auth:refresh_family:" + sid

    @staticmethod
    def live_key(sid: str) -> str:
        return "pub:auth:v1:live:" + sid

    def create_session(
        self,
        *,
        sid: str,
        user_id: str,
        role: str,
        expires_at: int,
        password_hash: str,
        refresh_hash: str,
    ) -> None:
        """Create all session keys atomically with one absolute expiry."""

        try:
            pipe = self.redis.pipeline(transaction=True)
            pipe.hset(
                self.session_key(sid),
                mapping={
                    "user_id": user_id,
                    "role": role,
                    "expires_at": str(expires_at),
                    "password_fingerprint": password_fingerprint(password_hash),
                },
            )
            pipe.expireat(self.session_key(sid), expires_at)
            pipe.sadd(self.user_key(user_id), sid)
            pipe.expireat(self.user_key(user_id), expires_at)
            pipe.hset(
                self.refresh_key(refresh_hash),
                mapping={"sid": sid, "state": "current"},
            )
            pipe.expireat(
                self.refresh_key(refresh_hash),
                expires_at + EXPIRED_REFRESH_GRACE_SECONDS,
            )
            pipe.sadd(self.family_key(sid), refresh_hash)
            pipe.expireat(
                self.family_key(sid), expires_at + EXPIRED_REFRESH_GRACE_SECONDS
            )
            pipe.set(self.live_key(sid), "1")
            pipe.expireat(self.live_key(sid), expires_at)
            pipe.sadd(ACTIVE_USERS_KEY, user_id)
            pipe.expireat(ACTIVE_USERS_KEY, expires_at)
            pipe.execute()
        except redis.RedisError as error:
            LOGGER.warning("Session creation storage failed: %r", error)
            raise auth_unavailable() from None

    def get_refresh(self, token_hash: str) -> dict[str, str]:
        try:
            return self.redis.hgetall(self.refresh_key(token_hash))
        except redis.RedisError:
            raise auth_unavailable() from None

    def get_session(self, sid: str) -> dict[str, str]:
        try:
            return self.redis.hgetall(self.session_key(sid))
        except redis.RedisError:
            raise auth_unavailable() from None

    def rotate_refresh(
        self,
        *,
        old_hash: str,
        new_hash: str,
        sid: str,
        expires_at: int,
    ) -> str:
        """Atomically rotate, returning ``rotated`` if another caller won."""

        key = self.refresh_key(old_hash)
        while True:
            try:
                with self.redis.pipeline() as pipe:
                    pipe.watch(key)
                    current = pipe.hgetall(key)
                    if not current:
                        pipe.unwatch()
                        return "missing"
                    if current.get("state") != "current":
                        pipe.unwatch()
                        return "rotated"
                    if current.get("sid") != sid:
                        pipe.unwatch()
                        return "missing"
                    pipe.multi()
                    pipe.hset(key, "state", "rotated")
                    pipe.hset(
                        self.refresh_key(new_hash),
                        mapping={"sid": sid, "state": "current"},
                    )
                    pipe.expireat(
                        self.refresh_key(new_hash),
                        expires_at + EXPIRED_REFRESH_GRACE_SECONDS,
                    )
                    pipe.sadd(self.family_key(sid), new_hash)
                    pipe.expireat(
                        self.family_key(sid),
                        expires_at + EXPIRED_REFRESH_GRACE_SECONDS,
                    )
                    pipe.execute()
                    return "success"
            except redis.WatchError:
                continue
            except redis.RedisError:
                raise auth_unavailable() from None

    @staticmethod
    def _require_aof(result: Any) -> None:
        local = result[0] if isinstance(result, list | tuple) and result else result
        if int(local or 0) < 1:
            LOGGER.warning("WAITAOF did not confirm the local append: %r", result)
            raise auth_unavailable()

    def revoke_session(self, sid: str) -> None:
        self.revoke_sessions([sid])

    def revoke_sessions(
        self, sids: Iterable[str], *, delete_user_sets: Iterable[str] = ()
    ) -> None:
        """Delete sessions and their indexed refresh families, never scanning."""

        unique_sids = tuple(dict.fromkeys(str(sid) for sid in sids))
        users_to_delete = tuple(dict.fromkeys(str(user) for user in delete_user_sets))
        if not unique_sids and not users_to_delete:
            return
        try:
            sessions = {
                sid: self.redis.hgetall(self.session_key(sid)) for sid in unique_sids
            }
            families = {
                sid: self.redis.smembers(self.family_key(sid)) for sid in unique_sids
            }
            # WAITAOF confirms writes made by the same connection. Keeping it
            # as the last command in this non-transactional pipeline prevents
            # the pool from handing the durability check a different socket.
            pipe = self.redis.pipeline(transaction=False)
            for sid in unique_sids:
                refresh_keys = [self.refresh_key(value) for value in families[sid]]
                pipe.delete(
                    self.live_key(sid),
                    self.session_key(sid),
                    self.family_key(sid),
                    *refresh_keys,
                )
                user_id = sessions[sid].get("user_id")
                if user_id:
                    pipe.srem(self.user_key(user_id), sid)
            for user_id in users_to_delete:
                pipe.delete(self.user_key(user_id))
                pipe.srem(ACTIVE_USERS_KEY, user_id)
            pipe.execute_command("WAITAOF", 1, 0, 1000)
            # appendfsync=everysec can cross the exact one-second timeout
            # boundary. A second bounded wait on the same socket accepts only
            # a real durability confirmation; two timeouts still fail closed.
            pipe.execute_command("WAITAOF", 1, 0, 1000)
            results = pipe.execute()
            self._require_aof(results[-1])
        except ApiError:
            raise
        except redis.RedisError as error:
            LOGGER.warning("Session revocation storage failed: %r", error)
            raise auth_unavailable() from None

    def revoke_user(self, user_id: str) -> None:
        self.revoke_users([user_id])

    def revoke_users(self, user_ids: Iterable[str]) -> None:
        """End every session of these users behind one durability wait."""

        users = tuple(dict.fromkeys(str(user) for user in user_ids))
        if not users:
            return
        try:
            sids = [
                sid
                for user in users
                for sid in self.redis.smembers(self.user_key(user))
            ]
        except redis.RedisError:
            raise auth_unavailable() from None
        self.revoke_sessions(sids, delete_user_sets=users)

    def session_snapshot(self) -> dict[str, dict[str, str]]:
        """Read indexed live sessions for reconciliation without Redis SCAN."""

        try:
            users = self.redis.smembers(ACTIVE_USERS_KEY)
            result: dict[str, dict[str, str]] = {}
            for user_id in users:
                for sid in self.redis.smembers(self.user_key(user_id)):
                    session = self.redis.hgetall(self.session_key(sid))
                    if session:
                        result[sid] = session
            return result
        except redis.RedisError:
            raise auth_unavailable() from None

    def liveness_ttl(self, sid: str) -> int:
        try:
            return int(self.redis.ttl(self.live_key(sid)))
        except redis.RedisError:
            raise auth_unavailable() from None


def is_expired(session: dict[str, Any], now: int | None = None) -> bool:
    try:
        expires_at = int(session["expires_at"])
    except (KeyError, TypeError, ValueError):
        return True
    return expires_at <= (int(time.time()) if now is None else now)
