from sqlalchemy import CheckConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import CITEXT, UUID
from sqlalchemy.schema import CreateIndex, CreateTable

from app.infrastructure.database import Base
from app.modules.platform.models import (
    Permission,
    Role,
    RolePermission,
    User,
    UserRole,
)

EXPECTED_TABLES = {
    "platform.users",
    "platform.roles",
    "platform.permissions",
    "platform.user_roles",
    "platform.role_permissions",
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


def test_all_identity_tables_compile_for_postgresql() -> None:
    dialect = postgresql.dialect()

    compiled_tables = "\n".join(
        str(CreateTable(model.__table__).compile(dialect=dialect))
        for model in (User, Role, Permission, UserRole, RolePermission)
    )

    assert "CREATE TABLE platform.users" in compiled_tables
    assert "CITEXT" in compiled_tables
    assert "UUID DEFAULT gen_random_uuid()" in compiled_tables
    assert "PRIMARY KEY (user_id, role_id)" in compiled_tables
    assert "ON DELETE SET NULL" in compiled_tables


def test_user_repr_does_not_expose_password_hash() -> None:
    password_hash = "argon2-hash-must-not-appear"
    user = User(
        username="student01",
        password_hash=password_hash,
        display_name="Student",
    )

    assert password_hash not in repr(user)
