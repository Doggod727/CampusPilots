from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict

from app.core.config import get_settings
from app.infrastructure.database import Database
from app.modules.agent_platform.catalog_persistence import CatalogRepository, PersistentCatalogLoader
from app.modules.agent_platform.domain.contracts import UserContext
from app.modules.agent_platform.orchestration.agent_registry import AgentRegistry
from app.modules.agent_platform.tool_gateway.errors import ToolNotFound
from app.modules.agent_platform.tool_gateway.registry import ToolRegistry
from app.modules.platform.auth import AuthenticatedUser
from app.modules.platform.auth_dependencies import require_permissions
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
