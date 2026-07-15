from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Iterable
from typing import Protocol

from redis.asyncio import Redis

from app.core.errors import AppError


class RateLimited(AppError):
    def __init__(self, retry_after: int) -> None:
        super().__init__(
            status_code=429,
            code="RATE_LIMITED",
            message="请求过于频繁",
            headers={"Retry-After": str(max(1, retry_after))},
        )


class RateLimitPort(Protocol):
    async def check(self, *, scope: str, subjects: Iterable[str], limit: int) -> None: ...


def _safe_key(scope: str, subject: str, window: int) -> str:
    digest = hashlib.sha256(subject.encode("utf-8")).hexdigest()
    return f"campuspilot:rate:{scope}:{window}:{digest}"


class InMemoryRateLimiter:
    def __init__(self, *, clock=time.time) -> None:
        self._clock = clock
        self._counts: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def check(self, *, scope: str, subjects: Iterable[str], limit: int) -> None:
        now = self._clock()
        window = int(now // 60)
        keys = tuple(_safe_key(scope, value, window) for value in set(subjects) if value)
        async with self._lock:
            if any(self._counts.get(key, 0) >= limit for key in keys):
                raise RateLimited(60 - int(now % 60))
            for key in keys:
                self._counts[key] = self._counts.get(key, 0) + 1


class RedisRateLimiter:
    def __init__(self, redis: Redis, *, clock=time.time) -> None:
        self._redis = redis
        self._clock = clock

    async def check(self, *, scope: str, subjects: Iterable[str], limit: int) -> None:
        now = self._clock()
        window = int(now // 60)
        keys = tuple(_safe_key(scope, value, window) for value in set(subjects) if value)
        pipe = self._redis.pipeline(transaction=True)
        for key in keys:
            pipe.incr(key)
            pipe.expire(key, 70)
        values = await pipe.execute()
        counts = values[0::2]
        if any(int(value) > limit for value in counts):
            raise RateLimited(60 - int(now % 60))
