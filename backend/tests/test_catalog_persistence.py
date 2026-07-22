import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.modules.agent_platform.catalog_persistence import CatalogContractMismatch, CatalogRepository, PersistentCatalogLoader
from app.modules.agent_platform.models import AgentDefinition, AgentVersion, ToolDefinition, ToolVersion
from app.modules.agent_platform.orchestration.agent_registry import AGENT_REGISTRATIONS
from app.modules.agent_platform.tool_gateway.catalog import TOOL_CONTRACTS


class Result:
    def __init__(self, rows): self.rows = rows
    def all(self): return self.rows
    def one_or_none(self): return self.rows[0] if self.rows else None


def agent_rows():
    rows=[]
    for item in AGENT_REGISTRATIONS:
        d=item.definition; v=item.version; did=uuid4()
        rows.append((AgentDefinition(id=did, code=d.code, name=d.name, description=d.description, enabled=True), AgentVersion(id=uuid4(), agent_id=did, version=v.version, system_prompt=v.system_prompt, output_schema=v.output_schema, tool_allowlist=list(v.tool_allowlist), status="active")))
    return rows


def tool_rows():
    rows=[]
    for item in TOOL_CONTRACTS.values():
        d=item.definition; did=uuid4()
        rows.append((ToolDefinition(id=did, name=d.name, module=d.module, description=d.description, risk_level=d.risk_level, visibility=d.visibility, enabled=True), ToolVersion(id=uuid4(), tool_id=did, version=d.version, input_schema=item.input_model.model_json_schema(), output_schema=item.output_model.model_json_schema(), required_permissions=list(d.required_permissions), timeout_ms=d.timeout_ms, idempotent=d.idempotent, requires_approval=d.requires_approval, implementation_ref="test", status="active")))
    return rows


def test_repository_queries_are_stable_and_session_owned() -> None:
    session=MagicMock(); session.execute=AsyncMock(side_effect=[Result(agent_rows()), Result(tool_rows())]); repo=CatalogRepository(session)
    assert len(asyncio.run(repo.list_active_agents())) == 6; assert len(asyncio.run(repo.list_active_tools())) == 17
    sql=str(session.execute.await_args_list[1].args[0].compile(dialect=postgresql.dialect()))
    assert "enabled IS true" not in sql and "status =" in sql and "ORDER BY" in sql
    session.commit.assert_not_called(); session.close.assert_not_called()


def test_loader_builds_registries_and_rejects_schema_drift() -> None:
    repo=MagicMock(); repo.list_active_agents=AsyncMock(return_value=tuple(type("R", (), {"definition": d, "version": v}) for d,v in agent_rows())); repo.list_active_tools=AsyncMock(return_value=tuple(type("R", (), {"definition": d, "version": v}) for d,v in tool_rows()))
    agents, tools=asyncio.run(PersistentCatalogLoader(repo).load()); assert len(agents.list_active()) == 6; assert all(tools.resolve(name) for name in TOOL_CONTRACTS)
    records=list(awaitable_result(repo.list_active_tools)); records[0].version.timeout_ms += 1; repo.list_active_tools=AsyncMock(return_value=tuple(records))
    with pytest.raises(CatalogContractMismatch): asyncio.run(PersistentCatalogLoader(repo).load())


def test_loader_preserves_disabled_tool_without_breaking_catalog() -> None:
    records=tuple(type("R", (), {"definition": d, "version": v}) for d,v in tool_rows())
    records[0].definition.enabled=False
    repo=MagicMock();repo.list_active_agents=AsyncMock(return_value=tuple(type("R", (), {"definition": d, "version": v}) for d,v in agent_rows()));repo.list_active_tools=AsyncMock(return_value=records)
    _agents,tools=asyncio.run(PersistentCatalogLoader(repo).load())
    with pytest.raises(Exception) as error:tools.resolve(records[0].definition.name)
    assert getattr(error.value,"code",None)=="TOOL_DISABLED"


def awaitable_result(mock): return mock.return_value
