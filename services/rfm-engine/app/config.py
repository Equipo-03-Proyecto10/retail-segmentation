"""Settings of rfm-engine (ADR-0042 rule 2)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import time

# The consent stream names a customer whose PROFILING decision may have changed
# (ADR-0066 section 2): its `entity_id` is the customer.
CONSENT_STREAM = "stream:preferences.v1.consent_changed"
# The streams that name a customer to recompute (ADR-0063). A recorded sale's
# `entity_id` is the sale; a reversal's is `sale_id:reversal_id` (ADR-0062, F21-02).
RECORDED_STREAM = "stream:sales.v1.recorded"
REVERSED_STREAM = "stream:sales.v1.reversed"


def _flag(name: str, default: bool = True) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _clock(name: str, default: time) -> time:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return time.fromisoformat(value.strip())
    except ValueError:
        raise ValueError(
            f"{name} must be a time such as 02:00, got {value!r}"
        ) from None


def _weekday(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        day = int(value)
    except ValueError:
        day = -1
    if not 0 <= day <= 6:
        raise ValueError(f"{name} must be 0 (Monday) to 6 (Sunday), got {value!r}")
    return day


@dataclass(frozen=True, slots=True)
class Settings:
    """Read once at start. Each switch defaults to true."""

    stream_consumer_enabled: bool
    outbox_relay_enabled: bool
    # The daily pass of customers whose window or recency changed (F22-07), run at
    # this UTC time. 02:00 by default, off the shop's hours (ADR-0063).
    pass_enabled: bool
    pass_at: time
    # The verification recalculation (F22-08): weekly by default, on this weekday
    # (0 is Monday) at this UTC time, an hour after the daily pass (ADR-0063).
    verification_enabled: bool
    verification_weekday: int
    verification_at: time
    # The rule set in force when the published version names none: the version the
    # seed publishes was derived from a web run, not from a rule set (F22-15).
    rule_set_id: int
    redis_state_url: str

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            stream_consumer_enabled=_flag("STREAM_CONSUMER_ENABLED"),
            outbox_relay_enabled=_flag("OUTBOX_RELAY_ENABLED"),
            pass_enabled=_flag("DAILY_PASS_ENABLED"),
            pass_at=_clock("DAILY_PASS_AT_UTC", time(2, 0)),
            verification_enabled=_flag("VERIFICATION_ENABLED"),
            verification_weekday=_weekday("VERIFICATION_WEEKDAY", 6),
            verification_at=_clock("VERIFICATION_AT_UTC", time(3, 0)),
            rule_set_id=int(os.environ.get("RFM_RULE_SET_ID", "1")),
            redis_state_url=os.environ.get(
                "REDIS_STATE_URL", "redis://redis-state:6379/0"
            ),
        )
