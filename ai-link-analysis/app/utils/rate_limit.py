# Implements an in-memory sliding-window `RateLimiter`
# keyed by identifier (such as client IP or user) with an injectable clock.

# Tracks recent hit timestamps per key,
# expires old entries outside the time window,
# returns wait time when over limit,
# and prunes stale keys above 10,000 entries.

"""Sliding-window per-key rate limiter (in memory)."""

import time
from collections import deque
from collections.abc import Callable


class RateLimiter:
    def __init__(
        self,
        limit: int,
        window_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.limit = limit
        self.window = window_seconds
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}

    def check(self, key: str) -> float:
        """Record a hit. Returns 0 if allowed, else seconds until a slot frees up."""
        if self.limit <= 0:
            return 0.0
        now = self._clock()
        q = self._hits.setdefault(key, deque())
        while q and now - q[0] >= self.window:
            q.popleft()
        if len(q) >= self.limit:
            return max(self.window - (now - q[0]), 1.0)
        q.append(now)
        if len(self._hits) > 10_000:
            self._prune(now)
        return 0.0

    def _prune(self, now: float) -> None:
        for k in [
            k for k, q in self._hits.items() if not q or now - q[-1] >= self.window
        ]:
            del self._hits[k]
