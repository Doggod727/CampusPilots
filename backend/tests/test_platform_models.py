from sqlalchemy import CHAR, CheckConstraint, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import CITEXT, INET, JSONB, UUID
from sqlalchemy.schema import CreateIndex, CreateTable

from app.infrastructure.database import Base
from app.modules.platform.models import (
    AppConfig,
    AuditLog,
    IdempotencyRecord,
    Permission,
    RefreshToken,
    Role,
    RolePermission,
    User,
    UserRole,
    SensitiveWord,
)

EXPECTED_TABLES = {
    "platform.users",
    "platform.roles",
    "platform.permissions",
    "platform.user_roles",
    "platform.role_permissions",
    "platform.refresh_tokens",
    "platform.app_configs",
    "platform.idempotency_records",
    "platform.audit_logs",
    "platform.sensitive_words",
}
EXPECTED_COLUMNS = {
    "platform.users": {
        "id",
        "username",
        "password_hash",
        "display_name",
        "email",
        "department",
        "status",
        "failed_login_count",
        "locked_until",
        "last_login_at",
        "password_changed_at",
        "version",
        "created_at",
        "updated_at",
        "deleted_at",
    },
    "platform.roles": {
        "id",
        "code",
        "name",
        "description",
        "is_system",
        "version",
        "created_at",
        "updated_at",
    },
    "platform.permissions": {
        "id",
        "code",
        "name",
        "module",
        "description",
        "created_at",
    },
    "platform.user_roles": {"user_id", "role_id", "assigned_by", "assigned_at"},
    "platform.role_permissions": {"role_id", "permission_id", "granted_at"},
    "platform.refresh_tokens": {
        "id",
        "jti",
        "user_id",
        "token_hash",
        "expires_at",
        "revoked_at",
        "replaced_by_jti",
        "created_ip",
        "user_agent",
        "created_at",
    },
    "platform.app_configs": {
        "key",
        "namespace",
        "value",
        "value_type",
        "description",
        "editable",
        "version",
        "updated_by",
        "created_at",
        "updated_at",
    },
    "platform.idempotency_records": {
        "id",
        "user_id",
        "endpoint",
        "idempotency_key",
        "request_hash",
        "response_status",
        "response_body",
        "resource_type",
        "resource_id",
        "created_at",
        "expires_at",
    },
    "platform.audit_logs": {
        "id",
        "actor_user_id",
        "actor_username",
        "action",
        "resource_type",
        "resource_id",
        "result",
        "request_id",
        "ip_address",
        "user_agent",
        "before_data",
        "after_data",
        "error_code",
        "created_at",
    },
    "platform.sensitive_words": {
        "id", "word", "match_type", "action", "replacement", "scope",
        "enabled", "created_by", "created_at", "updated_at",
    },
}


def constraint_names(model: type[Base]) -> set[str]:
    return {
        constraint.name
        for constraint in model.__table__.constraints
        if isinstance(constraint, CheckConstraint) and constraint.name is not None
    }


def index_sql(model: type[Base]) -> dict[str, str]:
    dialect = postgresql.dialect()
    return {
        index.name: str(CreateIndex(index).compile(dialect=dialect))
        for index in model.__table__.indexes
        if index.name is not None
    }


def test_identity_metadata_contains_only_scoped_tables() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES
    assert {
        table_name: set(table.c.keys())
        for table_name, table in Base.metadata.tables.items()
    } == EXPECTED_COLUMNS


def test_user_mapping_matches_platform_migration() -> None:
    table = User.__table__

    assert isinstance(table.c.id.type, UUID)
    assert isinstance(table.c.username.type, CITEXT)
    assert isinstance(table.c.email.type, CITEXT)
    assert table.c.password_hash.nullable is False
    assert table.c.email.nullable is True
    assert table.c.locked_until.type.timezone is True
    assert table.c.created_at.type.timezone is True
    assert constraint_names(User) == {
        "ck_users_username",
        "ck_users_status",
        "ck_users_failed_login_count",
        "ck_users_version",
    }

    indexes = index_sql(User)
    assert "UNIQUE INDEX uq_users_email_active" in indexes["uq_users_email_active"]
    assert "email IS NOT NULL AND deleted_at IS NULL" in indexes[
        "uq_users_email_active"
    ]
    assert "created_at DESC" in indexes["ix_users_status_created_at"]
    assert "WHERE deleted_at IS NULL" in indexes["ix_users_department"]


def test_role_and_permission_constraints_and_indexes_match_migration() -> None:
    assert constraint_names(Role) == {"ck_roles_code", "ck_roles_version"}
    assert constraint_names(Permission) == {"ck_permissions_code"}

    permission_indexes = index_sql(Permission)
    assert "(module, code)" in permission_indexes["ix_permissions_module"]


def test_association_primary_keys_and_foreign_key_actions_match_migration() -> None:
    assert [column.name for column in UserRole.__table__.primary_key.columns] == [
        "user_id",
        "role_id",
    ]
    assert [
        column.name for column in RolePermission.__table__.primary_key.columns
    ] == ["role_id", "permission_id"]

    user_role_foreign_keys = {
        foreign_key.parent.name: (
            foreign_key.target_fullname,
            foreign_key.ondelete,
        )
        for foreign_key in UserRole.__table__.foreign_keys
    }
    assert user_role_foreign_keys == {
        "user_id": ("platform.users.id", "CASCADE"),
        "role_id": ("platform.roles.id", "RESTRICT"),
        "assigned_by": ("platform.users.id", "SET NULL"),
    }

    role_permission_foreign_keys = {
        foreign_key.parent.name: (
            foreign_key.target_fullname,
            foreign_key.ondelete,
        )
        for foreign_key in RolePermission.__table__.foreign_keys
    }
    assert role_permission_foreign_keys == {
        "role_id": ("platform.roles.id", "CASCADE"),
        "permission_id": ("platform.permissions.id", "RESTRICT"),
    }
    assert set(index_sql(UserRole)) == {"ix_user_roles_role_id"}
    assert set(index_sql(RolePermission)) == {
        "ix_role_permissions_permission_id"
    }


def test_refresh_token_mapping_matches_platform_migration() -> None:
    table = RefreshToken.__table__

    assert isinstance(table.c.id.type, UUID)
    assert isinstance(table.c.jti.type, UUID)
    assert isinstance(table.c.token_hash.type, CHAR)
    assert table.c.token_hash.type.length == 64
    assert isinstance(table.c.created_ip.type, INET)
    assert table.c.user_agent.type.length == 500
    assert table.c.jti.unique is True
    assert table.c.token_hash.unique is True
    assert table.c.token_hash.nullable is False
    assert table.c.revoked_at.nullable is True
    assert table.c.replaced_by_jti.nullable is True
    assert table.c.expires_at.type.timezone is True
    assert table.c.created_at.type.timezone is True
    assert table.c.id.server_default is not None
    assert table.c.created_at.server_default is not None
    assert constraint_names(RefreshToken) == {
        "ck_refresh_tokens_expiry",
        "ck_refresh_tokens_replacement",
    }

    foreign_keys = {
        foreign_key.parent.name: (foreign_key.target_fullname, foreign_key.ondelete)
        for foreign_key in table.foreign_keys
    }
    assert foreign_keys == {"user_id": ("platform.users.id", "CASCADE")}

    indexes = index_sql(RefreshToken)
    assert set(indexes) == {
        "ix_refresh_tokens_user_active",
        "ix_refresh_tokens_expiry",
    }
    assert "(user_id, expires_at DESC)" in indexes["ix_refresh_tokens_user_active"]
    assert "WHERE revoked_at IS NULL" in indexes["ix_refresh_tokens_user_active"]
    assert "WHERE revoked_at IS NULL" in indexes["ix_refresh_tokens_expiry"]


def test_app_config_mapping_matches_platform_migration() -> None:
    table = AppConfig.__table__

    assert isinstance(table.c.value.type, JSONB)
    assert table.c.key.primary_key is True
    assert table.c.editable.server_default is not None
    assert table.c.version.server_default is not None
    assert table.c.created_at.type.timezone is True
    assert table.c.updated_at.type.timezone is True
    assert constraint_names(AppConfig) == {
        "ck_app_configs_key",
        "ck_app_configs_value_type",
        "ck_app_configs_version",
    }
    foreign_keys = {
        foreign_key.parent.name: (foreign_key.target_fullname, foreign_key.ondelete)
        for foreign_key in table.foreign_keys
    }
    assert foreign_keys == {"updated_by": ("platform.users.id", "SET NULL")}
    assert "(namespace, key)" in index_sql(AppConfig)["ix_app_configs_namespace"]


def test_idempotency_record_mapping_matches_platform_migration() -> None:
    table = IdempotencyRecord.__table__

    assert isinstance(table.c.id.type, UUID)
    assert isinstance(table.c.request_hash.type, CHAR)
    assert table.c.request_hash.type.length == 64
    assert isinstance(table.c.response_body.type, JSONB)
    assert table.c.endpoint.type.length == 200
    assert table.c.idempotency_key.type.length == 128
    assert table.c.response_status.nullable is True
    assert table.c.response_body.nullable is True
    assert table.c.expires_at.nullable is False
    assert table.c.created_at.type.timezone is True
    assert table.c.expires_at.type.timezone is True
    assert table.c.id.server_default is not None
    assert table.c.created_at.server_default is not None
    assert constraint_names(IdempotencyRecord) == {
        "ck_idempotency_expiry",
        "ck_idempotency_response_status",
    }
    unique_constraints = {
        constraint.name: [column.name for column in constraint.columns]
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert unique_constraints == {
        "uq_idempotency_scope": ["user_id", "endpoint", "idempotency_key"]
    }
    foreign_keys = {
        foreign_key.parent.name: (foreign_key.target_fullname, foreign_key.ondelete)
        for foreign_key in table.foreign_keys
    }
    assert foreign_keys == {"user_id": ("platform.users.id", "CASCADE")}
    assert set(index_sql(IdempotencyRecord)) == {"ix_idempotency_records_expiry"}
    assert "(expires_at)" in index_sql(IdempotencyRecord)[
        "ix_idempotency_records_expiry"
    ]


def test_audit_log_mapping_matches_platform_migration() -> None:
    table = AuditLog.__table__

    assert isinstance(table.c.id.type, UUID)
    assert isinstance(table.c.ip_address.type, INET)
    assert isinstance(table.c.before_data.type, JSONB)
    assert isinstance(table.c.after_data.type, JSONB)
    assert table.c.request_id.type.length == 64
    assert table.c.user_agent.type.length == 500
    assert table.c.id.server_default is not None
    assert table.c.created_at.server_default is not None
    assert constraint_names(AuditLog) == {
        "ck_audit_logs_result",
        "ck_audit_logs_before_object",
        "ck_audit_logs_after_object",
    }
    foreign_keys = {
        foreign_key.parent.name: (foreign_key.target_fullname, foreign_key.ondelete)
        for foreign_key in table.foreign_keys
    }
    assert foreign_keys == {"actor_user_id": ("platform.users.id", "SET NULL")}
    assert set(index_sql(AuditLog)) == {
        "ix_audit_logs_created_at",
        "ix_audit_logs_actor_created_at",
        "ix_audit_logs_resource",
        "ix_audit_logs_request_id",
    }


def test_sensitive_word_mapping_matches_platform_migration() -> None:
    table = SensitiveWord.__table__
    assert isinstance(table.c.id.type, UUID)
    assert table.c.word.type.length == 200
    assert table.c.replacement.type.length == 100
    assert table.c.enabled.server_default is not None
    assert constraint_names(SensitiveWord) == {
        "ck_sensitive_words_match_type",
        "ck_sensitive_words_action",
        "ck_sensitive_words_scope",
        "ck_sensitive_words_replacement",
    }
    foreign_keys = {
        foreign_key.parent.name: (foreign_key.target_fullname, foreign_key.ondelete)
        for foreign_key in table.foreign_keys
    }
    assert foreign_keys == {"created_by": ("platform.users.id", "SET NULL")}
    indexes = index_sql(SensitiveWord)
    assert set(indexes) == {
        "uq_sensitive_words_rule", "ix_sensitive_words_enabled_scope"
    }
    assert "lower(word)" in indexes["uq_sensitive_words_rule"]


def test_all_identity_tables_compile_for_postgresql() -> None:
    dialect = postgresql.dialect()

    compiled_tables = "\n".join(
        str(CreateTable(model.__table__).compile(dialect=dialect))
        for model in (
            User,
            Role,
            Permission,
            UserRole,
            RolePermission,
            RefreshToken,
            AppConfig,
            IdempotencyRecord,
            AuditLog,
            SensitiveWord,
        )
    )

    assert "CREATE TABLE platform.users" in compiled_tables
    assert "CITEXT" in compiled_tables
    assert "UUID DEFAULT gen_random_uuid()" in compiled_tables
    assert "PRIMARY KEY (user_id, role_id)" in compiled_tables
    assert "ON DELETE SET NULL" in compiled_tables
    assert "CREATE TABLE platform.refresh_tokens" in compiled_tables
    assert "CHAR(64)" in compiled_tables
    assert "INET" in compiled_tables
    assert "ck_refresh_tokens_replacement" in compiled_tables
    assert "CREATE TABLE platform.app_configs" in compiled_tables
    assert "JSONB" in compiled_tables
    assert "CREATE TABLE platform.idempotency_records" in compiled_tables
    assert "CONSTRAINT uq_idempotency_scope UNIQUE (user_id, endpoint, idempotency_key)" in compiled_tables
    assert "ck_idempotency_response_status" in compiled_tables
    assert "CREATE TABLE platform.audit_logs" in compiled_tables
    assert "ck_audit_logs_before_object" in compiled_tables
    assert "CREATE TABLE platform.sensitive_words" in compiled_tables
    assert "ck_sensitive_words_replacement" in compiled_tables


def test_user_repr_does_not_expose_password_hash() -> None:
    password_hash = "argon2-hash-must-not-appear"
    user = User(
        username="student01",
        password_hash=password_hash,
        display_name="Student",
    )

    assert password_hash not in repr(user)


def test_refresh_token_repr_does_not_expose_token_hash() -> None:
    token_hash = "a" * 64
    refresh_token = RefreshToken(
        jti="f8e22491-4fab-437d-8409-7b340c84423b",
        user_id="1f762d59-a3ea-4018-95fd-1e657149977e",
        token_hash=token_hash,
        expires_at="2026-07-21T00:00:00+00:00",
    )

    assert token_hash not in repr(refresh_token)


def test_idempotency_record_repr_does_not_expose_hash_or_response_body() -> None:
    request_hash = "b" * 64
    response_body = {"access_token": "must-not-appear"}
    record = IdempotencyRecord(
        user_id="1f762d59-a3ea-4018-95fd-1e657149977e",
        endpoint="/api/v1/users",
        idempotency_key="create-user-key",
        request_hash=request_hash,
        response_body=response_body,
        expires_at="2026-07-15T00:00:00+00:00",
    )

    assert request_hash not in repr(record)
    assert "must-not-appear" not in repr(record)
