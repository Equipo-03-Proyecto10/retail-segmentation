"""Environment switches and settings of products-service (ADR-0042 rule 2)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

CHANGED_STREAM = "stream:products.v1.changed"
CACHE_TTL_SECONDS = 600
MAX_BATCH_IDS = 50


def _flag(name: str, default: bool = True) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    """Read once at start. Each switch defaults to true."""

    cache_enabled: bool
    stream_consumer_enabled: bool
    relay_enabled: bool
    database_url: str | None
    redis_state_url: str
    redis_cache_url: str
    contracts_dir: Path

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            cache_enabled=_flag("CACHE_ENABLED"),
            stream_consumer_enabled=_flag("STREAM_CONSUMER_ENABLED"),
            relay_enabled=_flag("OUTBOX_RELAY_ENABLED"),
            database_url=os.environ.get("DATABASE_URL"),
            redis_state_url=os.environ.get(
                "REDIS_STATE_URL", "redis://redis-state:6379/0"
            ),
            redis_cache_url=os.environ.get(
                "REDIS_CACHE_URL", "redis://redis-cache:6379/0"
            ),
            contracts_dir=Path(
                os.environ.get(
                    "MOSAIQ_CONTRACTS_DIR",
                    str(Path(__file__).resolve().parent.parent / "contracts"),
                )
            ),
        )
