from uuid import UUID

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from app.modules.community.models import (
    CampusEvent,
    Comment,
    ContentReport,
    EventRegistration,
    LostFoundClaim,
    LostFoundItem,
    LostFoundMatch,
    Post,
    PostReaction,
    Topic,
)


def _table_sql(model: type[object]) -> str:
    return str(CreateTable(model.__table__).compile(dialect=postgresql.dialect()))


def _index_sql(model: type[object]) -> str:
    return "\n".join(
        str(CreateIndex(index).compile(dialect=postgresql.dialect()))
        for index in model.__table__.indexes
    )


def test_topic_model_matches_postgresql_contract() -> None:
    assert Topic.__table__.fullname == "community.topics"
    assert set(Topic.__table__.columns.keys()) == {
        "id", "code", "name", "description", "allow_anonymous", "sort_order",
        "status", "created_by", "version", "created_at", "updated_at", "deleted_at",
    }
    sql = _table_sql(Topic)
    indexes = _index_sql(Topic)
    assert "ck_topics_code" in sql and "ck_topics_status" in sql
    assert "ck_topics_version" in sql
    assert "CREATE UNIQUE INDEX uq_topics_name_active" in indexes
    assert "WHERE deleted_at IS NULL" in indexes
    assert "CREATE INDEX ix_topics_list" in indexes


def test_post_model_preserves_logical_ids_constraints_and_partial_indexes() -> None:
    sql = _table_sql(Post)
    indexes = _index_sql(Post)
    assert "FOREIGN KEY(topic_id) REFERENCES community.topics (id) ON DELETE RESTRICT" in sql
    assert "FOREIGN KEY(author_user_id)" not in sql
    assert "FOREIGN KEY(moderation_case_id)" not in sql
    for name in (
        "ck_posts_content", "ck_posts_status", "ck_posts_risk", "ck_posts_counts",
        "ck_posts_publish", "ck_posts_review_case", "ck_posts_version",
    ):
        assert name in sql
    assert "ix_posts_feed" in indexes and "status = 'published'" in indexes
    assert "ix_posts_author" in indexes and "ix_posts_moderation_case" in indexes


def test_comment_model_preserves_self_reference_and_safe_repr() -> None:
    sql = _table_sql(Comment)
    indexes = _index_sql(Comment)
    assert "FOREIGN KEY(post_id) REFERENCES community.posts (id) ON DELETE CASCADE" in sql
    assert "FOREIGN KEY(parent_comment_id) REFERENCES community.comments (id) ON DELETE SET NULL" in sql
    assert "FOREIGN KEY(author_user_id)" not in sql
    assert "ix_comments_post" in indexes and "status = 'published'" in indexes
    assert "ix_comments_author" in indexes

    secret_user = UUID("90000000-0000-4000-8000-000000000001")
    secret_case = UUID("90000000-0000-4000-8000-000000000002")
    post = Post(content_markdown="private post body", author_user_id=secret_user, moderation_case_id=secret_case)
    comment = Comment(content_markdown="private comment body", author_user_id=secret_user, moderation_case_id=secret_case)
    rendered = repr(post) + repr(comment)
    assert "private post body" not in rendered
    assert "private comment body" not in rendered
    assert str(secret_user) not in rendered and str(secret_case) not in rendered


def test_post_reaction_model_has_composite_primary_key_and_user_index() -> None:
    table = PostReaction.__table__
    assert [column.name for column in table.primary_key.columns] == [
        "post_id", "user_id", "reaction_type"
    ]
    sql = _table_sql(PostReaction)
    indexes = _index_sql(PostReaction)
    assert "ON DELETE CASCADE" in sql and "ck_post_reactions_type" in sql
    assert "ix_post_reactions_user" in indexes and "created_at DESC" in indexes


def test_content_report_model_preserves_business_uniqueness_and_safe_repr() -> None:
    sql = _table_sql(ContentReport)
    indexes = _index_sql(ContentReport)
    assert "UNIQUE (reporter_user_id, target_type, target_id)" in sql
    for name in (
        "ck_content_reports_target", "ck_content_reports_reason",
        "ck_content_reports_details", "ck_content_reports_status",
    ):
        assert name in sql
    assert "FOREIGN KEY(reporter_user_id)" not in sql
    assert "FOREIGN KEY(moderation_case_id)" not in sql
    assert "ix_content_reports_target" in indexes
    assert "ix_content_reports_case" in indexes and "moderation_case_id IS NOT NULL" in indexes

    secret_user = UUID("90000000-0000-4000-8000-000000000003")
    report = ContentReport(reporter_user_id=secret_user, details="private report details")
    assert str(secret_user) not in repr(report)
    assert "private report details" not in repr(report)


def test_campus_event_model_preserves_constraints_partial_indexes_and_safe_repr() -> None:
    sql = _table_sql(CampusEvent)
    indexes = _index_sql(CampusEvent)
    for name in (
        "ck_events_description", "ck_events_times", "ck_events_capacity",
        "ck_events_status", "ck_events_risk", "ck_events_publish",
        "ck_events_review_case", "ck_events_cancel_reason", "ck_events_version",
    ):
        assert name in sql
    assert "FOREIGN KEY(organizer_user_id)" not in sql
    assert "FOREIGN KEY(moderation_case_id)" not in sql
    assert "ix_events_public_list" in indexes and "status = 'published'" in indexes
    assert "ix_events_organizer" in indexes and "deleted_at IS NULL" in indexes

    event = CampusEvent(
        description_markdown="private event body",
        cancellation_reason="private cancellation reason",
    )
    assert "private event body" not in repr(event)
    assert "private cancellation reason" not in repr(event)


def test_event_registration_model_has_composite_key_and_cancellation_contract() -> None:
    table = EventRegistration.__table__
    assert [column.name for column in table.primary_key.columns] == ["event_id", "user_id"]
    sql = _table_sql(EventRegistration)
    indexes = _index_sql(EventRegistration)
    assert "ON DELETE CASCADE" in sql
    assert "FOREIGN KEY(user_id)" not in sql
    assert "ck_event_registrations_status" in sql
    assert "ck_event_registrations_cancelled" in sql
    assert "ix_event_registrations_user" in indexes and "registered_at DESC" in indexes


def test_lost_found_item_model_preserves_binary_contact_and_visibility_contract() -> None:
    sql = _table_sql(LostFoundItem)
    indexes = _index_sql(LostFoundItem)
    assert "contact_ciphertext BYTEA NOT NULL" in sql
    for name in (
        "ck_lost_found_type", "ck_lost_found_description", "ck_lost_found_contact_type",
        "ck_lost_found_status", "ck_lost_found_risk", "ck_lost_found_publish",
        "ck_lost_found_review_case", "ck_lost_found_completed", "ck_lost_found_version",
    ):
        assert name in sql
    assert "FOREIGN KEY(owner_user_id)" not in sql
    assert "ix_lost_found_public_list" in indexes and "status IN ('published', 'claiming')" in indexes
    assert "ix_lost_found_owner" in indexes

    item = LostFoundItem(contact_ciphertext=b"secret-contact", contact_hint="private hint")
    assert "secret-contact" not in repr(item) and "private hint" not in repr(item)


def test_lost_found_match_model_preserves_score_json_and_business_uniqueness() -> None:
    sql = _table_sql(LostFoundMatch)
    indexes = _index_sql(LostFoundMatch)
    assert "score NUMERIC(6, 5) NOT NULL" in sql
    assert "reasons JSONB NOT NULL" in sql
    assert "UNIQUE (source_item_id, candidate_item_id, algorithm_version)" in sql
    assert sql.count("ON DELETE CASCADE") == 2
    assert "ck_lost_found_matches_distinct" in sql
    assert "ck_lost_found_matches_score" in sql
    assert "ck_lost_found_matches_reasons" in sql
    assert "ix_lost_found_matches_source" in indexes and "score DESC" in indexes


def test_lost_found_claim_model_preserves_state_and_active_unique_contract() -> None:
    sql = _table_sql(LostFoundClaim)
    indexes = _index_sql(LostFoundClaim)
    assert "evidence_ciphertext BYTEA NOT NULL" in sql
    assert "ON DELETE RESTRICT" in sql and "ON DELETE SET NULL" in sql
    assert "FOREIGN KEY(claimant_user_id)" not in sql
    assert "FOREIGN KEY(decided_by)" not in sql
    for name in (
        "ck_lost_found_claims_status", "ck_lost_found_claims_decision",
        "ck_lost_found_claims_reason", "ck_lost_found_claims_completed",
        "ck_lost_found_claims_version",
    ):
        assert name in sql
    assert "CREATE UNIQUE INDEX uq_lost_found_claims_active" in indexes
    assert "status IN ('pending', 'verified')" in indexes
    assert "ix_lost_found_claims_claimant" in indexes
    assert "ix_lost_found_claims_target" in indexes

    claim = LostFoundClaim(
        evidence_ciphertext=b"secret-evidence", decision_reason="private decision"
    )
    assert "secret-evidence" not in repr(claim)
    assert "private decision" not in repr(claim)
