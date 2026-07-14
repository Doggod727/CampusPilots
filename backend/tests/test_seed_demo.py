import asyncio
import os
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

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
    session.execute = AsyncMock()

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


def test_seed_constants_match_the_m4_identity_baseline() -> None:
    assert len(seed_demo.PERMISSIONS) == 24
    assert {role.code for role in seed_demo.ROLES} == {
        "super_admin",
        "knowledge_admin",
        "service_staff",
        "community_operator",
        "student",
    }
    assert len(seed_demo.DEMO_ACCOUNTS) == 6
    assert seed_demo.ROLE_PERMISSION_CODES["super_admin"] == tuple(
        permission.code for permission in seed_demo.PERMISSIONS
    )
    assert "community:anonymous_identity:read" not in seed_demo.ROLE_PERMISSION_CODES[
        "community_operator"
    ]


def test_seed_demo_uses_one_transaction_and_hashes_each_account() -> None:
    session = _session_with_transaction()
    hasher = StubPasswordHasher()

    usernames = asyncio.run(seed_demo.seed_demo(session, "local-password", hasher))

    assert usernames == tuple(account.username for account in seed_demo.DEMO_ACCOUNTS)
    assert hasher.passwords == ["local-password"] * len(seed_demo.DEMO_ACCOUNTS)
    session.begin.assert_called_once_with()
    assert session.execute.await_count == 21


def test_seed_statements_use_postgresql_upserts_and_replace_mappings() -> None:
    permission_sql = _compiled_postgresql(seed_demo._permission_upsert_statement())
    role_sql = _compiled_postgresql(seed_demo._role_upsert_statement())
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
