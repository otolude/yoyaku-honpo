"""add notification outbox lifecycle

Revision ID: 8e5b2f1c4a90
Revises: bf82b90bcd5e
Create Date: 2026-08-19 23:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "8e5b2f1c4a90"
down_revision: str | Sequence[str] | None = "bf82b90bcd5e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Backfill existing result rows, then add safe outbox lifecycle state."""
    op.add_column("notification_logs", sa.Column("scheduled_at", sa.DateTime(timezone=True)))
    op.add_column("notification_logs", sa.Column("next_attempt_at", sa.DateTime(timezone=True)))
    op.add_column(
        "notification_logs",
        sa.Column("attempt_count", sa.SmallInteger(), server_default=sa.text("0")),
    )
    op.add_column("notification_logs", sa.Column("claimed_by", sa.UUID()))
    op.add_column("notification_logs", sa.Column("claimed_at", sa.DateTime(timezone=True)))
    op.add_column("notification_logs", sa.Column("lease_expires_at", sa.DateTime(timezone=True)))
    op.add_column("notification_logs", sa.Column("started_at", sa.DateTime(timezone=True)))
    op.add_column("notification_logs", sa.Column("finished_at", sa.DateTime(timezone=True)))
    op.execute(
        """
        UPDATE notification_logs
        SET scheduled_at = created_at,
            next_attempt_at = CASE WHEN status = 'pending' THEN created_at ELSE NULL END,
            attempt_count = 0,
            started_at = CASE WHEN status = 'succeeded' THEN sent_at ELSE NULL END,
            finished_at = CASE
                WHEN status = 'succeeded' THEN sent_at
                WHEN status = 'failed' THEN created_at
                ELSE NULL
            END
        """
    )
    op.alter_column("notification_logs", "scheduled_at", nullable=False)
    op.alter_column("notification_logs", "attempt_count", nullable=False)

    op.drop_constraint(
        op.f("ck_notification_logs_status_valid"), "notification_logs", type_="check"
    )
    op.drop_constraint(
        op.f("ck_notification_logs_sent_at_matches_status"), "notification_logs", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_notification_logs_status_valid"),
        "notification_logs",
        "status IN ('pending', 'processing', 'succeeded', 'failed', 'unknown', 'cancelled')",
    )
    op.create_check_constraint(
        op.f("ck_notification_logs_lifecycle_matches_status"),
        "notification_logs",
        "(status = 'pending' AND next_attempt_at IS NOT NULL "
        "AND claimed_by IS NULL AND claimed_at IS NULL AND lease_expires_at IS NULL "
        "AND finished_at IS NULL) OR "
        "(status = 'processing' AND next_attempt_at IS NULL "
        "AND claimed_by IS NOT NULL AND claimed_at IS NOT NULL "
        "AND lease_expires_at IS NOT NULL AND finished_at IS NULL) OR "
        "(status IN ('succeeded', 'failed', 'unknown', 'cancelled') "
        "AND next_attempt_at IS NULL AND claimed_by IS NULL AND claimed_at IS NULL "
        "AND lease_expires_at IS NULL AND finished_at IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f("ck_notification_logs_sent_at_matches_status"),
        "notification_logs",
        "(status = 'succeeded' AND sent_at IS NOT NULL) OR "
        "(status <> 'succeeded' AND sent_at IS NULL)",
    )
    op.create_check_constraint(
        op.f("ck_notification_logs_attempt_count_valid"),
        "notification_logs",
        "attempt_count BETWEEN 0 AND 3",
    )
    op.create_check_constraint(
        op.f("ck_notification_logs_claim_time_order_valid"),
        "notification_logs",
        "claimed_at IS NULL OR lease_expires_at IS NULL OR claimed_at <= lease_expires_at",
    )
    op.create_check_constraint(
        op.f("ck_notification_logs_started_after_scheduled"),
        "notification_logs",
        "started_at IS NULL OR started_at >= scheduled_at",
    )
    op.create_check_constraint(
        op.f("ck_notification_logs_finished_after_scheduled"),
        "notification_logs",
        "finished_at IS NULL OR finished_at >= scheduled_at",
    )
    op.create_check_constraint(
        op.f("ck_notification_logs_start_finish_order_valid"),
        "notification_logs",
        "started_at IS NULL OR finished_at IS NULL OR started_at <= finished_at",
    )
    op.create_check_constraint(
        op.f("ck_notification_logs_sent_after_started"),
        "notification_logs",
        "sent_at IS NULL OR (started_at IS NOT NULL AND sent_at >= started_at)",
    )
    op.create_index(
        "ix_notification_logs_pending_due",
        "notification_logs",
        ["next_attempt_at", "scheduled_at", "id"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_notification_logs_processing_lease",
        "notification_logs",
        ["lease_expires_at", "id"],
        postgresql_where=sa.text("status = 'processing'"),
    )

    op.create_table(
        "notification_attempts",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("notification_log_id", sa.BigInteger(), nullable=False),
        sa.Column("attempt_number", sa.SmallInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("claimed_by", sa.UUID(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("send_started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("discord_message_id", sa.BigInteger()),
        sa.Column("error_kind", sa.String(length=32)),
        sa.Column("error_code", sa.String(length=64)),
        sa.Column("error_summary", sa.String(length=500)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('claimed', 'sending', 'succeeded', 'failed', 'unknown')",
            name=op.f("ck_notification_attempts_status_valid"),
        ),
        sa.CheckConstraint(
            "attempt_number BETWEEN 1 AND 3",
            name=op.f("ck_notification_attempts_attempt_number_valid"),
        ),
        sa.CheckConstraint(
            "error_kind IS NULL OR error_kind IN "
            "('transient', 'permanent', 'rate_limited', 'unknown')",
            name=op.f("ck_notification_attempts_error_kind_valid"),
        ),
        sa.CheckConstraint(
            "(status = 'claimed' AND send_started_at IS NULL AND finished_at IS NULL "
            "AND discord_message_id IS NULL AND error_kind IS NULL AND error_code IS NULL "
            "AND error_summary IS NULL) OR "
            "(status = 'sending' AND send_started_at IS NOT NULL AND finished_at IS NULL "
            "AND discord_message_id IS NULL AND error_kind IS NULL AND error_code IS NULL "
            "AND error_summary IS NULL) OR "
            "(status = 'succeeded' AND send_started_at IS NOT NULL AND finished_at IS NOT NULL "
            "AND discord_message_id IS NOT NULL AND error_kind IS NULL AND error_code IS NULL "
            "AND error_summary IS NULL) OR "
            "(status = 'failed' AND finished_at IS NOT NULL AND discord_message_id IS NULL "
            "AND error_kind IS NOT NULL AND error_code IS NOT NULL AND error_summary IS NOT NULL) OR "
            "(status = 'unknown' AND send_started_at IS NOT NULL AND finished_at IS NOT NULL "
            "AND discord_message_id IS NULL AND error_kind = 'unknown' "
            "AND error_code IS NOT NULL AND error_summary IS NOT NULL)",
            name=op.f("ck_notification_attempts_fields_match_status"),
        ),
        sa.CheckConstraint(
            "discord_message_id IS NULL OR discord_message_id > 0",
            name=op.f("ck_notification_attempts_message_id_positive"),
        ),
        sa.CheckConstraint(
            "claimed_at <= updated_at", name=op.f("ck_notification_attempts_claimed_before_updated")
        ),
        sa.CheckConstraint(
            "send_started_at IS NULL OR send_started_at >= claimed_at",
            name=op.f("ck_notification_attempts_send_after_claimed"),
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR finished_at >= claimed_at",
            name=op.f("ck_notification_attempts_finished_after_claimed"),
        ),
        sa.CheckConstraint(
            "send_started_at IS NULL OR finished_at IS NULL OR send_started_at <= finished_at",
            name=op.f("ck_notification_attempts_send_finish_order_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["notification_log_id"],
            ["notification_logs.id"],
            name=op.f("fk_notification_attempts_notification_log_id_notification_logs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_attempts")),
        sa.UniqueConstraint(
            "notification_log_id",
            "attempt_number",
            name=op.f("uq_notification_attempts_notification_log_id_attempt_number"),
        ),
    )
    op.create_index(
        "ix_notification_attempts_log_number",
        "notification_attempts",
        ["notification_log_id", "attempt_number"],
    )


def downgrade() -> None:
    """Refuse to discard notification attempts or non-legacy outbox state."""
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM notification_attempts) OR EXISTS (
                SELECT 1 FROM notification_logs
                WHERE status IN ('processing', 'unknown', 'cancelled')
                   OR attempt_count <> 0
                   OR claimed_by IS NOT NULL OR claimed_at IS NOT NULL
                   OR lease_expires_at IS NOT NULL
                   OR (status = 'succeeded' AND started_at <> sent_at)
                   OR (status <> 'succeeded' AND started_at IS NOT NULL)
                   OR scheduled_at <> created_at
                   OR (status = 'pending' AND next_attempt_at <> scheduled_at)
                   OR (status = 'succeeded' AND finished_at <> sent_at)
                   OR (status = 'failed' AND finished_at <> created_at)
            ) THEN
                RAISE EXCEPTION
                    'cannot downgrade: notification outbox contains non-legacy lifecycle data';
            END IF;
        END
        $$
        """
    )
    op.drop_index("ix_notification_attempts_log_number", table_name="notification_attempts")
    op.drop_table("notification_attempts")
    op.drop_index("ix_notification_logs_processing_lease", table_name="notification_logs")
    op.drop_index("ix_notification_logs_pending_due", table_name="notification_logs")
    for name in (
        "sent_after_started",
        "start_finish_order_valid",
        "finished_after_scheduled",
        "started_after_scheduled",
        "claim_time_order_valid",
        "attempt_count_valid",
        "lifecycle_matches_status",
    ):
        op.drop_constraint(op.f(f"ck_notification_logs_{name}"), "notification_logs", type_="check")
    op.drop_constraint(
        op.f("ck_notification_logs_sent_at_matches_status"), "notification_logs", type_="check"
    )
    op.drop_constraint(
        op.f("ck_notification_logs_status_valid"), "notification_logs", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_notification_logs_status_valid"),
        "notification_logs",
        "status IN ('pending', 'succeeded', 'failed')",
    )
    op.create_check_constraint(
        op.f("ck_notification_logs_sent_at_matches_status"),
        "notification_logs",
        "(status = 'succeeded' AND sent_at IS NOT NULL) OR "
        "(status <> 'succeeded' AND sent_at IS NULL)",
    )
    for column in (
        "finished_at",
        "started_at",
        "lease_expires_at",
        "claimed_at",
        "claimed_by",
        "attempt_count",
        "next_attempt_at",
        "scheduled_at",
    ):
        op.drop_column("notification_logs", column)
