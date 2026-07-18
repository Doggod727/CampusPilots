"""Widen agent_steps.status so 'awaiting_approval' (17 chars) fits.

Revision ID: 0009_agent_steps_status_length
Revises: 0008_ai_knowledge_schema
"""
import sqlalchemy as sa
from alembic import op

revision = "0009_agent_steps_status_length"
down_revision = "0008_ai_knowledge_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "agent_steps",
        "status",
        schema="agent_platform",
        existing_type=sa.String(16),
        type_=sa.String(24),
        existing_server_default=sa.text("'created'"),
        existing_nullable=False,
    )


def downgrade() -> None:
    # 先把超长值收敛回 16 字符可容纳的状态，再缩回 varchar(16)。
    op.execute(
        "UPDATE agent_platform.agent_steps SET status = 'running' "
        "WHERE status = 'awaiting_approval'"
    )
    op.alter_column(
        "agent_steps",
        "status",
        schema="agent_platform",
        existing_type=sa.String(24),
        type_=sa.String(16),
        existing_server_default=sa.text("'created'"),
        existing_nullable=False,
    )
