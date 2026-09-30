"""Sliding-window rate limits, global and per client IP.

In memory: one app process behind one proxy, as the original was. A restart resets
the windows, which errs toward letting drawings through.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self, period_s: int, global_limit: int, per_ip_limit: int, clock=time.monotonic):
        self.period = period_s
        self.global_limit = global_limit
        self.per_ip_limit = per_ip_limit
        self._clock = clock
        self._all: deque[float] = deque()
        self._by_ip: dict[str, deque[float]] = defaultdict(deque)
        self._calls = 0

    def _trim(self, q: deque[float], now: float) -> None:
        while q and q[0] <= now - self.period:
            q.popleft()

    def sweep(self, now: float | None = None) -> None:
        """Drop IPs whose windows are empty, so memory tracks recent clients only (D-0006 #6)."""
        now = self._clock() if now is None else now
        for ip in list(self._by_ip):
            q = self._by_ip[ip]
            self._trim(q, now)
            if not q:
                del self._by_ip[ip]

    def allow(self, ip: str) -> bool:
        """Record and allow the request, or refuse it without recording (a refused request
        doesn't extend anyone's wait)."""
        now = self._clock()
        self._calls += 1
        if self._calls % 256 == 0:
            self.sweep(now)
        self._trim(self._all, now)
        mine = self._by_ip[ip]
        self._trim(mine, now)
        if len(self._all) >= self.global_limit or len(mine) >= self.per_ip_limit:
            if not mine:
                del self._by_ip[ip]
            return False
        self._all.append(now)
        mine.append(now)
        return True
