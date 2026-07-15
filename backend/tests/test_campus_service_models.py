from sqlalchemy import CheckConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from app.infrastructure.database import Base
from app.modules.campus_service.models import (
    Campus,
    Department,
    DepartmentContact,
    ElectricityAccount,
    ElectricityAccountMember,
    ElectricityTopupRequest,
    GuideCategory,
)

EXPECTED_TABLES = {
    "campus_service.campuses",
    "campus_service.departments",
    "campus_service.department_contacts",
    "campus_service.guide_categories",
    "campus_service.electricity_accounts",
    "campus_service.electricity_account_members",
    "campus_service.electricity_topup_requests",
}


def constraint_names(model: type[Base]) -> set[str]:
    return {
        constraint.name
        for constraint in model.__table__.constraints
        if isinstance(constraint, CheckConstraint) and constraint.name is not None
    }


def test_reference_metadata_contains_only_current_m2_models() -> None:
    tables = {
        key for key, table in Base.metadata.tables.items() if table.schema == "campus_service"
    }
    assert tables == EXPECTED_TABLES


def test_reference_columns_and_constraints_match_migration() -> None:
    assert set(Campus.__table__.columns.keys()) == {
        "code", "name", "address", "enabled", "sort_order", "created_at", "updated_at"
    }
    assert set(Department.__table__.columns.keys()) == {
        "id", "code", "name", "description", "enabled", "created_at", "updated_at"
    }
    assert set(DepartmentContact.__table__.columns.keys()) == {
        "id", "department_id", "campus_code", "contact_name", "office_name", "phone",
        "email", "location", "office_hours", "valid_from", "valid_until", "enabled",
        "created_at", "updated_at",
    }
    assert set(GuideCategory.__table__.columns.keys()) == {
        "id", "code", "name", "sort_order", "enabled", "created_at"
    }
    assert constraint_names(Campus) == {"ck_campuses_code", "ck_campuses_sort_order"}
    assert constraint_names(Department) == {"ck_departments_code"}
    assert constraint_names(DepartmentContact) == {
        "ck_department_contacts_channel", "ck_department_contacts_validity"
    }
    assert constraint_names(GuideCategory) == {
        "ck_guide_categories_code", "ck_guide_categories_sort_order"
    }


def test_contact_foreign_keys_and_active_index_match_migration() -> None:
    foreign_keys = {
        key.parent.name: (key.target_fullname, key.ondelete)
        for key in DepartmentContact.__table__.foreign_keys
    }
    assert foreign_keys == {
        "department_id": ("campus_service.departments.id", "CASCADE"),
        "campus_code": ("campus_service.campuses.code", "RESTRICT"),
    }
    index = next(iter(DepartmentContact.__table__.indexes))
    sql = str(CreateIndex(index).compile(dialect=postgresql.dialect()))
    assert index.name == "ix_department_contacts_active"
    assert "WHERE enabled = true" in sql
    assert "department_id, campus_code, valid_until" in sql


def test_reference_models_compile_for_postgresql() -> None:
    dialect = postgresql.dialect()
    ddl = "\n".join(
        str(CreateTable(model.__table__).compile(dialect=dialect))
        for model in (Campus, Department, DepartmentContact, GuideCategory)
    )
    assert "campus_service.campuses" in ddl
    assert "UUID DEFAULT gen_random_uuid()" in ddl
    assert "DATE DEFAULT CURRENT_DATE" in ddl
    assert "ON DELETE CASCADE" in ddl
    assert "ON DELETE RESTRICT" in ddl


def test_contact_repr_does_not_include_contact_values() -> None:
    contact = DepartmentContact(
        contact_name="Sensitive Name",
        office_name="Office",
        phone="010-secret",
        email="secret@example.edu",
        location="Private room",
    )
    representation = repr(contact)
    assert "Sensitive Name" not in representation
    assert "010-secret" not in representation
    assert "secret@example.edu" not in representation
    assert "Private room" not in representation


def test_electricity_metadata_matches_migration() -> None:
    assert set(ElectricityAccount.__table__.columns.keys()) == {
        "room_id", "campus_code", "dormitory_area", "building", "room", "balance",
        "currency", "source", "is_simulated", "source_updated_at", "created_at", "updated_at",
    }
    assert set(ElectricityAccountMember.__table__.columns.keys()) == {
        "room_id", "user_id", "member_role", "created_at"
    }
    assert set(ElectricityTopupRequest.__table__.columns.keys()) == {
        "id", "room_id", "requested_by", "amount", "currency", "status", "is_simulated",
        "agent_run_id", "approval_id", "idempotency_key", "request_hash", "notice", "created_at",
    }
    assert constraint_names(ElectricityAccount) == {
        "ck_electricity_balance", "ck_electricity_currency", "ck_electricity_source",
        "ck_electricity_demo_source",
    }
    assert constraint_names(ElectricityAccountMember) == {"ck_electricity_member_role"}
    assert constraint_names(ElectricityTopupRequest) == {
        "ck_electricity_topup_amount", "ck_electricity_topup_currency",
        "ck_electricity_topup_status", "ck_electricity_topup_simulated",
        "ck_electricity_topup_agent_approval",
    }


def test_electricity_models_compile_for_postgresql() -> None:
    dialect = postgresql.dialect()
    models = (ElectricityAccount, ElectricityAccountMember, ElectricityTopupRequest)
    ddl = "\n".join(str(CreateTable(model.__table__).compile(dialect=dialect)) for model in models)
    indexes = "\n".join(
        str(CreateIndex(index).compile(dialect=dialect))
        for model in models for index in model.__table__.indexes
    )
    assert "NUMERIC(10, 2)" in ddl
    assert "CHAR(3)" in ddl
    assert "CHAR(64)" in ddl
    assert "PRIMARY KEY (room_id, user_id)" in ddl
    assert "ON DELETE CASCADE" in ddl
    assert "ON DELETE RESTRICT" in ddl
    assert "uq_electricity_topup_idempotency" in ddl
    assert "ix_electricity_members_user" in indexes
    assert "created_at DESC" in indexes


def test_electricity_repr_hides_room_and_request_integrity_fields() -> None:
    account = ElectricityAccount(dormitory_area="Private dorm", building="Secret", room="101")
    topup = ElectricityTopupRequest(request_hash="sensitive-hash", idempotency_key="secret-key")
    assert "Private dorm" not in repr(account)
    assert "sensitive-hash" not in repr(topup)
    assert "secret-key" not in repr(topup)
