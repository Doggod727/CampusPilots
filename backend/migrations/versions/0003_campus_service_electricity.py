"""Add the M2 mock electricity schema.

Revision ID: 0003_campus_service_electricity
Revises: 0002_campus_service_schema
"""

from collections.abc import Iterator, Sequence

from alembic import op

revision: str = "0003_campus_service_electricity"
down_revision: str | None = "0002_campus_service_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE_SQL = r"""
CREATE TABLE IF NOT EXISTS campus_service.electricity_accounts (
    room_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    campus_code varchar(30) NOT NULL
        REFERENCES campus_service.campuses(code) ON DELETE RESTRICT,
    dormitory_area varchar(100) NOT NULL,
    building varchar(50) NOT NULL,
    room varchar(30) NOT NULL,
    balance numeric(10,2) NOT NULL DEFAULT 0,
    currency char(3) NOT NULL DEFAULT 'CNY',
    source varchar(16) NOT NULL DEFAULT 'mock',
    is_simulated boolean NOT NULL DEFAULT true,
    source_updated_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_electricity_room
        UNIQUE (campus_code, dormitory_area, building, room),
    CONSTRAINT ck_electricity_balance CHECK (balance >= 0),
    CONSTRAINT ck_electricity_currency CHECK (currency = 'CNY'),
    CONSTRAINT ck_electricity_source CHECK (source IN ('mock', 'external')),
    CONSTRAINT ck_electricity_demo_source
        CHECK (source <> 'mock' OR is_simulated = true)
);

CREATE TABLE IF NOT EXISTS campus_service.electricity_account_members (
    room_id uuid NOT NULL
        REFERENCES campus_service.electricity_accounts(room_id) ON DELETE CASCADE,
    user_id uuid NOT NULL,
    member_role varchar(16) NOT NULL DEFAULT 'resident',
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (room_id, user_id),
    CONSTRAINT ck_electricity_member_role
        CHECK (member_role IN ('resident', 'manager'))
);

CREATE INDEX IF NOT EXISTS ix_electricity_members_user
    ON campus_service.electricity_account_members (user_id, room_id);

CREATE TABLE IF NOT EXISTS campus_service.electricity_topup_requests (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    room_id uuid NOT NULL
        REFERENCES campus_service.electricity_accounts(room_id) ON DELETE RESTRICT,
    requested_by uuid NOT NULL,
    amount numeric(10,2) NOT NULL,
    currency char(3) NOT NULL DEFAULT 'CNY',
    status varchar(16) NOT NULL DEFAULT 'simulated',
    is_simulated boolean NOT NULL DEFAULT true,
    agent_run_id uuid NULL,
    approval_id uuid NULL,
    idempotency_key varchar(128) NOT NULL,
    request_hash char(64) NOT NULL,
    notice varchar(300) NOT NULL DEFAULT '演示申请，不产生真实扣款或到账',
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_electricity_topup_idempotency
        UNIQUE (requested_by, idempotency_key),
    CONSTRAINT ck_electricity_topup_amount CHECK (amount BETWEEN 1.00 AND 500.00),
    CONSTRAINT ck_electricity_topup_currency CHECK (currency = 'CNY'),
    CONSTRAINT ck_electricity_topup_status CHECK (status = 'simulated'),
    CONSTRAINT ck_electricity_topup_simulated CHECK (is_simulated = true),
    CONSTRAINT ck_electricity_topup_agent_approval
        CHECK ((agent_run_id IS NULL AND approval_id IS NULL)
            OR (agent_run_id IS NOT NULL AND approval_id IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS ix_electricity_topup_user_created
    ON campus_service.electricity_topup_requests (requested_by, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_electricity_topup_room_created
    ON campus_service.electricity_topup_requests (room_id, created_at DESC);

DROP TRIGGER IF EXISTS trg_electricity_accounts_updated_at
    ON campus_service.electricity_accounts;
CREATE TRIGGER trg_electricity_accounts_updated_at
BEFORE UPDATE ON campus_service.electricity_accounts
FOR EACH ROW EXECUTE FUNCTION campus_service.set_updated_at();

COMMENT ON TABLE campus_service.electricity_accounts
    IS '电费演示账户；source=mock 时必须 is_simulated=true';
COMMENT ON TABLE campus_service.electricity_account_members
    IS 'user_id 为 platform.users 逻辑引用，不建立跨 Schema 外键';
COMMENT ON TABLE campus_service.electricity_topup_requests
    IS '模拟充值申请，不接入支付、不改变账户余额';
"""

DOWNGRADE_STATEMENTS = (
    "DROP TABLE campus_service.electricity_topup_requests",
    "DROP TABLE campus_service.electricity_account_members",
    "DROP TABLE campus_service.electricity_accounts",
)


def _split_sql(script: str) -> Iterator[str]:
    statement_start = 0
    index = 0
    in_single_quote = False
    while index < len(script):
        character = script[index]
        if in_single_quote:
            if character == "'":
                if index + 1 < len(script) and script[index + 1] == "'":
                    index += 2
                    continue
                in_single_quote = False
            index += 1
            continue
        if character == "'":
            in_single_quote = True
            index += 1
        elif character == ";":
            statement = script[statement_start:index].strip()
            if statement:
                yield statement
            statement_start = index + 1
            index += 1
        else:
            index += 1
    trailing = script[statement_start:].strip()
    if trailing:
        yield trailing


def upgrade() -> None:
    for statement in _split_sql(UPGRADE_SQL):
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
