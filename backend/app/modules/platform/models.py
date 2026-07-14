from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT, UUID as PostgreSQLUUID
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
