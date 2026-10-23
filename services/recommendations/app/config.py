"""Settings of recommendations-service (ADR-0042 rule 2)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# A change in any of these boundaries can change an answer
# (docs/services/recommendations.md).
STREAMS = (
    "stream:products.v1.changed",
    "stream:categories.v1.changed",
    "stream:stock.v1.changed",
    "stream:preferences.v1.changed",
    "stream:sales.v1.recorded",
    "stream:segments.v1.run_recorded",
)
CACHE_TTL_SECONDS = 120
DEFAULT_LIMIT = 10
MAX_LIMIT = 50
# The consumption profile's window (RN-34), closed at both ends.
WINDOW_DAYS = 180


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
    redis_state_url: str
    redis_cache_url: str
    contracts_dir: Path

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            cache_enabled=_flag("CACHE_ENABLED"),
            stream_consumer_enabled=_flag("STREAM_CONSUMER_ENABLED"),
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
