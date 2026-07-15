import asyncio
from types import SimpleNamespace

import pytest

from app.modules.agent_platform.rate_limit import InMemoryRateLimiter, RateLimited, RedisRateLimiter


def test_in_memory_limiter_applies_user_and_ip_limit_with_retry_after():
    limiter = InMemoryRateLimiter(clock=lambda: 125.0)
    asyncio.run(limiter.check(scope="agent_run", subjects=("user-1", "10.0.0.1"), limit=1))
    with pytest.raises(RateLimited) as caught:
        asyncio.run(limiter.check(scope="agent_run", subjects=("user-1", "10.0.0.2"), limit=1))
    assert caught.value.status_code == 429
    assert caught.value.code == "RATE_LIMITED"
    assert caught.value.headers["Retry-After"] == "55"


def test_in_memory_limiter_uses_independent_scopes_and_windows():
    now = [1.0]
    limiter = InMemoryRateLimiter(clock=lambda: now[0])
    asyncio.run(limiter.check(scope="agent_run", subjects=("same",), limit=1))
    asyncio.run(limiter.check(scope="internal_tool", subjects=("same",), limit=1))
    now[0] = 61.0
    asyncio.run(limiter.check(scope="agent_run", subjects=("same",), limit=1))


class FakePipeline:
    def __init__(self, counts):
        self.counts = counts
        self.commands = []

    def incr(self, key): self.commands.append(("incr", key)); return self
    def expire(self, key, ttl): self.commands.append(("expire", key, ttl)); return self
    async def execute(self):
        values = []
        for count in self.counts:
            values.extend((count, True))
        return values


class FakeRedis:
    def __init__(self, counts): self.pipe = FakePipeline(counts)
    def pipeline(self, transaction=True): return self.pipe


def test_redis_limiter_uses_hashed_subjects_and_rejects_over_limit():
    redis = FakeRedis((3, 4))
    limiter = RedisRateLimiter(redis, clock=lambda: 10.0)
    with pytest.raises(RateLimited):
        asyncio.run(limiter.check(scope="internal_tool", subjects=("secret-user", "192.0.2.1"), limit=3))
    serialized = repr(redis.pipe.commands)
    assert "secret-user" not in serialized
    assert "192.0.2.1" not in serialized


def test_runtime_composition_import_and_construction_do_not_connect(monkeypatch):
    from app.modules.agent_platform.composition import RuntimeCompositionFactory
    settings = SimpleNamespace()
    factory = RuntimeCompositionFactory(settings)
    assert factory.settings is settings
