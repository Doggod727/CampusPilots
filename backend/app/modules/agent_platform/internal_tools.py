from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse

from app.core.errors import AppError
from app.core.config import get_settings
from app.core.request_id import REQUEST_ID_HEADER
from app.infrastructure.database import Database
from app.modules.agent_platform.approvals import (
    ApprovalRepository,
    ApprovalService,
    DatabaseApprovalVerifier,
)
from app.modules.agent_platform.composition import RuntimeCompositionFactory
from app.modules.agent_platform.domain.contracts import ToolCallRequest, UserContext
from app.modules.agent_platform.internal_auth import (
    InternalServicePrincipal,
    InternalUserContextLoader,
    get_internal_service_principal,
)
from app.modules.agent_platform.models import (
    AgentRun,
    AgentStep,
    ApprovalRequestModel,
    ToolCall,
)
from app.modules.agent_platform.tool_gateway.executor import ToolExecutor
from app.modules.agent_platform.tool_gateway.electricity_adapters import (
    ElectricityBalanceToolHandler,
    ElectricityTopupToolHandler,
)
from app.modules.agent_platform.tool_gateway.governance_adapters import (
    GovernanceAuthorizeToolHandler,
    GovernanceCheckContentHandler,
    GovernanceWriteAuditHandler,
    M4AuditAdapter,
    M4ContentSafetyAdapter,
    M4ToolAuthorizationAdapter,
)
from app.modules.agent_platform.tool_gateway.mocks import build_mock_handlers
from app.modules.campus_service.electricity import ElectricityService
from app.modules.campus_service.repositories import ElectricityRepository
from app.modules.platform.audit import redact
from app.modules.platform.audit import AuditService
from app.modules.platform.moderation import ModerationService
from app.modules.platform.moderation_scan import SensitiveWordScanner
from app.modules.platform.repositories import (
    AuditLogRepository,
    ModerationCaseRepository,
    RbacRepository,
    SensitiveWordRepository,
    UserRepository,
)
from app.shared.responses import SuccessResponse
from app.modules.agent_platform.rate_limit import RateLimitPort, RedisRateLimiter
from redis.asyncio import Redis

router = APIRouter(prefix="/internal/v1", tags=["InternalTools"])


class ToolInvokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    run_id: UUID
    step_id: UUID | None = None
    agent_code: str = Field(min_length=3, max_length=50)
    user_id: UUID
    approval_id: UUID | None = None
    arguments: dict[str, Any]


class ApprovalData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    run_id: UUID
    tool_name: str
    argument_summary: dict[str, Any]
    argument_hash: str
    status: str
    expires_at: datetime
    decided_at: datetime | None
    created_at: datetime


class ToolInvokeData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_call_id: UUID
    status: str
    approval: ApprovalData | None = None
    result: dict[str, Any] | None = None


ToolInvokeResponse = SuccessResponse[ToolInvokeData]


class InternalToolScopeInvalid(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=403, code="TOOL_FORBIDDEN", message="无权调用该工具")


class InternalToolIdempotencyConflict(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=409, code="IDEMPOTENCY_CONFLICT", message="幂等键与已有调用冲突")


@dataclass(frozen=True)
class RunStepContext:
    run: AgentRun
    step: AgentStep


class InternalToolRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_run_step_for_update(
        self, *, run_id: UUID, step_id: UUID | None
    ) -> RunStepContext | None:
        statement = (
            select(AgentRun, AgentStep)
            .join(AgentStep, AgentStep.run_id == AgentRun.id)
            .where(AgentRun.id == run_id)
        )
        if step_id is not None:
            statement = statement.where(AgentStep.id == step_id)
        else:
            statement = statement.order_by(AgentStep.sequence_no.desc()).limit(1)
        row = (await self._session.execute(statement.with_for_update())).one_or_none()
        return RunStepContext(*row) if row else None

    async def get_by_idempotency(
        self, *, run_id: UUID, tool_name: str, key: str
    ) -> ToolCall | None:
        statement = select(ToolCall).where(
            ToolCall.run_id == run_id,
            ToolCall.tool_name == tool_name,
            ToolCall.idempotency_key == key,
        ).with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_approval_call(
        self, approval_id: UUID
    ) -> tuple[ApprovalRequestModel, ToolCall] | None:
        statement = (
            select(ApprovalRequestModel, ToolCall)
            .join(ToolCall, ToolCall.id == ApprovalRequestModel.tool_call_id)
            .where(ApprovalRequestModel.id == approval_id)
            .with_for_update()
        )
        return (await self._session.execute(statement)).one_or_none()

    def add(self, entity: object) -> None:
        self._session.add(entity)


class InternalToolService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        repository: InternalToolRepository,
        user_loader: InternalUserContextLoader,
        electricity_repository: ElectricityRepository,
        executor: ToolExecutor,
        approval_service: ApprovalService,
        agent_registry,
        tool_registry,
    ) -> None:
        self._session = session
        self._repository = repository
        self._user_loader = user_loader
        self._electricity_repository = electricity_repository
        self._executor = executor
        self._approval_service = approval_service
        self._agents = agent_registry
        self._tools = tool_registry

    async def invoke(
        self,
        *,
        tool_name: str,
        payload: ToolInvokeRequest,
        idempotency_key: str,
        request_id: str,
    ) -> tuple[int, ToolInvokeData]:
        async with self._session.begin():
            user = await self._user_loader.load(payload.user_id, request_id)
            room_ids = await self._electricity_repository.list_room_ids_for_user(user.user_id)
            user = user.model_copy(update={"room_ids": room_ids})
            scope = await self._repository.get_run_step_for_update(
                run_id=payload.run_id, step_id=payload.step_id
            )
            if (
                scope is None
                or scope.run.user_id != user.user_id
                or scope.step.agent_code != payload.agent_code
                or scope.run.status not in {"routing", "running", "awaiting_approval"}
            ):
                raise InternalToolScopeInvalid()
            registration = self._agents.get_active(payload.agent_code)
            contract = self._tools.resolve(tool_name)
            request = ToolCallRequest(
                agent_run_id=payload.run_id,
                step_id=scope.step.id,
                tool_name=tool_name,
                tool_version=contract.definition.version,
                arguments=payload.arguments,
                idempotency_key=idempotency_key,
                approval_id=payload.approval_id,
            )
            prepared = self._executor.prepare(request)
            await self._executor.authorize(
                user,
                prepared,
                registration.version.tool_allowlist,
                trusted_runtime=True,
            )

            existing = await self._existing_call(payload, tool_name, idempotency_key)
            if existing is not None and existing.status == "succeeded":
                return 200, ToolInvokeData(
                    tool_call_id=existing.id,
                    status="succeeded",
                    result=redact(existing.result_summary),
                )

            if contract.definition.requires_approval and payload.approval_id is None:
                if existing is not None:
                    approval = await self._pending_approval(existing.id)
                    if approval is None:
                        raise InternalToolIdempotencyConflict()
                    return 202, self._awaiting(existing, approval, tool_name)
                call = ToolCall(
                    run_id=payload.run_id,
                    step_id=scope.step.id,
                    tool_name=tool_name,
                    tool_version=contract.definition.version,
                    arguments_hash=prepared.arguments_hash,
                    arguments_summary=redact(payload.arguments),
                    status="awaiting_approval",
                    idempotency_key=idempotency_key,
                    result_summary={},
                )
                self._repository.add(call)
                await self._session.flush()
                approval = await self._approval_service.create(
                    run_id=payload.run_id,
                    tool_call_id=call.id,
                    user_id=user.user_id,
                    action=tool_name,
                    display_summary=f"确认执行 {tool_name}",
                    arguments_hash=prepared.arguments_hash,
                )
                scope.run.status = "awaiting_approval"
                scope.step.status = "awaiting_approval"
                return 202, self._awaiting(call, approval, tool_name)

            call = existing
            if payload.approval_id is not None:
                approval_row = await self._repository.get_approval_call(payload.approval_id)
                if approval_row is None:
                    from app.modules.agent_platform.tool_gateway.errors import ToolApprovalInvalid
                    raise ToolApprovalInvalid()
                _approval, approved_call = approval_row
                if approved_call.run_id != payload.run_id or approved_call.step_id != scope.step.id:
                    from app.modules.agent_platform.tool_gateway.errors import ToolApprovalInvalid
                    raise ToolApprovalInvalid()
                call = approved_call
            if call is None:
                call = ToolCall(
                    run_id=payload.run_id,
                    step_id=scope.step.id,
                    tool_name=tool_name,
                    tool_version=contract.definition.version,
                    arguments_hash=prepared.arguments_hash,
                    arguments_summary=redact(payload.arguments),
                    status="authorized",
                    idempotency_key=idempotency_key,
                    result_summary={},
                )
                self._repository.add(call)
                await self._session.flush()
            if call.arguments_hash != prepared.arguments_hash:
                raise InternalToolIdempotencyConflict()
            call.status = "running"
            result = await self._executor.execute(
                context=user,
                request=request,
                agent_allowlist=registration.version.tool_allowlist,
                trusted_runtime=True,
            )
            call.status = "succeeded"
            call.result_summary = redact(result.data or {})
            call.duration_ms = result.duration_ms
            call.audit_id = result.audit_id
            call.finished_at = datetime.now(UTC)
            scope.run.status = "running"
            scope.step.status = "running"
            return 200, ToolInvokeData(
                tool_call_id=call.id, status="succeeded", result=call.result_summary
            )

    async def _existing_call(self, payload, tool_name, key):
        existing = await self._repository.get_by_idempotency(
            run_id=payload.run_id, tool_name=tool_name, key=key
        )
        if existing is not None:
            contract = self._tools.resolve(tool_name)
            request = ToolCallRequest(
                agent_run_id=payload.run_id,
                step_id=existing.step_id,
                tool_name=tool_name,
                tool_version=contract.definition.version,
                arguments=payload.arguments,
                idempotency_key=key,
                approval_id=payload.approval_id,
            )
            if existing.arguments_hash != self._executor.prepare(request).arguments_hash:
                raise InternalToolIdempotencyConflict()
        return existing

    async def _pending_approval(self, call_id: UUID):
        statement = select(ApprovalRequestModel).where(
            ApprovalRequestModel.tool_call_id == call_id,
            ApprovalRequestModel.status == "pending",
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    @staticmethod
    def _awaiting(call, approval, tool_name):
        return ToolInvokeData(
            tool_call_id=call.id,
            status="awaiting_approval",
            approval=ApprovalData(
                id=approval.id,
                run_id=approval.run_id,
                tool_name=tool_name,
                argument_summary=redact(call.arguments_summary),
                argument_hash=approval.arguments_hash,
                status=approval.status,
                expires_at=approval.expires_at,
                decided_at=approval.decided_at,
                created_at=approval.created_at,
            ),
        )


async def get_internal_tool_service() -> AsyncIterator[InternalToolService]:
    settings = get_settings()
    database = Database.from_settings(settings)
    try:
        async with database.session() as session:
            factory = RuntimeCompositionFactory(settings)
            async with session.begin():
                agents, tools = await factory.load_catalogs(session)
            executor, approval_service, _moderation = await factory.build_tool_executor(
                session, (agents, tools)
            )
            yield InternalToolService(
                session=session,
                repository=InternalToolRepository(session),
                user_loader=InternalUserContextLoader(
                    UserRepository(session), RbacRepository(session)
                ),
                electricity_repository=ElectricityRepository(session),
                executor=executor,
                approval_service=approval_service,
                agent_registry=agents,
                tool_registry=tools,
            )
    finally:
        await database.dispose()


async def get_internal_tool_rate_limiter() -> AsyncIterator[RateLimitPort]:
    settings = get_settings()
    client = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        yield RedisRateLimiter(client)
    finally:
        await client.aclose()


def get_internal_tool_rate_limit() -> int:
    return get_settings().internal_tool_rate_limit_per_minute


@router.post(
    "/tools/{tool_name}:invoke",
    operation_id="invokeInternalTool",
    response_model=ToolInvokeResponse,
)
async def invoke_internal_tool(
    tool_name: str,
    payload: ToolInvokeRequest,
    request: Request,
    _principal: Annotated[InternalServicePrincipal, Depends(get_internal_service_principal)],
    service: Annotated[InternalToolService, Depends(get_internal_tool_service)],
    limiter: Annotated[RateLimitPort, Depends(get_internal_tool_rate_limiter)],
    rate_limit: Annotated[int, Depends(get_internal_tool_rate_limit)],
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=8, max_length=128)
    ],
) -> JSONResponse:
    client_ip = request.client.host if request.client else "unknown"
    await limiter.check(
        scope="internal_tool",
        subjects=(str(payload.user_id), client_ip),
        limit=rate_limit,
    )
    status, data = await service.invoke(
        tool_name=tool_name,
        payload=payload,
        idempotency_key=idempotency_key,
        request_id=request.state.request_id,
    )
    body = ToolInvokeResponse(
        data=data,
        request_id=request.state.request_id,
        timestamp=datetime.now(UTC),
    ).model_dump(mode="json")
    return JSONResponse(
        body,
        status_code=status,
        headers={REQUEST_ID_HEADER: request.state.request_id},
    )
