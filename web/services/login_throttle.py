"""Bounded login-attempt throttling for the single application process.

The application runs as one monolith, but Gunicorn may have more than one
worker. NGINX therefore enforces the client-IP limit shared by workers while
this service enforces the account limit inside each worker (and provides the
same protection to a direct application connection). The state is deliberately
small, expiring, and never contains the submitted email address itself.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class ThrottleDecision:
    """Whether a request is currently blocked and for how long."""

    blocked: bool
    retry_after: int = 0


def account_key(email: str) -> str | None:
    """Return a non-reversible bucket key for a submitted account address."""
    normalized = email.strip().casefold()
    if not normalized:
        return None
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"account:{digest}"


def client_key(remote_addr: str | None) -> str:
    """Return a bucket key for the request's already trusted client address."""
    # ``remote_addr`` is resolved by ProxyFix only when a trusted proxy is
    # configured.  A missing address is still one conservative shared bucket.
    return f"client:{remote_addr or 'unknown'}"


class LoginThrottle:
    """Thread-safe sliding-window counter for failed sign-in attempts.

    A request is allowed through while each bucket has fewer than
    ``max_attempts`` failures in ``window_seconds``.  The oldest failure's
    expiry is returned to make the HTTP response actionable without exposing
    whether an account exists.
    """

    def __init__(
        self,
        max_attempts: int,
        window_seconds: int,
        *,
        max_keys: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_attempts = max(1, max_attempts)
        self.window_seconds = max(1, window_seconds)
        self.max_keys = max(1, max_keys)
        self._clock = clock
        self._attempts: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.RLock()

    def check(self, keys: Iterable[str]) -> ThrottleDecision:
        """Return whether one of ``keys`` has reached the failure threshold."""
        now = self._clock()
        with self._lock:
            retry_after = 0
            for key in keys:
                attempts = self._prune(key, now)
                if len(attempts) >= self.max_attempts:
                    retry_after = max(
                        retry_after,
                        max(1, int(attempts[0] + self.window_seconds - now)),
                    )
            self._trim()
            return ThrottleDecision(bool(retry_after), retry_after)

    def record_failure(self, keys: Iterable[str]) -> None:
        """Count one failed sign-in against every supplied bucket."""
        now = self._clock()
        with self._lock:
            for key in keys:
                attempts = self._prune(key, now)
                attempts.append(now)
                self._attempts.move_to_end(key)
            self._trim()

    def clear(self, keys: Iterable[str]) -> None:
        """Forget failures after a successful sign-in."""
        with self._lock:
            for key in keys:
                self._attempts.pop(key, None)

    def _prune(self, key: str, now: float) -> deque[float]:
        attempts = self._attempts.setdefault(key, deque())
        cutoff = now - self.window_seconds
        while attempts and attempts[0] <= cutoff:
            attempts.popleft()
        self._attempts.move_to_end(key)
        if not attempts:
            self._attempts.pop(key, None)
            attempts = self._attempts.setdefault(key, deque())
        return attempts

    def _trim(self) -> None:
        while len(self._attempts) > self.max_keys:
            self._attempts.popitem(last=False)
