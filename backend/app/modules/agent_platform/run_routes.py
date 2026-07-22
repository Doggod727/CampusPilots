from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
import re
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.responses import JSONResponse
from starlette.responses import StreamingResponse

from app.core.config import get_settings
from app.core.request_id import REQUEST_ID_HEADER
from app.infrastructure.database import Database
from app.modules.agent_platform.event_stream import AgentEventCursorInvalid, AgentRunEventAccessRepository, AgentRunEventStreamService
from app.modules.agent_platform.runtime_persistence import RuntimeEventRepository
from app.modules.agent_platform.run_queries import RunDTO, RunDetailDTO, RunPageDTO
from app.modules.agent_platform.run_service import AgentRunService, agent_run_service_context
from app.modules.agent_platform.approval_decision import ApprovalDecisionService, approval_decision_service_context
from app.modules.agent_platform.run_queries import ApprovalDTO
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.auth_dependencies import get_authenticated_user, require_any_permission
from app.shared.responses import SuccessResponse
from app.modules.agent_platform.rate_limit import (
    RateLimitPort,
    RedisRateLimiter,
    user_ip_rate_limit_subjects,
)
from redis.asyncio import Redis

router=APIRouter(prefix="/api/v1/agent-runs",tags=["AgentRuns"])


class AgentRunCreateRequest(BaseModel):
    model_config=ConfigDict(extra="forbid")
    input:str=Field(min_length=2,max_length=4000)
    conversation_id:UUID|None=None
    mode:Literal["auto","knowledge","service","community","governance","modelops"]="auto"
    context:dict[str,Any]=Field(default_factory=dict)

    @field_validator("context")
    @classmethod
    def validate_runtime_selection(cls, value: dict[str, Any]) -> dict[str, Any]:
        agents = value.get("requested_agent_codes", [])
        allowed_agents = {
            "knowledge_agent", "service_agent", "community_agent",
            "governance_agent", "modelops_agent",
        }
        if not isinstance(agents, list) or len(agents) > 3 or any(
            not isinstance(item, str) or item not in allowed_agents for item in agents
        ) or len(set(agents)) != len(agents):
            raise ValueError("requested_agent_codes must contain up to 3 unique specialist codes")
        tools = value.get("requested_tool_names", [])
        if not isinstance(tools, list) or len(tools) > 14 or any(
            not isinstance(item, str)
            or re.fullmatch(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$", item) is None
            for item in tools
        ) or len(set(tools)) != len(tools):
            raise ValueError("requested_tool_names must contain up to 14 unique tool names")
        return value


RunResponse=SuccessResponse[RunDTO]
RunListResponse=SuccessResponse[RunPageDTO]
RunDetailResponse=SuccessResponse[RunDetailDTO]
ApprovalResponse=SuccessResponse[ApprovalDTO]


class ApprovalDecisionRequest(BaseModel):
    model_config=ConfigDict(extra="forbid")
    decision:Literal["approve","reject"]
    argument_hash:str=Field(pattern=r"^[0-9a-f]{64}$",repr=False)
    comment:str|None=Field(default=None,max_length=300)


async def get_run_service() -> AsyncIterator[AgentRunService]:
    async with agent_run_service_context(get_settings()) as service: yield service


async def get_approval_service() -> AsyncIterator[ApprovalDecisionService]:
    async with approval_decision_service_context(get_settings()) as service: yield service


async def get_event_stream_service() -> AsyncIterator[AgentRunEventStreamService]:
    settings = get_settings()
    database = Database.from_settings(settings)
    try:
        async with database.session() as session:
            yield AgentRunEventStreamService(
                access=AgentRunEventAccessRepository(session),
                events=RuntimeEventRepository(session),
            )
    finally:
        await database.dispose()


async def get_agent_rate_limiter() -> AsyncIterator[RateLimitPort]:
    settings = get_settings()
    client = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        yield RedisRateLimiter(client)
    finally:
        await client.aclose()


def get_agent_run_rate_limit() -> int:
    return get_settings().agent_run_rate_limit_per_minute


async def enforce_agent_run_rate_limit(
    request: Request,
    actor: Annotated[
        AuthenticatedUser,
        Depends(require_any_permission("agent:run", "agent:run:create")),
    ],
    limiter: Annotated[RateLimitPort, Depends(get_agent_rate_limiter)],
    rate_limit: Annotated[int, Depends(get_agent_run_rate_limit)],
) -> AuthenticatedUser:
    client_ip = request.client.host if request.client else "unknown"
    await limiter.check(
        scope="agent_run",
        subjects=user_ip_rate_limit_subjects(actor.user_id, client_ip),
        limit=rate_limit,
    )
    return actor


@router.post("",operation_id="createAgentRun",status_code=202,response_model=RunResponse)
async def create_run(payload:AgentRunCreateRequest,request:Request,actor:Annotated[AuthenticatedUser,Depends(enforce_agent_run_rate_limit)],service:Annotated[AgentRunService,Depends(get_run_service)],idempotency_key:Annotated[str,Header(alias="Idempotency-Key",min_length=1,max_length=128)]) -> JSONResponse:
    result=await service.create(actor=actor,input_text=payload.input,conversation_id=payload.conversation_id,mode=payload.mode,context=payload.context,idempotency_key=idempotency_key,request_id=request.state.request_id)
    return JSONResponse(result.body,status_code=result.status_code,headers={REQUEST_ID_HEADER:result.request_id})


@router.get("",operation_id="listAgentRuns",response_model=RunListResponse)
async def list_runs(request:Request,actor:Annotated[AuthenticatedUser,Depends(require_any_permission("agent:run:read_own","agent:run:read_all"))],service:Annotated[AgentRunService,Depends(get_run_service)],page:Annotated[int,Query(ge=1)]=1,page_size:Annotated[int,Query(ge=1,le=100)]=20,status:Literal["created","routing","running","awaiting_input","awaiting_approval","succeeded","partial","failed","cancelled"]|None=None,conversation_id:UUID|None=None) -> RunListResponse:
    data=await service.list(actor=actor,page=page,page_size=page_size,status=status,conversation_id=conversation_id)
    return SuccessResponse(data=data,request_id=request.state.request_id,timestamp=datetime.now(UTC))


@router.get("/{run_id}",operation_id="getAgentRun",response_model=RunDetailResponse)
async def get_run(run_id:UUID,request:Request,actor:Annotated[AuthenticatedUser,Depends(require_any_permission("agent:run:read_own","agent:run:read_all"))],service:Annotated[AgentRunService,Depends(get_run_service)]) -> RunDetailResponse:
    data=await service.detail(actor=actor,run_id=run_id)
    return SuccessResponse(data=data,request_id=request.state.request_id,timestamp=datetime.now(UTC))


@router.get("/{run_id}/stream",operation_id="streamAgentRun")
async def stream_run(
    run_id: UUID,
    request: Request,
    actor: Annotated[AuthenticatedUser, Depends(require_any_permission("agent:run:read_own", "agent:run:read_all"))],
    service: Annotated[AgentRunEventStreamService, Depends(get_event_stream_service)],
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID", max_length=100)] = None,
) -> StreamingResponse:
    if last_event_id is None:
        cursor = 0
    elif re.fullmatch(r"[0-9]+", last_event_id) is None:
        raise AgentEventCursorInvalid()
    else:
        cursor = int(last_event_id)
    prepared = await service.prepare(
        run_id=run_id, user_id=actor.user_id,
        can_read_all="agent:run:read_all" in actor.permissions,
        after_sequence=cursor, request_id=request.state.request_id,
    )
    return StreamingResponse(
        service.iterate(prepared), media_type="text/event-stream",
        headers={
            REQUEST_ID_HEADER: request.state.request_id,
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{run_id}/cancel",operation_id="cancelAgentRun",response_model=RunResponse)
async def cancel_run(run_id:UUID,request:Request,actor:Annotated[AuthenticatedUser,Depends(require_any_permission("agent:run","agent:run:cancel"))],service:Annotated[AgentRunService,Depends(get_run_service)],idempotency_key:Annotated[str,Header(alias="Idempotency-Key",min_length=1,max_length=128)]) -> JSONResponse:
    result=await service.cancel(actor=actor,run_id=run_id,idempotency_key=idempotency_key,request_id=request.state.request_id)
    return JSONResponse(result.body,status_code=result.status_code,headers={REQUEST_ID_HEADER:result.request_id})


@router.post("/{run_id}/approvals/{approval_id}",operation_id="decideAgentToolApproval",response_model=ApprovalResponse)
async def decide_approval(run_id:UUID,approval_id:UUID,payload:ApprovalDecisionRequest,request:Request,actor:Annotated[AuthenticatedUser,Depends(get_authenticated_user)],service:Annotated[ApprovalDecisionService,Depends(get_approval_service)],idempotency_key:Annotated[str,Header(alias="Idempotency-Key",min_length=1,max_length=128)]) -> JSONResponse:
    result=await service.decide(actor=actor,run_id=run_id,approval_id=approval_id,decision=payload.decision,argument_hash=payload.argument_hash,comment=payload.comment,idempotency_key=idempotency_key,request_id=request.state.request_id)
    return JSONResponse(result.body,status_code=result.status_code,headers={REQUEST_ID_HEADER:result.request_id})
