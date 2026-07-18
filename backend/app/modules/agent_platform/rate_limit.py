from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Iterable
from typing import Protocol

from redis.asyncio import Redis

from app.core.errors import AppError


_ATOMIC_CHECK_AND_INCREMENT = """
local limit = tonumber(ARGV[1])
local ttl = tonumber(ARGV[2])

for _, key in ipairs(KEYS) do
    local current = tonumber(redis.call('GET', key) or '0')
    if current >= limit then
        return 0
    end
end

for _, key in ipairs(KEYS) do
    local current = redis.call('INCR', key)
    if current == 1 then
        redis.call('EXPIRE', key, ttl)
    end
end

return 1
"""


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


def user_ip_rate_limit_subjects(user_id: object, client_ip: str) -> tuple[str, str]:
    """Keep dimensions distinct while ensuring raw identities never enter Redis keys."""

    normalized_ip = client_ip.strip().lower() or "unknown"
    return f"user:{user_id}", f"ip:{normalized_ip}"


def _keys(scope: str, subjects: Iterable[str], window: int) -> tuple[str, ...]:
    unique_subjects = dict.fromkeys(value for value in subjects if value)
    return tuple(_safe_key(scope, value, window) for value in unique_subjects)


class InMemoryRateLimiter:
    def __init__(self, *, clock=time.time) -> None:
        self._clock = clock
        self._counts: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def check(self, *, scope: str, subjects: Iterable[str], limit: int) -> None:
        now = self._clock()
        window = int(now // 60)
        keys = _keys(scope, subjects, window)
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
        keys = _keys(scope, subjects, window)
        if not keys:
            return
        accepted = await self._redis.eval(
            _ATOMIC_CHECK_AND_INCREMENT,
            len(keys),
            *keys,
            limit,
            70,
        )
        if int(accepted) != 1:
            raise RateLimited(60 - int(now % 60))
