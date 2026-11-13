"""The service's own retention job (ADR-0067, F24-02): decisions older than
RETENTION_DAYS are deleted, with everything they hold, once an hour.

Each worker runs the loop, which is harmless: the delete is by age, so two
runs at once delete the same rows once. A failed run is logged and the next
one tries again; it never reaches a request.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime

from flask import Flask
from mosaiq_kit import Kit

from . import decision_store
from .config import RETENTION_INTERVAL_SECONDS

LOGGER = logging.getLogger(__name__)


def run_once(kit: Kit, now: datetime | None = None) -> int:
    """Delete what is past retention and say how many decisions went."""
    with kit.db.connection() as connection:
        return decision_store.purge_expired(connection, now or datetime.now(UTC))


def start(app: Flask, kit: Kit, interval: float = RETENTION_INTERVAL_SECONDS) -> None:
    """Run the job now and then every `interval` seconds, on a daemon thread
    that `app.extensions["recommendations_retention"].set()` stops."""
    stop = threading.Event()

    def loop() -> None:
        while not stop.is_set():
            try:
                deleted = run_once(kit)
                if deleted:
                    LOGGER.info("Retention deleted %d decisions", deleted)
            except Exception:  # noqa: BLE001 - the job must outlive one failure
                LOGGER.exception("Retention run failed")
            stop.wait(interval)

    threading.Thread(target=loop, name="recommendations-retention", daemon=True).start()
    app.extensions["recommendations_retention"] = stop
