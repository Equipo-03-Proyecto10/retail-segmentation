"""The container snapshot a host timer writes (host/mosaiq-ops-snapshot.sh).

The console never sees the Docker socket (ADR-0055): the host runs `docker ps`
and `docker stats` and writes one JSON document, which is mounted read-only.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

STALE_AFTER_SECONDS = 60


@dataclass(frozen=True)
class Container:
    state: str
    status: str
    image: str
    restarts: int
    cpu_percent: float | None
    mem_used: str | None
    mem_limit: str | None
    mem_percent: float | None


@dataclass(frozen=True)
class Snapshot:
    taken_at: float | None
    stale: bool
    error: str | None
    containers: dict[str, Container] = field(default_factory=dict)


def _percent(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value.rstrip("%"))
    except ValueError:
        return None


def _project(labels: str, name: str) -> str:
    """The Compose project, `mosaiq-<service>`, from the label list or the name."""
    for label in labels.split(","):
        key, _, value = label.partition("=")
        if key == "com.docker.compose.project":
            return value
    return name.rsplit("-", 1)[0]


def read_snapshot(path: Path, now: float | None = None) -> Snapshot:
    """Containers keyed by Compose project; a missing or old file is stale."""
    now = time.time() if now is None else now
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Snapshot(None, True, "no snapshot yet")
    except (OSError, ValueError) as exc:
        return Snapshot(None, True, f"unreadable snapshot: {type(exc).__name__}")

    taken_at = float(document.get("taken_at", 0))
    stats = {row.get("Name"): row for row in document.get("stats", [])}
    restarts = document.get("restarts", {})
    containers: dict[str, Container] = {}
    for row in document.get("ps", []):
        name = row.get("Names", "")
        stat = stats.get(name, {})
        used, _, limit = (stat.get("MemUsage") or "").partition(" / ")
        containers[_project(row.get("Labels", ""), name)] = Container(
            state=row.get("State", ""),
            status=row.get("Status", ""),
            image=row.get("Image", ""),
            restarts=int(restarts.get(name, 0)),
            cpu_percent=_percent(stat.get("CPUPerc")),
            mem_used=used or None,
            mem_limit=limit or None,
            mem_percent=_percent(stat.get("MemPerc")),
        )
    return Snapshot(taken_at, now - taken_at > STALE_AFTER_SECONDS, None, containers)
