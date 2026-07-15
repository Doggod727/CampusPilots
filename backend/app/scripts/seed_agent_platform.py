import asyncio

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.modules.agent_platform.models import (
    AgentDefinition, AgentVersion, ModelVersion, ToolDefinition, ToolVersion,
)
from app.modules.agent_platform.orchestration.agent_registry import AGENT_REGISTRATIONS
from app.modules.agent_platform.tool_gateway.catalog import TOOL_CONTRACTS

IMPLEMENTATIONS = {
    name: f"app.modules.{contract.definition.module}.tools:{name.replace('.', '_')}"
    for name, contract in TOOL_CONTRACTS.items()
}
MODEL_SEEDS = (
    dict(name="deepseek-complex-generator", purpose="complex_generation", provider="deepseek", base_model="deepseek-v4-pro", version="api-2026-07", quantization=None, config={"api_key_env": "DEEPSEEK_API_KEY", "timeout_seconds": 60, "max_retries": 2}),
    dict(name="local-agent-router", purpose="agent_router", provider="rule", base_model="rule-router-v1", version="1.0.0", quantization=None, config={"fallback_provider": "deepseek", "confidence_threshold": 0.80}),
    dict(name="local-rag-reranker", purpose="rag_reranker", provider="local", base_model="BAAI/bge-reranker-base", version="demo-1", quantization="int8", config={"device": "cpu", "max_candidates": 20}),
    dict(name="local-embedding", purpose="embedding", provider="local", base_model="BAAI/bge-small-zh-v1.5", version="demo-1", quantization="int8", config={"device": "cpu", "dimensions": 512}),
)


def agent_definition_upsert(registration):
    d = registration.definition
    stmt = insert(AgentDefinition).values(code=d.code, name=d.name, description=d.description, enabled=d.enabled)
    return stmt.on_conflict_do_update(index_elements=[AgentDefinition.code], set_={"name": stmt.excluded.name, "description": stmt.excluded.description, "enabled": stmt.excluded.enabled})


def agent_version_upsert(registration):
    v = registration.version
    stmt = insert(AgentVersion).values(agent_id=select(AgentDefinition.id).where(AgentDefinition.code == v.agent_code).scalar_subquery(), version=v.version, system_prompt=v.system_prompt, output_schema=v.output_schema, tool_allowlist=list(v.tool_allowlist), status=v.status)
    return stmt.on_conflict_do_update(index_elements=[AgentVersion.agent_id, AgentVersion.version], set_={"system_prompt": stmt.excluded.system_prompt, "output_schema": stmt.excluded.output_schema, "tool_allowlist": stmt.excluded.tool_allowlist, "status": stmt.excluded.status})


def tool_definition_upsert(contract):
    d = contract.definition
    stmt = insert(ToolDefinition).values(name=d.name, module=d.module, description=d.description, risk_level=d.risk_level, visibility=d.visibility, enabled=d.enabled)
    return stmt.on_conflict_do_update(index_elements=[ToolDefinition.name], set_={"module": stmt.excluded.module, "description": stmt.excluded.description, "risk_level": stmt.excluded.risk_level, "visibility": stmt.excluded.visibility, "enabled": stmt.excluded.enabled})


def tool_version_upsert(contract):
    d = contract.definition
    input_schema = contract.input_model.model_json_schema()
    output_schema = contract.output_model.model_json_schema()
    if input_schema != d.input_schema or output_schema != d.output_schema:
        raise RuntimeError("frozen Tool schema drift detected")
    stmt = insert(ToolVersion).values(tool_id=select(ToolDefinition.id).where(ToolDefinition.name == d.name).scalar_subquery(), version=d.version, input_schema=input_schema, output_schema=output_schema, required_permissions=list(d.required_permissions), timeout_ms=d.timeout_ms, idempotent=d.idempotent, requires_approval=d.requires_approval, implementation_ref=IMPLEMENTATIONS[d.name], status="active")
    return stmt.on_conflict_do_update(index_elements=[ToolVersion.tool_id, ToolVersion.version], set_={"input_schema": stmt.excluded.input_schema, "output_schema": stmt.excluded.output_schema, "required_permissions": stmt.excluded.required_permissions, "timeout_ms": stmt.excluded.timeout_ms, "idempotent": stmt.excluded.idempotent, "requires_approval": stmt.excluded.requires_approval, "implementation_ref": stmt.excluded.implementation_ref, "status": stmt.excluded.status})


def model_upsert(values):
    stmt = insert(ModelVersion).values(**values, metrics={}, status="active")
    return stmt.on_conflict_do_update(index_elements=[ModelVersion.name, ModelVersion.version], set_={"config": stmt.excluded.config, "metrics": stmt.excluded.metrics, "status": stmt.excluded.status})


async def seed_agent_platform(session: AsyncSession) -> None:
    async with session.begin():
        for registration in AGENT_REGISTRATIONS:
            await session.execute(agent_definition_upsert(registration)); await session.execute(agent_version_upsert(registration))
        for contract in TOOL_CONTRACTS.values():
            await session.execute(tool_definition_upsert(contract)); await session.execute(tool_version_upsert(contract))
        for values in MODEL_SEEDS:
            await session.execute(model_upsert(values))


async def _run() -> None:
    database = Database.from_settings()
    try:
        async with database.session() as session: await seed_agent_platform(session)
    finally: await database.dispose()


def main() -> None:
    asyncio.run(_run()); print("Seeded Agent Platform catalog baseline.")


if __name__ == "__main__": main()
