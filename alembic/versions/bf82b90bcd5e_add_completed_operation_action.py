"""add completed operation action

Revision ID: bf82b90bcd5e
Revises: ffc99a7e1d4f
Create Date: 2026-08-17 23:16:25.414464
"""

from collections.abc import Sequence

from alembic import op

revision: str = "bf82b90bcd5e"
down_revision: str | Sequence[str] | None = "ffc99a7e1d4f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Allow system audit rows for completed one-time schedules."""
    op.drop_constraint(
        op.f("ck_operation_logs_action_valid"),
        "operation_logs",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_operation_logs_action_valid"),
        "operation_logs",
        "action IN ('created', 'edited', 'deleted', 'paused', 'resumed', "
        "'completed', 'ended', 'failed')",
    )


def downgrade() -> None:
    """Remove completed only when no completed audit rows would be invalidated."""
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM operation_logs
                WHERE action = 'completed'
            ) THEN
                RAISE EXCEPTION
                    'cannot downgrade: operation_logs contains completed actions';
            END IF;
        END
        $$
        """
    )
    op.drop_constraint(
        op.f("ck_operation_logs_action_valid"),
        "operation_logs",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_operation_logs_action_valid"),
        "operation_logs",
        "action IN ('created', 'edited', 'deleted', 'paused', 'resumed', 'ended', 'failed')",
    )
