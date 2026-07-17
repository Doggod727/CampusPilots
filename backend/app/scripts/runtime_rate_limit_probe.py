from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
import os
import re
import socket
import time
from typing import Any, AsyncIterator, Mapping
from urllib.parse import urlsplit, urlunsplit
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import httpx
from redis.asyncio import Redis
from sqlalchemy import delete, func, select
import uvicorn

from app.core.config import Settings, get_settings
from app.infrastructure.database import Database
from app.main import create_app
from app.modules.agent_platform.models import (
    AgentRun,
    AgentRuntimeCommand,
    AgentStep,
    ToolCall,
)
from app.modules.agent_platform.checkpointing import RuntimeTerminalCoordinator
from app.modules.agent_platform.internal_tools import (
    get_internal_tool_rate_limiter,
    get_internal_tool_service,
)
from app.modules.agent_platform.rate_limit import (
    RedisRateLimiter,
    _safe_key,
    user_ip_rate_limit_subjects,
)
from app.modules.agent_platform.run_queries import (
    AgentRunQueryRepository,
    AgentRunQueryService,
)
from app.modules.agent_platform.run_routes import get_agent_rate_limiter, get_run_service
from app.modules.agent_platform.run_service import AgentRunService
from app.modules.agent_platform.runtime_persistence import (
    RuntimeCheckpointRepository,
    RuntimeEventRepository,
)
from app.modules.agent_platform.traces import TraceRepository, TraceService
from app.modules.platform.idempotency import IdempotencyService
from app.modules.platform.models import AuditLog, IdempotencyRecord
from app.modules.platform.repositories import (
    IdempotencyRecordRepository,
    RbacRepository,
    UserRepository,
)
from app.modules.platform.tokens import TokenService


AGENT_LIMIT = 20
INTERNAL_TOOL_LIMIT = 60
PROBE_REDIS_DATABASE = 15
REQUIRED_PERMISSIONS = {"agent:run", "service:read"}
TAG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{7,39}$")
PROBE_REDIS_LEASE_KEY = "campuspilot:rate-probe:lease"
AGENT_IP = "198.51.100.10"
AGENT_OTHER_IP = "198.51.100.11"
INTERNAL_IP = "203.0.113.20"
INTERNAL_OTHER_IP = "203.0.113.21"
_RELEASE_REDIS_LEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    for index = 2, #KEYS do
        redis.call('DEL', KEYS[index])
    end
    return redis.call('DEL', KEYS[1])
end
return 0
"""


class ProbeFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ProbeIdentity:
    user_id: UUID
    username: str
    access_token: str = field(repr=False)
    run_id: UUID
    step_id: UUID


class ProbeRunDispatcher:
    """Exercise the real Run service without exposing probe rows to Runtime Workers."""

    async def start(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    async def cancel(self, *_args: Any, **_kwargs: Any) -> None:
        return None


@dataclass(frozen=True)
class ProbeSummary:
    agent_success_count: int
    internal_tool_success_count: int
    user_limit_verified: bool
    ip_limit_verified: bool
    window_recovery_verified: bool
    atomic_isolation_verified: bool
    proxy_header_verified: bool
    database_cleanup_verified: bool
    redis_cleanup_verified: bool

    def public_dict(self) -> dict[str, Any]:
        return {"ok": True, **self.__dict__}


def build_probe_redis_url(redis_url: str, database: int = PROBE_REDIS_DATABASE) -> str:
    parsed = urlsplit(redis_url)
    if parsed.scheme not in {"redis", "rediss"} or not parsed.hostname:
        raise ProbeFailure("RATE_LIMIT_PROBE_REDIS_URL_INVALID")
    if database < 1 or database > 15:
        raise ProbeFailure("RATE_LIMIT_PROBE_REDIS_DATABASE_INVALID")
    return urlunsplit(parsed._replace(path=f"/{database}"))


def validate_rate_limited_response(
    *,
    status_code: int,
    body: Mapping[str, Any],
    headers: Mapping[str, str],
    request_id: str,
) -> int:
    if status_code != 429:
        raise ProbeFailure(f"RATE_LIMIT_PROBE_429_STATUS_{status_code}")
    if body.get("code") != "RATE_LIMITED":
        raise ProbeFailure("RATE_LIMIT_PROBE_429_CODE_INVALID")
    if body.get("request_id") != request_id:
        raise ProbeFailure("RATE_LIMIT_PROBE_429_BODY_REQUEST_ID_INVALID")
    if headers.get("X-Request-Id") != request_id:
        raise ProbeFailure("RATE_LIMIT_PROBE_429_HEADER_REQUEST_ID_INVALID")
    raw_retry = headers.get("Retry-After")
    try:
        retry_after = int(raw_retry or "")
    except ValueError as exc:
        raise ProbeFailure("RATE_LIMIT_PROBE_RETRY_AFTER_INVALID") from exc
    if retry_after < 1 or retry_after > 60:
        raise ProbeFailure("RATE_LIMIT_PROBE_RETRY_AFTER_INVALID")
    return retry_after


def _probe_tag() -> str:
    return f"{datetime.now(UTC):%Y%m%d%H%M%S}-{uuid4().hex[:10]}"


def validate_tag(value: str) -> str:
    if TAG_PATTERN.fullmatch(value) is None:
        raise ProbeFailure("RATE_LIMIT_PROBE_TAG_INVALID")
    return value


def _prefix(tag: str) -> str:
    return f"rlp-{validate_tag(tag)}-"


def _request_id(prefix: str, group: str, sequence: int) -> str:
    value = f"{prefix}{group}{sequence:03d}"
    if len(value) > 64:
        raise ProbeFailure("RATE_LIMIT_PROBE_TAG_INVALID")
    return value


async def _prepare_database(
    database: Database, settings: Settings, *, tag: str
) -> tuple[ProbeIdentity, ProbeIdentity]:
    prefix = _prefix(tag)
    identities: list[ProbeIdentity] = []
    async with database.session() as session:
        async with session.begin():
            active_commands = (
                await session.execute(
                    select(func.count())
                    .select_from(AgentRuntimeCommand)
                    .where(AgentRuntimeCommand.status.in_(("pending", "processing")))
                )
            ).scalar_one()
            if active_commands:
                raise ProbeFailure("RATE_LIMIT_PROBE_ACTIVE_COMMANDS_PRESENT")

            users = UserRepository(session)
            rbac = RbacRepository(session)
            tokens = TokenService(settings)
            for label, username in (("a", "student01"), ("b", "student02")):
                user = await users.get_by_username(username)
                if user is None or user.status != "active":
                    raise ProbeFailure("RATE_LIMIT_PROBE_DEMO_USER_UNAVAILABLE")
                roles = await rbac.list_roles_for_user(user.id)
                permissions = await rbac.list_permission_codes_for_user(user.id)
                if not REQUIRED_PERMISSIONS.issubset(permissions):
                    raise ProbeFailure("RATE_LIMIT_PROBE_DEMO_USER_FORBIDDEN")
                access = tokens.issue_access(
                    user_id=user.id,
                    username=user.username,
                    roles=(role.code for role in roles),
                    permissions=permissions,
                )
                run_id = uuid5(NAMESPACE_URL, f"campuspilot:rate-probe:{tag}:{label}:run")
                step_id = uuid5(NAMESPACE_URL, f"campuspilot:rate-probe:{tag}:{label}:step")
                session.add(
                    AgentRun(
                        id=run_id,
                        user_id=user.id,
                        client_request_id=f"{prefix}internal-{label}",
                        input_summary="rate-limit verification",
                        status="running",
                        step_count=1,
                        specialist_count=1,
                        started_at=datetime.now(UTC),
                    )
                )
                session.add(
                    AgentStep(
                        id=step_id,
                        run_id=run_id,
                        sequence_no=1,
                        agent_code="service_agent",
                        task_type="service",
                        status="running",
                        input_summary={},
                        output_summary={},
                        started_at=datetime.now(UTC),
                    )
                )
                identities.append(
                    ProbeIdentity(
                        user_id=user.id,
                        username=user.username,
                        access_token=access.token,
                        run_id=run_id,
                        step_id=step_id,
                    )
                )
    return identities[0], identities[1]


async def _cleanup_database(database: Database, *, prefix: str) -> bool:
    async with database.session() as session:
        async with session.begin():
            await session.execute(delete(AuditLog).where(AuditLog.request_id.like(f"{prefix}%")))
            await session.execute(
                delete(IdempotencyRecord).where(
                    IdempotencyRecord.idempotency_key.like(f"{prefix}%")
                )
            )
            await session.execute(
                delete(AgentRun).where(AgentRun.client_request_id.like(f"{prefix}%"))
            )
        remaining = (
            await session.execute(
                select(func.count())
                .select_from(AgentRun)
                .where(AgentRun.client_request_id.like(f"{prefix}%"))
            )
        ).scalar_one()
        idempotency = (
            await session.execute(
                select(func.count())
                .select_from(IdempotencyRecord)
                .where(IdempotencyRecord.idempotency_key.like(f"{prefix}%"))
            )
        ).scalar_one()
        audits = (
            await session.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.request_id.like(f"{prefix}%"))
            )
        ).scalar_one()
    return remaining == idempotency == audits == 0


async def _database_evidence(database: Database, *, prefix: str) -> dict[str, int]:
    async with database.session() as session:
        run_ids = tuple(
            (
                await session.execute(
                    select(AgentRun.id).where(
                        AgentRun.client_request_id.like(f"{prefix}%")
                    )
                )
            ).scalars()
        )
        if not run_ids:
            raise ProbeFailure("RATE_LIMIT_PROBE_DATABASE_EVIDENCE_INVALID")

        async def count(model: Any, *conditions: Any) -> int:
            return int(
                (
                    await session.execute(
                        select(func.count()).select_from(model).where(*conditions)
                    )
                ).scalar_one()
            )

        return {
            "runs": len(run_ids),
            "commands": await count(AgentRuntimeCommand, AgentRuntimeCommand.run_id.in_(run_ids)),
            "pending_commands": await count(
                AgentRuntimeCommand,
                AgentRuntimeCommand.run_id.in_(run_ids),
                AgentRuntimeCommand.status == "pending",
            ),
            "tool_calls": await count(ToolCall, ToolCall.run_id.in_(run_ids)),
            "succeeded_tool_calls": await count(
                ToolCall, ToolCall.run_id.in_(run_ids), ToolCall.status == "succeeded"
            ),
            "idempotency": await count(
                IdempotencyRecord,
                IdempotencyRecord.idempotency_key.like(f"{prefix}%"),
            ),
            "audits": await count(AuditLog, AuditLog.request_id.like(f"{prefix}%")),
        }


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@asynccontextmanager
async def _serve_api(redis: Redis, database: Database) -> AsyncIterator[str]:
    port = _free_loopback_port()
    internal_service_dependency = get_internal_tool_service()
    internal_service = await anext(internal_service_dependency)
    async with database.session() as run_session:
        queries = AgentRunQueryService(AgentRunQueryRepository(run_session))
        run_service = AgentRunService(
            session=run_session,
            trace=TraceService(TraceRepository(run_session)),
            queries=queries,
            idempotency=IdempotencyService(
                session=run_session,
                repository=IdempotencyRecordRepository(run_session),
            ),
            dispatcher=ProbeRunDispatcher(),
            terminal=RuntimeTerminalCoordinator(
                RuntimeCheckpointRepository(run_session),
                RuntimeEventRepository(run_session),
            ),
        )
        limiter = RedisRateLimiter(redis)
        application = create_app()
        application.dependency_overrides[get_run_service] = lambda: run_service
        application.dependency_overrides[get_internal_tool_service] = lambda: internal_service
        application.dependency_overrides[get_agent_rate_limiter] = lambda: limiter
        application.dependency_overrides[get_internal_tool_rate_limiter] = lambda: limiter
        server = uvicorn.Server(
            uvicorn.Config(
                application,
                host="127.0.0.1",
                port=port,
                log_level="critical",
                access_log=False,
                proxy_headers=True,
                forwarded_allow_ips="127.0.0.1",
            )
        )
        task = asyncio.create_task(server.serve())
        try:
            for _ in range(100):
                if server.started:
                    break
                if task.done():
                    raise ProbeFailure("RATE_LIMIT_PROBE_API_START_FAILED")
                await asyncio.sleep(0.05)
            else:
                raise ProbeFailure("RATE_LIMIT_PROBE_API_START_TIMEOUT")
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            try:
                await asyncio.wait_for(task, timeout=10)
            except TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await internal_service_dependency.aclose()


async def _align_fixed_window() -> int:
    position = time.time() % 60
    if position > 3:
        await asyncio.sleep(61 - position)
    return int(time.time() // 60)


async def _wait_for_next_window(window: int) -> None:
    while int(time.time() // 60) <= window:
        remaining = 60 - (time.time() % 60) + 0.05
        await asyncio.sleep(min(5.0, max(0.05, remaining)))


def _json(response: httpx.Response) -> Mapping[str, Any]:
    try:
        value = response.json()
    except ValueError as exc:
        raise ProbeFailure("RATE_LIMIT_PROBE_RESPONSE_INVALID") from exc
    if not isinstance(value, dict):
        raise ProbeFailure("RATE_LIMIT_PROBE_RESPONSE_INVALID")
    return value


def _expect(response: httpx.Response, expected: int, request_id: str) -> None:
    if (
        response.status_code != expected
        or response.headers.get("X-Request-Id") != request_id
    ):
        raise ProbeFailure("RATE_LIMIT_PROBE_SUCCESS_RESPONSE_INVALID")


async def _redis_count(
    redis: Redis, *, scope: str, subject: str, window: int
) -> int:
    return int(await redis.get(_safe_key(scope, subject, window)) or 0)


def _probe_rate_keys(
    first: ProbeIdentity,
    second: ProbeIdentity,
    *,
    first_window: int,
    last_window: int,
) -> tuple[str, ...]:
    if first_window > last_window or last_window - first_window > 20:
        raise ProbeFailure("RATE_LIMIT_PROBE_WINDOW_RANGE_INVALID")
    first_user, _ = user_ip_rate_limit_subjects(first.user_id, AGENT_IP)
    second_user, _ = user_ip_rate_limit_subjects(second.user_id, AGENT_IP)
    agent_subjects = {
        first_user,
        second_user,
        *(user_ip_rate_limit_subjects(first.user_id, ip)[1] for ip in (AGENT_IP, AGENT_OTHER_IP)),
        "ip:127.0.0.1",
        "ip:::1",
    }
    internal_subjects = {
        first_user,
        second_user,
        *(user_ip_rate_limit_subjects(first.user_id, ip)[1] for ip in (INTERNAL_IP, INTERNAL_OTHER_IP)),
        "ip:127.0.0.1",
        "ip:::1",
    }
    return tuple(
        _safe_key(scope, subject, window)
        for window in range(first_window, last_window + 1)
        for scope, subjects in (
            ("agent_run", agent_subjects),
            ("internal_tool", internal_subjects),
        )
        for subject in sorted(subjects)
    )


async def _release_probe_redis(
    redis: Redis, *, owner_value: str, keys: tuple[str, ...]
) -> bool:
    released = await redis.eval(
        _RELEASE_REDIS_LEASE,
        len(keys) + 1,
        PROBE_REDIS_LEASE_KEY,
        *keys,
        owner_value,
    )
    remaining = await redis.mget(keys) if keys else ()
    return int(released) == 1 and all(value is None for value in remaining)


async def _run_http_matrix(
    *,
    base_url: str,
    redis: Redis,
    prefix: str,
    first: ProbeIdentity,
    second: ProbeIdentity,
    internal_secret: str,
) -> tuple[int, int, bool, bool, bool, bool, bool]:
    window = await _align_fixed_window()
    agent_success = 0
    internal_success = 0

    async with httpx.AsyncClient(base_url=base_url, timeout=20) as client:
        async def agent_call(identity: ProbeIdentity, ip: str, sequence: int) -> httpx.Response:
            request_id = _request_id(prefix, "ar", sequence)
            return await client.post(
                "/api/v1/agent-runs",
                headers={
                    "Authorization": f"Bearer {identity.access_token}",
                    "Idempotency-Key": f"{prefix}agent-{sequence:03d}",
                    "X-Request-Id": request_id,
                    "X-Forwarded-For": ip,
                },
                json={"input": "验证双维度固定窗口限流"},
            )

        for sequence in range(1, AGENT_LIMIT + 1):
            response = await agent_call(first, AGENT_IP, sequence)
            _expect(response, 202, _request_id(prefix, "ar", sequence))
            agent_success += 1

        user_blocked = await agent_call(first, AGENT_OTHER_IP, 21)
        validate_rate_limited_response(
            status_code=user_blocked.status_code,
            body=_json(user_blocked),
            headers=user_blocked.headers,
            request_id=_request_id(prefix, "ar", 21),
        )
        ip_blocked = await agent_call(second, AGENT_IP, 22)
        validate_rate_limited_response(
            status_code=ip_blocked.status_code,
            body=_json(ip_blocked),
            headers=ip_blocked.headers,
            request_id=_request_id(prefix, "ar", 22),
        )
        isolated = await agent_call(second, AGENT_OTHER_IP, 23)
        _expect(isolated, 202, _request_id(prefix, "ar", 23))
        agent_success += 1

        async def internal_call(
            identity: ProbeIdentity, ip: str, sequence: int
        ) -> httpx.Response:
            request_id = _request_id(prefix, "it", sequence)
            return await client.post(
                "/internal/v1/tools/service.get_guide:invoke",
                headers={
                    "Authorization": f"Bearer {internal_secret}",
                    "Idempotency-Key": f"{prefix}tool-{sequence:03d}",
                    "X-Request-Id": request_id,
                    "X-Forwarded-For": ip,
                },
                json={
                    "run_id": str(identity.run_id),
                    "step_id": str(identity.step_id),
                    "agent_code": "service_agent",
                    "user_id": str(identity.user_id),
                    "arguments": {"query": "不存在的限流探针指南"},
                },
            )

        for sequence in range(1, INTERNAL_TOOL_LIMIT + 1):
            response = await internal_call(first, INTERNAL_IP, sequence)
            _expect(response, 200, _request_id(prefix, "it", sequence))
            internal_success += 1

        internal_user_blocked = await internal_call(first, INTERNAL_OTHER_IP, 61)
        validate_rate_limited_response(
            status_code=internal_user_blocked.status_code,
            body=_json(internal_user_blocked),
            headers=internal_user_blocked.headers,
            request_id=_request_id(prefix, "it", 61),
        )
        internal_ip_blocked = await internal_call(second, INTERNAL_IP, 62)
        validate_rate_limited_response(
            status_code=internal_ip_blocked.status_code,
            body=_json(internal_ip_blocked),
            headers=internal_ip_blocked.headers,
            request_id=_request_id(prefix, "it", 62),
        )
        internal_isolated = await internal_call(second, INTERNAL_OTHER_IP, 63)
        _expect(internal_isolated, 200, _request_id(prefix, "it", 63))
        internal_success += 1

        if int(time.time() // 60) != window:
            raise ProbeFailure("RATE_LIMIT_PROBE_BURST_CROSSED_WINDOW")

        first_user, agent_ip_subject = user_ip_rate_limit_subjects(
            first.user_id, AGENT_IP
        )
        second_user, agent_other_ip_subject = user_ip_rate_limit_subjects(
            second.user_id, AGENT_OTHER_IP
        )
        _, internal_ip_subject = user_ip_rate_limit_subjects(
            first.user_id, INTERNAL_IP
        )
        _, internal_other_ip_subject = user_ip_rate_limit_subjects(
            second.user_id, INTERNAL_OTHER_IP
        )
        expected_counts = (
            await _redis_count(redis, scope="agent_run", subject=first_user, window=window),
            await _redis_count(redis, scope="agent_run", subject=agent_ip_subject, window=window),
            await _redis_count(redis, scope="agent_run", subject=second_user, window=window),
            await _redis_count(redis, scope="agent_run", subject=agent_other_ip_subject, window=window),
            await _redis_count(redis, scope="internal_tool", subject=first_user, window=window),
            await _redis_count(redis, scope="internal_tool", subject=internal_ip_subject, window=window),
            await _redis_count(redis, scope="internal_tool", subject=second_user, window=window),
            await _redis_count(redis, scope="internal_tool", subject=internal_other_ip_subject, window=window),
        )
        atomic_isolation = expected_counts == (20, 20, 1, 1, 60, 60, 1, 1)
        if not atomic_isolation:
            raise ProbeFailure("RATE_LIMIT_PROBE_ATOMIC_ISOLATION_INVALID")

        await _wait_for_next_window(window)
        recovered_agent = await agent_call(first, AGENT_IP, 24)
        _expect(recovered_agent, 202, _request_id(prefix, "ar", 24))
        agent_success += 1
        recovered_internal = await internal_call(first, INTERNAL_IP, 64)
        _expect(recovered_internal, 200, _request_id(prefix, "it", 64))
        internal_success += 1

    return agent_success, internal_success, True, True, True, atomic_isolation, True


async def run_probe(settings: Settings, *, tag: str) -> ProbeSummary:
    prefix = _prefix(tag)
    database = Database.from_settings(settings)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    owner_value = uuid4().hex
    redis_owned = False
    database_clean = False
    redis_clean = False
    first_identity: ProbeIdentity | None = None
    second_identity: ProbeIdentity | None = None
    first_window = int(time.time() // 60) - 1
    try:
        await redis.ping()
        redis_owned = bool(
            await redis.set(PROBE_REDIS_LEASE_KEY, owner_value, nx=True, ex=600)
        )
        if not redis_owned:
            raise ProbeFailure("RATE_LIMIT_PROBE_REDIS_DATABASE_BUSY")
        if await redis.dbsize() != 1:
            raise ProbeFailure("RATE_LIMIT_PROBE_REDIS_DATABASE_NOT_EMPTY")
        first_identity, second_identity = await _prepare_database(
            database, settings, tag=tag
        )
        if settings.internal_tool_secret is None:
            raise ProbeFailure("RATE_LIMIT_PROBE_INTERNAL_SECRET_UNAVAILABLE")

        async with _serve_api(redis, database) as base_url:
            matrix = await _run_http_matrix(
                base_url=base_url,
                redis=redis,
                prefix=prefix,
                first=first_identity,
                second=second_identity,
                internal_secret=settings.internal_tool_secret.get_secret_value(),
            )

        evidence = await _database_evidence(database, prefix=prefix)
        if evidence != {
            "runs": 24,
            "commands": 0,
            "pending_commands": 0,
            "tool_calls": 62,
            "succeeded_tool_calls": 62,
            "idempotency": 22,
            "audits": 62,
        }:
            raise ProbeFailure("RATE_LIMIT_PROBE_DATABASE_EVIDENCE_INVALID")
        return ProbeSummary(
            agent_success_count=matrix[0],
            internal_tool_success_count=matrix[1],
            user_limit_verified=matrix[2],
            ip_limit_verified=matrix[3],
            window_recovery_verified=matrix[4],
            atomic_isolation_verified=matrix[5],
            proxy_header_verified=matrix[6],
            database_cleanup_verified=False,
            redis_cleanup_verified=False,
        )
    finally:
        try:
            database_clean = await _cleanup_database(database, prefix=prefix)
        finally:
            if redis_owned:
                keys = ()
                if first_identity is not None and second_identity is not None:
                    keys = _probe_rate_keys(
                        first_identity,
                        second_identity,
                        first_window=first_window,
                        last_window=int(time.time() // 60) + 1,
                    )
                redis_clean = await _release_probe_redis(
                    redis, owner_value=owner_value, keys=keys
                )
            await redis.aclose()
            await database.dispose()
        if not database_clean:
            raise ProbeFailure("RATE_LIMIT_PROBE_DATABASE_CLEANUP_FAILED")
        if redis_owned and not redis_clean:
            raise ProbeFailure("RATE_LIMIT_PROBE_REDIS_CLEANUP_FAILED")


def main() -> int:
    overrides = {
        "REDIS_URL": None,
        "AGENT_RUN_RATE_LIMIT_PER_MINUTE": "20",
        "INTERNAL_TOOL_RATE_LIMIT_PER_MINUTE": "60",
    }
    original_environment = {name: os.environ.get(name) for name in overrides}
    get_settings.cache_clear()
    try:
        base_settings = get_settings()
        overrides["REDIS_URL"] = build_probe_redis_url(base_settings.redis_url)
        for name, value in overrides.items():
            if value is not None:
                os.environ[name] = value
        get_settings.cache_clear()
        settings = get_settings()
        summary = asyncio.run(run_probe(settings, tag=_probe_tag()))
        summary = ProbeSummary(
            **{
                **summary.__dict__,
                "database_cleanup_verified": True,
                "redis_cleanup_verified": True,
            }
        )
        print(json.dumps(summary.public_dict(), ensure_ascii=False, sort_keys=True))
        return 0
    except ProbeFailure as exc:
        print(json.dumps({"ok": False, "error_code": exc.code}, sort_keys=True))
        return 1
    except Exception:
        print(json.dumps({"ok": False, "error_code": "RATE_LIMIT_PROBE_FAILED"}, sort_keys=True))
        return 1
    finally:
        for name, original in original_environment.items():
            if original is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = original
        get_settings.cache_clear()


if __name__ == "__main__":
    raise SystemExit(main())
