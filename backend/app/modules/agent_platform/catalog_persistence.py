from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.modules.agent_platform.domain.contracts import AgentRegistration
from app.modules.agent_platform.models import AgentDefinition, AgentVersion, ToolDefinition, ToolVersion
from app.modules.agent_platform.orchestration.agent_registry import AgentRegistry
from app.modules.agent_platform.tool_gateway.catalog import TOOL_CONTRACTS, ToolContract
from app.modules.agent_platform.tool_gateway.registry import ToolRegistry


class CatalogContractMismatch(AppError):
    def __init__(self) -> None:
        super().__init__(status_code=409, code="CATALOG_CONTRACT_MISMATCH", message="运行时目录契约不一致")


@dataclass(frozen=True)
class AgentCatalogRecord:
    definition: AgentDefinition
    version: AgentVersion


@dataclass(frozen=True)
class ToolCatalogRecord:
    definition: ToolDefinition
    version: ToolVersion


class CatalogRepository:
    def __init__(self, session: AsyncSession) -> None: self._session = session

    async def list_active_agents(self) -> tuple[AgentCatalogRecord, ...]:
        stmt = select(AgentDefinition, AgentVersion).join(AgentVersion, AgentVersion.agent_id == AgentDefinition.id).where(AgentDefinition.enabled.is_(True), AgentVersion.status == "active").order_by(AgentDefinition.code, AgentVersion.version)
        return tuple(AgentCatalogRecord(*row) for row in (await self._session.execute(stmt)).all())

    async def get_agent(self, code: str, version: str | None = None) -> AgentCatalogRecord | None:
        stmt = select(AgentDefinition, AgentVersion).join(AgentVersion, AgentVersion.agent_id == AgentDefinition.id).where(AgentDefinition.code == code)
        stmt = stmt.where(AgentVersion.version == version) if version else stmt.where(AgentVersion.status == "active")
        row = (await self._session.execute(stmt)).one_or_none(); return AgentCatalogRecord(*row) if row else None

    async def list_active_tools(self) -> tuple[ToolCatalogRecord, ...]:
        stmt = select(ToolDefinition, ToolVersion).join(ToolVersion, ToolVersion.tool_id == ToolDefinition.id).where(ToolVersion.status == "active").order_by(ToolDefinition.name, ToolVersion.version)
        return tuple(ToolCatalogRecord(*row) for row in (await self._session.execute(stmt)).all())

    async def get_tool(self, name: str, version: str | None = None) -> ToolCatalogRecord | None:
        stmt = select(ToolDefinition, ToolVersion).join(ToolVersion, ToolVersion.tool_id == ToolDefinition.id).where(ToolDefinition.name == name)
        stmt = stmt.where(ToolVersion.version == version) if version else stmt.where(ToolVersion.status == "active")
        row = (await self._session.execute(stmt)).one_or_none(); return ToolCatalogRecord(*row) if row else None

    async def get_tool_for_update(self, name: str) -> ToolCatalogRecord | None:
        stmt = (
            select(ToolDefinition, ToolVersion)
            .join(ToolVersion, ToolVersion.tool_id == ToolDefinition.id)
            .where(ToolDefinition.name == name, ToolVersion.status == "active")
            .with_for_update()
        )
        row = (await self._session.execute(stmt)).one_or_none()
        return ToolCatalogRecord(*row) if row else None


class PersistentCatalogLoader:
    def __init__(self, repository: CatalogRepository) -> None: self._repository = repository

    async def load(self) -> tuple[AgentRegistry, ToolRegistry]:
        agent_records = await self._repository.list_active_agents()
        tool_records = await self._repository.list_active_tools()
        agents = []
        for record in agent_records:
            try:
                agents.append(AgentRegistration.model_validate({"definition": {"code": record.definition.code, "name": record.definition.name, "description": record.definition.description, "enabled": record.definition.enabled}, "version": {"agent_code": record.definition.code, "version": record.version.version, "system_prompt": record.version.system_prompt, "output_schema": record.version.output_schema, "tool_allowlist": record.version.tool_allowlist, "status": record.version.status}}))
            except Exception as exc: raise CatalogContractMismatch() from exc
        tools: list[ToolContract] = []
        for record in tool_records:
            frozen = TOOL_CONTRACTS.get(record.definition.name)
            if frozen is None or not self._matches(record, frozen): raise CatalogContractMismatch()
            tools.append(ToolContract(
                definition=frozen.definition.model_copy(update={"enabled": record.definition.enabled}),
                input_model=frozen.input_model,
                output_model=frozen.output_model,
            ))
        if len(tools) != len(TOOL_CONTRACTS): raise CatalogContractMismatch()
        return AgentRegistry(agents), ToolRegistry(tools)

    @staticmethod
    def _matches(record: ToolCatalogRecord, frozen: ToolContract) -> bool:
        d, v, expected = record.definition, record.version, frozen.definition
        return v.status == "active" and d.module == expected.module and d.risk_level == expected.risk_level and d.visibility == expected.visibility and v.version == expected.version and v.input_schema == frozen.input_model.model_json_schema() and v.output_schema == frozen.output_model.model_json_schema() and tuple(sorted(v.required_permissions)) == expected.required_permissions and v.timeout_ms == expected.timeout_ms and v.idempotent == expected.idempotent and v.requires_approval == expected.requires_approval
