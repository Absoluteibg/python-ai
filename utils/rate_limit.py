"""
Minimal per-IP sliding-window rate limiter.

Kept dependency-free and in-process on purpose: for a single, vertically
scaled instance this is enough to stop one client from starving the
worker pool or getting our outbound IP rate-limited by YouTube. If this
ever needs to run as multiple replicas behind a load balancer, swap the
in-memory dict for Redis (INCR + EXPIRE) — the interface below wouldn't
need to change.
"""
import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._lock = threading.Lock()
        self._hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            q = self._hits[key]
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= self.max_requests:
                return False
            q.append(now)
            return True

    def retry_after(self, key: str) -> int:
        with self._lock:
            q = self._hits.get(key)
            if not q:
                return 0
            oldest = q[0]
            wait = self.window_seconds - (time.monotonic() - oldest)
            return max(0, int(wait) + 1)
          
