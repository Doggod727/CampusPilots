"""DeepSeek Provider 故障矩阵真实环境探针（#191）。

两阶段结构：
- Phase A（传输矩阵）：真实 httpx 网关 × 三类故障（错误 Key/不可达地址/本地延迟服务）
  × 四条调用链（Router/Specialist/M1 RAG Answer/SSE 流），断言 502/504 稳定映射与泄密防护。
- Phase B（公共边界）：进程内覆盖 DEEPSEEK_BASE_URL 为不可达地址（不改父进程与 .env），
  真实 API + PostgreSQL 验证 sync chat 502 信封、SSE error 事件、Agent Run 收敛 failed，
  并扫描 HTTP 响应、SSE、数据库事件/审计/消息中无密钥与上游正文；探针数据精确清理。

只输出稳定错误码与公开摘要，绝不打印令牌、密钥、连接串或上游正文。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
import socket
from typing import Any, AsyncIterator, Mapping
from uuid import UUID, uuid4

import httpx
from sqlalchemy import delete, func, select

from app.modules.agent_platform.deepseek import (
    DeepSeekGateway,
    DeepSeekRouterAdapter,
    DeepSeekSpecialistProvider,
    DeepSeekTimeout,
    DeepSeekUnavailable,
)
from app.modules.agent_platform.domain.contracts import AgentTask, UserContext

PROBE_KEY = f"sk-probe-invalid-{uuid4().hex[:16]}"  # 非真实密钥，仅用于故障注入
DELAY_BODY_MARKER = "delayed-probe-marker"
UNAVAILABLE = "AGENT_PROVIDER_UNAVAILABLE"
TIMEOUT = "AGENT_PROVIDER_TIMEOUT"
FORBIDDEN_MARKERS = (PROBE_KEY, PROBE_KEY[-8:], "Authentication Fails", DELAY_BODY_MARKER)
GATEWAY_ENTRIES = ("router", "specialist", "rag_answer", "sse_stream")


class ProbeFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def assert_no_leak(rendered: str, *, stage: str, forbidden: tuple[str, ...] = FORBIDDEN_MARKERS) -> None:
    for marker in forbidden:
        if marker and marker in rendered:
            raise ProbeFailure(f"PROVIDER_FAULT_PROBE_LEAK_{stage}")


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


class SlowUpstreamServer:
    """接受请求但延迟响应的本地上游，用于触发真实 httpx 读取超时。"""

    def __init__(self, delay_seconds: float = 5.0) -> None:
        self._delay = delay_seconds
        self._server: asyncio.AbstractServer | None = None
        self.port = 0

    async def __aenter__(self) -> "SlowUpstreamServer":
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = int(self._server.sockets[0].getsockname()[1])
        return self

    async def __aexit__(self, *_args: Any) -> bool:
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()
        return False

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            with suppress(Exception):
                await asyncio.wait_for(reader.read(65536), timeout=2.0)
            await asyncio.sleep(self._delay)
            payload = json.dumps({"choices": [{"message": {"content": json.dumps({"answer": DELAY_BODY_MARKER})}}]})
            body = payload.encode()
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                + str(len(body)).encode()
                + b"\r\n\r\n"
                + body
            )
            await writer.drain()
        finally:
            writer.close()


async def run_gateway_entry(entry: str, gateway: DeepSeekGateway) -> None:
    if entry == "router":
        await DeepSeekRouterAdapter(gateway).route("查询图书馆规定")
        return
    if entry == "specialist":
        await DeepSeekSpecialistProvider(gateway).invoke(
            AgentTask(task_id=uuid4(), agent_run_id=uuid4(), target_agent="knowledge_agent", objective="查询校规"),
            UserContext(user_id=uuid4(), username="probe", request_id="provider-fault-probe"),
        )
        return
    if entry == "rag_answer":
        await gateway.json_completion(
            (
                {"role": "system", "content": "仅依据sources回答，输出JSON对象answer，不输出思维链。"},
                {"role": "user", "content": json.dumps({"question": "校历安排", "sources": [{"source": 1, "content": "九月中旬开学"}]}, ensure_ascii=False)},
            )
        )
        return
    if entry == "sse_stream":
        async for _ in gateway.stream_text(({"role": "user", "content": "校历安排"},)):
            pass
        return
    raise ProbeFailure("PROVIDER_FAULT_PROBE_ENTRY_UNKNOWN")


@dataclass(frozen=True)
class MatrixCase:
    name: str
    expected_code: str
    expected_status: int


MATRIX_CASES = (
    MatrixCase("rejected_credentials", UNAVAILABLE, 502),
    MatrixCase("unreachable_endpoint", UNAVAILABLE, 502),
    MatrixCase("delayed_upstream", TIMEOUT, 504),
)


async def expect_provider_error(entry: str, case: MatrixCase, gateway: DeepSeekGateway) -> None:
    expected = DeepSeekUnavailable if case.expected_code == UNAVAILABLE else DeepSeekTimeout
    try:
        await run_gateway_entry(entry, gateway)
    except expected as exc:
        if exc.status_code != case.expected_status or exc.code != case.expected_code:
            raise ProbeFailure(f"PROVIDER_FAULT_PROBE_MAPPING_{case.name}_{entry}") from exc
        assert_no_leak(f"{exc!r}|{exc.details}", stage=f"{case.name}_{entry}")
        return
    except Exception as exc:  # noqa: BLE001 - 任何其他异常类都是映射缺陷
        raise ProbeFailure(f"PROVIDER_FAULT_PROBE_CLASS_{case.name}_{entry}") from exc
    raise ProbeFailure(f"PROVIDER_FAULT_PROBE_NOERROR_{case.name}_{entry}")


async def phase_matrix() -> dict[str, bool]:
    results: dict[str, bool] = {}
    # 1) 错误 Key（真实 DeepSeek 端点；鉴权拒绝不消耗配额；网络不可达时同样映射 502）
    bad_key_gateway = DeepSeekGateway(api_key=PROBE_KEY)
    for entry in GATEWAY_ENTRIES:
        await expect_provider_error(entry, MATRIX_CASES[0], bad_key_gateway)
        results[f"rejected_credentials_{entry}"] = True
    # 2) 不可达地址（loopback 空闲端口，连接即被拒绝）
    refused_port = _free_loopback_port()
    unreachable_gateway = DeepSeekGateway(api_key=PROBE_KEY, base_url=f"http://127.0.0.1:{refused_port}")
    for entry in GATEWAY_ENTRIES:
        await expect_provider_error(entry, MATRIX_CASES[1], unreachable_gateway)
        results[f"unreachable_endpoint_{entry}"] = True
    # 3) 本地延迟服务（1 秒客户端超时 vs 5 秒延迟响应 → 真实读取超时）
    async with SlowUpstreamServer(delay_seconds=5.0) as slow:
        delayed_gateway = DeepSeekGateway(
            api_key=PROBE_KEY, base_url=f"http://127.0.0.1:{slow.port}", timeout_seconds=1
        )
        for entry in GATEWAY_ENTRIES:
            await expect_provider_error(entry, MATRIX_CASES[2], delayed_gateway)
            results[f"delayed_upstream_{entry}"] = True
    return results


def _request_headers(token: str, prefix: str, label: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": f"{prefix}{label}",
        "X-Request-Id": f"{prefix}{label}",
    }


@asynccontextmanager
async def _serve_api(port: int) -> AsyncIterator[None]:
    import uvicorn

    from app.main import create_app

    server = uvicorn.Server(
        uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="critical", access_log=False)
    )
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(300):
            if server.started:
                break
            await asyncio.sleep(0.1)
        else:
            raise ProbeFailure("PROVIDER_FAULT_PROBE_API_START_TIMEOUT")
        yield
    finally:
        server.should_exit = True
        await task


async def phase_boundary(prefix: str) -> dict[str, Any]:
    from app.core.config import Settings
    from app.infrastructure.database import Database
    from app.modules.agent_platform.composition import RuntimeCompositionFactory
    from app.modules.agent_platform.models import AgentRun
    from app.modules.agent_platform.runtime_worker import RuntimeWorker, TraceRuntimeFailureHandler
    from app.modules.ai_knowledge.models import Conversation, KnowledgeBase, Message
    from app.modules.platform.models import AuditLog, IdempotencyRecord
    from app.modules.platform.repositories import RbacRepository, UserRepository
    from app.modules.platform.tokens import TokenService

    os.environ["DEEPSEEK_BASE_URL"] = f"http://127.0.0.1:{_free_loopback_port()}"
    settings = Settings()
    database = Database.from_settings(settings)
    evidence: dict[str, Any] = {}
    try:
        async with database.session() as session:
            # 活动命令前置校验：探针 Run 只能由本进程内 Worker 领取
            from app.modules.agent_platform.models import AgentRuntimeCommand

            active = (
                await session.execute(
                    select(func.count())
                    .select_from(AgentRuntimeCommand)
                    .where(AgentRuntimeCommand.status.in_(("pending", "processing")))
                )
            ).scalar_one()
            if active:
                raise ProbeFailure("PROVIDER_FAULT_PROBE_ACTIVE_COMMANDS_PRESENT")
            knowledge_base_id = (
                await session.execute(select(KnowledgeBase.id).limit(1))
            ).scalar_one_or_none()
            if knowledge_base_id is None:
                raise ProbeFailure("PROVIDER_FAULT_PROBE_KB_UNAVAILABLE")
            users = UserRepository(session)
            rbac = RbacRepository(session)
            tokens = TokenService(settings)
            identities: dict[str, str] = {}
            for username in ("knowledge01", "student01"):
                user = await users.get_by_username(username)
                if user is None or user.status != "active":
                    raise ProbeFailure("PROVIDER_FAULT_PROBE_DEMO_USER_UNAVAILABLE")
                roles = await rbac.list_roles_for_user(user.id)
                permissions = await rbac.list_permission_codes_for_user(user.id)
                access = tokens.issue_access(
                    user_id=user.id,
                    username=user.username,
                    roles=(role.code for role in roles),
                    permissions=permissions,
                )
                identities[username] = access.token

        port = _free_loopback_port()
        async with _serve_api(port):
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=90.0) as client:
                # 1) 同步 Chat：不可达 Provider → 502 AGENT_PROVIDER_UNAVAILABLE 统一信封
                sync_headers = _request_headers(identities["student01"], prefix, "chat-sync-001")
                sync_response = await client.post(
                    "/api/v1/chat/completions",
                    json={"question": "四川大学有几个校区？请说明各校区地址。", "knowledge_base_ids": [str(knowledge_base_id)]},
                    headers=sync_headers,
                )
                observed_status = sync_response.status_code
                try:
                    observed_code = str(sync_response.json().get("code", "NO_CODE"))[:40]
                except Exception:
                    observed_code = "NON_JSON"
                if observed_status != 502 or observed_code != UNAVAILABLE:
                    raise ProbeFailure(f"PROVIDER_FAULT_PROBE_CHAT_SYNC_MAPPING_{observed_status}_{observed_code}")
                if sync_response.json().get("request_id") != sync_headers["X-Request-Id"]:
                    raise ProbeFailure("PROVIDER_FAULT_PROBE_CHAT_SYNC_REQUEST_ID")
                assert_no_leak(sync_response.text, stage="CHAT_SYNC")
                evidence["chat_sync_502"] = True

                # 2) SSE Chat：error 事件携带稳定错误码且不含敏感内容
                sse_headers = _request_headers(identities["student01"], prefix, "chat-sse-001")
                sse_events: list[tuple[str, str]] = []
                async with client.stream(
                    "POST",
                    "/api/v1/chat/stream",
                    json={"question": "四川大学有几个校区？望江校区地址是什么？", "knowledge_base_ids": [str(knowledge_base_id)]},
                    headers=sse_headers,
                ) as sse_response:
                    if sse_response.status_code != 200:
                        raise ProbeFailure("PROVIDER_FAULT_PROBE_CHAT_SSE_STATUS")
                    event_name = ""
                    async for line in sse_response.aiter_lines():
                        if line.startswith("event: "):
                            event_name = line[7:].strip()
                        elif line.startswith("data: ") and event_name:
                            sse_events.append((event_name, line[6:]))
                            event_name = ""
                        if sse_events and sse_events[-1][0] in {"done", "error"}:
                            break
                if not sse_events or sse_events[-1][0] != "error":
                    sequence = "_".join(name for name, _ in sse_events) or "EMPTY"
                    done_reason = ""
                    if sse_events and sse_events[-1][0] == "done":
                        with suppress(Exception):
                            done_reason = str(json.loads(sse_events[-1][1]).get("finish_reason", ""))[:20]
                    raise ProbeFailure(
                        f"PROVIDER_FAULT_PROBE_CHAT_SSE_NO_ERROR_EVENT_{sequence[:40]}_{done_reason}"
                    )
                error_payload = json.loads(sse_events[-1][1])
                if error_payload.get("code") != UNAVAILABLE:
                    raise ProbeFailure("PROVIDER_FAULT_PROBE_CHAT_SSE_CODE")
                assert_no_leak(json.dumps(error_payload, ensure_ascii=False), stage="CHAT_SSE")
                evidence["chat_sse_error_event"] = True

                # 3) Agent Run：Specialist 不可达 → Run 收敛 failed + 稳定错误码
                run_headers = _request_headers(identities["student01"], prefix, "run-001")
                run_response = await client.post(
                    "/api/v1/agent-runs",
                    json={"input": "查询图书馆知识文档与校规问答", "mode": "auto"},
                    headers=run_headers,
                )
                if run_response.status_code != 202:
                    raise ProbeFailure("PROVIDER_FAULT_PROBE_RUN_CREATE")
                run_id = UUID(run_response.json()["data"]["id"])
                evidence["run_created"] = True

        factory = RuntimeCompositionFactory(settings)
        worker = RuntimeWorker(
            sessions=database.session,
            processor_factory=factory.command_processor,
            worker_id=f"provider-fault-probe:{os.getpid()}",
            poll_interval=0.5,
            failures=TraceRuntimeFailureHandler(),
        )
        terminal_status = ""
        deadline = datetime.now(UTC).timestamp() + 90
        while datetime.now(UTC).timestamp() < deadline:
            await worker.run_once()
            async with database.session() as session:
                terminal_status = (
                    await session.execute(select(AgentRun.status).where(AgentRun.id == run_id))
                ).scalar_one()
            if terminal_status in {"succeeded", "partial", "failed", "cancelled"}:
                break
            await asyncio.sleep(0.5)
        if terminal_status != "failed":
            raise ProbeFailure("PROVIDER_FAULT_PROBE_RUN_TERMINAL")

        async with database.session() as session:
            run = (
                await session.execute(
                    select(AgentRun).where(AgentRun.id == run_id)
                )
            ).scalar_one()
            if run.error_code != UNAVAILABLE:
                raise ProbeFailure("PROVIDER_FAULT_PROBE_RUN_ERROR_CODE")
            evidence["run_failed_502"] = True
            from app.modules.agent_platform.models import AgentRunEvent, AgentRuntimeCommand

            event_rows = (
                (
                    await session.execute(
                        select(AgentRunEvent.event, AgentRunEvent.data).where(AgentRunEvent.run_id == run_id)
                    )
                )
                .all()
            )
            assert_no_leak(json.dumps([dict(row._mapping) for row in event_rows], ensure_ascii=False, default=str), stage="RUN_EVENTS")
            command_errors = (
                (
                    await session.execute(
                        select(AgentRuntimeCommand.error_code).where(AgentRuntimeCommand.run_id == run_id)
                    )
                )
                .scalars()
                .all()
            )
            if any(code not in {UNAVAILABLE, None} for code in command_errors):
                raise ProbeFailure("PROVIDER_FAULT_PROBE_COMMAND_ERROR_CODE")
            evidence["run_evidence_clean"] = True

            probe_messages = (
                (
                    await session.execute(
                        select(Message).where(Message.request_id.like(f"{prefix}%"))
                    )
                )
                .scalars()
                .all()
            )
            for message in probe_messages:
                if message.error_code not in {UNAVAILABLE, None}:
                    raise ProbeFailure("PROVIDER_FAULT_PROBE_MESSAGE_ERROR_CODE")
                assert_no_leak(f"{message.content}|{message.error_code}", stage="MESSAGES")
            evidence["message_evidence_clean"] = True

            audit_rows = (
                (
                    await session.execute(
                        select(AuditLog).where(AuditLog.request_id.like(f"{prefix}%"))
                    )
                )
                .scalars()
                .all()
            )
            for audit in audit_rows:
                assert_no_leak(
                    json.dumps(
                        {
                            "action": audit.action,
                            "before_data": audit.before_data,
                            "after_data": audit.after_data,
                        },
                        ensure_ascii=False,
                        default=str,
                    ),
                    stage="AUDIT",
                )
            evidence["audit_evidence_clean"] = True
    finally:
        async with database.session() as session:
            async with session.begin():
                await session.execute(delete(AuditLog).where(AuditLog.request_id.like(f"{prefix}%")))
                await session.execute(
                    delete(IdempotencyRecord).where(IdempotencyRecord.idempotency_key.like(f"{prefix}%"))
                )
                await session.execute(
                    delete(Conversation).where(
                        Conversation.id.in_(
                            select(Message.conversation_id).where(Message.request_id.like(f"{prefix}%"))
                        )
                    )
                )
                await session.execute(delete(AgentRun).where(AgentRun.client_request_id.like(f"{prefix}%")))
        async with database.session() as session:
            from app.modules.agent_platform.models import AgentRun as RunModel

            leftovers = (
                await session.execute(
                    select(func.count()).select_from(RunModel).where(RunModel.client_request_id.like(f"{prefix}%"))
                )
            ).scalar_one()
            audit_leftovers = (
                await session.execute(
                    select(func.count()).select_from(AuditLog).where(AuditLog.request_id.like(f"{prefix}%"))
                )
            ).scalar_one()
            conversation_leftovers = (
                await session.execute(
                    select(func.count())
                    .select_from(Message)
                    .where(Message.request_id.like(f"{prefix}%"))
                )
            ).scalar_one()
        evidence["database_cleanup"] = leftovers == audit_leftovers == conversation_leftovers == 0
        if not evidence["database_cleanup"]:
            raise ProbeFailure("PROVIDER_FAULT_PROBE_CLEANUP_FAILED")
        await database.dispose()
    return evidence


async def amain() -> None:
    tag = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    prefix = f"dfp-{tag}-{uuid4().hex[:6]}-"
    matrix = await phase_matrix()
    boundary = await phase_boundary(prefix)
    summary = {
        "ok": True,
        "matrix_checks": len(matrix),
        "matrix": matrix,
        "boundary": boundary,
    }
    rendered = json.dumps(summary, ensure_ascii=False, sort_keys=True)
    assert_no_leak(rendered, stage="SUMMARY")
    print(rendered)


def main() -> None:
    try:
        asyncio.run(amain())
    except ProbeFailure as exc:
        print(json.dumps({"ok": False, "error": exc.code}, ensure_ascii=False))
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
