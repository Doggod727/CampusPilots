from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CHAR,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import (
    CITEXT,
    INET,
    JSONB,
    UUID as PostgreSQLUUID,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base

PLATFORM_SCHEMA = "platform"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "username::text ~ '^[A-Za-z][A-Za-z0-9_.-]{2,49}$'",
            name="ck_users_username",
        ),
        CheckConstraint(
            "status IN ('active', 'disabled', 'locked')",
            name="ck_users_status",
        ),
        CheckConstraint(
            "failed_login_count >= 0",
            name="ck_users_failed_login_count",
        ),
        CheckConstraint("version >= 1", name="ck_users_version"),
        {"schema": PLATFORM_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    username: Mapped[str] = mapped_column(CITEXT(), unique=True)
    password_hash: Mapped[str] = mapped_column(Text())
    display_name: Mapped[str] = mapped_column(String(50))
    email: Mapped[str | None] = mapped_column(CITEXT())
    department: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(16), server_default=text("'active'"))
    failed_login_count: Mapped[int] = mapped_column(Integer(), server_default=text("0"))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )
    version: Mapped[int] = mapped_column(Integer(), server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index(
    "uq_users_email_active",
    User.email,
    unique=True,
    postgresql_where=text("email IS NOT NULL AND deleted_at IS NULL"),
)
Index(
    "ix_users_status_created_at",
    User.status,
    User.created_at.desc(),
    postgresql_where=text("deleted_at IS NULL"),
)
Index(
    "ix_users_department",
    User.department,
    postgresql_where=text("deleted_at IS NULL"),
)


class Role(Base):
    __tablename__ = "roles"
    __table_args__ = (
        CheckConstraint(
            "code ~ '^[a-z][a-z0-9_]{2,49}$'",
            name="ck_roles_code",
        ),
        CheckConstraint("version >= 1", name="ck_roles_version"),
        {"schema": PLATFORM_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    code: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(50))
    description: Mapped[str | None] = mapped_column(String(500))
    is_system: Mapped[bool] = mapped_column(Boolean(), server_default=text("false"))
    version: Mapped[int] = mapped_column(Integer(), server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )


class Permission(Base):
    __tablename__ = "permissions"
    __table_args__ = (
        CheckConstraint(
            "code ~ '^[a-z][a-z0-9_]*:[a-z][a-z0-9_:]*$'",
            name="ck_permissions_code",
        ),
        {"schema": PLATFORM_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    code: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    module: Mapped[str] = mapped_column(String(50))
    description: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )


Index("ix_permissions_module", Permission.module, Permission.code)


class UserRole(Base):
    __tablename__ = "user_roles"
    __table_args__ = ({"schema": PLATFORM_SCHEMA},)

    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("platform.users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("platform.roles.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    assigned_by: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("platform.users.id", ondelete="SET NULL"),
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )


Index("ix_user_roles_role_id", UserRole.role_id, UserRole.user_id)


class RolePermission(Base):
    __tablename__ = "role_permissions"
    __table_args__ = ({"schema": PLATFORM_SCHEMA},)

    role_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("platform.roles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    permission_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("platform.permissions.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )


Index(
    "ix_role_permissions_permission_id",
    RolePermission.permission_id,
    RolePermission.role_id,
)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    __table_args__ = (
        CheckConstraint(
            "expires_at > created_at",
            name="ck_refresh_tokens_expiry",
        ),
        CheckConstraint(
            "replaced_by_jti IS NULL OR replaced_by_jti <> jti",
            name="ck_refresh_tokens_replacement",
        ),
        {"schema": PLATFORM_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    jti: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), unique=True)
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("platform.users.id", ondelete="CASCADE"),
    )
    token_hash: Mapped[str] = mapped_column(CHAR(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by_jti: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    created_ip: Mapped[str | None] = mapped_column(INET())
    user_agent: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )


Index(
    "ix_refresh_tokens_user_active",
    RefreshToken.user_id,
    RefreshToken.expires_at.desc(),
    postgresql_where=text("revoked_at IS NULL"),
)
Index(
    "ix_refresh_tokens_expiry",
    RefreshToken.expires_at,
    postgresql_where=text("revoked_at IS NULL"),
)


class SensitiveWord(Base):
    __tablename__ = "sensitive_words"
    __table_args__ = (
        CheckConstraint(
            "match_type IN ('exact', 'contains', 'regex')",
            name="ck_sensitive_words_match_type",
        ),
        CheckConstraint(
            "action IN ('mask', 'block', 'review')",
            name="ck_sensitive_words_action",
        ),
        CheckConstraint(
            "scope IN ('user_input', 'ai_output', 'community', 'all')",
            name="ck_sensitive_words_scope",
        ),
        CheckConstraint(
            "action <> 'mask' OR replacement IS NOT NULL",
            name="ck_sensitive_words_replacement",
        ),
        {"schema": PLATFORM_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    word: Mapped[str] = mapped_column(String(200))
    match_type: Mapped[str] = mapped_column(String(16))
    action: Mapped[str] = mapped_column(String(16))
    replacement: Mapped[str | None] = mapped_column(String(100))
    scope: Mapped[str] = mapped_column(String(20))
    enabled: Mapped[bool] = mapped_column(Boolean(), server_default=text("true"))
    created_by: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("platform.users.id", ondelete="SET NULL"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


Index(
    "uq_sensitive_words_rule",
    func.lower(SensitiveWord.word),
    SensitiveWord.match_type,
    SensitiveWord.scope,
    unique=True,
)
Index("ix_sensitive_words_enabled_scope", SensitiveWord.scope, SensitiveWord.enabled)


class ModerationCase(Base):
    __tablename__ = "moderation_cases"
    __table_args__ = (
        CheckConstraint(
            "target_module IN ('ai_knowledge', 'campus_service', 'community')",
            name="ck_moderation_cases_target_module",
        ),
        CheckConstraint(
            "risk_level IN ('low', 'medium', 'high', 'critical')",
            name="ck_moderation_cases_risk_level",
        ),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'escalated')",
            name="ck_moderation_cases_status",
        ),
        CheckConstraint(
            "jsonb_typeof(rule_hits) = 'array'",
            name="ck_moderation_cases_rule_hits",
        ),
        CheckConstraint(
            "((status = 'pending' AND reviewer_id IS NULL AND reviewed_at IS NULL) "
            "OR (status <> 'pending' AND reviewer_id IS NOT NULL "
            "AND reviewed_at IS NOT NULL AND decision_reason IS NOT NULL))",
            name="ck_moderation_cases_decision",
        ),
        CheckConstraint("version >= 1", name="ck_moderation_cases_version"),
        {"schema": PLATFORM_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    target_module: Mapped[str] = mapped_column(String(30))
    target_type: Mapped[str] = mapped_column(String(50))
    target_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    content_excerpt: Mapped[str] = mapped_column(String(500))
    risk_level: Mapped[str] = mapped_column(String(16))
    rule_hits: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB(), server_default=text("'[]'::jsonb")
    )
    status: Mapped[str] = mapped_column(String(16), server_default=text("'pending'"))
    submitted_by: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("platform.users.id", ondelete="SET NULL")
    )
    reviewer_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("platform.users.id", ondelete="SET NULL")
    )
    decision_reason: Mapped[str | None] = mapped_column(String(500))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer(), server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


Index(
    "ix_moderation_cases_queue",
    ModerationCase.status, ModerationCase.risk_level, ModerationCase.created_at.desc(),
)
Index(
    "ix_moderation_cases_target",
    ModerationCase.target_module, ModerationCase.target_type, ModerationCase.target_id,
)
Index(
    "ix_moderation_cases_rule_hits_gin",
    ModerationCase.rule_hits,
    postgresql_using="gin",
)


class AppConfig(Base):
    __tablename__ = "app_configs"
    __table_args__ = (
        CheckConstraint(
            "key ~ '^[a-z][a-z0-9_.-]{2,99}$'",
            name="ck_app_configs_key",
        ),
        CheckConstraint(
            "value_type IN ('string', 'integer', 'number', 'boolean', 'json')",
            name="ck_app_configs_value_type",
        ),
        CheckConstraint("version >= 1", name="ck_app_configs_version"),
        {"schema": PLATFORM_SCHEMA},
    )

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    namespace: Mapped[str] = mapped_column(String(50))
    value: Mapped[object] = mapped_column(JSONB())
    value_type: Mapped[str] = mapped_column(String(16))
    description: Mapped[str | None] = mapped_column(String(500))
    editable: Mapped[bool] = mapped_column(Boolean(), server_default=text("true"))
    version: Mapped[int] = mapped_column(Integer(), server_default=text("1"))
    updated_by: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("platform.users.id", ondelete="SET NULL"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )


Index("ix_app_configs_namespace", AppConfig.namespace, AppConfig.key)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "endpoint",
            "idempotency_key",
            name="uq_idempotency_scope",
        ),
        CheckConstraint(
            "expires_at > created_at",
            name="ck_idempotency_expiry",
        ),
        CheckConstraint(
            "response_status IS NULL OR response_status BETWEEN 100 AND 599",
            name="ck_idempotency_response_status",
        ),
        {"schema": PLATFORM_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("platform.users.id", ondelete="CASCADE"),
    )
    endpoint: Mapped[str] = mapped_column(String(200))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(CHAR(64))
    response_status: Mapped[int | None] = mapped_column(Integer())
    response_body: Mapped[object | None] = mapped_column(JSONB())
    resource_type: Mapped[str | None] = mapped_column(String(100))
    resource_id: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


Index("ix_idempotency_records_expiry", IdempotencyRecord.expires_at)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        CheckConstraint(
            "result IN ('success', 'failure')",
            name="ck_audit_logs_result",
        ),
        CheckConstraint(
            "before_data IS NULL OR jsonb_typeof(before_data) = 'object'",
            name="ck_audit_logs_before_object",
        ),
        CheckConstraint(
            "after_data IS NULL OR jsonb_typeof(after_data) = 'object'",
            name="ck_audit_logs_after_object",
        ),
        {"schema": PLATFORM_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("platform.users.id", ondelete="SET NULL"),
    )
    actor_username: Mapped[str | None] = mapped_column(String(50))
    action: Mapped[str] = mapped_column(String(100))
    resource_type: Mapped[str] = mapped_column(String(100))
    resource_id: Mapped[str | None] = mapped_column(String(100))
    result: Mapped[str] = mapped_column(String(16))
    request_id: Mapped[str] = mapped_column(String(64))
    ip_address: Mapped[str | None] = mapped_column(INET())
    user_agent: Mapped[str | None] = mapped_column(String(500))
    before_data: Mapped[dict[str, object] | None] = mapped_column(JSONB())
    after_data: Mapped[dict[str, object] | None] = mapped_column(JSONB())
    error_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )


Index("ix_audit_logs_created_at", AuditLog.created_at.desc())
Index(
    "ix_audit_logs_actor_created_at",
    AuditLog.actor_user_id,
    AuditLog.created_at.desc(),
)
Index(
    "ix_audit_logs_resource",
    AuditLog.resource_type,
    AuditLog.resource_id,
    AuditLog.created_at.desc(),
)
Index("ix_audit_logs_request_id", AuditLog.request_id)
