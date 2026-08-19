"""
A tiny, dependency-free, thread-safe TTL cache.

Under load on a single (vertically scaled) instance, the biggest win is
avoiding redundant outbound work. Voice assistants get a lot of repeat
queries ("play the same song", "open google") in a short window, so a
small in-memory cache with a short TTL meaningfully cuts latency and
outbound request volume without adding infra (Redis, etc.) that a single
box doesn't need.
"""
import threading
import time
from collections import OrderedDict
from typing import Any, Optional


class TTLCache:
    def __init__(self, max_items: int = 500, ttl_seconds: int = 600):
        self._max_items = max_items
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._store: "OrderedDict[str, tuple[float, Any]]" = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self.misses += 1
                return None
            expires_at, value = entry
            if expires_at < time.monotonic():
                del self._store[key]
                self.misses += 1
                return None
            # LRU touch
            self._store.move_to_end(key)
            self.hits += 1
            return value

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
            self._store[key] = (time.monotonic() + self._ttl, value)
            while len(self._store) > self._max_items:
                self._store.popitem(last=False)

    def stats(self) -> dict:
        with self._lock:
            total = self.hits + self.misses
            hit_rate = (self.hits / total) if total else 0.0
            return {
                "size": len(self._store),
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": round(hit_rate, 3),
            }
          
