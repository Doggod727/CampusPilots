from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
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
