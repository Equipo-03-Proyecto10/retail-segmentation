"""Environment settings for experimentation's engagement consumer."""

from __future__ import annotations

import os
from dataclasses import dataclass

ENGAGEMENT_STREAM = "stream:engagement.v1.interaction_recorded"


def _flag(name: str, default: bool = True) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    """Read once at startup; the stream consumer is enabled by default."""

    stream_consumer_enabled: bool
    redis_state_url: str

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            stream_consumer_enabled=_flag("STREAM_CONSUMER_ENABLED"),
            redis_state_url=os.environ.get(
                "REDIS_STATE_URL", "redis://redis-state:6379/0"
            ),
        )
