# Implements `TTLCache`, an in-memory dictionary cache with time-to-live
# expiration, oldest-entry eviction at capacity, and an injectable clock.

# Provides `cache_key` to normalize URLs
# (lowercasing scheme and host, stripping trailing slash, keeping query string)
# so equivalent URLs share a cache entry.

"""In-memory TTL cache and cache-key helper."""

import time
from collections.abc import Callable
from typing import Generic, TypeVar
from urllib.parse import urlparse

V = TypeVar("V")


class TTLCache(Generic[V]):
    def __init__(
        self,
        ttl_seconds: float,
        max_entries: int,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        self._clock = clock
        self._data: dict[str, tuple[float, V]] = {}

    def get(self, key: str) -> V | None:
        if self.ttl <= 0:
            return None
        item = self._data.get(key)
        if item is None:
            return None
        expires_at, value = item
        if self._clock() >= expires_at:
            del self._data[key]
            return None
        return value

    def set(self, key: str, value: V) -> None:
        if self.ttl <= 0 or self.max_entries <= 0:
            return
        self._data.pop(key, None)
        while len(self._data) >= self.max_entries:
            self._data.pop(next(iter(self._data)))  # evict oldest
        self._data[key] = (self._clock() + self.ttl, value)

    def __len__(self) -> int:
        return len(self._data)


def cache_key(url: str) -> str:
    """Normalize so https://Example.com/ and https://example.com share one entry."""
    p = urlparse(url)
    key = f"{p.scheme.lower()}://{(p.hostname or '').lower()}{p.path.rstrip('/')}"
    return key + (f"?{p.query}" if p.query else "")
