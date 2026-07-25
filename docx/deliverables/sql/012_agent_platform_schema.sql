BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE SCHEMA IF NOT EXISTS agent_platform;

CREATE OR REPLACE FUNCTION agent_platform.set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

CREATE TABLE IF NOT EXISTS agent_platform.agent_definitions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code varchar(50) NOT NULL UNIQUE,
    name varchar(100) NOT NULL,
    description varchar(500) NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_agent_code CHECK (code ~ '^[a-z][a-z0-9_]{2,49}$')
);

CREATE TABLE IF NOT EXISTS agent_platform.agent_versions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id uuid NOT NULL
        REFERENCES agent_platform.agent_definitions(id) ON DELETE CASCADE,
    version varchar(30) NOT NULL,
    system_prompt text NOT NULL,
    output_schema jsonb NOT NULL DEFAULT '{}'::jsonb,
    tool_allowlist jsonb NOT NULL DEFAULT '[]'::jsonb,
    status varchar(16) NOT NULL DEFAULT 'draft',
    created_by uuid NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (agent_id, version),
    CONSTRAINT ck_agent_version_status CHECK (status IN ('draft', 'active', 'inactive')),
    CONSTRAINT ck_agent_output_schema CHECK (jsonb_typeof(output_schema) = 'object'),
    CONSTRAINT ck_agent_tool_allowlist CHECK (jsonb_typeof(tool_allowlist) = 'array')
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_one_active_version
    ON agent_platform.agent_versions (agent_id) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS agent_platform.tool_definitions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name varchar(100) NOT NULL UNIQUE,
    module varchar(10) NOT NULL,
    description varchar(500) NOT NULL,
    risk_level varchar(2) NOT NULL,
    visibility varchar(20) NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_tool_name
        CHECK (name ~ '^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$'),
    CONSTRAINT ck_tool_module CHECK (module IN ('m1', 'm2', 'm3', 'm4', 'm5')),
    CONSTRAINT ck_tool_risk CHECK (risk_level IN ('r0', 'r1', 'r2', 'r3')),
    CONSTRAINT ck_tool_visibility
        CHECK (visibility IN ('agent', 'runtime_internal', 'mcp'))
);

CREATE TABLE IF NOT EXISTS agent_platform.tool_versions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tool_id uuid NOT NULL
        REFERENCES agent_platform.tool_definitions(id) ON DELETE CASCADE,
    version varchar(30) NOT NULL,
    input_schema jsonb NOT NULL,
    output_schema jsonb NOT NULL,
    required_permissions jsonb NOT NULL DEFAULT '[]'::jsonb,
    timeout_ms integer NOT NULL DEFAULT 10000,
    idempotent boolean NOT NULL,
    requires_approval boolean NOT NULL,
    implementation_ref varchar(200) NOT NULL,
    status varchar(16) NOT NULL DEFAULT 'draft',
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tool_id, version),
    CONSTRAINT ck_tool_version_input CHECK (jsonb_typeof(input_schema) = 'object'),
    CONSTRAINT ck_tool_version_output CHECK (jsonb_typeof(output_schema) = 'object'),
    CONSTRAINT ck_tool_version_permissions CHECK (jsonb_typeof(required_permissions) = 'array'),
    CONSTRAINT ck_tool_version_timeout CHECK (timeout_ms BETWEEN 100 AND 60000),
    CONSTRAINT ck_tool_version_status CHECK (status IN ('draft', 'active', 'inactive'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_tool_one_active_version
    ON agent_platform.tool_versions (tool_id) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS agent_platform.datasets (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name varchar(100) NOT NULL,
    purpose varchar(30) NOT NULL,
    description varchar(500) NULL,
    owner_user_id uuid NOT NULL,
    deleted_at timestamptz NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_dataset_purpose
        CHECK (purpose IN ('agent_router', 'instruction_tuning', 'rag_reranker', 'evaluation'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_dataset_active_name
    ON agent_platform.datasets (lower(name)) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS agent_platform.dataset_versions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id uuid NOT NULL
        REFERENCES agent_platform.datasets(id) ON DELETE CASCADE,
    version integer NOT NULL,
    artifact_key varchar(500) NOT NULL,
    artifact_sha256 char(64) NOT NULL,
    format varchar(16) NOT NULL,
    sample_count integer NOT NULL DEFAULT 0,
    split_config jsonb NOT NULL DEFAULT '{}'::jsonb,
    validation_status varchar(16) NOT NULL DEFAULT 'pending',
    validation_report jsonb NOT NULL DEFAULT '{}'::jsonb,
    contains_sensitive_data boolean NOT NULL DEFAULT false,
    frozen_at timestamptz NULL,
    created_by uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (dataset_id, version),
    CONSTRAINT ck_dataset_version_format CHECK (format IN ('jsonl', 'csv')),
    CONSTRAINT ck_dataset_version_count CHECK (sample_count >= 0),
    CONSTRAINT ck_dataset_version_split CHECK (jsonb_typeof(split_config) = 'object'),
    CONSTRAINT ck_dataset_version_report CHECK (jsonb_typeof(validation_report) = 'object'),
    CONSTRAINT ck_dataset_validation_status
        CHECK (validation_status IN ('pending', 'valid', 'invalid'))
);

CREATE TABLE IF NOT EXISTS agent_platform.training_jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_version_id uuid NOT NULL
        REFERENCES agent_platform.dataset_versions(id) ON DELETE RESTRICT,
    base_model varchar(200) NOT NULL,
    method varchar(16) NOT NULL,
    config jsonb NOT NULL,
    resource_limits jsonb NOT NULL DEFAULT '{}'::jsonb,
    status varchar(16) NOT NULL DEFAULT 'queued',
    progress smallint NOT NULL DEFAULT 0,
    artifact_key varchar(500) NULL,
    artifact_sha256 char(64) NULL,
    metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
    error_code varchar(100) NULL,
    error_message varchar(500) NULL,
    created_by uuid NOT NULL,
    started_at timestamptz NULL,
    finished_at timestamptz NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_training_method CHECK (method IN ('lora', 'qlora')),
    CONSTRAINT ck_training_config CHECK (jsonb_typeof(config) = 'object'),
    CONSTRAINT ck_training_resources CHECK (jsonb_typeof(resource_limits) = 'object'),
    CONSTRAINT ck_training_metrics CHECK (jsonb_typeof(metrics) = 'object'),
    CONSTRAINT ck_training_status
        CHECK (status IN ('queued', 'preparing', 'training', 'evaluating', 'succeeded', 'failed', 'cancelled')),
    CONSTRAINT ck_training_progress CHECK (progress BETWEEN 0 AND 100)
);

CREATE INDEX IF NOT EXISTS ix_training_jobs_queue
    ON agent_platform.training_jobs (status, created_at);

CREATE TABLE IF NOT EXISTS agent_platform.model_versions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name varchar(100) NOT NULL,
    purpose varchar(30) NOT NULL,
    provider varchar(20) NOT NULL,
    base_model varchar(200) NOT NULL,
    version varchar(50) NOT NULL,
    quantization varchar(30) NULL,
    artifact_key varchar(500) NULL,
    artifact_sha256 char(64) NULL,
    config jsonb NOT NULL DEFAULT '{}'::jsonb,
    metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
    status varchar(16) NOT NULL DEFAULT 'candidate',
    training_job_id uuid NULL
        REFERENCES agent_platform.training_jobs(id) ON DELETE SET NULL,
    created_by uuid NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    activated_at timestamptz NULL,
    UNIQUE (name, version),
    CONSTRAINT ck_model_purpose
        CHECK (purpose IN ('complex_generation', 'agent_router', 'rag_reranker', 'embedding')),
    CONSTRAINT ck_model_provider CHECK (provider IN ('deepseek', 'local', 'rule')),
    CONSTRAINT ck_model_config CHECK (jsonb_typeof(config) = 'object'),
    CONSTRAINT ck_model_metrics CHECK (jsonb_typeof(metrics) = 'object'),
    CONSTRAINT ck_model_status CHECK (status IN ('candidate', 'active', 'inactive', 'failed'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_model_one_active_purpose
    ON agent_platform.model_versions (purpose) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS agent_platform.evaluation_jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    target_type varchar(20) NOT NULL,
    target_id uuid NULL,
    dataset_version_id uuid NULL
        REFERENCES agent_platform.dataset_versions(id) ON DELETE RESTRICT,
    status varchar(16) NOT NULL DEFAULT 'queued',
    config jsonb NOT NULL DEFAULT '{}'::jsonb,
    summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    report_key varchar(500) NULL,
    error_code varchar(100) NULL,
    created_by uuid NOT NULL,
    started_at timestamptz NULL,
    finished_at timestamptz NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_evaluation_target
        CHECK (target_type IN ('agent', 'tool', 'model', 'rag', 'system')),
    CONSTRAINT ck_evaluation_status
        CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
    CONSTRAINT ck_evaluation_config CHECK (jsonb_typeof(config) = 'object'),
    CONSTRAINT ck_evaluation_summary CHECK (jsonb_typeof(summary) = 'object')
);

CREATE TABLE IF NOT EXISTS agent_platform.evaluation_metrics (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    evaluation_id uuid NOT NULL
        REFERENCES agent_platform.evaluation_jobs(id) ON DELETE CASCADE,
    name varchar(100) NOT NULL,
    value double precision NOT NULL,
    unit varchar(30) NULL,
    slice_name varchar(100) NOT NULL DEFAULT 'all',
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (evaluation_id, name, slice_name)
);

CREATE TABLE IF NOT EXISTS agent_platform.agent_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL,
    conversation_id uuid NULL,
    client_request_id varchar(64) NOT NULL,
    input_summary varchar(1000) NOT NULL,
    status varchar(24) NOT NULL DEFAULT 'created',
    route_decision jsonb NULL,
    model_name varchar(100) NULL,
    model_version_id uuid NULL
        REFERENCES agent_platform.model_versions(id) ON DELETE SET NULL,
    step_count smallint NOT NULL DEFAULT 0,
    specialist_count smallint NOT NULL DEFAULT 0,
    finish_reason varchar(50) NULL,
    error_code varchar(100) NULL,
    started_at timestamptz NULL,
    finished_at timestamptz NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, client_request_id),
    CONSTRAINT ck_agent_run_status
        CHECK (status IN ('created', 'routing', 'running', 'awaiting_approval', 'succeeded', 'partial', 'failed', 'cancelled')),
    CONSTRAINT ck_agent_run_route
        CHECK (route_decision IS NULL OR jsonb_typeof(route_decision) = 'object'),
    CONSTRAINT ck_agent_run_steps CHECK (step_count BETWEEN 0 AND 6),
    CONSTRAINT ck_agent_run_specialists CHECK (specialist_count BETWEEN 0 AND 3)
);

CREATE INDEX IF NOT EXISTS ix_agent_runs_user_created
    ON agent_platform.agent_runs (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_agent_runs_status_created
    ON agent_platform.agent_runs (status, created_at DESC);

CREATE TABLE IF NOT EXISTS agent_platform.agent_steps (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id uuid NOT NULL
        REFERENCES agent_platform.agent_runs(id) ON DELETE CASCADE,
    parent_step_id uuid NULL
        REFERENCES agent_platform.agent_steps(id) ON DELETE SET NULL,
    sequence_no smallint NOT NULL,
    agent_code varchar(50) NOT NULL,
    task_type varchar(50) NOT NULL,
    status varchar(16) NOT NULL DEFAULT 'created',
    input_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    output_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    signature_hash char(64) NULL,
    error_code varchar(100) NULL,
    started_at timestamptz NULL,
    finished_at timestamptz NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, sequence_no),
    CONSTRAINT ck_agent_step_sequence CHECK (sequence_no BETWEEN 1 AND 6),
    CONSTRAINT ck_agent_step_status
        CHECK (status IN ('created', 'running', 'awaiting_approval', 'succeeded', 'partial', 'failed', 'cancelled')),
    CONSTRAINT ck_agent_step_input CHECK (jsonb_typeof(input_summary) = 'object'),
    CONSTRAINT ck_agent_step_output CHECK (jsonb_typeof(output_summary) = 'object')
);

CREATE INDEX IF NOT EXISTS ix_agent_steps_run
    ON agent_platform.agent_steps (run_id, sequence_no);
CREATE INDEX IF NOT EXISTS ix_agent_steps_signature
    ON agent_platform.agent_steps (run_id, signature_hash)
    WHERE signature_hash IS NOT NULL;

CREATE TABLE IF NOT EXISTS agent_platform.tool_calls (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id uuid NOT NULL
        REFERENCES agent_platform.agent_runs(id) ON DELETE CASCADE,
    step_id uuid NOT NULL
        REFERENCES agent_platform.agent_steps(id) ON DELETE CASCADE,
    tool_name varchar(100) NOT NULL,
    tool_version varchar(30) NOT NULL,
    arguments_hash char(64) NOT NULL,
    arguments_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    status varchar(24) NOT NULL DEFAULT 'prepared',
    idempotency_key varchar(128) NULL,
    resource_type varchar(100) NULL,
    resource_id varchar(100) NULL,
    result_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    duration_ms integer NULL,
    error_code varchar(100) NULL,
    audit_id uuid NULL,
    started_at timestamptz NULL,
    finished_at timestamptz NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_tool_call_status
        CHECK (status IN ('prepared', 'awaiting_approval', 'authorized', 'running', 'succeeded', 'failed', 'rejected', 'expired')),
    CONSTRAINT ck_tool_call_arguments CHECK (jsonb_typeof(arguments_summary) = 'object'),
    CONSTRAINT ck_tool_call_result CHECK (jsonb_typeof(result_summary) = 'object'),
    CONSTRAINT ck_tool_call_duration CHECK (duration_ms IS NULL OR duration_ms >= 0)
);

CREATE INDEX IF NOT EXISTS ix_tool_calls_run_created
    ON agent_platform.tool_calls (run_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_tool_calls_idempotency
    ON agent_platform.tool_calls (run_id, tool_name, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS agent_platform.approval_requests (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id uuid NOT NULL
        REFERENCES agent_platform.agent_runs(id) ON DELETE CASCADE,
    tool_call_id uuid NOT NULL
        REFERENCES agent_platform.tool_calls(id) ON DELETE CASCADE,
    user_id uuid NOT NULL,
    action varchar(100) NOT NULL,
    display_summary varchar(1000) NOT NULL,
    arguments_hash char(64) NOT NULL,
    status varchar(16) NOT NULL DEFAULT 'pending',
    expires_at timestamptz NOT NULL,
    decided_by uuid NULL,
    decided_at timestamptz NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_approval_status
        CHECK (status IN ('pending', 'approved', 'rejected', 'expired', 'consumed')),
    CONSTRAINT ck_approval_expiry CHECK (expires_at > created_at),
    CONSTRAINT ck_approval_decision
        CHECK ((status = 'pending' AND decided_at IS NULL)
            OR (status <> 'pending' AND status = 'expired')
            OR (status IN ('approved', 'rejected', 'consumed')
                AND decided_by IS NOT NULL AND decided_at IS NOT NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_approval_one_pending_tool_call
    ON agent_platform.approval_requests (tool_call_id) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS ix_approval_user_pending
    ON agent_platform.approval_requests (user_id, expires_at)
    WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS agent_platform.agent_handoffs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id uuid NOT NULL
        REFERENCES agent_platform.agent_runs(id) ON DELETE CASCADE,
    from_agent varchar(50) NOT NULL,
    to_agent varchar(50) NOT NULL,
    task_id uuid NOT NULL,
    context_summary varchar(1000) NOT NULL,
    structured_context jsonb NOT NULL DEFAULT '{}'::jsonb,
    constraints jsonb NOT NULL DEFAULT '[]'::jsonb,
    artifact_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
    status varchar(16) NOT NULL DEFAULT 'created',
    error_code varchar(100) NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz NULL,
    CONSTRAINT ck_handoff_distinct_agents CHECK (from_agent <> to_agent),
    CONSTRAINT ck_handoff_context CHECK (jsonb_typeof(structured_context) = 'object'),
    CONSTRAINT ck_handoff_constraints CHECK (jsonb_typeof(constraints) = 'array'),
    CONSTRAINT ck_handoff_artifacts CHECK (jsonb_typeof(artifact_refs) = 'array'),
    CONSTRAINT ck_handoff_status
        CHECK (status IN ('created', 'accepted', 'running', 'succeeded', 'failed', 'cancelled'))
);

CREATE INDEX IF NOT EXISTS ix_handoffs_run_created
    ON agent_platform.agent_handoffs (run_id, created_at);

DROP TRIGGER IF EXISTS trg_agent_definitions_updated_at ON agent_platform.agent_definitions;
CREATE TRIGGER trg_agent_definitions_updated_at
BEFORE UPDATE ON agent_platform.agent_definitions
FOR EACH ROW EXECUTE FUNCTION agent_platform.set_updated_at();
DROP TRIGGER IF EXISTS trg_tool_definitions_updated_at ON agent_platform.tool_definitions;
CREATE TRIGGER trg_tool_definitions_updated_at
BEFORE UPDATE ON agent_platform.tool_definitions
FOR EACH ROW EXECUTE FUNCTION agent_platform.set_updated_at();
DROP TRIGGER IF EXISTS trg_datasets_updated_at ON agent_platform.datasets;
CREATE TRIGGER trg_datasets_updated_at
BEFORE UPDATE ON agent_platform.datasets
FOR EACH ROW EXECUTE FUNCTION agent_platform.set_updated_at();
DROP TRIGGER IF EXISTS trg_training_jobs_updated_at ON agent_platform.training_jobs;
CREATE TRIGGER trg_training_jobs_updated_at
BEFORE UPDATE ON agent_platform.training_jobs
FOR EACH ROW EXECUTE FUNCTION agent_platform.set_updated_at();
DROP TRIGGER IF EXISTS trg_agent_runs_updated_at ON agent_platform.agent_runs;
CREATE TRIGGER trg_agent_runs_updated_at
BEFORE UPDATE ON agent_platform.agent_runs
FOR EACH ROW EXECUTE FUNCTION agent_platform.set_updated_at();

COMMENT ON SCHEMA agent_platform IS 'M5 智能体、Tool、训练、模型与评估数据域';
COMMENT ON TABLE agent_platform.agent_runs IS '完整原文不入表；只保存脱敏输入摘要与结构化轨迹';
COMMENT ON TABLE agent_platform.approval_requests IS '写 Tool 的参数哈希和一次性人工确认';
COMMENT ON TABLE agent_platform.model_versions IS '复杂生成使用 DeepSeek；本地仅小模型、Embedding、Reranker';

COMMIT;
