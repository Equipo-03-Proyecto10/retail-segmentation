"""Access-change stream consumption and the database reconciliation backstop."""

from __future__ import annotations

import logging
import threading
from typing import Any

from mosaiq_kit import ApiError, StreamConsumer

from app.service import AuthService

LOGGER = logging.getLogger(__name__)
ACCESS_CHANGED_STREAM = "stream:users.v1.access_changed"


class Reconciler:
    """Compare all indexed live sessions with the published credential view."""

    def __init__(self, service: AuthService, interval_seconds: float = 60):
        self.service = service
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def run_once(self) -> int:
        try:
            sessions = self.service.store.session_snapshot()
            users = sorted(
                {session["user_id"] for session in sessions.values() if session}
            )
            # Fetch the complete comparison set before deleting anything. A
            # failed pass therefore cannot partially revoke sessions.
            credentials = self.service.credentials_by_user(users)
        except ApiError as error:
            LOGGER.warning("Session reconcile dependency unavailable: %s", error.code)
            return 0
        revoked = [
            sid
            for sid, session in sessions.items()
            if not self.service.credential_matches_session(
                credentials.get(session["user_id"]), session
            )
        ]
        if not revoked:
            return 0
        try:
            self.service.store.revoke_sessions(revoked)
        except ApiError as error:
            LOGGER.warning("Session reconcile revocation failed: %s", error.code)
            return 0
        return len(revoked)

    def run_forever(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                self.run_once()
            except Exception:
                LOGGER.exception("Session reconcile pass failed")

    def start(self) -> threading.Thread:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(
                target=self.run_forever,
                name="auth-reconcile",
                daemon=True,
            )
            self._thread.start()
        return self._thread

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)


class BackgroundWorkers:
    """Start and stop auth's stream consumer and reconcile thread together."""

    def __init__(self, service: AuthService, stream_redis: Any, interval: float = 60):
        self.service = service
        self.consumer = StreamConsumer(
            stream_redis,
            ACCESS_CHANGED_STREAM,
            self._access_changed,
        )
        self.reconciler = Reconciler(service, interval)

    def _access_changed(self, fields: dict[str, str]) -> None:
        user_id = fields.get("entity_id")
        if not user_id:
            return
        # revoke_user performs WAITAOF before returning. StreamConsumer XACKs
        # only after this handler returns successfully.
        self.service.store.revoke_user(user_id)

    def start(self) -> None:
        self.consumer.start()
        self.reconciler.start()

    def stop(self) -> None:
        self.consumer.stop()
        if self.consumer._thread is not None:
            self.consumer._thread.join(timeout=6)
        self.reconciler.stop()
