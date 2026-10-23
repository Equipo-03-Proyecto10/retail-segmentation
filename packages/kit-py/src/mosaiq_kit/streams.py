"""Transactional outbox relay and Redis Stream consumer helpers."""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

import psycopg
import redis
from psycopg import sql

LOGGER = logging.getLogger(__name__)


def _timestamp(value: datetime) -> str:
    utc = value.astimezone(UTC)
    timespec = "microseconds" if utc.microsecond else "seconds"
    return utc.isoformat(timespec=timespec).replace("+00:00", "Z")


class OutboxRelay:
    """Relay one service boundary's outbox to its configured streams."""

    def __init__(
        self,
        dsn: str,
        suffix: str,
        streams: Iterable[str],
        redis: redis.Redis,
    ):
        self.dsn = dsn
        self.suffix = suffix
        self.streams = frozenset(streams)
        self.redis = redis
        self._connection: psycopg.Connection | None = None
        # None purges on the first pass; monotonic time starts near zero at boot.
        self._last_purge: float | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def _schema(self) -> sql.Identifier:
        return sql.Identifier(self.suffix + "_events")

    def _connect(self) -> psycopg.Connection:
        if self._connection is None or self._connection.closed:
            self._connection = psycopg.connect(self.dsn)
            self._connection.execute(
                sql.SQL("LISTEN {}").format(sql.Identifier(self.suffix + "_outbox"))
            )
            self._connection.commit()
        return self._connection

    def run_once(self, wait_seconds: float = 0) -> int:
        """Wait for a wake-up, poll once, and return the relayed row count."""

        connection = self._connect()
        if wait_seconds > 0:
            next(connection.notifies(timeout=min(wait_seconds, 5), stop_after=1), None)
        relayed = 0
        try:
            with connection.transaction():
                rows = connection.execute(
                    sql.SQL(
                        "SELECT event_id, stream, event_type, entity_id, occurred_at "
                        "FROM {}.outbox WHERE relayed_at IS NULL "
                        "ORDER BY occurred_at LIMIT %s FOR UPDATE SKIP LOCKED"
                    ).format(self._schema),
                    (100,),
                ).fetchall()
                event_ids = []
                for event_id, stream_name, event_type, entity_id, occurred_at in rows:
                    if stream_name not in self.streams:
                        LOGGER.error(
                            "Outbox row has unconfigured stream %r", stream_name
                        )
                        continue
                    self.redis.xadd(
                        stream_name,
                        {
                            "event_id": str(event_id),
                            "v": 1,
                            "event_type": event_type,
                            "entity_id": entity_id,
                            "occurred_at": _timestamp(occurred_at),
                        },
                        maxlen=10_000,
                        approximate=True,
                    )
                    event_ids.append(event_id)
                if event_ids:
                    durability = self.redis.execute_command("WAITAOF", 1, 0, 2000)
                    if not durability or int(durability[0]) < 1:
                        raise redis.RedisError(
                            "The local AOF did not confirm the event batch."
                        )
                    connection.execute(
                        sql.SQL(
                            "UPDATE {}.outbox SET relayed_at = now() "
                            "WHERE event_id = ANY(%s)"
                        ).format(self._schema),
                        (event_ids,),
                    )
                    relayed = len(event_ids)
        except redis.RedisError:
            LOGGER.exception("Outbox delivery failed; rows remain unrelayed")
            return 0
        if self._last_purge is None or time.monotonic() - self._last_purge >= 3600:
            with connection.transaction():
                connection.execute(
                    sql.SQL(
                        "DELETE FROM {}.outbox WHERE relayed_at IS NOT NULL "
                        "AND relayed_at < now() - (%s * interval '1 day')"
                    ).format(self._schema),
                    (7,),
                )
            self._last_purge = time.monotonic()
        return relayed

    def run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once(wait_seconds=5)
            except Exception:
                LOGGER.exception("Outbox relay pass failed")
                if self._connection is not None:
                    try:
                        self._connection.close()
                    except Exception:
                        LOGGER.exception("Outbox connection cleanup failed")
                self._connection = None
                self._stop.wait(1)

    def start(self) -> threading.Thread:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(
                target=self.run_forever,
                name=self.suffix + "-outbox",
                daemon=True,
            )
            self._thread.start()
        return self._thread

    def stop(self) -> None:
        self._stop.set()
        if self._connection is not None:
            self._connection.close()


class StreamConsumer:
    """Consume one stream as the current service's own group."""

    def __init__(
        self,
        redis: redis.Redis,
        stream: str,
        handler: Callable[[dict[str, str]], None],
        *,
        handled_capacity: int = 10_000,
    ):
        group = os.environ.get("MOSAIQ_SERVICE_NAME")
        if not group:
            raise ValueError("MOSAIQ_SERVICE_NAME is required for a stream consumer.")
        self.redis = redis
        self.stream = stream
        self.handler = handler
        self.group = group
        self.consumer = socket.gethostname()
        self.handled_capacity = handled_capacity
        self._handled: OrderedDict[str, None] = OrderedDict()
        self._group_ready = False
        self._started = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _ensure_group(self) -> None:
        if self._group_ready:
            return
        try:
            self.redis.xgroup_create(self.stream, self.group, id="$", mkstream=True)
        except redis.ResponseError as error:
            if "BUSYGROUP" not in str(error):
                raise
        self._group_ready = True

    @staticmethod
    def _decode(value: Any) -> Any:
        if isinstance(value, bytes):
            return value.decode()
        if isinstance(value, dict):
            return {
                StreamConsumer._decode(key): StreamConsumer._decode(item)
                for key, item in value.items()
            }
        return value

    def _delivery_count(self, message_id: str) -> int:
        pending = self.redis.xpending_range(
            self.stream,
            self.group,
            min=message_id,
            max=message_id,
            count=1,
        )
        if not pending:
            return 1
        return int(pending[0]["times_delivered"])

    def _remember(self, event_id: str) -> None:
        self._handled[event_id] = None
        self._handled.move_to_end(event_id)
        while len(self._handled) > self.handled_capacity:
            self._handled.popitem(last=False)

    def _handle_entries(self, entries: list[tuple[Any, Any]]) -> int:
        processed = 0
        for raw_id, raw_fields in entries:
            message_id = str(self._decode(raw_id))
            if raw_fields is None:
                self.redis.xack(self.stream, self.group, message_id)
                processed += 1
                continue
            fields = self._decode(raw_fields)
            event_id = str(fields.get("event_id", ""))
            if event_id and event_id in self._handled:
                self.redis.xack(self.stream, self.group, message_id)
                processed += 1
                continue
            try:
                self.handler(fields)
            except Exception:
                deliveries = self._delivery_count(message_id)
                if deliveries >= 5:
                    LOGGER.exception(
                        "Stream entry exhausted five deliveries: %r", fields
                    )
                    self.redis.xack(self.stream, self.group, message_id)
                    processed += 1
                else:
                    LOGGER.warning(
                        "Stream handler failed on delivery %d for entry %r",
                        deliveries,
                        message_id,
                    )
                continue
            if event_id:
                self._remember(event_id)
            self.redis.xack(self.stream, self.group, message_id)
            processed += 1
        return processed

    def _read(self, marker: str, block: int | None = None) -> list[tuple[Any, Any]]:
        response = self.redis.xreadgroup(
            self.group,
            self.consumer,
            {self.stream: marker},
            count=100,
            block=block,
        )
        return response[0][1] if response else []

    def _run_pass(self, block_ms: int | None) -> int:
        processed = 0
        if not self._started:
            processed += self._handle_entries(self._read("0"))
            self._started = True
        pending = self.redis.xpending_range(
            self.stream,
            self.group,
            min="-",
            max="+",
            count=100,
            idle=60_000,
        )
        ids = [item["message_id"] for item in pending]
        if ids:
            claimed = self.redis.xclaim(
                self.stream,
                self.group,
                self.consumer,
                min_idle_time=60_000,
                message_ids=ids,
            )
            processed += self._handle_entries(claimed)
        processed += self._handle_entries(self._read(">", block_ms))
        return processed

    def run_once(self, block_ms: int | None = None) -> int:
        """Recover pending work in order, then read new entries."""

        self._ensure_group()
        try:
            return self._run_pass(block_ms)
        except redis.ResponseError as error:
            if "NOGROUP" not in str(error):
                raise
            self._group_ready = False
            self._started = False
            self._ensure_group()
            return self._run_pass(block_ms)

    def run_forever(self) -> None:
        """Run passes until stopped.

        A lost Redis connection is an expected outage, not a code fault: it logs
        one warning, backs off from one second doubling up to 30 s, and logs one
        line when a pass succeeds again. Any other exception keeps its traceback
        and the one-second wait.
        """

        delay = 1.0
        outage = False
        while not self._stop.is_set():
            try:
                self.run_once(block_ms=5000)
            except (redis.ConnectionError, redis.TimeoutError) as error:
                if not outage:
                    LOGGER.warning(
                        "Stream consumer lost Redis (%s); retrying with backoff",
                        error,
                    )
                    outage = True
                self._stop.wait(delay)
                delay = min(delay * 2, 30.0)
            except Exception:
                LOGGER.exception("Stream consumer pass failed")
                self._stop.wait(1)
            else:
                if outage:
                    LOGGER.info("Stream consumer reconnected to Redis")
                    outage = False
                delay = 1.0

    def start(self) -> threading.Thread:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(
                target=self.run_forever,
                name=self.group + "-consumer",
                daemon=True,
            )
            self._thread.start()
        return self._thread

    def stop(self) -> None:
        self._stop.set()
