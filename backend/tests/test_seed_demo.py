import asyncio
import os
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql

from app.scripts import seed_demo


class StubPasswordHasher:
    def __init__(self) -> None:
        self.passwords: list[str] = []

    def hash(self, password: str) -> str:
        self.passwords.append(password)
        return f"argon2id-hash-{len(self.passwords)}"


def _session_with_transaction() -> MagicMock:
    session = MagicMock()
    result = MagicMock()
    result.scalar_one.return_value = UUID("90000000-0000-4000-8000-000000000003")
    session.execute = AsyncMock(return_value=result)

    @asynccontextmanager
    async def begin():
        yield

    session.begin.side_effect = begin
    return session


def _compiled_postgresql(statement: object) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def test_seed_constants_include_the_m5_platform_compatibility_baseline() -> None:
    assert len(seed_demo.PERMISSIONS) == 44
    assert {role.code for role in seed_demo.ROLES} == {
        "super_admin",
        "knowledge_admin",
        "service_staff",
        "community_operator",
        "student",
        "model_engineer",
        "agent_runtime",
    }
    assert len(seed_demo.DEMO_ACCOUNTS) == 6
    assert seed_demo.ROLE_PERMISSION_CODES["super_admin"] == tuple(
        permission.code for permission in seed_demo.PERMISSIONS
    )
    assert "community:anonymous_identity:read" not in seed_demo.ROLE_PERMISSION_CODES[
        "community_operator"
    ]
    assert len(seed_demo.CONFIGS) == 9
    assert {config[0] for config in seed_demo.CONFIGS} == {
        "auth.max_failed_logins",
        "auth.lock_minutes",
        "agent.max_steps",
        "agent.max_specialists",
        "agent.approval_ttl_seconds",
        "agent.parallelism",
        "modelops.router_confidence",
        "modelops.reranker_enabled",
        "mcp.enabled",
    }
    assert seed_demo.ROLE_PERMISSION_CODES["agent_runtime"] == (
        "agent:run", "moderation:execute", "audit:write"
    )
    assert "electricity:read_own" in seed_demo.ROLE_PERMISSION_CODES["student"]


def test_campus_service_seed_constants_match_sql_004_baseline() -> None:
    assert len(seed_demo.CAMPUS_SEEDS) == 2
    assert len(seed_demo.DEPARTMENT_SEEDS) == 3
    assert len(seed_demo.CONTACT_SEEDS) == 3
    assert len(seed_demo.GUIDE_CATEGORY_SEEDS) == 3
    assert len(seed_demo.SERVICE_GUIDE_SEEDS) == 2
    assert len(seed_demo.GUIDE_APPLICABILITY_SEEDS) == 4
    assert len(seed_demo.GUIDE_MATERIAL_SEEDS) == 4
    assert len(seed_demo.GUIDE_STEP_SEEDS) == 5
    assert {seed.code for seed in seed_demo.CAMPUS_SEEDS} == {"main", "east"}
    assert {seed.code for seed in seed_demo.DEPARTMENT_SEEDS} == {
        "student_affairs", "logistics", "academic_affairs"
    }
    assert {seed.code for seed in seed_demo.SERVICE_GUIDE_SEEDS} == {
        "enrollment_certificate", "student_card_replacement"
    }


def test_seed_demo_uses_one_transaction_and_hashes_each_account() -> None:
    session = _session_with_transaction()
    hasher = StubPasswordHasher()

    usernames = asyncio.run(seed_demo.seed_demo(session, "local-password", hasher))

    assert usernames == tuple(account.username for account in seed_demo.DEMO_ACCOUNTS)
    assert hasher.passwords == ["local-password"] * len(seed_demo.DEMO_ACCOUNTS)
    session.begin.assert_called_once_with()
    assert session.execute.await_count == 49


def test_seed_statements_use_postgresql_upserts_and_replace_mappings() -> None:
    permission_sql = _compiled_postgresql(seed_demo._permission_upsert_statement())
    role_sql = _compiled_postgresql(seed_demo._role_upsert_statement())
    config_sql = str(
        seed_demo._config_upsert_statement().compile(
            dialect=postgresql.dialect(),
        )
    )
    scope_config_sql = str(
        seed_demo._work_order_scope_config_upsert_statement(
            UUID("90000000-0000-4000-8000-000000000003")
        ).compile(dialect=postgresql.dialect())
    )
    user_sql = _compiled_postgresql(
        seed_demo._user_upsert_statement(seed_demo.DEMO_ACCOUNTS[0], "argon2id-hash")
    )
    role_mapping_sql = _compiled_postgresql(
        seed_demo._role_permission_insert_statement(
            "student", seed_demo.ROLE_PERMISSION_CODES["student"]
        )
    )
    user_mapping_sql = _compiled_postgresql(
        seed_demo._user_role_insert_statement(seed_demo.DEMO_ACCOUNTS[0])
    )
    role_clear_sql = _compiled_postgresql(
        seed_demo._clear_role_permissions_statement(
            tuple(role.code for role in seed_demo.ROLES)
        )
    )
    user_clear_sql = _compiled_postgresql(
        seed_demo._clear_user_roles_statement(
            tuple(account.username for account in seed_demo.DEMO_ACCOUNTS)
        )
    )

    assert "ON CONFLICT (code) DO UPDATE" in permission_sql
    assert "ON CONFLICT (code) DO UPDATE" in role_sql
    assert "ON CONFLICT (key) DO UPDATE" in config_sql
    assert "platform.app_configs" in config_sql
    assert "ON CONFLICT (key) DO UPDATE" in scope_config_sql
    assert (
        seed_demo._work_order_scope_config_upsert_statement(
            UUID("90000000-0000-4000-8000-000000000003")
        ).compile(dialect=postgresql.dialect()).params["key"]
        == seed_demo.WORK_ORDER_SCOPES_CONFIG_KEY
    )
    assert "ON CONFLICT (username) DO UPDATE" in user_sql
    assert "argon2id-hash" in user_sql
    assert "ON CONFLICT DO NOTHING" in role_mapping_sql
    assert "ON CONFLICT DO NOTHING" in user_mapping_sql
    assert "platform.roles" in role_mapping_sql
    assert "platform.users" in user_mapping_sql
    assert "DELETE FROM platform.role_permissions" in role_clear_sql
    assert "SELECT platform.roles.id" in role_clear_sql
    assert "DELETE FROM platform.user_roles" in user_clear_sql
    assert "SELECT platform.users.id" in user_clear_sql


def test_seed_electricity_statements_are_idempotent_and_bind_real_demo_users() -> None:
    campus_sql = _compiled_postgresql(seed_demo._campus_upsert_statement())
    account_sql = _compiled_postgresql(seed_demo._electricity_account_upsert_statement())
    members_sql = _compiled_postgresql(seed_demo._electricity_members_insert_statement())

    assert "ON CONFLICT (code) DO UPDATE" in campus_sql
    assert "ON CONFLICT (room_id) DO UPDATE" in account_sql
    assert "source = excluded.source" in account_sql
    assert "ON CONFLICT DO NOTHING" in members_sql
    assert "platform.users" in members_sql
    assert "student01" in members_sql and "student02" in members_sql
    assert "electricity_account_members" in members_sql


def test_campus_service_seed_statements_are_idempotent_and_resolve_parent_codes() -> None:
    department_sql = _compiled_postgresql(seed_demo._department_upsert_statement())
    category_sql = _compiled_postgresql(seed_demo._guide_category_upsert_statement())
    contact_sql = _compiled_postgresql(
        seed_demo._contact_upsert_statement(seed_demo.CONTACT_SEEDS[0])
    )
    guide_sql = _compiled_postgresql(
        seed_demo._service_guide_upsert_statement(seed_demo.SERVICE_GUIDE_SEEDS[0])
    )
    applicability_sql = _compiled_postgresql(
        seed_demo._guide_applicability_upsert_statement(
            seed_demo.GUIDE_APPLICABILITY_SEEDS[0]
        )
    )
    step_sql = _compiled_postgresql(
        seed_demo._guide_step_upsert_statement(seed_demo.GUIDE_STEP_SEEDS[0])
    )
    material_compiled = seed_demo._guide_material_upsert_statement(
        seed_demo.GUIDE_MATERIAL_SEEDS[1]
    ).compile(dialect=postgresql.dialect())
    material_sql = str(material_compiled)

    assert "ON CONFLICT (code) DO UPDATE" in department_sql
    assert "ON CONFLICT (code) DO UPDATE" in category_sql
    assert "campus_service.departments.id" in contact_sql
    assert "FROM campus_service.departments" in contact_sql
    assert "departments.code = 'student_affairs'" in contact_sql
    assert "ON CONFLICT (id) DO UPDATE" in contact_sql
    assert "SELECT" in guide_sql
    assert "campus_service.guide_categories" in guide_sql
    assert "campus_service.departments" in guide_sql
    assert "guide_categories.code = 'student_certificate'" in guide_sql
    assert "departments.code = 'student_affairs'" in guide_sql
    assert "ON CONFLICT (code) DO UPDATE" in guide_sql
    assert "SELECT campus_service.service_guides.id" in applicability_sql
    assert "service_guides.code = 'enrollment_certificate'" in applicability_sql
    assert "ON CONFLICT (guide_id, campus_code, student_type) DO UPDATE" in applicability_sql
    assert "campus_service.service_guides.id" in step_sql
    assert "ON CONFLICT (guide_id, step_no) DO UPDATE" in step_sql
    assert "campus_service.service_guides.id" in material_sql
    assert "ON CONFLICT (id) DO UPDATE" in material_sql
    assert {"student_types": ["international"]} in material_compiled.params.values()


def test_missing_seed_password_stops_before_database_initialization() -> None:
    with patch.object(seed_demo.Database, "from_settings") as from_settings:
        with patch.dict(os.environ, {"DEMO_SEED_PASSWORD": ""}):
            with pytest.raises(SystemExit, match="DEMO_SEED_PASSWORD"):
                seed_demo.main()

    from_settings.assert_not_called()


def test_seed_result_does_not_include_the_password() -> None:
    output = seed_demo.format_seed_result(("admin01", "student01"))

    assert output == "Seeded demo accounts: admin01, student01"
    assert "local-password" not in output
    assert seed_demo.DEMO_ELECTRICITY_ROOM_ID not in output
    assert "演示宿舍区" not in output
    assert seed_demo.CONTACT_SEEDS[0].phone not in output
    assert seed_demo.CONTACT_SEEDS[0].email not in output
    assert seed_demo.SERVICE_GUIDE_SEEDS[0].source_url not in output
