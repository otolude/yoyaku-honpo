"""add post draft usage tables

Revision ID: c72e91f4b6a3
Revises: a41f8c7d2e90
Create Date: 2026-09-03 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c72e91f4b6a3"
down_revision: str | Sequence[str] | None = "a41f8c7d2e90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "post_draft_operator_budget_buckets",
        sa.Column("period_type", sa.String(length=16), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column(
            "reserved_request_count",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "reserved_cost_microunits",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
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
            name=op.f("ck_post_draft_operator_budget_buckets_period_type_valid"),
        ),
        sa.CheckConstraint(
            "period_type <> 'monthly' OR period_start = date_trunc('month', period_start)::date",
            name=op.f("ck_post_draft_operator_budget_buckets_monthly_period_starts_first"),
        ),
        sa.CheckConstraint(
            "reserved_request_count >= 0",
            name=op.f("ck_post_draft_operator_budget_buckets_request_count_nonnegative"),
        ),
        sa.CheckConstraint(
            "reserved_cost_microunits >= 0",
            name=op.f("ck_post_draft_operator_budget_buckets_reserved_cost_nonnegative"),
        ),
        sa.CheckConstraint(
            "version >= 1",
            name=op.f("ck_post_draft_operator_budget_buckets_version_positive"),
        ),
        sa.CheckConstraint(
            "updated_at >= created_at",
            name=op.f("ck_post_draft_operator_budget_buckets_updated_after_created"),
        ),
        sa.PrimaryKeyConstraint(
            "period_type",
            "period_start",
            name=op.f("pk_post_draft_operator_budget_buckets"),
        ),
    )
    op.create_table(
        "post_draft_rate_limit_buckets",
        sa.Column("scope_type", sa.String(length=16), nullable=False),
        sa.Column("scope_id", sa.BigInteger(), nullable=False),
        sa.Column("window_type", sa.String(length=16), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_count", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
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
            "scope_type IN ('user', 'guild')",
            name=op.f("ck_post_draft_rate_limit_buckets_scope_type_valid"),
        ),
        sa.CheckConstraint(
            "window_type IN ('short', 'daily')",
            name=op.f("ck_post_draft_rate_limit_buckets_window_type_valid"),
        ),
        sa.CheckConstraint(
            "(scope_type = 'user' AND window_type = 'short') OR "
            "(scope_type = 'guild' AND window_type = 'daily')",
            name=op.f("ck_post_draft_rate_limit_buckets_scope_window_valid"),
        ),
        sa.CheckConstraint(
            "scope_id > 0",
            name=op.f("ck_post_draft_rate_limit_buckets_scope_id_positive"),
        ),
        sa.CheckConstraint(
            "request_count >= 0",
            name=op.f("ck_post_draft_rate_limit_buckets_request_count_nonnegative"),
        ),
        sa.CheckConstraint(
            "version >= 1",
            name=op.f("ck_post_draft_rate_limit_buckets_version_positive"),
        ),
        sa.CheckConstraint(
            "updated_at >= created_at",
            name=op.f("ck_post_draft_rate_limit_buckets_updated_after_created"),
        ),
        sa.PrimaryKeyConstraint(
            "scope_type",
            "scope_id",
            "window_type",
            "window_start",
            name=op.f("pk_post_draft_rate_limit_buckets"),
        ),
    )
    op.create_index(
        op.f("ix_post_draft_rate_limit_buckets_window_start"),
        "post_draft_rate_limit_buckets",
        ["window_start"],
    )
    op.create_table(
        "post_draft_usage_reservation_receipts",
        sa.Column("operation_key", sa.UUID(), nullable=False),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "expires_at > reserved_at",
            name=op.f("ck_post_draft_usage_reservation_receipts_expires_after_reserved"),
        ),
        sa.PrimaryKeyConstraint(
            "operation_key",
            name=op.f("pk_post_draft_usage_reservation_receipts"),
        ),
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM post_draft_operator_budget_buckets)
               OR EXISTS (SELECT 1 FROM post_draft_rate_limit_buckets)
               OR EXISTS (SELECT 1 FROM post_draft_usage_reservation_receipts) THEN
                RAISE EXCEPTION 'cannot downgrade: post draft usage data exists';
            END IF;
        END
        $$
        """
    )
    op.drop_table("post_draft_usage_reservation_receipts")
    op.drop_index(
        op.f("ix_post_draft_rate_limit_buckets_window_start"),
        table_name="post_draft_rate_limit_buckets",
    )
    op.drop_table("post_draft_rate_limit_buckets")
    op.drop_table("post_draft_operator_budget_buckets")
