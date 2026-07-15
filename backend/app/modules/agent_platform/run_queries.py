from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import ceil
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_platform.models import (
    AgentRun,
    AgentStep,
    ApprovalRequestModel,
    ToolCall,
)
from app.modules.agent_platform.tool_gateway.catalog import TOOL_CONTRACTS
from app.modules.agent_platform.traces import AgentRunNotFound
from app.modules.platform.audit import redact
from app.modules.platform.user_schemas import PageMetaData

RunStatus = Literal[
    "created", "routing", "running", "awaiting_approval",
    "succeeded", "partial", "failed", "cancelled",
]
RouteName = Literal[
    "knowledge", "service", "community", "governance", "modelops",
    "mixed", "clarify",
]


class RunDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    status: RunStatus
    route: RouteName | None
    router_model: str | None
    router_confidence: float | None = Field(default=None, ge=0, le=1)
    input_summary: str
    final_answer: str | None
    error_code: str | None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None


class RunStepDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    sequence: int = Field(ge=1)
    agent_code: str
    step_type: str
    status: str
    input_summary: dict[str, Any]
    output_summary: dict[str, Any]
    error_code: str | None
    started_at: datetime | None
    finished_at: datetime | None


class ToolCallDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    tool_name: str
    status: str
    risk_level: Literal["r0", "r1", "r2", "r3"]
    approval_id: UUID | None
    duration_ms: int | None = Field(default=None, ge=0)
    error_code: str | None
    started_at: datetime | None
    finished_at: datetime | None


class ApprovalDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    run_id: UUID
    tool_name: str
    argument_summary: dict[str, Any]
    status: str
    expires_at: datetime
    decided_at: datetime | None
    created_at: datetime


class RunDetailDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    run: RunDTO
    steps: tuple[RunStepDTO, ...]
    tool_calls: tuple[ToolCallDTO, ...]
    approvals: tuple[ApprovalDTO, ...]


class RunPageDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    items: tuple[RunDTO, ...]
    pagination: PageMetaData


@dataclass(frozen=True)
class RunAggregate:
    run: AgentRun
    steps: tuple[AgentStep, ...]
    tool_calls: tuple[ToolCall, ...]
    approvals: tuple[ApprovalRequestModel, ...]


class AgentRunQueryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_runs(
        self, *, user_id: UUID, can_read_all: bool, page: int, page_size: int,
        status: str | None = None,
    ) -> tuple[tuple[AgentRun, ...], int]:
        filters = [] if can_read_all else [AgentRun.user_id == user_id]
        if status is not None:
            filters.append(AgentRun.status == status)
        count_stmt = select(func.count()).select_from(AgentRun).where(*filters)
        rows_stmt = (
            select(AgentRun).where(*filters)
            .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
            .offset((page - 1) * page_size).limit(page_size)
        )
        total = (await self._session.execute(count_stmt)).scalar_one()
        rows = tuple((await self._session.execute(rows_stmt)).scalars().all())
        return rows, total

    async def get_aggregate(
        self, *, run_id: UUID, user_id: UUID, can_read_all: bool,
    ) -> RunAggregate | None:
        run_stmt = select(AgentRun).where(AgentRun.id == run_id)
        if not can_read_all:
            run_stmt = run_stmt.where(AgentRun.user_id == user_id)
        run = (await self._session.execute(run_stmt)).scalar_one_or_none()
        if run is None:
            return None
        steps = tuple((await self._session.execute(
            select(AgentStep).where(AgentStep.run_id == run_id)
            .order_by(AgentStep.sequence_no)
        )).scalars().all())
        calls = tuple((await self._session.execute(
            select(ToolCall).where(ToolCall.run_id == run_id)
            .order_by(ToolCall.created_at, ToolCall.id)
        )).scalars().all())
        approvals = tuple((await self._session.execute(
            select(ApprovalRequestModel).where(ApprovalRequestModel.run_id == run_id)
            .order_by(ApprovalRequestModel.created_at, ApprovalRequestModel.id)
        )).scalars().all())
        return RunAggregate(run, steps, calls, approvals)


class AgentRunQueryService:
    def __init__(self, repository: AgentRunQueryRepository) -> None:
        self._repository = repository

    async def list_runs(self, **kwargs: Any) -> RunPageDTO:
        rows, total = await self._repository.list_runs(**kwargs)
        page, page_size = kwargs["page"], kwargs["page_size"]
        return RunPageDTO(
            items=tuple(_run_dto(row, ()) for row in rows),
            pagination=PageMetaData(
                page=page, page_size=page_size, total=total,
                total_pages=ceil(total / page_size) if total else 0,
            ),
        )

    async def get_detail(self, **kwargs: Any) -> RunDetailDTO:
        aggregate = await self._repository.get_aggregate(**kwargs)
        if aggregate is None:
            raise AgentRunNotFound()
        approval_by_call = {item.tool_call_id: item.id for item in aggregate.approvals}
        call_by_id = {item.id: item for item in aggregate.tool_calls}
        return RunDetailDTO(
            run=_run_dto(aggregate.run, aggregate.steps),
            steps=tuple(_step_dto(item) for item in aggregate.steps),
            tool_calls=tuple(_tool_dto(item, approval_by_call.get(item.id)) for item in aggregate.tool_calls),
            approvals=tuple(_approval_dto(item, call_by_id[item.tool_call_id]) for item in aggregate.approvals if item.tool_call_id in call_by_id),
        )


def _run_dto(run: AgentRun, steps: tuple[AgentStep, ...]) -> RunDTO:
    route = run.route_decision or {}
    final_answer = None
    for step in reversed(steps):
        if step.task_type in {"merge", "generate"} and step.status in {"succeeded", "partial"}:
            value = (step.output_summary or {}).get("answer") or (step.output_summary or {}).get("final_answer")
            if isinstance(value, str):
                final_answer = value
                break
    route_name = route.get("target_agent") or route.get("route")
    return RunDTO(
        id=run.id, status=run.status, route=route_name,
        router_model=run.model_name,
        router_confidence=route.get("confidence"), input_summary=run.input_summary,
        final_answer=final_answer, error_code=run.error_code,
        created_at=run.created_at, updated_at=run.updated_at,
        finished_at=run.finished_at,
    )


def _step_dto(step: AgentStep) -> RunStepDTO:
    return RunStepDTO(
        id=step.id, sequence=step.sequence_no, agent_code=step.agent_code,
        step_type=step.task_type, status=step.status,
        input_summary=redact(step.input_summary) or {},
        output_summary=redact(step.output_summary) or {}, error_code=step.error_code,
        started_at=step.started_at, finished_at=step.finished_at,
    )


def _tool_dto(call: ToolCall, approval_id: UUID | None) -> ToolCallDTO:
    contract = TOOL_CONTRACTS.get(call.tool_name)
    risk = contract.definition.risk_level if contract else "r3"
    return ToolCallDTO(
        id=call.id, tool_name=call.tool_name, status=call.status,
        risk_level=risk, approval_id=approval_id, duration_ms=call.duration_ms,
        error_code=call.error_code, started_at=call.started_at,
        finished_at=call.finished_at,
    )


def _approval_dto(item: ApprovalRequestModel, call: ToolCall) -> ApprovalDTO:
    return ApprovalDTO(
        id=item.id, run_id=item.run_id, tool_name=call.tool_name,
        argument_summary={"display_summary": item.display_summary},
        status=item.status, expires_at=item.expires_at,
        decided_at=item.decided_at, created_at=item.created_at,
    )
