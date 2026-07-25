BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS citext;
CREATE SCHEMA IF NOT EXISTS platform;

CREATE OR REPLACE FUNCTION platform.set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

CREATE TABLE IF NOT EXISTS platform.users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    username citext NOT NULL UNIQUE,
    password_hash text NOT NULL,
    display_name varchar(50) NOT NULL,
    email citext NULL,
    department varchar(100) NULL,
    status varchar(16) NOT NULL DEFAULT 'active',
    failed_login_count integer NOT NULL DEFAULT 0,
    locked_until timestamptz NULL,
    last_login_at timestamptz NULL,
    password_changed_at timestamptz NOT NULL DEFAULT now(),
    version integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz NULL,
    CONSTRAINT ck_users_username
        CHECK (username::text ~ '^[A-Za-z][A-Za-z0-9_.-]{2,49}$'),
    CONSTRAINT ck_users_status
        CHECK (status IN ('active', 'disabled', 'locked')),
    CONSTRAINT ck_users_failed_login_count
        CHECK (failed_login_count >= 0),
    CONSTRAINT ck_users_version
        CHECK (version >= 1)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_users_email_active
    ON platform.users (email)
    WHERE email IS NOT NULL AND deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_users_status_created_at
    ON platform.users (status, created_at DESC)
    WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_users_department
    ON platform.users (department)
    WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS platform.roles (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code varchar(50) NOT NULL UNIQUE,
    name varchar(50) NOT NULL,
    description varchar(500) NULL,
    is_system boolean NOT NULL DEFAULT false,
    version integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_roles_code CHECK (code ~ '^[a-z][a-z0-9_]{2,49}$'),
    CONSTRAINT ck_roles_version CHECK (version >= 1)
);

CREATE TABLE IF NOT EXISTS platform.permissions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code varchar(100) NOT NULL UNIQUE,
    name varchar(100) NOT NULL,
    module varchar(50) NOT NULL,
    description varchar(500) NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_permissions_code
        CHECK (code ~ '^[a-z][a-z0-9_]*:[a-z][a-z0-9_:]*$')
);

CREATE INDEX IF NOT EXISTS ix_permissions_module
    ON platform.permissions (module, code);

CREATE TABLE IF NOT EXISTS platform.user_roles (
    user_id uuid NOT NULL REFERENCES platform.users(id) ON DELETE CASCADE,
    role_id uuid NOT NULL REFERENCES platform.roles(id) ON DELETE RESTRICT,
    assigned_by uuid NULL REFERENCES platform.users(id) ON DELETE SET NULL,
    assigned_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, role_id)
);

CREATE INDEX IF NOT EXISTS ix_user_roles_role_id
    ON platform.user_roles (role_id, user_id);

CREATE TABLE IF NOT EXISTS platform.role_permissions (
    role_id uuid NOT NULL REFERENCES platform.roles(id) ON DELETE CASCADE,
    permission_id uuid NOT NULL REFERENCES platform.permissions(id) ON DELETE RESTRICT,
    granted_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (role_id, permission_id)
);

CREATE INDEX IF NOT EXISTS ix_role_permissions_permission_id
    ON platform.role_permissions (permission_id, role_id);

CREATE TABLE IF NOT EXISTS platform.refresh_tokens (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    jti uuid NOT NULL UNIQUE,
    user_id uuid NOT NULL REFERENCES platform.users(id) ON DELETE CASCADE,
    token_hash char(64) NOT NULL UNIQUE,
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz NULL,
    replaced_by_jti uuid NULL,
    created_ip inet NULL,
    user_agent varchar(500) NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_refresh_tokens_expiry
        CHECK (expires_at > created_at),
    CONSTRAINT ck_refresh_tokens_replacement
        CHECK (replaced_by_jti IS NULL OR replaced_by_jti <> jti)
);

CREATE INDEX IF NOT EXISTS ix_refresh_tokens_user_active
    ON platform.refresh_tokens (user_id, expires_at DESC)
    WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_refresh_tokens_expiry
    ON platform.refresh_tokens (expires_at)
    WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS platform.sensitive_words (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    word varchar(200) NOT NULL,
    match_type varchar(16) NOT NULL,
    action varchar(16) NOT NULL,
    replacement varchar(100) NULL,
    scope varchar(20) NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    created_by uuid NULL REFERENCES platform.users(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_sensitive_words_match_type
        CHECK (match_type IN ('exact', 'contains', 'regex')),
    CONSTRAINT ck_sensitive_words_action
        CHECK (action IN ('mask', 'block', 'review')),
    CONSTRAINT ck_sensitive_words_scope
        CHECK (scope IN ('user_input', 'ai_output', 'community', 'all')),
    CONSTRAINT ck_sensitive_words_replacement
        CHECK (action <> 'mask' OR replacement IS NOT NULL)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_sensitive_words_rule
    ON platform.sensitive_words (lower(word), match_type, scope);
CREATE INDEX IF NOT EXISTS ix_sensitive_words_enabled_scope
    ON platform.sensitive_words (scope, enabled);

CREATE TABLE IF NOT EXISTS platform.moderation_cases (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    target_module varchar(30) NOT NULL,
    target_type varchar(50) NOT NULL,
    target_id uuid NOT NULL,
    content_excerpt varchar(500) NOT NULL,
    risk_level varchar(16) NOT NULL,
    rule_hits jsonb NOT NULL DEFAULT '[]'::jsonb,
    status varchar(16) NOT NULL DEFAULT 'pending',
    submitted_by uuid NULL REFERENCES platform.users(id) ON DELETE SET NULL,
    reviewer_id uuid NULL REFERENCES platform.users(id) ON DELETE SET NULL,
    decision_reason varchar(500) NULL,
    reviewed_at timestamptz NULL,
    version integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_moderation_cases_target_module
        CHECK (target_module IN ('ai_knowledge', 'campus_service', 'community')),
    CONSTRAINT ck_moderation_cases_risk_level
        CHECK (risk_level IN ('low', 'medium', 'high', 'critical')),
    CONSTRAINT ck_moderation_cases_status
        CHECK (status IN ('pending', 'approved', 'rejected', 'escalated')),
    CONSTRAINT ck_moderation_cases_rule_hits
        CHECK (jsonb_typeof(rule_hits) = 'array'),
    CONSTRAINT ck_moderation_cases_decision
        CHECK (
            (status = 'pending' AND reviewer_id IS NULL AND reviewed_at IS NULL)
            OR
            (status <> 'pending' AND reviewer_id IS NOT NULL
             AND reviewed_at IS NOT NULL AND decision_reason IS NOT NULL)
        ),
    CONSTRAINT ck_moderation_cases_version CHECK (version >= 1)
);

CREATE INDEX IF NOT EXISTS ix_moderation_cases_queue
    ON platform.moderation_cases (status, risk_level, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_moderation_cases_target
    ON platform.moderation_cases (target_module, target_type, target_id);
CREATE INDEX IF NOT EXISTS ix_moderation_cases_rule_hits_gin
    ON platform.moderation_cases USING gin (rule_hits);

CREATE TABLE IF NOT EXISTS platform.audit_logs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_user_id uuid NULL REFERENCES platform.users(id) ON DELETE SET NULL,
    actor_username varchar(50) NULL,
    action varchar(100) NOT NULL,
    resource_type varchar(100) NOT NULL,
    resource_id varchar(100) NULL,
    result varchar(16) NOT NULL,
    request_id varchar(64) NOT NULL,
    ip_address inet NULL,
    user_agent varchar(500) NULL,
    before_data jsonb NULL,
    after_data jsonb NULL,
    error_code varchar(100) NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_audit_logs_result CHECK (result IN ('success', 'failure')),
    CONSTRAINT ck_audit_logs_before_object
        CHECK (before_data IS NULL OR jsonb_typeof(before_data) = 'object'),
    CONSTRAINT ck_audit_logs_after_object
        CHECK (after_data IS NULL OR jsonb_typeof(after_data) = 'object')
);

CREATE INDEX IF NOT EXISTS ix_audit_logs_created_at
    ON platform.audit_logs (created_at DESC);
CREATE INDEX IF NOT EXISTS ix_audit_logs_actor_created_at
    ON platform.audit_logs (actor_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_audit_logs_resource
    ON platform.audit_logs (resource_type, resource_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_audit_logs_request_id
    ON platform.audit_logs (request_id);

CREATE TABLE IF NOT EXISTS platform.app_configs (
    key varchar(100) PRIMARY KEY,
    namespace varchar(50) NOT NULL,
    value jsonb NOT NULL,
    value_type varchar(16) NOT NULL,
    description varchar(500) NULL,
    editable boolean NOT NULL DEFAULT true,
    version integer NOT NULL DEFAULT 1,
    updated_by uuid NULL REFERENCES platform.users(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_app_configs_key
        CHECK (key ~ '^[a-z][a-z0-9_.-]{2,99}$'),
    CONSTRAINT ck_app_configs_value_type
        CHECK (value_type IN ('string', 'integer', 'number', 'boolean', 'json')),
    CONSTRAINT ck_app_configs_version CHECK (version >= 1)
);

CREATE INDEX IF NOT EXISTS ix_app_configs_namespace
    ON platform.app_configs (namespace, key);

CREATE TABLE IF NOT EXISTS platform.idempotency_records (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES platform.users(id) ON DELETE CASCADE,
    endpoint varchar(200) NOT NULL,
    idempotency_key varchar(128) NOT NULL,
    request_hash char(64) NOT NULL,
    response_status integer NULL,
    response_body jsonb NULL,
    resource_type varchar(100) NULL,
    resource_id varchar(100) NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    CONSTRAINT uq_idempotency_scope UNIQUE (user_id, endpoint, idempotency_key),
    CONSTRAINT ck_idempotency_expiry CHECK (expires_at > created_at),
    CONSTRAINT ck_idempotency_response_status
        CHECK (response_status IS NULL OR response_status BETWEEN 100 AND 599)
);

CREATE INDEX IF NOT EXISTS ix_idempotency_records_expiry
    ON platform.idempotency_records (expires_at);

DROP TRIGGER IF EXISTS trg_users_updated_at ON platform.users;
CREATE TRIGGER trg_users_updated_at
BEFORE UPDATE ON platform.users
FOR EACH ROW EXECUTE FUNCTION platform.set_updated_at();

DROP TRIGGER IF EXISTS trg_roles_updated_at ON platform.roles;
CREATE TRIGGER trg_roles_updated_at
BEFORE UPDATE ON platform.roles
FOR EACH ROW EXECUTE FUNCTION platform.set_updated_at();

DROP TRIGGER IF EXISTS trg_sensitive_words_updated_at ON platform.sensitive_words;
CREATE TRIGGER trg_sensitive_words_updated_at
BEFORE UPDATE ON platform.sensitive_words
FOR EACH ROW EXECUTE FUNCTION platform.set_updated_at();

DROP TRIGGER IF EXISTS trg_moderation_cases_updated_at ON platform.moderation_cases;
CREATE TRIGGER trg_moderation_cases_updated_at
BEFORE UPDATE ON platform.moderation_cases
FOR EACH ROW EXECUTE FUNCTION platform.set_updated_at();

DROP TRIGGER IF EXISTS trg_app_configs_updated_at ON platform.app_configs;
CREATE TRIGGER trg_app_configs_updated_at
BEFORE UPDATE ON platform.app_configs
FOR EACH ROW EXECUTE FUNCTION platform.set_updated_at();

COMMENT ON SCHEMA platform IS 'M4 平台治理与公共基础数据域';
COMMENT ON TABLE platform.users IS '演示用户；生产校园统一认证不在 MVP 范围';
COMMENT ON COLUMN platform.users.password_hash IS 'Argon2 哈希，禁止保存或记录明文';
COMMENT ON TABLE platform.refresh_tokens IS '只保存 Refresh Token SHA-256 哈希和轮换状态';
COMMENT ON TABLE platform.moderation_cases IS '跨模块审核案件；target_id 不设跨 Schema 外键';
COMMENT ON TABLE platform.audit_logs IS '脱敏、只读审计记录；应用层不提供删除接口';
COMMENT ON TABLE platform.app_configs IS '仅保存非密钥业务配置；API Key/JWT Secret 必须使用环境变量';

COMMIT;
