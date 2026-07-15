import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.dialects import postgresql

from app.scripts import seed_agent_platform as seed


def session_stub():
    session = MagicMock(); session.execute = AsyncMock()
    @asynccontextmanager
    async def begin(): yield
    session.begin.side_effect = begin
    return session


def compile_sql(statement):
    return str(statement.compile(dialect=postgresql.dialect()))


def test_seed_uses_one_transaction_and_exact_catalog_counts() -> None:
    session = session_stub(); asyncio.run(seed.seed_agent_platform(session))
    session.begin.assert_called_once_with(); assert session.execute.await_count == 44
    assert len(seed.AGENT_REGISTRATIONS) == 6 and len(seed.TOOL_CONTRACTS) == 14 and len(seed.MODEL_SEEDS) == 4


def test_seed_uses_generated_frozen_schemas_and_postgresql_upserts() -> None:
    contract = seed.TOOL_CONTRACTS["electricity.create_topup_request"]
    sql = compile_sql(seed.tool_version_upsert(contract))
    assert "ON CONFLICT (tool_id, version) DO UPDATE" in sql
    assert contract.input_model.model_json_schema() == contract.definition.input_schema
    assert contract.output_model.model_json_schema() == contract.definition.output_schema
    assert "DEEPSEEK_API_KEY" in str(seed.MODEL_SEEDS)
    assert "sk-" not in str(seed.MODEL_SEEDS).lower()


def test_seed_output_is_safe_and_stable() -> None:
    assert "password" not in "Seeded Agent Platform catalog baseline.".lower()
    for model in seed.MODEL_SEEDS:
        assert "api_key" not in model["config"] or model["config"]["api_key_env"] == "DEEPSEEK_API_KEY"
