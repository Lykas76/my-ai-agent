"""Bounded, process-local rate limiter. Reverse-proxy headers are not trusted."""
import time
from collections import OrderedDict


class RateLimiter:
    def __init__(self, limit=60, window=60, capacity=1024):
        if type(limit) is not int or limit < 1:
            raise ValueError("Invalid rate limit")
        self.limit, self.window, self.capacity = limit, window, capacity
        self.buckets = OrderedDict()

    def allow(self, key, now=None):
        now = time.monotonic() if now is None else now
        start, count = self.buckets.get(key, (now, 0))
        if now - start >= self.window:
            start, count = now, 0
        self.buckets[key] = (start, count + 1)
        self.buckets.move_to_end(key)
        while len(self.buckets) > self.capacity:
            self.buckets.popitem(last=False)
        return count < self.limit
