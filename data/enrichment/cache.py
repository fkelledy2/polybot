# data/enrichment/cache.py
# ─────────────────────────────────────────────────────────────
# Thread-safe TTL cache shared across all enrichers.
# Prevents hammering external APIs on every scan.
# ─────────────────────────────────────────────────────────────

import threading
import time
from typing import Any, Optional


class TTLCache:
    """
    In-memory key/value store where every entry expires after `ttl` seconds.

    Usage:
        cache.get("key")               → value or None if missing/expired
        cache.set("key", value, ttl=300)
    """

    def __init__(self):
        self._store: dict[str, tuple[Any, float]] = {}
        self._lock  = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expiry = entry
            if time.monotonic() > expiry:
                del self._store[key]
                return None
            return value

    def set(self, key: str, value: Any, ttl: int = 300) -> None:
        with self._lock:
            self._store[key] = (value, time.monotonic() + ttl)

    def clear_expired(self) -> None:
        now = time.monotonic()
        with self._lock:
            self._store = {k: v for k, v in self._store.items() if v[1] > now}


# Module-level singleton — all enrichers share one cache
_cache = TTLCache()
