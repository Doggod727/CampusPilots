from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from app.infrastructure.database import Base
from app.modules.agent_platform import models

EXPECTED = {
    "agent_definitions", "agent_versions", "tool_definitions", "tool_versions",
    "datasets", "dataset_versions", "training_jobs", "model_versions",
    "evaluation_jobs", "evaluation_metrics", "agent_runs", "agent_steps",
    "tool_calls", "approval_requests", "agent_handoffs",
    "agent_runtime_commands", "agent_runtime_checkpoints", "agent_run_events",
}


def test_agent_platform_metadata_contains_all_eighteen_tables() -> None:
    assert {table.name for table in Base.metadata.tables.values() if table.schema == "agent_platform"} == EXPECTED


def test_all_agent_platform_tables_and_indexes_compile_for_postgresql() -> None:
    dialect = postgresql.dialect()
    tables = [table for table in Base.metadata.tables.values() if table.schema == "agent_platform"]
    ddl = "\n".join(str(CreateTable(table).compile(dialect=dialect)) for table in tables)
    indexes = "\n".join(str(CreateIndex(index).compile(dialect=dialect)) for table in tables for index in table.indexes)
    for name in EXPECTED:
        assert f"agent_platform.{name}" in ddl
    for token in ("JSONB", "UUID", "CHAR(64)", "ON DELETE CASCADE", "ON DELETE SET NULL", "ON DELETE RESTRICT"):
        assert token in ddl
    for name in ("uq_agent_one_active_version", "uq_tool_one_active_version", "uq_model_one_active_purpose", "uq_approval_one_pending_tool_call", "uq_tool_calls_idempotency"):
        assert name in indexes and "WHERE" in indexes


def test_sensitive_model_values_are_not_exposed_by_repr() -> None:
    values = [
        models.AgentVersion(system_prompt="secret prompt", output_schema={"secret": "x"}, tool_allowlist=[]),
        models.ToolVersion(input_schema={"password": "x"}, output_schema={}, required_permissions=[]),
        models.ToolCall(arguments_hash="a" * 64, arguments_summary={"token": "x"}, result_summary={}),
        models.ApprovalRequestModel(arguments_hash="b" * 64, display_summary="private approval"),
        models.AgentRuntimeCheckpoint(encrypted_state="ciphertext", state_sha256="c" * 64),
    ]
    rendered = " ".join(map(repr, values))
    for secret in ("secret prompt", "password", "a" * 64, "b" * 64, "private approval", "ciphertext"):
        assert secret not in rendered
