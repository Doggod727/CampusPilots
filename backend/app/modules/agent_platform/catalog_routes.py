from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse

from app.core.config import get_settings
from app.infrastructure.database import Database
from app.modules.agent_platform.catalog_persistence import CatalogRepository, PersistentCatalogLoader
from app.modules.agent_platform.domain.contracts import UserContext
from app.modules.agent_platform.orchestration.agent_registry import AgentRegistry
from app.modules.agent_platform.tool_gateway.catalog import TOOL_CONTRACTS, ToolContract
from app.modules.agent_platform.tool_gateway.errors import ToolNotFound
from app.modules.agent_platform.tool_gateway.registry import ToolRegistry
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.auth_dependencies import require_permissions
from app.modules.platform.audit import AuditService
from app.modules.platform.idempotency import IdempotencyConflict, IdempotencyService
from app.modules.platform.repositories import AuditLogRepository, IdempotencyRecordRepository
from app.core.errors import AppError
from app.core.request_id import REQUEST_ID_HEADER
from app.shared.responses import SuccessResponse

router = APIRouter(prefix="/api/v1", tags=["AgentCatalog", "ToolCatalog"])


class AgentCatalogData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[dict]


class ToolCatalogItemData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    module: Literal["m1", "m2", "m3", "m4", "m5"]
    description: str
    risk_level: Literal["r0", "r1", "r2", "r3"]
    enabled: bool
    version: str
    input_schema: dict
    output_schema: dict
    required_permissions: list[str]
    timeout_ms: int
    idempotent: bool
    requires_approval: bool


class ToolCatalogData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[ToolCatalogItemData]


AgentCatalogResponse = SuccessResponse[AgentCatalogData]
ToolCatalogResponse = SuccessResponse[ToolCatalogData]
ToolResponse = SuccessResponse[ToolCatalogItemData]


class ToolStateUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: bool
    confirmed: bool
    reason: str = Field(min_length=2, max_length=300)


class ToolStateConfirmationRequired(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=409, code="TOOL_STATE_CONFIRMATION_REQUIRED", message="Tool 状态变更需要明确确认")


class CatalogProvider:
    """Lazily cache validated registries without import-time I/O."""

    def __init__(self) -> None:
        self._snapshot: tuple[AgentRegistry, ToolRegistry] | None = None
        self._lock = asyncio.Lock()

    async def get(self) -> tuple[AgentRegistry, ToolRegistry]:
        if self._snapshot is not None:
            return self._snapshot
        async with self._lock:
            if self._snapshot is None:
                database = Database.from_settings(get_settings())
                try:
                    async with database.session() as session:
                        self._snapshot = await PersistentCatalogLoader(CatalogRepository(session)).load()
                finally:
                    await database.dispose()
        return self._snapshot

    def invalidate(self) -> None:
        self._snapshot = None


catalog_provider = CatalogProvider()


async def get_catalogs() -> tuple[AgentRegistry, ToolRegistry]:
    return await catalog_provider.get()


class ToolCatalogAdminService:
    def __init__(self, session, repository, idempotency, audit) -> None:
        self.session=session; self.repository=repository; self.idempotency=idempotency; self.audit=audit

    async def update(self, *, actor: AuthenticatedUser, name: str, payload: ToolStateUpdateRequest, key: str, request_id: str):
        if not payload.confirmed:
            raise ToolStateConfirmationRequired()
        request_body=payload.model_dump(mode="json")
        async with self.session.begin():
            decision=await self.idempotency.begin(user_id=actor.user_id,endpoint=f"PATCH /api/v1/tools/{name}",idempotency_key=key,request_body=request_body)
            if decision.replay:
                return decision.replay.response_status,dict(decision.replay.response_body),str(decision.replay.response_body["request_id"])
            if decision.pending: raise IdempotencyConflict()
            record=await self.repository.get_tool_for_update(name)
            if record is None: raise ToolNotFound()
            before=record.definition.enabled; record.definition.enabled=payload.enabled
            frozen=TOOL_CONTRACTS.get(name)
            if frozen is None: raise ToolNotFound()
            contract=ToolContract(definition=frozen.definition.model_copy(update={"enabled":payload.enabled}),input_model=frozen.input_model,output_model=frozen.output_model)
            data=_tool_data(contract)
            response=SuccessResponse(data=data,request_id=request_id,timestamp=datetime.now(UTC)).model_dump(mode="json")
            self.audit.record_success(action="tool.state.update",resource_type="tool",resource_id=name,request_id=request_id,actor_user_id=actor.user_id,actor_username=actor.username,before_data={"enabled":before},after_data={"enabled":payload.enabled,"reason":payload.reason})
            if not await self.idempotency.complete(record_id=decision.record_id,response_status=200,response_body=response,resource_type="tool",resource_id=name): raise IdempotencyConflict()
        return 200,response,request_id


async def get_catalog_admin_service() -> AsyncIterator[ToolCatalogAdminService]:
    database=Database.from_settings(get_settings())
    try:
        async with database.session() as session:
            yield ToolCatalogAdminService(session,CatalogRepository(session),IdempotencyService(session=session,repository=IdempotencyRecordRepository(session)),AuditService(AuditLogRepository(session)))
    finally:
        await database.dispose()


def _context(user: AuthenticatedUser, request_id: str) -> UserContext:
    return UserContext(
        user_id=user.user_id, username=user.username,
        roles=tuple(role.code for role in user.roles),
        permissions=user.permissions, request_id=request_id,
    )


def _tool_data(contract) -> ToolCatalogItemData:
    item = contract.definition
    return ToolCatalogItemData(
        name=item.name, module=item.module, description=item.description,
        risk_level=item.risk_level, enabled=item.enabled, version=item.version,
        input_schema=item.input_schema, output_schema=item.output_schema,
        required_permissions=list(item.required_permissions), timeout_ms=item.timeout_ms,
        idempotent=item.idempotent, requires_approval=item.requires_approval,
    )


@router.get("/agents", operation_id="listAgents", response_model=AgentCatalogResponse)
async def list_agents(
    request: Request,
    _: Annotated[AuthenticatedUser, Depends(require_permissions("agent:catalog:read"))],
    catalogs: Annotated[tuple[AgentRegistry, ToolRegistry], Depends(get_catalogs)],
) -> AgentCatalogResponse:
    agents, _tools = catalogs
    items = [item.model_dump() for item in agents.list_catalog()]
    return SuccessResponse(data=AgentCatalogData(items=items), request_id=request.state.request_id, timestamp=datetime.now(UTC))


@router.get("/tools", operation_id="listTools", response_model=ToolCatalogResponse)
async def list_tools(
    request: Request,
    user: Annotated[AuthenticatedUser, Depends(require_permissions("tool:catalog:read"))],
    catalogs: Annotated[tuple[AgentRegistry, ToolRegistry], Depends(get_catalogs)],
    module: Annotated[Literal["m1", "m2", "m3", "m4", "m5"] | None, Query()] = None,
    enabled: Annotated[bool | None, Query()] = None,
) -> ToolCatalogResponse:
    agents, tools = catalogs
    allowlist = {name for agent in agents.list_active() for name in agent.version.tool_allowlist}
    visible = tools.list_allowed(_context(user, request.state.request_id), allowlist)
    items = [_tool_data(item) for item in visible if (module is None or item.definition.module == module) and (enabled is None or item.definition.enabled is enabled)]
    return SuccessResponse(data=ToolCatalogData(items=items), request_id=request.state.request_id, timestamp=datetime.now(UTC))


@router.get("/tools/{tool_name}", operation_id="getTool", response_model=ToolResponse)
async def get_tool(
    tool_name: str,
    request: Request,
    user: Annotated[AuthenticatedUser, Depends(require_permissions("tool:catalog:read"))],
    catalogs: Annotated[tuple[AgentRegistry, ToolRegistry], Depends(get_catalogs)],
) -> ToolResponse:
    agents, tools = catalogs
    allowlist = {name for agent in agents.list_active() for name in agent.version.tool_allowlist}
    allowed = {item.definition.name: item for item in tools.list_allowed(_context(user, request.state.request_id), allowlist)}
    if tool_name not in allowed:
        raise ToolNotFound()
    return SuccessResponse(data=_tool_data(allowed[tool_name]), request_id=request.state.request_id, timestamp=datetime.now(UTC))


@router.patch("/tools/{tool_name}", operation_id="updateToolRuntimeState", response_model=ToolResponse)
async def update_tool_runtime_state(
    tool_name: str,
    payload: ToolStateUpdateRequest,
    request: Request,
    actor: Annotated[AuthenticatedUser, Depends(require_permissions("tool:catalog:write"))],
    service: Annotated[ToolCatalogAdminService, Depends(get_catalog_admin_service)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> JSONResponse:
    status,body,response_request_id=await service.update(actor=actor,name=tool_name,payload=payload,key=idempotency_key,request_id=request.state.request_id)
    catalog_provider.invalidate()
    return JSONResponse(body,status_code=status,headers={REQUEST_ID_HEADER:response_request_id})
