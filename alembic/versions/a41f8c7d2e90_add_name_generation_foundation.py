"""add provider-independent name generation foundation

Revision ID: a41f8c7d2e90
Revises: 6c9d4e7f2a10
Create Date: 2026-08-30 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a41f8c7d2e90"
down_revision: str | Sequence[str] | None = "6c9d4e7f2a10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUSES = "'pending', 'processing', 'succeeded', 'failed', 'skipped', 'abandoned'"
_RESULT_CODES = (
    "'generated', 'stale_schedule', 'manual_name', 'ineligible_schedule', "
    "'generation_disabled', 'price_unknown', 'budget_invalid', 'budget_exhausted', "
    "'timeout', 'invalid_response', 'generator_unavailable', 'generator_error', "
    "'stale_after_generation', 'startup_abandoned', 'shutdown_unknown'"
)


def upgrade() -> None:
    op.drop_constraint(op.f("ck_operation_logs_action_valid"), "operation_logs", type_="check")
    op.create_check_constraint(
        op.f("ck_operation_logs_action_valid"),
        "operation_logs",
        "action IN ('created', 'edited', 'deleted', 'paused', 'resumed', "
        "'completed', 'ended', 'failed', 'name_generated')",
    )
    op.create_table(
        "name_generation_jobs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("schedule_id", sa.BigInteger(), nullable=False),
        sa.Column("expected_schedule_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "reserved_cost_microunits", sa.BigInteger(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("result_code", sa.String(length=32)),
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
            "expected_schedule_version >= 1",
            name=op.f("ck_name_generation_jobs_expected_version_positive"),
        ),
        sa.CheckConstraint(
            "reserved_cost_microunits >= 0",
            name=op.f("ck_name_generation_jobs_reserved_cost_nonnegative"),
        ),
        sa.CheckConstraint(
            f"status IN ({_STATUSES})", name=op.f("ck_name_generation_jobs_status_valid")
        ),
        sa.CheckConstraint(
            f"result_code IS NULL OR result_code IN ({_RESULT_CODES})",
            name=op.f("ck_name_generation_jobs_result_code_valid"),
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND claimed_at IS NULL AND lease_expires_at IS NULL "
            "AND started_at IS NULL AND finished_at IS NULL AND result_code IS NULL) OR "
            "(status = 'processing' AND claimed_at IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND started_at IS NOT NULL AND finished_at IS NULL AND result_code IS NULL "
            "AND claimed_at <= started_at AND started_at < lease_expires_at) OR "
            "(status IN ('succeeded', 'failed', 'skipped', 'abandoned') "
            "AND finished_at IS NOT NULL AND result_code IS NOT NULL)",
            name=op.f("ck_name_generation_jobs_lifecycle_valid"),
        ),
        sa.CheckConstraint(
            "updated_at >= created_at", name=op.f("ck_name_generation_jobs_updated_after_created")
        ),
        sa.ForeignKeyConstraint(
            ["schedule_id"],
            ["schedules.id"],
            ondelete="RESTRICT",
            name=op.f("fk_name_generation_jobs_schedule_id_schedules"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_name_generation_jobs")),
        sa.UniqueConstraint(
            "schedule_id",
            "expected_schedule_version",
            name=op.f("uq_name_generation_jobs_schedule_id_expected_schedule_version"),
        ),
    )
    op.create_index(
        "ix_name_generation_jobs_pending_created",
        "name_generation_jobs",
        ["created_at", "id"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_name_generation_jobs_processing_lease",
        "name_generation_jobs",
        ["lease_expires_at"],
        postgresql_where=sa.text("status = 'processing'"),
    )
    op.create_index(
        "ix_name_generation_jobs_terminal_finished",
        "name_generation_jobs",
        ["status", "finished_at"],
    )
    op.create_index(
        "uq_name_generation_jobs_single_processing",
        "name_generation_jobs",
        [sa.text("(1)")],
        unique=True,
        postgresql_where=sa.text("status = 'processing'"),
    )

    op.create_table(
        "name_generation_budget_buckets",
        sa.Column("period_type", sa.String(length=16), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column(
            "reserved_request_count", sa.BigInteger(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "reserved_cost_microunits", sa.BigInteger(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
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
            "period_type IN ('daily', 'monthly')",
            name=op.f("ck_name_generation_budget_buckets_period_type_valid"),
        ),
        sa.CheckConstraint(
            "period_type <> 'monthly' OR period_start = date_trunc('month', period_start)::date",
            name=op.f("ck_name_generation_budget_buckets_monthly_period_starts_first"),
        ),
        sa.CheckConstraint(
            "reserved_request_count >= 0",
            name=op.f("ck_name_generation_budget_buckets_request_count_nonnegative"),
        ),
        sa.CheckConstraint(
            "reserved_cost_microunits >= 0",
            name=op.f("ck_name_generation_budget_buckets_reserved_cost_nonnegative"),
        ),
        sa.CheckConstraint(
            "version >= 1", name=op.f("ck_name_generation_budget_buckets_version_positive")
        ),
        sa.CheckConstraint(
            "updated_at >= created_at",
            name=op.f("ck_name_generation_budget_buckets_updated_after_created"),
        ),
        sa.PrimaryKeyConstraint(
            "period_type", "period_start", name=op.f("pk_name_generation_budget_buckets")
        ),
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM name_generation_jobs)
               OR EXISTS (SELECT 1 FROM name_generation_budget_buckets)
               OR EXISTS (SELECT 1 FROM operation_logs WHERE action = 'name_generated') THEN
                RAISE EXCEPTION 'cannot downgrade: name generation data exists';
            END IF;
        END
        $$
        """
    )
    op.drop_table("name_generation_budget_buckets")
    op.drop_index("uq_name_generation_jobs_single_processing", table_name="name_generation_jobs")
    op.drop_index("ix_name_generation_jobs_terminal_finished", table_name="name_generation_jobs")
    op.drop_index("ix_name_generation_jobs_processing_lease", table_name="name_generation_jobs")
    op.drop_index("ix_name_generation_jobs_pending_created", table_name="name_generation_jobs")
    op.drop_table("name_generation_jobs")
    op.drop_constraint(op.f("ck_operation_logs_action_valid"), "operation_logs", type_="check")
    op.create_check_constraint(
        op.f("ck_operation_logs_action_valid"),
        "operation_logs",
        "action IN ('created', 'edited', 'deleted', 'paused', 'resumed', "
        "'completed', 'ended', 'failed')",
    )
