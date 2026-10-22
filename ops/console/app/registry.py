"""The services to supervise, read from a copy of contracts/registry.yaml."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Service:
    name: str
    port: int
    wave: str
    rule: str
    rn: tuple[str, ...]


def load_registry(path: Path) -> list[Service]:
    """Every registered service, in registry order."""
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [
        Service(
            name=entry["name"],
            port=int(entry["port"]),
            wave=entry.get("wave", ""),
            rule=entry.get("rule", ""),
            rn=tuple(entry.get("rn", ())),
        )
        for entry in document["services"]
    ]
