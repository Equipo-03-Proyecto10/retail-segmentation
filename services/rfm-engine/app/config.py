"""Settings of rfm-engine (ADR-0042 rule 2)."""

from __future__ import annotations

import os
from dataclasses import dataclass

# The streams that name a customer to recompute (ADR-0063). A recorded sale's
# `entity_id` is the sale; a reversal's is `sale_id:reversal_id` (ADR-0062, F21-02).
RECORDED_STREAM = "stream:sales.v1.recorded"
REVERSED_STREAM = "stream:sales.v1.reversed"


def _flag(name: str, default: bool = True) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    """Read once at start. Each switch defaults to true."""

    stream_consumer_enabled: bool
    outbox_relay_enabled: bool
    # The rule set a sale is scored under. Until model-registry publishes the
    # version that names one (F22-13, api_models.published_version_v1), the one
    # rule set the schema seeds is in force: the web's published rule version.
    rule_set_id: int
    redis_state_url: str

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            stream_consumer_enabled=_flag("STREAM_CONSUMER_ENABLED"),
            outbox_relay_enabled=_flag("OUTBOX_RELAY_ENABLED"),
            rule_set_id=int(os.environ.get("RFM_RULE_SET_ID", "1")),
            redis_state_url=os.environ.get(
                "REDIS_STATE_URL", "redis://redis-state:6379/0"
            ),
        )
