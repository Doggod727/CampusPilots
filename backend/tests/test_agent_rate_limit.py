import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.modules.agent_platform.internal_auth import (
    InternalServicePrincipal,
    get_internal_service_principal,
)
from app.modules.agent_platform.internal_tools import (
    ToolInvokeData,
    get_internal_tool_rate_limit,
    get_internal_tool_rate_limiter,
    get_internal_tool_service,
)
from app.modules.agent_platform.rate_limit import (
    InMemoryRateLimiter,
    RateLimited,
    RedisRateLimiter,
    user_ip_rate_limit_subjects,
)
from app.modules.agent_platform.run_routes import (
    get_agent_rate_limiter,
    get_agent_run_rate_limit,
    get_run_service,
)
from app.modules.platform.auth import AuthenticatedRole, AuthenticatedUser
from app.modules.platform.auth_dependencies import get_authenticated_user


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


class FakeRedis:
    def __init__(self):
        self.counts = {}
        self.calls = []

    async def eval(self, script, number_of_keys, *values):
        keys = values[:number_of_keys]
        limit, ttl = values[number_of_keys:]
        self.calls.append((script, keys, limit, ttl))
        if any(self.counts.get(key, 0) >= limit for key in keys):
            return 0
        for key in keys:
            self.counts[key] = self.counts.get(key, 0) + 1
        return 1


def test_redis_limiter_uses_hashed_subjects_and_rejects_over_limit():
    redis = FakeRedis()
    limiter = RedisRateLimiter(redis, clock=lambda: 10.0)
    asyncio.run(
        limiter.check(
            scope="internal_tool",
            subjects=user_ip_rate_limit_subjects("secret-user", "192.0.2.1"),
            limit=1,
        )
    )
    with pytest.raises(RateLimited):
        asyncio.run(
            limiter.check(
                scope="internal_tool",
                subjects=user_ip_rate_limit_subjects("secret-user", "192.0.2.2"),
                limit=1,
            )
        )
    asyncio.run(
        limiter.check(
            scope="internal_tool",
            subjects=user_ip_rate_limit_subjects("other-user", "192.0.2.2"),
            limit=1,
        )
    )
    serialized = repr(redis.calls)
    assert "secret-user" not in serialized
    assert "192.0.2.1" not in serialized
    assert all(call[2:] == (1, 70) for call in redis.calls)


def test_in_memory_rejection_does_not_consume_another_dimension():
    limiter = InMemoryRateLimiter(clock=lambda: 10.0)
    asyncio.run(
        limiter.check(
            scope="agent_run",
            subjects=user_ip_rate_limit_subjects("user-a", "192.0.2.1"),
            limit=1,
        )
    )
    with pytest.raises(RateLimited):
        asyncio.run(
            limiter.check(
                scope="agent_run",
                subjects=user_ip_rate_limit_subjects("user-a", "192.0.2.2"),
                limit=1,
            )
        )
    asyncio.run(
        limiter.check(
            scope="agent_run",
            subjects=user_ip_rate_limit_subjects("user-b", "192.0.2.2"),
            limit=1,
        )
    )


def _actor() -> AuthenticatedUser:
    now = datetime(2026, 7, 17, tzinfo=UTC)
    return AuthenticatedUser(
        uuid4(),
        "student01",
        "Student",
        None,
        None,
        "active",
        (AuthenticatedRole(uuid4(), "student", "Student"),),
        ("agent:run",),
        None,
        now,
        1,
    )


def test_rate_limit_guard_rejects_before_constructing_business_services():
    app = create_app()
    actor = _actor()
    run_limiter = InMemoryRateLimiter(clock=lambda: 10.0)
    internal_limiter = InMemoryRateLimiter(clock=lambda: 10.0)
    constructions = {"run": 0, "internal": 0}

    class RunService:
        async def create(self, **kwargs):
            request_id = kwargs["request_id"]
            return SimpleNamespace(
                status_code=202,
                request_id=request_id,
                body={"code": "OK", "message": "success", "data": {}, "request_id": request_id},
            )

    class InternalService:
        async def invoke(self, **kwargs):
            return 200, ToolInvokeData(
                tool_call_id=uuid4(), status="succeeded", result={"items": []}
            )

    async def auth():
        return actor

    async def run_service():
        constructions["run"] += 1
        yield RunService()

    async def internal_service():
        constructions["internal"] += 1
        yield InternalService()

    app.dependency_overrides[get_authenticated_user] = auth
    app.dependency_overrides[get_run_service] = run_service
    app.dependency_overrides[get_agent_rate_limiter] = lambda: run_limiter
    app.dependency_overrides[get_agent_run_rate_limit] = lambda: 1
    app.dependency_overrides[get_internal_service_principal] = (
        lambda: InternalServicePrincipal()
    )
    app.dependency_overrides[get_internal_tool_service] = internal_service
    app.dependency_overrides[get_internal_tool_rate_limiter] = lambda: internal_limiter
    app.dependency_overrides[get_internal_tool_rate_limit] = lambda: 1
    client = TestClient(app)

    run_headers = {"Idempotency-Key": "run-limit-key", "X-Request-Id": "run-limit-request"}
    assert client.post("/api/v1/agent-runs", headers=run_headers, json={"input": "测试限流"}).status_code == 202
    run_limited = client.post("/api/v1/agent-runs", headers=run_headers, json={"input": "测试限流"})
    assert run_limited.status_code == 429
    assert run_limited.json()["code"] == "RATE_LIMITED"
    assert run_limited.headers["Retry-After"] == "50"
    assert constructions["run"] == 1

    payload = {
        "run_id": str(uuid4()),
        "step_id": str(uuid4()),
        "agent_code": "service_agent",
        "user_id": str(uuid4()),
        "arguments": {"query": "校历"},
    }
    internal_headers = {
        "Idempotency-Key": "internal-limit-key",
        "X-Request-Id": "internal-limit-request",
    }
    endpoint = "/internal/v1/tools/service.get_guide:invoke"
    assert client.post(endpoint, headers=internal_headers, json=payload).status_code == 200
    internal_limited = client.post(endpoint, headers=internal_headers, json=payload)
    assert internal_limited.status_code == 429
    assert internal_limited.json()["code"] == "RATE_LIMITED"
    assert constructions["internal"] == 1


def test_runtime_composition_import_and_construction_do_not_connect(monkeypatch):
    from app.modules.agent_platform.composition import RuntimeCompositionFactory
    settings = SimpleNamespace()
    factory = RuntimeCompositionFactory(settings)
    assert factory.settings is settings
