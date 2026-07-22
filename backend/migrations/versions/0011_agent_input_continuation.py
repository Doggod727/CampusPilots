"""Add recoverable user-input continuation to Agent Runs.

Revision ID: 0011_agent_input_continuation
Revises: 0010_unified_chat_permissions
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011_agent_input_continuation"
down_revision: str | None = "0010_unified_chat_permissions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_agent_run_status", "agent_runs", schema="agent_platform", type_="check")
    op.create_check_constraint(
        "ck_agent_run_status", "agent_runs",
        "status IN ('created', 'routing', 'running', 'awaiting_input', 'awaiting_approval', 'succeeded', 'partial', 'failed', 'cancelled')",
        schema="agent_platform",
    )
    op.drop_constraint("ck_runtime_command_action", "agent_runtime_commands", schema="agent_platform", type_="check")
    op.create_check_constraint(
        "ck_runtime_command_action", "agent_runtime_commands",
        "action IN ('start', 'resume', 'input', 'cancel')", schema="agent_platform",
    )
    op.drop_constraint("ck_agent_run_event_type", "agent_run_events", schema="agent_platform", type_="check")
    op.create_check_constraint(
        "ck_agent_run_event_type", "agent_run_events",
        "event IN ('meta', 'route', 'agent_step', 'tool_call', 'approval_required', 'input_required', 'handoff', 'delta', 'sources', 'done', 'error')",
        schema="agent_platform",
    )


def downgrade() -> None:
    op.execute("UPDATE agent_platform.agent_runs SET status = 'partial', finish_reason = 'clarification_required', finished_at = now() WHERE status = 'awaiting_input'")
    op.execute("DELETE FROM agent_platform.agent_runtime_commands WHERE action = 'input'")
    op.execute("DELETE FROM agent_platform.agent_run_events WHERE event = 'input_required'")
    op.drop_constraint("ck_agent_run_event_type", "agent_run_events", schema="agent_platform", type_="check")
    op.create_check_constraint("ck_agent_run_event_type", "agent_run_events", "event IN ('meta', 'route', 'agent_step', 'tool_call', 'approval_required', 'handoff', 'delta', 'sources', 'done', 'error')", schema="agent_platform")
    op.drop_constraint("ck_runtime_command_action", "agent_runtime_commands", schema="agent_platform", type_="check")
    op.create_check_constraint("ck_runtime_command_action", "agent_runtime_commands", "action IN ('start', 'resume', 'cancel')", schema="agent_platform")
    op.drop_constraint("ck_agent_run_status", "agent_runs", schema="agent_platform", type_="check")
    op.create_check_constraint("ck_agent_run_status", "agent_runs", "status IN ('created', 'routing', 'running', 'awaiting_approval', 'succeeded', 'partial', 'failed', 'cancelled')", schema="agent_platform")
