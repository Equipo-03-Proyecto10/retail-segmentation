"""Access-change stream consumption and the database reconciliation backstop."""

from __future__ import annotations

import logging
import threading
from typing import Any

from mosaiq_kit import ApiError, StreamConsumer

from app.service import AuthService
from app.store import SessionStore

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


class AccessChangedConsumer(StreamConsumer):
    """Revoke a whole read of access changes behind one durability wait.

    The kit hands entries to a handler one at a time, and every revocation
    waits for the append-only file, up to a second under appendfsync everysec,
    so a burst of N changes would take about N seconds. Here the entries of one
    XREADGROUP read are revoked together and acknowledged after a single wait.
    Duplicates by event_id and the five-delivery rule keep the kit's meaning.
    """

    def __init__(self, redis: Any, store: SessionStore):
        super().__init__(redis, ACCESS_CHANGED_STREAM, self._one)
        self.store = store

    def _one(self, fields: dict[str, str]) -> None:
        self.store.revoke_users(
            [fields["entity_id"]] if fields.get("entity_id") else []
        )

    def _handle_entries(self, entries: list[tuple[Any, Any]]) -> int:
        acknowledged: list[str] = []
        batch: list[tuple[str, str]] = []
        users: list[str] = []
        seen: set[str] = set()
        for raw_id, raw_fields in entries:
            message_id = str(self._decode(raw_id))
            if raw_fields is None:
                acknowledged.append(message_id)
                continue
            fields = self._decode(raw_fields)
            event_id = str(fields.get("event_id", ""))
            if event_id and (event_id in self._handled or event_id in seen):
                acknowledged.append(message_id)
                continue
            if event_id:
                seen.add(event_id)
            batch.append((message_id, event_id))
            if fields.get("entity_id"):
                users.append(str(fields["entity_id"]))
        if batch:
            try:
                # WAITAOF runs inside, so the XACK below follows durability.
                self.store.revoke_users(users)
            except Exception:
                for message_id, _event_id in batch:
                    deliveries = self._delivery_count(message_id)
                    if deliveries >= 5:
                        LOGGER.exception(
                            "Access change %s exhausted five deliveries", message_id
                        )
                        acknowledged.append(message_id)
                    else:
                        LOGGER.warning(
                            "Access change %s failed on delivery %d",
                            message_id,
                            deliveries,
                        )
            else:
                for message_id, event_id in batch:
                    if event_id:
                        self._remember(event_id)
                    acknowledged.append(message_id)
        if acknowledged:
            self.redis.xack(self.stream, self.group, *acknowledged)
        return len(acknowledged)


class BackgroundWorkers:
    """Start and stop auth's stream consumer and reconcile thread together."""

    def __init__(self, service: AuthService, stream_redis: Any, interval: float = 60):
        self.service = service
        self.consumer = AccessChangedConsumer(stream_redis, service.store)
        self.reconciler = Reconciler(service, interval)

    def start(self) -> None:
        self.consumer.start()
        self.reconciler.start()

    def stop(self) -> None:
        self.consumer.stop()
        if self.consumer._thread is not None:
            self.consumer._thread.join(timeout=6)
        self.reconciler.stop()
