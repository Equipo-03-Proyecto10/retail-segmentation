"""The one background loop: probe, read the snapshot and Redis, record."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

import redis

from .host import read_snapshot
from .probes import contract_version, probe
from .redis_stats import inspect
from .state import State

log = logging.getLogger(__name__)

VERSION_TTL_SECONDS = 300


class Poller:
    def __init__(self, state: State, config: dict) -> None:
        self.state = state
        self.config = config
        self.http = httpx.Client(timeout=config["PROBE_TIMEOUT"])
        self.redis = {
            name: redis.Redis.from_url(
                url,
                username=config["REDIS_USERNAME"],
                password=config["REDIS_PASSWORD"] or None,
                socket_timeout=config["PROBE_TIMEOUT"],
                socket_connect_timeout=config["PROBE_TIMEOUT"],
            )
            for name, url in config["REDIS_URLS"].items()
        }
        self.versions_at: dict[str, float] = {}
        self.pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="probe")

    def poll_once(self) -> None:
        now = time.time()
        rows = list(self.state.rows.values())
        host = self.config["HOST_TEMPLATE"]
        probes = dict(
            zip(
                (r.service.name for r in rows),
                self.pool.map(lambda r: probe(self.http, r.service, host), rows),
                strict=True,
            )
        )
        due = [
            r.service
            for r in rows
            if now - self.versions_at.get(r.service.name, 0) > VERSION_TTL_SECONDS
        ]
        versions = dict(
            zip(
                (s.name for s in due),
                self.pool.map(lambda s: contract_version(self.http, s, host), due),
                strict=True,
            )
        )
        for name, version in versions.items():
            if version is not None:
                self.versions_at[name] = now
        snapshot = read_snapshot(self.config["SNAPSHOT"], now)
        redis_instances = [inspect(n, c) for n, c in self.redis.items()]
        self.state.record(now, probes, versions, snapshot, redis_instances)

    def run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                self.poll_once()
            except Exception:  # noqa: BLE001 — the loop must outlive one bad poll
                log.exception("poll failed")
            stop.wait(self.config["POLL_SECONDS"])

    def start(self) -> threading.Event:
        stop = threading.Event()
        threading.Thread(
            target=self.run, args=(stop,), name="poller", daemon=True
        ).start()
        return stop
