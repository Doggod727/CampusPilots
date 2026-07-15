"""Add durable runtime delivery records.

Revision ID: 0006_agent_runtime_delivery
Revises: 0005_agent_platform_schema
"""

from collections.abc import Iterator

from alembic import op

revision = "0006_agent_runtime_delivery"
down_revision = "0005_agent_platform_schema"
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
CREATE TABLE IF NOT EXISTS agent_platform.agent_runtime_commands (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id uuid NOT NULL REFERENCES agent_platform.agent_runs(id) ON DELETE CASCADE,
    action varchar(16) NOT NULL,
    approval_id uuid REFERENCES agent_platform.approval_requests(id) ON DELETE SET NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    status varchar(16) NOT NULL DEFAULT 'pending',
    attempt_count smallint NOT NULL DEFAULT 0,
    max_attempts smallint NOT NULL DEFAULT 3,
    available_at timestamptz NOT NULL DEFAULT now(),
    claimed_by varchar(100),
    claimed_at timestamptz,
    completed_at timestamptz,
    error_code varchar(100),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_runtime_command_action CHECK (action IN ('start', 'resume', 'cancel')),
    CONSTRAINT ck_runtime_command_approval CHECK ((action = 'resume' AND approval_id IS NOT NULL) OR (action <> 'resume' AND approval_id IS NULL)),
    CONSTRAINT ck_runtime_command_payload CHECK (jsonb_typeof(payload) = 'object'),
    CONSTRAINT ck_runtime_command_status CHECK (status IN ('pending', 'processing', 'succeeded', 'failed')),
    CONSTRAINT ck_runtime_command_attempts CHECK (attempt_count >= 0 AND max_attempts BETWEEN 1 AND 10 AND attempt_count <= max_attempts),
    CONSTRAINT ck_runtime_command_claim CHECK ((status = 'processing' AND claimed_by IS NOT NULL AND claimed_at IS NOT NULL) OR status <> 'processing'),
    CONSTRAINT ck_runtime_command_completion CHECK ((status IN ('succeeded', 'failed') AND completed_at IS NOT NULL) OR (status NOT IN ('succeeded', 'failed') AND completed_at IS NULL))
);
CREATE INDEX IF NOT EXISTS ix_runtime_commands_queue ON agent_platform.agent_runtime_commands(status, available_at, created_at);
CREATE INDEX IF NOT EXISTS ix_runtime_commands_run ON agent_platform.agent_runtime_commands(run_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_runtime_command_active_action
ON agent_platform.agent_runtime_commands(run_id, action, COALESCE(approval_id, '00000000-0000-0000-0000-000000000000'::uuid))
WHERE status IN ('pending', 'processing');

CREATE TABLE IF NOT EXISTS agent_platform.agent_runtime_checkpoints (
    run_id uuid PRIMARY KEY REFERENCES agent_platform.agent_runs(id) ON DELETE CASCADE,
    state_version integer NOT NULL,
    encrypted_state text NOT NULL,
    state_sha256 char(64) NOT NULL,
    expires_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_runtime_checkpoint_version CHECK (state_version > 0),
    CONSTRAINT ck_runtime_checkpoint_hash CHECK (state_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_runtime_checkpoint_expiry CHECK (expires_at > updated_at)
);
CREATE INDEX IF NOT EXISTS ix_runtime_checkpoints_expiry ON agent_platform.agent_runtime_checkpoints(expires_at);

CREATE TABLE IF NOT EXISTS agent_platform.agent_run_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id uuid NOT NULL REFERENCES agent_platform.agent_runs(id) ON DELETE CASCADE,
    sequence integer NOT NULL,
    event varchar(32) NOT NULL,
    data jsonb NOT NULL DEFAULT '{}'::jsonb,
    request_id varchar(64),
    occurred_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_agent_run_event_sequence UNIQUE (run_id, sequence),
    CONSTRAINT ck_agent_run_event_sequence CHECK (sequence > 0),
    CONSTRAINT ck_agent_run_event_type CHECK (event IN ('meta', 'route', 'agent_step', 'tool_call', 'approval_required', 'handoff', 'delta', 'sources', 'done', 'error')),
    CONSTRAINT ck_agent_run_event_data CHECK (jsonb_typeof(data) = 'object')
);
CREATE INDEX IF NOT EXISTS ix_agent_run_events_replay ON agent_platform.agent_run_events(run_id, sequence);

COMMENT ON TABLE agent_platform.agent_runtime_commands IS '事务 Outbox；仅保存安全命令元数据，不保存原始用户输入或 Tool 参数';
COMMENT ON TABLE agent_platform.agent_runtime_checkpoints IS '有 TTL 的认证加密运行状态；密钥仅来自环境变量';
COMMENT ON TABLE agent_platform.agent_run_events IS '面向 SSE 的脱敏、单调递增运行事件';
"""

DOWNGRADE_STATEMENTS = (
    "DROP TABLE agent_platform.agent_run_events",
    "DROP TABLE agent_platform.agent_runtime_checkpoints",
    "DROP TABLE agent_platform.agent_runtime_commands",
)


def _split_sql(script: str) -> Iterator[str]:
    start = 0
    in_quote = False
    for index, character in enumerate(script):
        if character == "'":
            in_quote = not in_quote
        elif character == ";" and not in_quote:
            statement = script[start:index].strip()
            if statement:
                yield statement
            start = index + 1
    trailing = script[start:].strip()
    if trailing:
        yield trailing


def upgrade() -> None:
    for statement in _split_sql(UPGRADE_SQL):
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
