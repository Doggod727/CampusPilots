from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CHAR,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base

CAMPUS_SERVICE_SCHEMA = "campus_service"


class Campus(Base):
    __tablename__ = "campuses"
    __table_args__ = (
        CheckConstraint("code ~ '^[a-z][a-z0-9_-]{1,29}$'", name="ck_campuses_code"),
        CheckConstraint("sort_order >= 0", name="ck_campuses_sort_order"),
        {"schema": CAMPUS_SERVICE_SCHEMA},
    )

    code: Mapped[str] = mapped_column(String(30), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    address: Mapped[str | None] = mapped_column(String(300))
    enabled: Mapped[bool] = mapped_column(Boolean(), server_default=text("true"))
    sort_order: Mapped[int] = mapped_column(Integer(), server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class Department(Base):
    __tablename__ = "departments"
    __table_args__ = (
        CheckConstraint("code ~ '^[a-z][a-z0-9_]{2,49}$'", name="ck_departments_code"),
        {"schema": CAMPUS_SERVICE_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    code: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(String(500))
    enabled: Mapped[bool] = mapped_column(Boolean(), server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class DepartmentContact(Base):
    __tablename__ = "department_contacts"
    __table_args__ = (
        CheckConstraint(
            "phone IS NOT NULL OR email IS NOT NULL",
            name="ck_department_contacts_channel",
        ),
        CheckConstraint(
            "valid_until IS NULL OR valid_until >= valid_from",
            name="ck_department_contacts_validity",
        ),
        Index(
            "ix_department_contacts_active",
            "department_id",
            "campus_code",
            "valid_until",
            postgresql_where=text("enabled = true"),
        ),
        {"schema": CAMPUS_SERVICE_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    department_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("campus_service.departments.id", ondelete="CASCADE"),
    )
    campus_code: Mapped[str] = mapped_column(
        String(30), ForeignKey("campus_service.campuses.code", ondelete="RESTRICT")
    )
    contact_name: Mapped[str | None] = mapped_column(String(50))
    office_name: Mapped[str] = mapped_column(String(100))
    phone: Mapped[str | None] = mapped_column(String(30))
    email: Mapped[str | None] = mapped_column(String(254))
    location: Mapped[str] = mapped_column(String(200))
    office_hours: Mapped[str | None] = mapped_column(String(200))
    valid_from: Mapped[date] = mapped_column(Date(), server_default=text("CURRENT_DATE"))
    valid_until: Mapped[date | None] = mapped_column(Date())
    enabled: Mapped[bool] = mapped_column(Boolean(), server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class GuideCategory(Base):
    __tablename__ = "guide_categories"
    __table_args__ = (
        CheckConstraint("code ~ '^[a-z][a-z0-9_]{2,49}$'", name="ck_guide_categories_code"),
        CheckConstraint("sort_order >= 0", name="ck_guide_categories_sort_order"),
        {"schema": CAMPUS_SERVICE_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    code: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    sort_order: Mapped[int] = mapped_column(Integer(), server_default=text("0"))
    enabled: Mapped[bool] = mapped_column(Boolean(), server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class ServiceGuide(Base):
    __tablename__ = "service_guides"
    __table_args__ = (
        CheckConstraint(
            "code ~ '^[a-z][a-z0-9_]{2,59}$'", name="ck_service_guides_code"
        ),
        CheckConstraint(
            "status IN ('draft', 'published', 'archived')",
            name="ck_service_guides_status",
        ),
        CheckConstraint(
            "status <> 'published' OR published_at IS NOT NULL",
            name="ck_service_guides_publish_state",
        ),
        CheckConstraint("version >= 1", name="ck_service_guides_version"),
        Index("ix_service_guides_listing", "status", "category_id", text("updated_at DESC")),
        Index("ix_service_guides_department", "department_id", "status"),
        {"schema": CAMPUS_SERVICE_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    code: Mapped[str] = mapped_column(String(60), unique=True)
    category_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("campus_service.guide_categories.id", ondelete="RESTRICT"),
    )
    department_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("campus_service.departments.id", ondelete="RESTRICT"),
    )
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(String(500))
    location: Mapped[str | None] = mapped_column(String(300))
    service_hours: Mapped[str | None] = mapped_column(String(200))
    source_url: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(16), server_default=text("'published'"))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[date | None] = mapped_column(Date())
    version: Mapped[int] = mapped_column(Integer(), server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class GuideApplicability(Base):
    __tablename__ = "guide_applicabilities"
    __table_args__ = (
        CheckConstraint(
            "student_type IN ('undergraduate', 'postgraduate', 'international', 'all')",
            name="ck_guide_applicabilities_student_type",
        ),
        Index(
            "ix_guide_applicabilities_audience",
            "campus_code",
            "student_type",
            "guide_id",
        ),
        {"schema": CAMPUS_SERVICE_SCHEMA},
    )

    guide_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("campus_service.service_guides.id", ondelete="CASCADE"),
        primary_key=True,
    )
    campus_code: Mapped[str] = mapped_column(
        String(30),
        ForeignKey("campus_service.campuses.code", ondelete="RESTRICT"),
        primary_key=True,
    )
    student_type: Mapped[str] = mapped_column(String(30), primary_key=True)
    notes: Mapped[str | None] = mapped_column(String(300))


class GuideMaterial(Base):
    __tablename__ = "guide_materials"
    __table_args__ = (
        CheckConstraint("copies BETWEEN 0 AND 20", name="ck_guide_materials_copies"),
        CheckConstraint(
            "jsonb_typeof(condition) = 'object'", name="ck_guide_materials_condition"
        ),
        CheckConstraint("sort_order >= 0", name="ck_guide_materials_sort_order"),
        Index("ix_guide_materials_guide", "guide_id", "sort_order", "id"),
        Index("ix_guide_materials_condition_gin", "condition", postgresql_using="gin"),
        {"schema": CAMPUS_SERVICE_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    guide_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("campus_service.service_guides.id", ondelete="CASCADE"),
    )
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(String(500))
    required: Mapped[bool] = mapped_column(Boolean(), server_default=text("true"))
    copies: Mapped[int] = mapped_column(Integer(), server_default=text("1"))
    condition: Mapped[dict[str, object]] = mapped_column(
        JSONB(), server_default=text("'{}'::jsonb")
    )
    sort_order: Mapped[int] = mapped_column(Integer(), server_default=text("0"))


class GuideStep(Base):
    __tablename__ = "guide_steps"
    __table_args__ = (
        UniqueConstraint("guide_id", "step_no"),
        CheckConstraint("step_no >= 1", name="ck_guide_steps_step_no"),
        CheckConstraint(
            "estimated_minutes IS NULL OR estimated_minutes BETWEEN 1 AND 10080",
            name="ck_guide_steps_estimated_minutes",
        ),
        {"schema": CAMPUS_SERVICE_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    guide_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("campus_service.service_guides.id", ondelete="CASCADE"),
    )
    step_no: Mapped[int] = mapped_column(Integer())
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text())
    location: Mapped[str | None] = mapped_column(String(300))
    estimated_minutes: Mapped[int | None] = mapped_column(Integer())


class ElectricityAccount(Base):
    __tablename__ = "electricity_accounts"
    __table_args__ = (
        UniqueConstraint(
            "campus_code", "dormitory_area", "building", "room", name="uq_electricity_room"
        ),
        CheckConstraint("balance >= 0", name="ck_electricity_balance"),
        CheckConstraint("currency = 'CNY'", name="ck_electricity_currency"),
        CheckConstraint("source IN ('mock', 'external')", name="ck_electricity_source"),
        CheckConstraint(
            "source <> 'mock' OR is_simulated = true", name="ck_electricity_demo_source"
        ),
        {"schema": CAMPUS_SERVICE_SCHEMA},
    )

    room_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    campus_code: Mapped[str] = mapped_column(
        String(30), ForeignKey("campus_service.campuses.code", ondelete="RESTRICT")
    )
    dormitory_area: Mapped[str] = mapped_column(String(100))
    building: Mapped[str] = mapped_column(String(50))
    room: Mapped[str] = mapped_column(String(30))
    balance: Mapped[Decimal] = mapped_column(Numeric(10, 2), server_default=text("0"))
    currency: Mapped[str] = mapped_column(CHAR(3), server_default=text("'CNY'"))
    source: Mapped[str] = mapped_column(String(16), server_default=text("'mock'"))
    is_simulated: Mapped[bool] = mapped_column(Boolean(), server_default=text("true"))
    source_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class ElectricityAccountMember(Base):
    __tablename__ = "electricity_account_members"
    __table_args__ = (
        CheckConstraint(
            "member_role IN ('resident', 'manager')", name="ck_electricity_member_role"
        ),
        Index("ix_electricity_members_user", "user_id", "room_id"),
        {"schema": CAMPUS_SERVICE_SCHEMA},
    )

    room_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("campus_service.electricity_accounts.room_id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    member_role: Mapped[str] = mapped_column(
        String(16), server_default=text("'resident'")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class ElectricityTopupRequest(Base):
    __tablename__ = "electricity_topup_requests"
    __table_args__ = (
        UniqueConstraint(
            "requested_by", "idempotency_key", name="uq_electricity_topup_idempotency"
        ),
        CheckConstraint(
            "amount BETWEEN 1.00 AND 500.00", name="ck_electricity_topup_amount"
        ),
        CheckConstraint("currency = 'CNY'", name="ck_electricity_topup_currency"),
        CheckConstraint("status = 'simulated'", name="ck_electricity_topup_status"),
        CheckConstraint("is_simulated = true", name="ck_electricity_topup_simulated"),
        CheckConstraint(
            "(agent_run_id IS NULL AND approval_id IS NULL) OR "
            "(agent_run_id IS NOT NULL AND approval_id IS NOT NULL)",
            name="ck_electricity_topup_agent_approval",
        ),
        Index("ix_electricity_topup_user_created", "requested_by", text("created_at DESC")),
        Index("ix_electricity_topup_room_created", "room_id", text("created_at DESC")),
        {"schema": CAMPUS_SERVICE_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    room_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("campus_service.electricity_accounts.room_id", ondelete="RESTRICT"),
    )
    requested_by: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(CHAR(3), server_default=text("'CNY'"))
    status: Mapped[str] = mapped_column(String(16), server_default=text("'simulated'"))
    is_simulated: Mapped[bool] = mapped_column(Boolean(), server_default=text("true"))
    agent_run_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    approval_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(CHAR(64))
    notice: Mapped[str] = mapped_column(
        String(300), server_default=text("'演示申请，不产生真实扣款或到账'")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
