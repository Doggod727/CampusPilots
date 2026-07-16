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
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
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
