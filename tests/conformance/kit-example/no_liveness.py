"""The kit-py example with the session liveness check switched off.

CI runs the conformance suite against this build and requires the redis-state
case to fail, so the suite is shown not to pass vacuously (F13-09). Every
`EXISTS` on the liveness key answers "live" without reaching Redis; everything
else still goes to redis-state.
"""

from __future__ import annotations

from typing import Any

import mosaiq_kit.auth

_initialize = mosaiq_kit.auth.Authenticator.__init__


class _AlwaysLive:
    def __init__(self, client: Any):
        self._client = client

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    def exists(self, *names: str) -> int:
        return len(names)


def _without_liveness(self: Any, suffix: str) -> None:
    _initialize(self, suffix)
    self.redis = _AlwaysLive(self.redis)


mosaiq_kit.auth.Authenticator.__init__ = _without_liveness

from app import app  # noqa: E402

__all__ = ["app"]
