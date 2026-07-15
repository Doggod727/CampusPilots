from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse

from app.core.config import get_settings
from app.core.request_id import REQUEST_ID_HEADER
from app.modules.agent_platform.orchestration.runtime import InMemoryCommandDispatcher
from app.modules.agent_platform.run_queries import RunDTO, RunDetailDTO, RunPageDTO
from app.modules.agent_platform.run_service import AgentRunService, agent_run_service_context
from app.modules.agent_platform.approval_decision import ApprovalDecisionService, approval_decision_service_context
from app.modules.agent_platform.run_queries import ApprovalDTO
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.auth_dependencies import get_authenticated_user, require_any_permission
from app.shared.responses import SuccessResponse

router=APIRouter(prefix="/api/v1/agent-runs",tags=["AgentRuns"])
dispatcher=InMemoryCommandDispatcher()


class AgentRunCreateRequest(BaseModel):
    model_config=ConfigDict(extra="forbid")
    input:str=Field(min_length=2,max_length=4000)
    conversation_id:UUID|None=None
    mode:Literal["auto","knowledge","service","community","governance","modelops"]="auto"
    context:dict[str,Any]=Field(default_factory=dict)


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
    async with agent_run_service_context(get_settings(),dispatcher) as service: yield service


async def get_approval_service() -> AsyncIterator[ApprovalDecisionService]:
    async with approval_decision_service_context(get_settings(),dispatcher) as service: yield service


@router.post("",operation_id="createAgentRun",status_code=202,response_model=RunResponse)
async def create_run(payload:AgentRunCreateRequest,request:Request,actor:Annotated[AuthenticatedUser,Depends(require_any_permission("agent:run","agent:run:create"))],service:Annotated[AgentRunService,Depends(get_run_service)],idempotency_key:Annotated[str,Header(alias="Idempotency-Key",min_length=1,max_length=128)]) -> JSONResponse:
    result=await service.create(actor=actor,input_text=payload.input,conversation_id=payload.conversation_id,mode=payload.mode,context=payload.context,idempotency_key=idempotency_key,request_id=request.state.request_id)
    return JSONResponse(result.body,status_code=result.status_code,headers={REQUEST_ID_HEADER:result.request_id})


@router.get("",operation_id="listAgentRuns",response_model=RunListResponse)
async def list_runs(request:Request,actor:Annotated[AuthenticatedUser,Depends(require_any_permission("agent:run:read_own","agent:run:read_all"))],service:Annotated[AgentRunService,Depends(get_run_service)],page:Annotated[int,Query(ge=1)]=1,page_size:Annotated[int,Query(ge=1,le=100)]=20,status:Literal["created","routing","running","awaiting_approval","succeeded","partial","failed","cancelled"]|None=None) -> RunListResponse:
    data=await service.list(actor=actor,page=page,page_size=page_size,status=status)
    return SuccessResponse(data=data,request_id=request.state.request_id,timestamp=datetime.now(UTC))


@router.get("/{run_id}",operation_id="getAgentRun",response_model=RunDetailResponse)
async def get_run(run_id:UUID,request:Request,actor:Annotated[AuthenticatedUser,Depends(require_any_permission("agent:run:read_own","agent:run:read_all"))],service:Annotated[AgentRunService,Depends(get_run_service)]) -> RunDetailResponse:
    data=await service.detail(actor=actor,run_id=run_id)
    return SuccessResponse(data=data,request_id=request.state.request_id,timestamp=datetime.now(UTC))


@router.post("/{run_id}/cancel",operation_id="cancelAgentRun",response_model=RunResponse)
async def cancel_run(run_id:UUID,request:Request,actor:Annotated[AuthenticatedUser,Depends(require_any_permission("agent:run","agent:run:cancel"))],service:Annotated[AgentRunService,Depends(get_run_service)],idempotency_key:Annotated[str,Header(alias="Idempotency-Key",min_length=1,max_length=128)]) -> JSONResponse:
    result=await service.cancel(actor=actor,run_id=run_id,idempotency_key=idempotency_key,request_id=request.state.request_id)
    return JSONResponse(result.body,status_code=result.status_code,headers={REQUEST_ID_HEADER:result.request_id})


@router.post("/{run_id}/approvals/{approval_id}",operation_id="decideAgentToolApproval",response_model=ApprovalResponse)
async def decide_approval(run_id:UUID,approval_id:UUID,payload:ApprovalDecisionRequest,request:Request,actor:Annotated[AuthenticatedUser,Depends(get_authenticated_user)],service:Annotated[ApprovalDecisionService,Depends(get_approval_service)],idempotency_key:Annotated[str,Header(alias="Idempotency-Key",min_length=1,max_length=128)]) -> JSONResponse:
    result=await service.decide(actor=actor,run_id=run_id,approval_id=approval_id,decision=payload.decision,argument_hash=payload.argument_hash,comment=payload.comment,idempotency_key=idempotency_key,request_id=request.state.request_id)
    return JSONResponse(result.body,status_code=result.status_code,headers={REQUEST_ID_HEADER:result.request_id})
