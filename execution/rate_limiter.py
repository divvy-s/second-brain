from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class Bucket:
    capacity: int
    refill_per_second: float
    tokens: float
    updated_at: float


class RateLimiter:
    def __init__(self, capacity: int = 60, refill_per_second: float = 1.0) -> None:
        self.capacity = capacity
        self.refill_per_second = refill_per_second
        self.buckets: dict[str, Bucket] = {}

    def allow(self, key: str, cost: float = 1.0) -> bool:
        now = time.monotonic()
        bucket = self.buckets.get(key)
        if bucket is None:
            bucket = Bucket(self.capacity, self.refill_per_second, float(self.capacity), now)
            self.buckets[key] = bucket
        elapsed = now - bucket.updated_at
        bucket.tokens = min(bucket.capacity, bucket.tokens + elapsed * bucket.refill_per_second)
        bucket.updated_at = now
        if bucket.tokens >= cost:
            bucket.tokens -= cost
            return True
        return False

