from __future__ import annotations

from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.core.config import Settings
from app.infrastructure.database import Database
from app.modules.agent_platform.domain.contracts import UserContext
from app.modules.agent_platform.checkpointing import RuntimeStartPayloadCodec, RuntimeTerminalCoordinator
from app.modules.agent_platform.orchestration.runtime import RuntimeDispatcherPort
from app.modules.agent_platform.run_queries import AgentRunQueryRepository, AgentRunQueryService, RunDTO, RunDetailDTO, RunPageDTO, run_dto
from app.modules.agent_platform.traces import AgentRunNotFound, AgentRunStateConflict, TraceRepository, TraceService
from app.modules.agent_platform.runtime_persistence import RuntimeCheckpointRepository, RuntimeCommandRepository, RuntimeEventRepository
from app.modules.agent_platform.runtime_worker import OutboxRuntimeDispatcher
from app.modules.agent_platform.runtime_worker import RedisRuntimeWakeup
from app.modules.ai_knowledge.conversations import ConversationNotFound, ConversationRepository
from redis.asyncio import Redis
from app.modules.platform.audit import redact
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.idempotency import IdempotencyConflict, IdempotencyService
from app.modules.platform.repositories import IdempotencyRecordRepository
from app.shared.responses import SuccessResponse
from app.core.errors import AppError


AGENT_DEBUG_PERMISSIONS = frozenset({"model:read", "tool:catalog:write"})


class AgentDebugForbidden(AppError):
    def __init__(self) -> None:
        super().__init__(
            status_code=403,
            code="AGENT_DEBUG_FORBIDDEN",
            message="当前账号不能显式选择 Agent、Tool 或内部运行模式",
        )


@dataclass(frozen=True)
class RunMutationResult:
    status_code: int
    request_id: str
    body: dict[str, Any] = field(repr=False)


class AgentRunService:
    def __init__(self, *, session, trace: TraceService, queries: AgentRunQueryService,
                 idempotency: IdempotencyService, dispatcher: RuntimeDispatcherPort,
                 terminal: RuntimeTerminalCoordinator,
                 conversations: ConversationRepository | None = None,
                 now=None) -> None:
        self._session=session; self._trace=trace; self._queries=queries
        self._idempotency=idempotency; self._dispatcher=dispatcher; self._terminal=terminal
        self._conversations=conversations
        self._now=now or (lambda:datetime.now(UTC))

    async def create(self, *, actor:AuthenticatedUser, input_text:str, conversation_id:UUID|None,
                     mode:str, context:Mapping[str,Any], idempotency_key:str, request_id:str) -> RunMutationResult:
        request_body={"input":input_text,"conversation_id":str(conversation_id) if conversation_id else None,"mode":mode,"context":redact(context) or {}}
        runtime_context = dict(request_body["context"])
        runtime_context["_run_mode"] = mode
        requested_agents = runtime_context.get("requested_agent_codes")
        requested_tools = runtime_context.get("requested_tool_names")
        if (mode != "auto" or requested_agents or requested_tools) and not (
            AGENT_DEBUG_PERMISSIONS & set(actor.permissions)
        ):
            raise AgentDebugForbidden()
        if mode != "auto" and not requested_agents:
            runtime_context["requested_agent_codes"] = [f"{mode}_agent"]
        async with self._session.begin():
            decision=await self._idempotency.begin(user_id=actor.user_id,endpoint="POST /api/v1/agent-runs",idempotency_key=idempotency_key,request_body=request_body)
            if decision.replay: return RunMutationResult(decision.replay.response_status,str(decision.replay.response_body["request_id"]),dict(decision.replay.response_body))
            if decision.pending: raise IdempotencyConflict()
            if conversation_id is not None and self._conversations is not None:
                conversation = await self._conversations.get_owned(
                    conversation_id, actor.user_id, lock=True,
                )
                if conversation is None:
                    raise ConversationNotFound()
                now = self._utc()
                conversation.last_message_at = now
                conversation.updated_at = now
                if conversation.title == "新对话":
                    conversation.title = input_text[:100]
            run=self._trace.create_run(user_id=actor.user_id,client_request_id=request_id,input_summary=input_text[:1000],conversation_id=conversation_id)
            await self._dispatcher.start(run.id,_user_context(actor,request_id),input_text,runtime_context)
            data=run_dto(run,())
            response=SuccessResponse(data=data,request_id=request_id,timestamp=self._utc()).model_dump(mode="json")
            if not await self._idempotency.complete(record_id=decision.record_id,response_status=202,response_body=response,resource_type="agent_run",resource_id=str(run.id)):
                raise AgentRunStateConflict()
        if isinstance(self._dispatcher, OutboxRuntimeDispatcher):
            await self._dispatcher.notify_best_effort()
        return RunMutationResult(202,request_id,response)

    async def list(self, *, actor:AuthenticatedUser, page:int, page_size:int, status:str|None,
                   conversation_id:UUID|None=None) -> RunPageDTO:
        return await self._queries.list_runs(
            user_id=actor.user_id,
            can_read_all="agent:run:read_all" in actor.permissions,
            page=page,
            page_size=page_size,
            status=status,
            conversation_id=conversation_id,
        )

    async def detail(self, *, actor:AuthenticatedUser, run_id:UUID) -> RunDetailDTO:
        return await self._queries.get_detail(run_id=run_id,user_id=actor.user_id,can_read_all="agent:run:read_all" in actor.permissions)

    async def cancel(self, *, actor:AuthenticatedUser, run_id:UUID, idempotency_key:str, request_id:str) -> RunMutationResult:
        should_dispatch = False
        async with self._session.begin():
            decision=await self._idempotency.begin(user_id=actor.user_id,endpoint=f"POST /api/v1/agent-runs/{run_id}/cancel",idempotency_key=idempotency_key,request_body={"run_id":str(run_id)})
            if decision.replay: return RunMutationResult(decision.replay.response_status,str(decision.replay.response_body["request_id"]),dict(decision.replay.response_body))
            if decision.pending: raise IdempotencyConflict()
            aggregate=await self._queries.get_aggregate(run_id=run_id,user_id=actor.user_id,can_read_all=False)
            if aggregate is None: raise AgentRunNotFound()
            if aggregate.run.status not in TraceService.TERMINAL:
                await self._trace.finalize(run_id,"cancelled",finish_reason="user_cancelled")
                await self._terminal.complete(
                    run_id=run_id,
                    status="cancelled",
                    request_id=request_id,
                )
                aggregate.run.status="cancelled"; aggregate.run.finished_at=self._utc(); aggregate.run.updated_at=self._utc()
                await self._dispatcher.cancel(run_id)
                should_dispatch = True
            data=run_dto(aggregate.run,aggregate.steps)
            response=SuccessResponse(data=data,request_id=request_id,timestamp=self._utc()).model_dump(mode="json")
            if not await self._idempotency.complete(record_id=decision.record_id,response_status=200,response_body=response,resource_type="agent_run",resource_id=str(run_id)):
                raise AgentRunStateConflict()
        if should_dispatch and isinstance(self._dispatcher, OutboxRuntimeDispatcher):
            await self._dispatcher.notify_best_effort()
        return RunMutationResult(200,request_id,response)

    def _utc(self):
        value=self._now(); return value if value.tzinfo else value.replace(tzinfo=UTC)


def _user_context(user:AuthenticatedUser,request_id:str) -> UserContext:
    return UserContext(user_id=user.user_id,username=user.username,roles=tuple(role.code for role in user.roles),permissions=user.permissions,request_id=request_id)


@asynccontextmanager
async def agent_run_service_context(settings:Settings):
    database=Database.from_settings(settings)
    redis=Redis.from_url(settings.redis_url,decode_responses=True)
    try:
        async with database.session() as session:
            query_repo=AgentRunQueryRepository(session)
            dispatcher=OutboxRuntimeDispatcher(
                RuntimeCommandRepository(session),
                max_attempts=settings.agent_runtime_max_attempts,
                wakeup=RedisRuntimeWakeup(redis),
                start_codec=RuntimeStartPayloadCodec(
                    settings.agent_checkpoint_secret.get_secret_value()
                ) if settings.agent_checkpoint_secret is not None else None,
            )
            yield AgentRunService(
                session=session,
                trace=TraceService(TraceRepository(session)),
                queries=AgentRunQueryService(query_repo),
                idempotency=IdempotencyService(session=session,repository=IdempotencyRecordRepository(session)),
                dispatcher=dispatcher,
                terminal=RuntimeTerminalCoordinator(
                    RuntimeCheckpointRepository(session), RuntimeEventRepository(session)
                ),
                conversations=ConversationRepository(session),
            )
    finally:
        await redis.aclose()
        await database.dispose()
