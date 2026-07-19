"""Grant model engineers the existing Agent Run capabilities needed for debug mode.

Revision ID: 0010_unified_chat_permissions
Revises: 0009_agent_steps_status_length
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010_unified_chat_permissions"
down_revision: str | None = "0009_agent_steps_status_length"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO platform.role_permissions (role_id, permission_id)
        SELECT r.id, p.id
        FROM platform.roles r
        JOIN platform.permissions p
          ON p.code IN ('agent:run', 'agent:run:read_own')
        WHERE r.code = 'model_engineer'
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM platform.role_permissions rp
        USING platform.roles r, platform.permissions p
        WHERE rp.role_id = r.id
          AND rp.permission_id = p.id
          AND r.code = 'model_engineer'
          AND p.code IN ('agent:run', 'agent:run:read_own')
        """
    )
