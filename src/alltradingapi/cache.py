"""Thread-safe in-memory TTL cache.

Used only for the cheap, slow-changing metadata endpoints (``assets()``,
``timeframes()``) by default. Analysis/candles are never cached client-side.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Hashable
from typing import Any

__all__ = ["TTLCache"]

_Entry = tuple[float, Any]  # (expires_at, value)


class TTLCache:
    """A tiny, thread-safe TTL cache keyed by any hashable tuple."""

    def __init__(self, ttl: float = 60.0, maxsize: int = 128) -> None:
        self._ttl = max(0.0, float(ttl))
        self._maxsize = max(1, int(maxsize))
        self._store: dict[Hashable, _Entry] = {}
        self._lock = threading.Lock()

    @property
    def ttl(self) -> float:
        """Configured time-to-live in seconds."""
        return self._ttl

    def get(self, key: Hashable) -> Any | None:
        """Return the cached value, or ``None`` when missing/expired."""
        now = time.monotonic()
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at <= now:
                del self._store[key]
                return None
            return value

    def put(self, key: Hashable, value: Any) -> None:
        """Store *value* under *key* for the configured TTL."""
        if self._ttl <= 0:
            return
        now = time.monotonic()
        with self._lock:
            if len(self._store) >= self._maxsize and key not in self._store:
                self._evict_oldest_locked()
            self._store[key] = (now + self._ttl, value)

    def clear(self) -> None:
        """Drop every entry."""
        with self._lock:
            self._store.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)

    def _evict_oldest_locked(self) -> None:
        if not self._store:
            return
        oldest_key = None
        oldest_expiry = float("inf")
        for k, (expires_at, _value) in self._store.items():
            if expires_at < oldest_expiry:
                oldest_expiry = expires_at
                oldest_key = k
        if oldest_key is not None:
            del self._store[oldest_key]
