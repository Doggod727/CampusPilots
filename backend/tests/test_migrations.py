import os
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
TABLES = (
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

    assert "0001_platform_schema (head)" in output


def test_offline_upgrade_contains_complete_platform_schema() -> None:
    output = run_alembic("upgrade", "head", "--sql")

    assert "CREATE SCHEMA IF NOT EXISTS platform" in output
    for table in TABLES:
        assert f"CREATE TABLE IF NOT EXISTS platform.{table}" in output
    assert "CREATE EXTENSION IF NOT EXISTS pgcrypto" in output
    assert "CREATE EXTENSION IF NOT EXISTS citext" in output
    assert "CREATE OR REPLACE FUNCTION platform.set_updated_at()" in output
    assert "CREATE INDEX IF NOT EXISTS ix_moderation_cases_rule_hits_gin" in output
    assert "CREATE TRIGGER trg_users_updated_at" in output


def test_offline_downgrade_removes_owned_objects_but_keeps_extensions() -> None:
    output = run_alembic("downgrade", "head:base", "--sql")

    drop_positions = [
        output.index(f"DROP TABLE platform.{table}")
        for table in reversed(TABLES)
    ]
    assert drop_positions == sorted(drop_positions)
    assert "DROP FUNCTION platform.set_updated_at()" in output
    assert "DROP SCHEMA platform" in output
    assert "DROP EXTENSION" not in output
