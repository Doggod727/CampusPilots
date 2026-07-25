BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE SCHEMA IF NOT EXISTS community;

CREATE OR REPLACE FUNCTION community.set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

CREATE TABLE IF NOT EXISTS community.topics (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code varchar(50) NOT NULL UNIQUE,
    name varchar(50) NOT NULL,
    description varchar(300) NULL,
    allow_anonymous boolean NOT NULL DEFAULT false,
    sort_order integer NOT NULL DEFAULT 0,
    status varchar(16) NOT NULL DEFAULT 'active',
    created_by uuid NOT NULL,
    version integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz NULL,
    CONSTRAINT ck_topics_code CHECK (code ~ '^[a-z][a-z0-9-]{2,49}$'),
    CONSTRAINT ck_topics_status CHECK (status IN ('active', 'archived')),
    CONSTRAINT ck_topics_version CHECK (version >= 1)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_topics_name_active
    ON community.topics (lower(name)) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_topics_list
    ON community.topics (status, sort_order, created_at) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS community.posts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    topic_id uuid NOT NULL REFERENCES community.topics(id) ON DELETE RESTRICT,
    author_user_id uuid NOT NULL,
    title varchar(120) NOT NULL,
    content_markdown text NOT NULL,
    is_anonymous boolean NOT NULL DEFAULT false,
    status varchar(20) NOT NULL DEFAULT 'pending_review',
    risk_level varchar(16) NOT NULL DEFAULT 'low',
    moderation_case_id uuid NULL,
    moderation_policy_version varchar(50) NOT NULL,
    like_count integer NOT NULL DEFAULT 0,
    favorite_count integer NOT NULL DEFAULT 0,
    comment_count integer NOT NULL DEFAULT 0,
    report_count integer NOT NULL DEFAULT 0,
    published_at timestamptz NULL,
    version integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz NULL,
    CONSTRAINT ck_posts_content CHECK (char_length(content_markdown) BETWEEN 1 AND 5000),
    CONSTRAINT ck_posts_status CHECK (
        status IN ('pending_review', 'published', 'rejected', 'hidden', 'deleted')
    ),
    CONSTRAINT ck_posts_risk CHECK (risk_level IN ('low', 'medium', 'high', 'critical')),
    CONSTRAINT ck_posts_counts CHECK (
        like_count >= 0 AND favorite_count >= 0 AND comment_count >= 0 AND report_count >= 0
    ),
    CONSTRAINT ck_posts_publish CHECK (
        (status = 'published' AND published_at IS NOT NULL) OR status <> 'published'
    ),
    CONSTRAINT ck_posts_review_case CHECK (
        status <> 'pending_review' OR moderation_case_id IS NOT NULL
    ),
    CONSTRAINT ck_posts_version CHECK (version >= 1)
);

CREATE INDEX IF NOT EXISTS ix_posts_feed
    ON community.posts (topic_id, published_at DESC, id DESC)
    WHERE status = 'published' AND deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_posts_author
    ON community.posts (author_user_id, created_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_posts_moderation_case
    ON community.posts (moderation_case_id) WHERE moderation_case_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS community.comments (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    post_id uuid NOT NULL REFERENCES community.posts(id) ON DELETE CASCADE,
    parent_comment_id uuid NULL REFERENCES community.comments(id) ON DELETE SET NULL,
    author_user_id uuid NOT NULL,
    content_markdown text NOT NULL,
    is_anonymous boolean NOT NULL DEFAULT false,
    status varchar(20) NOT NULL DEFAULT 'pending_review',
    risk_level varchar(16) NOT NULL DEFAULT 'low',
    moderation_case_id uuid NULL,
    moderation_policy_version varchar(50) NOT NULL,
    published_at timestamptz NULL,
    version integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz NULL,
    CONSTRAINT ck_comments_content CHECK (char_length(content_markdown) BETWEEN 1 AND 1000),
    CONSTRAINT ck_comments_status CHECK (
        status IN ('pending_review', 'published', 'rejected', 'hidden', 'deleted')
    ),
    CONSTRAINT ck_comments_risk CHECK (risk_level IN ('low', 'medium', 'high', 'critical')),
    CONSTRAINT ck_comments_publish CHECK (
        (status = 'published' AND published_at IS NOT NULL) OR status <> 'published'
    ),
    CONSTRAINT ck_comments_review_case CHECK (
        status <> 'pending_review' OR moderation_case_id IS NOT NULL
    ),
    CONSTRAINT ck_comments_version CHECK (version >= 1)
);

CREATE INDEX IF NOT EXISTS ix_comments_post
    ON community.comments (post_id, created_at, id)
    WHERE status = 'published' AND deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_comments_author
    ON community.comments (author_user_id, created_at DESC) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS community.post_reactions (
    post_id uuid NOT NULL REFERENCES community.posts(id) ON DELETE CASCADE,
    user_id uuid NOT NULL,
    reaction_type varchar(16) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (post_id, user_id, reaction_type),
    CONSTRAINT ck_post_reactions_type CHECK (reaction_type IN ('like', 'favorite'))
);

CREATE INDEX IF NOT EXISTS ix_post_reactions_user
    ON community.post_reactions (user_id, reaction_type, created_at DESC);

CREATE TABLE IF NOT EXISTS community.content_reports (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    reporter_user_id uuid NOT NULL,
    target_type varchar(20) NOT NULL,
    target_id uuid NOT NULL,
    reason_code varchar(30) NOT NULL,
    details varchar(500) NOT NULL,
    status varchar(16) NOT NULL DEFAULT 'submitted',
    moderation_case_id uuid NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (reporter_user_id, target_type, target_id),
    CONSTRAINT ck_content_reports_target
        CHECK (target_type IN ('post', 'comment', 'event', 'lost_found')),
    CONSTRAINT ck_content_reports_reason
        CHECK (reason_code IN ('spam', 'abuse', 'privacy', 'fraud', 'unsafe', 'other')),
    CONSTRAINT ck_content_reports_details CHECK (char_length(details) BETWEEN 2 AND 500),
    CONSTRAINT ck_content_reports_status
        CHECK (status IN ('submitted', 'linked', 'closed'))
);

CREATE INDEX IF NOT EXISTS ix_content_reports_target
    ON community.content_reports (target_type, target_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_content_reports_case
    ON community.content_reports (moderation_case_id)
    WHERE moderation_case_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS community.campus_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organizer_user_id uuid NOT NULL,
    title varchar(120) NOT NULL,
    description_markdown text NOT NULL,
    category varchar(50) NOT NULL,
    location varchar(200) NOT NULL,
    starts_at timestamptz NOT NULL,
    ends_at timestamptz NOT NULL,
    registration_deadline timestamptz NOT NULL,
    capacity integer NOT NULL,
    registered_count integer NOT NULL DEFAULT 0,
    status varchar(20) NOT NULL DEFAULT 'pending_review',
    risk_level varchar(16) NOT NULL DEFAULT 'low',
    moderation_case_id uuid NULL,
    moderation_policy_version varchar(50) NOT NULL,
    cancellation_reason varchar(500) NULL,
    published_at timestamptz NULL,
    version integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz NULL,
    CONSTRAINT ck_events_description CHECK (char_length(description_markdown) BETWEEN 1 AND 5000),
    CONSTRAINT ck_events_times CHECK (
        starts_at < ends_at AND registration_deadline <= starts_at
    ),
    CONSTRAINT ck_events_capacity CHECK (
        capacity BETWEEN 1 AND 10000 AND registered_count BETWEEN 0 AND capacity
    ),
    CONSTRAINT ck_events_status CHECK (
        status IN ('pending_review', 'published', 'rejected', 'cancelled', 'ended', 'deleted')
    ),
    CONSTRAINT ck_events_risk CHECK (risk_level IN ('low', 'medium', 'high', 'critical')),
    CONSTRAINT ck_events_publish CHECK (
        (status = 'published' AND published_at IS NOT NULL) OR status <> 'published'
    ),
    CONSTRAINT ck_events_review_case CHECK (
        status <> 'pending_review' OR moderation_case_id IS NOT NULL
    ),
    CONSTRAINT ck_events_cancel_reason CHECK (
        status <> 'cancelled' OR cancellation_reason IS NOT NULL
    ),
    CONSTRAINT ck_events_version CHECK (version >= 1)
);

CREATE INDEX IF NOT EXISTS ix_events_public_list
    ON community.campus_events (starts_at, id)
    WHERE status = 'published' AND deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_events_organizer
    ON community.campus_events (organizer_user_id, created_at DESC)
    WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS community.event_registrations (
    event_id uuid NOT NULL REFERENCES community.campus_events(id) ON DELETE CASCADE,
    user_id uuid NOT NULL,
    status varchar(16) NOT NULL DEFAULT 'registered',
    registered_at timestamptz NOT NULL DEFAULT now(),
    cancelled_at timestamptz NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (event_id, user_id),
    CONSTRAINT ck_event_registrations_status CHECK (status IN ('registered', 'cancelled')),
    CONSTRAINT ck_event_registrations_cancelled CHECK (
        (status = 'cancelled' AND cancelled_at IS NOT NULL) OR status = 'registered'
    )
);

CREATE INDEX IF NOT EXISTS ix_event_registrations_user
    ON community.event_registrations (user_id, status, registered_at DESC);

CREATE TABLE IF NOT EXISTS community.lost_found_items (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_user_id uuid NOT NULL,
    item_type varchar(10) NOT NULL,
    title varchar(120) NOT NULL,
    category varchar(50) NOT NULL,
    description varchar(2000) NOT NULL,
    occurred_at timestamptz NOT NULL,
    location varchar(200) NOT NULL,
    contact_type varchar(16) NOT NULL,
    contact_ciphertext bytea NOT NULL,
    contact_hint varchar(50) NOT NULL,
    status varchar(20) NOT NULL DEFAULT 'pending_review',
    risk_level varchar(16) NOT NULL DEFAULT 'low',
    moderation_case_id uuid NULL,
    moderation_policy_version varchar(50) NOT NULL,
    published_at timestamptz NULL,
    completed_at timestamptz NULL,
    version integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz NULL,
    CONSTRAINT ck_lost_found_type CHECK (item_type IN ('lost', 'found')),
    CONSTRAINT ck_lost_found_description CHECK (char_length(description) BETWEEN 5 AND 2000),
    CONSTRAINT ck_lost_found_contact_type CHECK (contact_type IN ('phone', 'email', 'wechat', 'other')),
    CONSTRAINT ck_lost_found_status CHECK (
        status IN ('pending_review', 'published', 'claiming', 'completed', 'closed', 'rejected', 'deleted')
    ),
    CONSTRAINT ck_lost_found_risk CHECK (risk_level IN ('low', 'medium', 'high', 'critical')),
    CONSTRAINT ck_lost_found_publish CHECK (
        (status IN ('published', 'claiming') AND published_at IS NOT NULL)
        OR status NOT IN ('published', 'claiming')
    ),
    CONSTRAINT ck_lost_found_review_case CHECK (
        status <> 'pending_review' OR moderation_case_id IS NOT NULL
    ),
    CONSTRAINT ck_lost_found_completed CHECK (
        (status = 'completed' AND completed_at IS NOT NULL) OR status <> 'completed'
    ),
    CONSTRAINT ck_lost_found_version CHECK (version >= 1)
);

CREATE INDEX IF NOT EXISTS ix_lost_found_public_list
    ON community.lost_found_items (item_type, category, occurred_at DESC, id DESC)
    WHERE status IN ('published', 'claiming') AND deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_lost_found_owner
    ON community.lost_found_items (owner_user_id, created_at DESC) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS community.lost_found_matches (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_item_id uuid NOT NULL REFERENCES community.lost_found_items(id) ON DELETE CASCADE,
    candidate_item_id uuid NOT NULL REFERENCES community.lost_found_items(id) ON DELETE CASCADE,
    score numeric(6,5) NOT NULL,
    reasons jsonb NOT NULL,
    algorithm_version varchar(50) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_item_id, candidate_item_id, algorithm_version),
    CONSTRAINT ck_lost_found_matches_distinct CHECK (source_item_id <> candidate_item_id),
    CONSTRAINT ck_lost_found_matches_score CHECK (score BETWEEN 0 AND 1),
    CONSTRAINT ck_lost_found_matches_reasons CHECK (jsonb_typeof(reasons) = 'array')
);

CREATE INDEX IF NOT EXISTS ix_lost_found_matches_source
    ON community.lost_found_matches (source_item_id, score DESC, created_at DESC);

CREATE TABLE IF NOT EXISTS community.lost_found_claims (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    target_item_id uuid NOT NULL REFERENCES community.lost_found_items(id) ON DELETE RESTRICT,
    claimant_item_id uuid NULL REFERENCES community.lost_found_items(id) ON DELETE SET NULL,
    claimant_user_id uuid NOT NULL,
    evidence_ciphertext bytea NOT NULL,
    status varchar(16) NOT NULL DEFAULT 'pending',
    decision_reason varchar(500) NULL,
    decided_by uuid NULL,
    decided_at timestamptz NULL,
    claimant_confirmed_at timestamptz NULL,
    owner_confirmed_at timestamptz NULL,
    completed_at timestamptz NULL,
    version integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_lost_found_claims_status
        CHECK (status IN ('pending', 'verified', 'rejected', 'cancelled', 'completed')),
    CONSTRAINT ck_lost_found_claims_decision CHECK (
        (status IN ('verified', 'rejected', 'completed') AND decided_by IS NOT NULL AND decided_at IS NOT NULL)
        OR status IN ('pending', 'cancelled')
    ),
    CONSTRAINT ck_lost_found_claims_reason CHECK (
        status <> 'rejected' OR decision_reason IS NOT NULL
    ),
    CONSTRAINT ck_lost_found_claims_completed CHECK (
        (status = 'completed' AND claimant_confirmed_at IS NOT NULL
         AND owner_confirmed_at IS NOT NULL AND completed_at IS NOT NULL)
        OR status <> 'completed'
    ),
    CONSTRAINT ck_lost_found_claims_version CHECK (version >= 1)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_lost_found_claims_active
    ON community.lost_found_claims (target_item_id, claimant_user_id)
    WHERE status IN ('pending', 'verified');
CREATE INDEX IF NOT EXISTS ix_lost_found_claims_claimant
    ON community.lost_found_claims (claimant_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_lost_found_claims_target
    ON community.lost_found_claims (target_item_id, status, created_at DESC);

DROP TRIGGER IF EXISTS trg_topics_updated_at ON community.topics;
CREATE TRIGGER trg_topics_updated_at BEFORE UPDATE ON community.topics
FOR EACH ROW EXECUTE FUNCTION community.set_updated_at();

DROP TRIGGER IF EXISTS trg_posts_updated_at ON community.posts;
CREATE TRIGGER trg_posts_updated_at BEFORE UPDATE ON community.posts
FOR EACH ROW EXECUTE FUNCTION community.set_updated_at();

DROP TRIGGER IF EXISTS trg_comments_updated_at ON community.comments;
CREATE TRIGGER trg_comments_updated_at BEFORE UPDATE ON community.comments
FOR EACH ROW EXECUTE FUNCTION community.set_updated_at();

DROP TRIGGER IF EXISTS trg_content_reports_updated_at ON community.content_reports;
CREATE TRIGGER trg_content_reports_updated_at BEFORE UPDATE ON community.content_reports
FOR EACH ROW EXECUTE FUNCTION community.set_updated_at();

DROP TRIGGER IF EXISTS trg_events_updated_at ON community.campus_events;
CREATE TRIGGER trg_events_updated_at BEFORE UPDATE ON community.campus_events
FOR EACH ROW EXECUTE FUNCTION community.set_updated_at();

DROP TRIGGER IF EXISTS trg_event_registrations_updated_at ON community.event_registrations;
CREATE TRIGGER trg_event_registrations_updated_at BEFORE UPDATE ON community.event_registrations
FOR EACH ROW EXECUTE FUNCTION community.set_updated_at();

DROP TRIGGER IF EXISTS trg_lost_found_items_updated_at ON community.lost_found_items;
CREATE TRIGGER trg_lost_found_items_updated_at BEFORE UPDATE ON community.lost_found_items
FOR EACH ROW EXECUTE FUNCTION community.set_updated_at();

DROP TRIGGER IF EXISTS trg_lost_found_claims_updated_at ON community.lost_found_claims;
CREATE TRIGGER trg_lost_found_claims_updated_at BEFORE UPDATE ON community.lost_found_claims
FOR EACH ROW EXECUTE FUNCTION community.set_updated_at();

COMMENT ON SCHEMA community IS 'M3 校园社区、活动与失物招领数据域';
COMMENT ON COLUMN community.posts.author_user_id IS '逻辑引用 platform.users；匿名响应不得返回';
COMMENT ON COLUMN community.comments.author_user_id IS '逻辑引用 platform.users；匿名响应不得返回';
COMMENT ON COLUMN community.lost_found_items.contact_ciphertext IS '由应用层 AEAD 加密；仅认领验证后授权解密';
COMMENT ON COLUMN community.lost_found_claims.evidence_ciphertext IS '关键特征验证材料，应用层加密存储';
COMMENT ON COLUMN community.content_reports.moderation_case_id IS '逻辑引用 platform.moderation_cases';

COMMIT;
