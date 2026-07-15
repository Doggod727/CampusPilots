import os
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PLATFORM_TABLES = (
    "users",
    "roles",
    "permissions",
    "user_roles",
    "role_permissions",
    "refresh_tokens",
    "sensitive_words",
    "moderation_cases",
    "audit_logs",
    "app_configs",
    "idempotency_records",
)
CAMPUS_SERVICE_TABLES = (
    "campuses",
    "departments",
    "department_contacts",
    "guide_categories",
    "service_guides",
    "guide_applicabilities",
    "guide_materials",
    "guide_steps",
    "work_orders",
    "work_order_events",
    "work_order_ratings",
)
M5_PERMISSION_CODES = (
    "agent:run",
    "agent:run:read_own",
    "agent:run:read_all",
    "agent:catalog:read",
    "tool:catalog:read",
    "tool:catalog:write",
    "dataset:read",
    "dataset:write",
    "training:run",
    "training:read",
    "model:read",
    "model:write",
    "model:activate",
    "evaluation:run",
    "evaluation:read",
    "moderation:execute",
    "audit:write",
    "service:read",
    "electricity:read_own",
    "electricity:topup_request:create",
)
M5_CONFIG_KEYS = (
    "agent.max_steps",
    "agent.max_specialists",
    "agent.approval_ttl_seconds",
    "agent.parallelism",
    "modelops.router_confidence",
    "modelops.reranker_enabled",
    "mcp.enabled",
)


def migration_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "DATABASE_URL": "postgresql+asyncpg://user:password@localhost/campuspilot",
            "REDIS_URL": "redis://localhost:6379/0",
            "JWT_SECRET": "migration-test-jwt-secret",
            "FRONTEND_ORIGIN": "http://localhost:5173",
            "DEEPSEEK_API_KEY": "migration-test-deepseek-key",
        }
    )
    return environment


def run_alembic(*arguments: str) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *arguments],
        cwd=BACKEND_ROOT,
        env=migration_environment(),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout + result.stderr


def test_migration_has_single_head() -> None:
    output = run_alembic("heads")

    assert "0003_platform_m5_compat (head)" in output


def test_offline_upgrade_contains_complete_platform_schema() -> None:
    output = run_alembic("upgrade", "head", "--sql")

    assert "CREATE SCHEMA IF NOT EXISTS platform" in output
    for table in PLATFORM_TABLES:
        assert f"CREATE TABLE IF NOT EXISTS platform.{table}" in output
    assert "CREATE EXTENSION IF NOT EXISTS pgcrypto" in output
    assert "CREATE EXTENSION IF NOT EXISTS citext" in output
    assert "CREATE OR REPLACE FUNCTION platform.set_updated_at()" in output
    assert "CREATE INDEX IF NOT EXISTS ix_moderation_cases_rule_hits_gin" in output
    assert "CREATE TRIGGER trg_users_updated_at" in output
    assert "CREATE SCHEMA IF NOT EXISTS campus_service" in output
    for table in CAMPUS_SERVICE_TABLES:
        assert f"CREATE TABLE IF NOT EXISTS campus_service.{table}" in output
    assert "CREATE OR REPLACE FUNCTION campus_service.set_updated_at()" in output
    assert "CREATE INDEX IF NOT EXISTS ix_guide_materials_condition_gin" in output
    assert "CREATE TRIGGER trg_work_orders_updated_at" in output


def test_offline_downgrade_removes_owned_objects_but_keeps_extensions() -> None:
    output = run_alembic("downgrade", "head:base", "--sql")

    drop_positions = [
        output.index(f"DROP TABLE platform.{table}")
        for table in reversed(PLATFORM_TABLES)
    ]
    assert drop_positions == sorted(drop_positions)
    assert "DROP FUNCTION platform.set_updated_at()" in output
    assert "DROP SCHEMA platform" in output
    assert "DROP EXTENSION" not in output


def test_offline_downgrade_removes_campus_service_before_platform() -> None:
    output = run_alembic("downgrade", "head:base", "--sql")

    campus_drop_positions = [
        output.index(f"DROP TABLE campus_service.{table}")
        for table in reversed(CAMPUS_SERVICE_TABLES)
    ]
    assert campus_drop_positions == sorted(campus_drop_positions)
    assert output.index("DROP SCHEMA campus_service") < output.index(
        "DROP TABLE platform.idempotency_records"
    )
    assert "DROP EXTENSION" not in output


def test_m5_compatibility_revision_is_rendered_offline() -> None:
    upgrade = run_alembic("upgrade", "head", "--sql")
    downgrade = run_alembic("downgrade", "head:0002_campus_service_schema", "--sql")

    assert "tool_input" in upgrade
    assert "tool_output" in upgrade
    assert "agent_context" in upgrade
    assert "agent_platform" in upgrade
    for permission_code in M5_PERMISSION_CODES:
        assert f"'{permission_code}'" in upgrade
    for role_code in ("model_engineer", "agent_runtime"):
        assert f"'{role_code}'" in upgrade
    for config_key in M5_CONFIG_KEYS:
        assert f"'{config_key}'" in upgrade
    assert "ON CONFLICT (code) DO UPDATE" in upgrade
    assert "ON CONFLICT (key) DO UPDATE" in upgrade

    assert "DELETE FROM platform.sensitive_words" in downgrade
    assert "DELETE FROM platform.moderation_cases" in downgrade
    assert "DELETE FROM platform.user_roles" in downgrade
    assert "DELETE FROM platform.role_permissions" in downgrade
    assert "DELETE FROM platform.roles" in downgrade
    assert "DELETE FROM platform.permissions" in downgrade
    assert "DELETE FROM platform.app_configs" in downgrade
    assert "CHECK (scope IN ('user_input', 'ai_output', 'community', 'all'))" in downgrade
    assert "CHECK (target_module IN ('ai_knowledge', 'campus_service', 'community'))" in downgrade
