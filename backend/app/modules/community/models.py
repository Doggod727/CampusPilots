from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base

COMMUNITY_SCHEMA = "community"


class Topic(Base):
    __tablename__ = "topics"
    __table_args__ = (
        CheckConstraint("code ~ '^[a-z][a-z0-9-]{2,49}$'", name="ck_topics_code"),
        CheckConstraint("status IN ('active', 'archived')", name="ck_topics_status"),
        CheckConstraint("version >= 1", name="ck_topics_version"),
        Index(
            "uq_topics_name_active", text("lower(name)"), unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_topics_list", "status", "sort_order", "created_at",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        {"schema": COMMUNITY_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    code: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(50))
    description: Mapped[str | None] = mapped_column(String(300))
    allow_anonymous: Mapped[bool] = mapped_column(Boolean(), server_default=text("false"))
    sort_order: Mapped[int] = mapped_column(Integer(), server_default=text("0"))
    status: Mapped[str] = mapped_column(String(16), server_default=text("'active'"))
    created_by: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    version: Mapped[int] = mapped_column(Integer(), server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Post(Base):
    __tablename__ = "posts"
    __table_args__ = (
        CheckConstraint("char_length(content_markdown) BETWEEN 1 AND 5000", name="ck_posts_content"),
        CheckConstraint("status IN ('pending_review', 'published', 'rejected', 'hidden', 'deleted')", name="ck_posts_status"),
        CheckConstraint("risk_level IN ('low', 'medium', 'high', 'critical')", name="ck_posts_risk"),
        CheckConstraint("like_count >= 0 AND favorite_count >= 0 AND comment_count >= 0 AND report_count >= 0", name="ck_posts_counts"),
        CheckConstraint("(status = 'published' AND published_at IS NOT NULL) OR status <> 'published'", name="ck_posts_publish"),
        CheckConstraint("status <> 'pending_review' OR moderation_case_id IS NOT NULL", name="ck_posts_review_case"),
        CheckConstraint("version >= 1", name="ck_posts_version"),
        Index(
            "ix_posts_feed", "topic_id", text("published_at DESC"), text("id DESC"),
            postgresql_where=text("status = 'published' AND deleted_at IS NULL"),
        ),
        Index("ix_posts_author", "author_user_id", text("created_at DESC"), postgresql_where=text("deleted_at IS NULL")),
        Index("ix_posts_moderation_case", "moderation_case_id", postgresql_where=text("moderation_case_id IS NOT NULL")),
        {"schema": COMMUNITY_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    topic_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), ForeignKey("community.topics.id", ondelete="RESTRICT"))
    author_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    title: Mapped[str] = mapped_column(String(120))
    content_markdown: Mapped[str] = mapped_column(Text())
    is_anonymous: Mapped[bool] = mapped_column(Boolean(), server_default=text("false"))
    status: Mapped[str] = mapped_column(String(20), server_default=text("'pending_review'"))
    risk_level: Mapped[str] = mapped_column(String(16), server_default=text("'low'"))
    moderation_case_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    moderation_policy_version: Mapped[str] = mapped_column(String(50))
    like_count: Mapped[int] = mapped_column(Integer(), server_default=text("0"))
    favorite_count: Mapped[int] = mapped_column(Integer(), server_default=text("0"))
    comment_count: Mapped[int] = mapped_column(Integer(), server_default=text("0"))
    report_count: Mapped[int] = mapped_column(Integer(), server_default=text("0"))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer(), server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Comment(Base):
    __tablename__ = "comments"
    __table_args__ = (
        CheckConstraint("char_length(content_markdown) BETWEEN 1 AND 1000", name="ck_comments_content"),
        CheckConstraint("status IN ('pending_review', 'published', 'rejected', 'hidden', 'deleted')", name="ck_comments_status"),
        CheckConstraint("risk_level IN ('low', 'medium', 'high', 'critical')", name="ck_comments_risk"),
        CheckConstraint("(status = 'published' AND published_at IS NOT NULL) OR status <> 'published'", name="ck_comments_publish"),
        CheckConstraint("status <> 'pending_review' OR moderation_case_id IS NOT NULL", name="ck_comments_review_case"),
        CheckConstraint("version >= 1", name="ck_comments_version"),
        Index(
            "ix_comments_post", "post_id", "created_at", "id",
            postgresql_where=text("status = 'published' AND deleted_at IS NULL"),
        ),
        Index("ix_comments_author", "author_user_id", text("created_at DESC"), postgresql_where=text("deleted_at IS NULL")),
        {"schema": COMMUNITY_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    post_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), ForeignKey("community.posts.id", ondelete="CASCADE"))
    parent_comment_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True), ForeignKey("community.comments.id", ondelete="SET NULL"))
    author_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    content_markdown: Mapped[str] = mapped_column(Text())
    is_anonymous: Mapped[bool] = mapped_column(Boolean(), server_default=text("false"))
    status: Mapped[str] = mapped_column(String(20), server_default=text("'pending_review'"))
    risk_level: Mapped[str] = mapped_column(String(16), server_default=text("'low'"))
    moderation_case_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    moderation_policy_version: Mapped[str] = mapped_column(String(50))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer(), server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PostReaction(Base):
    __tablename__ = "post_reactions"
    __table_args__ = (
        CheckConstraint("reaction_type IN ('like', 'favorite')", name="ck_post_reactions_type"),
        Index("ix_post_reactions_user", "user_id", "reaction_type", text("created_at DESC")),
        {"schema": COMMUNITY_SCHEMA},
    )

    post_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("community.posts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    reaction_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class ContentReport(Base):
    __tablename__ = "content_reports"
    __table_args__ = (
        UniqueConstraint("reporter_user_id", "target_type", "target_id"),
        CheckConstraint("target_type IN ('post', 'comment', 'event', 'lost_found')", name="ck_content_reports_target"),
        CheckConstraint("reason_code IN ('spam', 'abuse', 'privacy', 'fraud', 'unsafe', 'other')", name="ck_content_reports_reason"),
        CheckConstraint("char_length(details) BETWEEN 2 AND 500", name="ck_content_reports_details"),
        CheckConstraint("status IN ('submitted', 'linked', 'closed')", name="ck_content_reports_status"),
        Index("ix_content_reports_target", "target_type", "target_id", text("created_at DESC")),
        Index("ix_content_reports_case", "moderation_case_id", postgresql_where=text("moderation_case_id IS NOT NULL")),
        {"schema": COMMUNITY_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    reporter_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    target_type: Mapped[str] = mapped_column(String(20))
    target_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    reason_code: Mapped[str] = mapped_column(String(30))
    details: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(16), server_default=text("'submitted'"))
    moderation_case_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class CampusEvent(Base):
    __tablename__ = "campus_events"
    __table_args__ = (
        CheckConstraint("char_length(description_markdown) BETWEEN 1 AND 5000", name="ck_events_description"),
        CheckConstraint("starts_at < ends_at AND registration_deadline <= starts_at", name="ck_events_times"),
        CheckConstraint("capacity BETWEEN 1 AND 10000 AND registered_count BETWEEN 0 AND capacity", name="ck_events_capacity"),
        CheckConstraint("status IN ('pending_review', 'published', 'rejected', 'cancelled', 'ended', 'deleted')", name="ck_events_status"),
        CheckConstraint("risk_level IN ('low', 'medium', 'high', 'critical')", name="ck_events_risk"),
        CheckConstraint("(status = 'published' AND published_at IS NOT NULL) OR status <> 'published'", name="ck_events_publish"),
        CheckConstraint("status <> 'pending_review' OR moderation_case_id IS NOT NULL", name="ck_events_review_case"),
        CheckConstraint("status <> 'cancelled' OR cancellation_reason IS NOT NULL", name="ck_events_cancel_reason"),
        CheckConstraint("version >= 1", name="ck_events_version"),
        Index("ix_events_public_list", "starts_at", "id", postgresql_where=text("status = 'published' AND deleted_at IS NULL")),
        Index("ix_events_organizer", "organizer_user_id", text("created_at DESC"), postgresql_where=text("deleted_at IS NULL")),
        {"schema": COMMUNITY_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    organizer_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    title: Mapped[str] = mapped_column(String(120))
    description_markdown: Mapped[str] = mapped_column(Text())
    category: Mapped[str] = mapped_column(String(50))
    location: Mapped[str] = mapped_column(String(200))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    registration_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    capacity: Mapped[int] = mapped_column(Integer())
    registered_count: Mapped[int] = mapped_column(Integer(), server_default=text("0"))
    status: Mapped[str] = mapped_column(String(20), server_default=text("'pending_review'"))
    risk_level: Mapped[str] = mapped_column(String(16), server_default=text("'low'"))
    moderation_case_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    moderation_policy_version: Mapped[str] = mapped_column(String(50))
    cancellation_reason: Mapped[str | None] = mapped_column(String(500))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer(), server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EventRegistration(Base):
    __tablename__ = "event_registrations"
    __table_args__ = (
        CheckConstraint("status IN ('registered', 'cancelled')", name="ck_event_registrations_status"),
        CheckConstraint("(status = 'cancelled' AND cancelled_at IS NOT NULL) OR status = 'registered'", name="ck_event_registrations_cancelled"),
        Index("ix_event_registrations_user", "user_id", "status", text("registered_at DESC")),
        {"schema": COMMUNITY_SCHEMA},
    )

    event_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("community.campus_events.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), server_default=text("'registered'"))
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class LostFoundItem(Base):
    __tablename__ = "lost_found_items"
    __table_args__ = (
        CheckConstraint("item_type IN ('lost', 'found')", name="ck_lost_found_type"),
        CheckConstraint("char_length(description) BETWEEN 5 AND 2000", name="ck_lost_found_description"),
        CheckConstraint("contact_type IN ('phone', 'email', 'wechat', 'other')", name="ck_lost_found_contact_type"),
        CheckConstraint("status IN ('pending_review', 'published', 'claiming', 'completed', 'closed', 'rejected', 'deleted')", name="ck_lost_found_status"),
        CheckConstraint("risk_level IN ('low', 'medium', 'high', 'critical')", name="ck_lost_found_risk"),
        CheckConstraint("(status IN ('published', 'claiming') AND published_at IS NOT NULL) OR status NOT IN ('published', 'claiming')", name="ck_lost_found_publish"),
        CheckConstraint("status <> 'pending_review' OR moderation_case_id IS NOT NULL", name="ck_lost_found_review_case"),
        CheckConstraint("(status = 'completed' AND completed_at IS NOT NULL) OR status <> 'completed'", name="ck_lost_found_completed"),
        CheckConstraint("version >= 1", name="ck_lost_found_version"),
        Index(
            "ix_lost_found_public_list", "item_type", "category", text("occurred_at DESC"), text("id DESC"),
            postgresql_where=text("status IN ('published', 'claiming') AND deleted_at IS NULL"),
        ),
        Index("ix_lost_found_owner", "owner_user_id", text("created_at DESC"), postgresql_where=text("deleted_at IS NULL")),
        {"schema": COMMUNITY_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    item_type: Mapped[str] = mapped_column(String(10))
    title: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(50))
    description: Mapped[str] = mapped_column(String(2000))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    location: Mapped[str] = mapped_column(String(200))
    contact_type: Mapped[str] = mapped_column(String(16))
    contact_ciphertext: Mapped[bytes] = mapped_column(LargeBinary())
    contact_hint: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), server_default=text("'pending_review'"))
    risk_level: Mapped[str] = mapped_column(String(16), server_default=text("'low'"))
    moderation_case_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    moderation_policy_version: Mapped[str] = mapped_column(String(50))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer(), server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LostFoundMatch(Base):
    __tablename__ = "lost_found_matches"
    __table_args__ = (
        UniqueConstraint("source_item_id", "candidate_item_id", "algorithm_version"),
        CheckConstraint("source_item_id <> candidate_item_id", name="ck_lost_found_matches_distinct"),
        CheckConstraint("score BETWEEN 0 AND 1", name="ck_lost_found_matches_score"),
        CheckConstraint("jsonb_typeof(reasons) = 'array'", name="ck_lost_found_matches_reasons"),
        Index("ix_lost_found_matches_source", "source_item_id", text("score DESC"), text("created_at DESC")),
        {"schema": COMMUNITY_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    source_item_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), ForeignKey("community.lost_found_items.id", ondelete="CASCADE"))
    candidate_item_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), ForeignKey("community.lost_found_items.id", ondelete="CASCADE"))
    score: Mapped[Decimal] = mapped_column(Numeric(6, 5))
    reasons: Mapped[list[object]] = mapped_column(JSONB())
    algorithm_version: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class LostFoundClaim(Base):
    __tablename__ = "lost_found_claims"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'verified', 'rejected', 'cancelled', 'completed')", name="ck_lost_found_claims_status"),
        CheckConstraint("(status IN ('verified', 'rejected', 'completed') AND decided_by IS NOT NULL AND decided_at IS NOT NULL) OR status IN ('pending', 'cancelled')", name="ck_lost_found_claims_decision"),
        CheckConstraint("status <> 'rejected' OR decision_reason IS NOT NULL", name="ck_lost_found_claims_reason"),
        CheckConstraint("(status = 'completed' AND claimant_confirmed_at IS NOT NULL AND owner_confirmed_at IS NOT NULL AND completed_at IS NOT NULL) OR status <> 'completed'", name="ck_lost_found_claims_completed"),
        CheckConstraint("version >= 1", name="ck_lost_found_claims_version"),
        Index(
            "uq_lost_found_claims_active", "target_item_id", "claimant_user_id", unique=True,
            postgresql_where=text("status IN ('pending', 'verified')"),
        ),
        Index("ix_lost_found_claims_claimant", "claimant_user_id", text("created_at DESC")),
        Index("ix_lost_found_claims_target", "target_item_id", "status", text("created_at DESC")),
        {"schema": COMMUNITY_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    target_item_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), ForeignKey("community.lost_found_items.id", ondelete="RESTRICT"))
    claimant_item_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True), ForeignKey("community.lost_found_items.id", ondelete="SET NULL"))
    claimant_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    evidence_ciphertext: Mapped[bytes] = mapped_column(LargeBinary())
    status: Mapped[str] = mapped_column(String(16), server_default=text("'pending'"))
    decision_reason: Mapped[str | None] = mapped_column(String(500))
    decided_by: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimant_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    owner_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer(), server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
