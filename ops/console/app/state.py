"""What the poller last saw, and the last hour of readiness per service."""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field

from .host import Snapshot
from .probes import UP, Probe
from .redis_stats import Instance
from .registry import Service


@dataclass(frozen=True)
class Sample:
    at: float
    ready: bool
    latency_ms: float | None


@dataclass
class Row:
    service: Service
    probe: Probe | None = None
    contract_version: str | None = None
    history: deque[Sample] = field(default_factory=deque)

    @property
    def uptime_percent(self) -> float | None:
        if not self.history:
            return None
        ready = sum(1 for sample in self.history if sample.ready)
        return round(100 * ready / len(self.history), 1)

    @property
    def healthy(self) -> bool:
        return self.probe is not None and self.probe.ready == UP


class State:
    """Written by the poller thread only; read by requests under the same lock."""

    def __init__(self, services: list[Service], history_size: int) -> None:
        self.lock = threading.Lock()
        self.rows = {
            s.name: Row(s, history=deque(maxlen=history_size)) for s in services
        }
        self.polled_at: float | None = None
        self.snapshot = Snapshot(None, True, "no snapshot yet")
        self.redis: list[Instance] = []

    def record(
        self,
        at: float,
        probes: dict[str, Probe],
        versions: dict[str, str | None],
        snapshot: Snapshot,
        redis_instances: list[Instance],
    ) -> None:
        with self.lock:
            for name, probe in probes.items():
                row = self.rows[name]
                row.probe = probe
                row.history.append(Sample(at, probe.ready == UP, probe.latency_ms))
            for name, version in versions.items():
                self.rows[name].contract_version = version
            self.polled_at = at
            self.snapshot = snapshot
            self.redis = redis_instances
