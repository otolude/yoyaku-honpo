"""SQLAlchemy models for the Phase 1 scheduling database."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    SmallInteger,
    String,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from discord_ai_reminder_bot.domain.enums import (
    ActorType,
    BudgetPeriodType,
    DeleteKind,
    DeliveryAttemptStatus,
    DeliveryErrorKind,
    DisplayNameSource,
    NameGenerationJobStatus,
    NameGenerationResultCode,
    NotificationAttemptStatus,
    NotificationErrorKind,
    NotificationRecipientType,
    NotificationStatus,
    NotificationType,
    OperationAction,
    RunStatus,
    ScheduleStatus,
    ScheduleType,
    enum_values,
)
from discord_ai_reminder_bot.infrastructure.database.base import Base

SCHEDULE_TYPES = enum_values(ScheduleType)
SCHEDULE_STATUSES = enum_values(ScheduleStatus)
DISPLAY_NAME_SOURCES = enum_values(DisplayNameSource)
NAME_GENERATION_JOB_STATUSES = enum_values(NameGenerationJobStatus)
NAME_GENERATION_RESULT_CODES = enum_values(NameGenerationResultCode)
BUDGET_PERIOD_TYPES = enum_values(BudgetPeriodType)
RUN_STATUSES = enum_values(RunStatus)
DELIVERY_ATTEMPT_STATUSES = enum_values(DeliveryAttemptStatus)
DELIVERY_ERROR_KINDS = enum_values(DeliveryErrorKind)
OPERATION_ACTIONS = enum_values(OperationAction)
ACTOR_TYPES = enum_values(ActorType)
DELETE_KINDS = enum_values(DeleteKind)
NOTIFICATION_TYPES = enum_values(NotificationType)
NOTIFICATION_RECIPIENT_TYPES = enum_values(NotificationRecipientType)
NOTIFICATION_STATUSES = enum_values(NotificationStatus)
NOTIFICATION_ATTEMPT_STATUSES = enum_values(NotificationAttemptStatus)
NOTIFICATION_ERROR_KINDS = enum_values(NotificationErrorKind)
DISPLAY_NAME_FORMAT_CHARACTERS_SQL = (
    r"U&'[\00AD\0600-\0605\061C\06DD\070F\0890-\0891\08E2\180E"
    r"\200B-\200F\202A-\202E\2060-\2064\2066-\206F\FEFF\FFF9-\FFFB"
    r"\+0110BD\+0110CD\+013430-\+01343F\+01BCA0-\+01BCA3"
    r"\+01D173-\+01D17A\+0E0001\+0E0020-\+0E007F]'"
)


def sql_values(values: tuple[str, ...]) -> str:
    """Render trusted constant values for CHECK constraint declarations."""
    return ", ".join(f"'{value}'" for value in values)


class Schedule(Base):
    """A complete one-time or recurring schedule."""

    __tablename__ = "schedules"
    __table_args__ = (
        UniqueConstraint("public_id"),
        CheckConstraint(
            f"schedule_type IN ({sql_values(SCHEDULE_TYPES)})",
            name="schedule_type_valid",
        ),
        CheckConstraint(
            f"status IN ({sql_values(SCHEDULE_STATUSES)})",
            name="status_valid",
        ),
        CheckConstraint(
            "content IS NULL OR char_length(content) BETWEEN 1 AND 2000",
            name="content_length_valid",
        ),
        CheckConstraint(
            f"display_name_source IN ({sql_values(DISPLAY_NAME_SOURCES)})",
            name="display_name_source_valid",
        ),
        CheckConstraint(
            "(display_name_source = 'unset' AND display_name IS NULL) OR "
            "(display_name_source IN ('ai', 'manual') "
            "AND display_name IS NOT NULL "
            "AND char_length(display_name) BETWEEN 1 AND 32 "
            "AND display_name = btrim(display_name) "
            "AND display_name !~ '[[:cntrl:]]' "
            f"AND display_name !~ {DISPLAY_NAME_FORMAT_CHARACTERS_SQL})",
            name="display_name_matches_source",
        ),
        CheckConstraint(
            "(status = 'draft' AND content IS NULL) "
            "OR status IN ('paused', 'deleted') "
            "OR (status IN ('active', 'failed', 'completed', 'ended') "
            "AND content IS NOT NULL)",
            name="content_matches_status",
        ),
        CheckConstraint(
            "(status IN ('draft', 'active') AND next_run_at IS NOT NULL) "
            "OR (status IN ('paused', 'failed', 'completed', 'ended', 'deleted') "
            "AND next_run_at IS NULL)",
            name="next_run_matches_status",
        ),
        CheckConstraint(
            "(schedule_type = 'once' AND local_time IS NULL AND weekday IS NULL) "
            "OR (schedule_type = 'daily' AND local_time IS NOT NULL AND weekday IS NULL) "
            "OR (schedule_type = 'weekly' AND local_time IS NOT NULL AND weekday IS NOT NULL)",
            name="recurrence_fields_valid",
        ),
        CheckConstraint("weekday IS NULL OR weekday BETWEEN 0 AND 6", name="weekday_valid"),
        CheckConstraint(
            "end_date IS NULL OR schedule_type IN ('daily', 'weekly')",
            name="end_date_recurring_only",
        ),
        CheckConstraint(
            "(status IN ('completed', 'failed') AND schedule_type = 'once') "
            "OR (status IN ('paused', 'ended') AND schedule_type IN ('daily', 'weekly')) "
            "OR status IN ('draft', 'active', 'deleted')",
            name="status_matches_schedule_type",
        ),
        CheckConstraint(
            "(status IN ('completed', 'ended', 'deleted') AND terminal_at IS NOT NULL) "
            "OR (status NOT IN ('completed', 'ended', 'deleted') AND terminal_at IS NULL)",
            name="terminal_at_matches_status",
        ),
        CheckConstraint(
            "(status = 'deleted' AND deleted_at IS NOT NULL) "
            "OR (status <> 'deleted' AND deleted_at IS NULL)",
            name="deleted_at_matches_status",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("guild_id > 0", name="guild_id_positive"),
        CheckConstraint("channel_id > 0", name="channel_id_positive"),
        CheckConstraint("creator_user_id > 0", name="creator_user_id_positive"),
        Index("ix_schedules_guild_status_next_run", "guild_id", "status", "next_run_at"),
        Index("ix_schedules_creator_status_next_run", "creator_user_id", "status", "next_run_at"),
        Index("ix_schedules_status_terminal_at", "status", "terminal_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), default=uuid.uuid7, nullable=False
    )
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    creator_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    schedule_type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(32), nullable=True)
    display_name_source: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        default=DisplayNameSource.UNSET.value,
        server_default=text("'unset'"),
    )
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    local_time: Mapped[time | None] = mapped_column(Time(timezone=False), nullable=True)
    weekday: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class NameGenerationJob(Base):
    """Non-identifying durable coordination record for one content version."""

    __tablename__ = "name_generation_jobs"
    __table_args__ = (
        UniqueConstraint("schedule_id", "expected_schedule_version"),
        CheckConstraint(
            f"status IN ({sql_values(NAME_GENERATION_JOB_STATUSES)})", name="status_valid"
        ),
        CheckConstraint(
            f"result_code IS NULL OR result_code IN ({sql_values(NAME_GENERATION_RESULT_CODES)})",
            name="result_code_valid",
        ),
        CheckConstraint("expected_schedule_version >= 1", name="expected_version_positive"),
        CheckConstraint("reserved_cost_microunits >= 0", name="reserved_cost_nonnegative"),
        CheckConstraint(
            "(status = 'pending' AND claimed_at IS NULL AND lease_expires_at IS NULL "
            "AND started_at IS NULL AND finished_at IS NULL AND result_code IS NULL) OR "
            "(status = 'processing' AND claimed_at IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND started_at IS NOT NULL AND finished_at IS NULL AND result_code IS NULL "
            "AND claimed_at <= started_at AND started_at < lease_expires_at) OR "
            "(status IN ('succeeded', 'failed', 'skipped', 'abandoned') "
            "AND finished_at IS NOT NULL AND result_code IS NOT NULL)",
            name="lifecycle_valid",
        ),
        CheckConstraint("updated_at >= created_at", name="updated_after_created"),
        Index(
            "ix_name_generation_jobs_pending_created",
            "created_at",
            "id",
            postgresql_where=text("status = 'pending'"),
        ),
        Index(
            "ix_name_generation_jobs_processing_lease",
            "lease_expires_at",
            postgresql_where=text("status = 'processing'"),
        ),
        Index("ix_name_generation_jobs_terminal_finished", "status", "finished_at"),
        Index(
            "uq_name_generation_jobs_single_processing",
            text("(1)"),
            unique=True,
            postgresql_where=text("status = 'processing'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    schedule_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("schedules.id", ondelete="RESTRICT"), nullable=False
    )
    expected_schedule_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    reserved_cost_microunits: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_code: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class NameGenerationBudgetBucket(Base):
    """Aggregate operator budget reservation without customer identifiers."""

    __tablename__ = "name_generation_budget_buckets"
    __table_args__ = (
        CheckConstraint(
            f"period_type IN ({sql_values(BUDGET_PERIOD_TYPES)})", name="period_type_valid"
        ),
        CheckConstraint(
            "period_type <> 'monthly' OR period_start = date_trunc('month', period_start)::date",
            name="monthly_period_starts_first",
        ),
        CheckConstraint("reserved_request_count >= 0", name="request_count_nonnegative"),
        CheckConstraint("reserved_cost_microunits >= 0", name="reserved_cost_nonnegative"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("updated_at >= created_at", name="updated_after_created"),
    )

    period_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    period_start: Mapped[date] = mapped_column(Date, primary_key=True)
    reserved_request_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    reserved_cost_microunits: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class PostDraftOperatorBudgetBucket(Base):
    """Content-free aggregate operator budget reservation for post drafts."""

    __tablename__ = "post_draft_operator_budget_buckets"
    __table_args__ = (
        CheckConstraint(
            "period_type IN ('daily', 'monthly')",
            name="period_type_valid",
        ),
        CheckConstraint(
            "period_type <> 'monthly' OR period_start = date_trunc('month', period_start)::date",
            name="monthly_start_first",
        ),
        CheckConstraint("reserved_request_count >= 0", name="request_count_nonnegative"),
        CheckConstraint("reserved_cost_microunits >= 0", name="reserved_cost_nonnegative"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("updated_at >= created_at", name="updated_after_created"),
    )

    period_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    period_start: Mapped[date] = mapped_column(Date, primary_key=True)
    reserved_request_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    reserved_cost_microunits: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class PostDraftRateLimitBucket(Base):
    """Content-free fixed-window usage count for one user or guild."""

    __tablename__ = "post_draft_rate_limit_buckets"
    __table_args__ = (
        CheckConstraint("scope_type IN ('user', 'guild')", name="scope_type_valid"),
        CheckConstraint("window_type IN ('short', 'daily')", name="window_type_valid"),
        CheckConstraint(
            "(scope_type = 'user' AND window_type = 'short') OR "
            "(scope_type = 'guild' AND window_type = 'daily')",
            name="scope_window_valid",
        ),
        CheckConstraint("scope_id > 0", name="scope_id_positive"),
        CheckConstraint("request_count >= 0", name="request_count_nonnegative"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("updated_at >= created_at", name="updated_after_created"),
        Index("ix_post_draft_rate_limit_buckets_window_start", "window_start"),
    )

    scope_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    scope_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    window_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    request_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class PostDraftUsageReservationReceipt(Base):
    """Opaque idempotency receipt without request payload or subject identifiers."""

    __tablename__ = "post_draft_usage_reservation_receipts"
    __table_args__ = (CheckConstraint("expires_at > reserved_at", name="expires_after_reserved"),)

    operation_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    reserved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ScheduleRun(Base):
    """The execution result for one scheduled occurrence."""

    __tablename__ = "schedule_runs"
    __table_args__ = (
        UniqueConstraint("schedule_id", "scheduled_for"),
        CheckConstraint(f"status IN ({sql_values(RUN_STATUSES)})", name="status_valid"),
        CheckConstraint("attempt_count BETWEEN 0 AND 4", name="attempt_count_valid"),
        CheckConstraint(
            "(status = 'pending' AND next_attempt_at IS NOT NULL) "
            "OR (status <> 'pending' AND next_attempt_at IS NULL)",
            name="next_attempt_matches_status",
        ),
        CheckConstraint(
            "(status IN ('succeeded', 'failed', 'skipped') AND finished_at IS NOT NULL) "
            "OR (status IN ('pending', 'processing') AND finished_at IS NULL)",
            name="finished_at_matches_status",
        ),
        CheckConstraint(
            "(status = 'succeeded' AND discord_message_id IS NOT NULL) "
            "OR (status <> 'succeeded' AND discord_message_id IS NULL)",
            name="message_id_matches_status",
        ),
        CheckConstraint(
            "discord_message_id IS NULL OR discord_message_id > 0",
            name="message_id_positive",
        ),
        Index(
            "ix_schedule_runs_pending_due",
            "next_attempt_at",
            "scheduled_for",
            postgresql_where=text("status = 'pending'"),
        ),
        Index(
            "ix_schedule_runs_processing_lease",
            "lease_expires_at",
            postgresql_where=text("status = 'processing'"),
        ),
        Index(
            "ix_schedule_runs_schedule_scheduled_desc", "schedule_id", text("scheduled_for DESC")
        ),
        Index("ix_schedule_runs_status_finished_at", "status", "finished_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    schedule_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("schedules.id", ondelete="RESTRICT"), nullable=False
    )
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    attempt_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default=text("0")
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claimed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    discord_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    result_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class DeliveryAttempt(Base):
    """One of at most four Discord delivery attempts for a schedule run."""

    __tablename__ = "delivery_attempts"
    __table_args__ = (
        UniqueConstraint("schedule_run_id", "attempt_number"),
        CheckConstraint(
            f"status IN ({sql_values(DELIVERY_ATTEMPT_STATUSES)})",
            name="status_valid",
        ),
        CheckConstraint("attempt_number BETWEEN 1 AND 4", name="attempt_number_valid"),
        CheckConstraint(
            f"error_kind IS NULL OR error_kind IN ({sql_values(DELIVERY_ERROR_KINDS)})",
            name="error_kind_valid",
        ),
        CheckConstraint(
            "(status IN ('sending', 'succeeded', 'unknown') AND send_started_at IS NOT NULL) "
            "OR (status = 'claimed' AND send_started_at IS NULL) "
            "OR status = 'failed'",
            name="send_started_at_matches_status",
        ),
        CheckConstraint(
            "(status IN ('succeeded', 'failed', 'unknown') AND finished_at IS NOT NULL) "
            "OR (status IN ('claimed', 'sending') AND finished_at IS NULL)",
            name="finished_at_matches_status",
        ),
        CheckConstraint(
            "(status = 'succeeded' AND discord_message_id IS NOT NULL) "
            "OR (status <> 'succeeded' AND discord_message_id IS NULL)",
            name="message_id_matches_status",
        ),
        CheckConstraint(
            "discord_message_id IS NULL OR discord_message_id > 0",
            name="message_id_positive",
        ),
        Index("ix_delivery_attempts_status_claimed_at", "status", "claimed_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    schedule_run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("schedule_runs.id", ondelete="RESTRICT"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    claimed_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    send_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    discord_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    error_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(String(500), nullable=True)


class OperationLog(Base):
    """Audit trail for user and system changes to a schedule."""

    __tablename__ = "operation_logs"
    __table_args__ = (
        CheckConstraint(f"action IN ({sql_values(OPERATION_ACTIONS)})", name="action_valid"),
        CheckConstraint(f"actor_type IN ({sql_values(ACTOR_TYPES)})", name="actor_type_valid"),
        CheckConstraint(
            f"delete_kind IS NULL OR delete_kind IN ({sql_values(DELETE_KINDS)})",
            name="delete_kind_valid",
        ),
        CheckConstraint(
            "(actor_type = 'user' AND actor_user_id IS NOT NULL) "
            "OR (actor_type = 'system' AND actor_user_id IS NULL)",
            name="actor_user_matches_type",
        ),
        CheckConstraint(
            "(action = 'deleted' AND delete_kind IS NOT NULL AND delete_reason IS NOT NULL "
            "AND char_length(delete_reason) BETWEEN 1 AND 500) "
            "OR (action <> 'deleted' AND delete_kind IS NULL AND delete_reason IS NULL)",
            name="delete_fields_match_action",
        ),
        CheckConstraint(
            "actor_user_id IS NULL OR actor_user_id > 0",
            name="actor_user_id_positive",
        ),
        Index("ix_operation_logs_schedule_created_desc", "schedule_id", text("created_at DESC")),
        Index("ix_operation_logs_actor_created_desc", "actor_user_id", text("created_at DESC")),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    schedule_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("schedules.id", ondelete="RESTRICT"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    delete_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    delete_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    changes: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class NotificationLog(Base):
    """Delivery result for draft and operator notifications."""

    __tablename__ = "notification_logs"
    __table_args__ = (
        UniqueConstraint("deduplication_key"),
        CheckConstraint(
            f"notification_type IN ({sql_values(NOTIFICATION_TYPES)})",
            name="notification_type_valid",
        ),
        CheckConstraint(
            f"recipient_type IN ({sql_values(NOTIFICATION_RECIPIENT_TYPES)})",
            name="recipient_type_valid",
        ),
        CheckConstraint(
            f"status IN ({sql_values(NOTIFICATION_STATUSES)})",
            name="status_valid",
        ),
        CheckConstraint(
            "(recipient_type = 'log' AND recipient_id IS NULL) "
            "OR (recipient_type <> 'log' AND recipient_id IS NOT NULL)",
            name="recipient_id_matches_type",
        ),
        CheckConstraint("recipient_id IS NULL OR recipient_id > 0", name="recipient_id_positive"),
        CheckConstraint(
            "(status = 'pending' AND next_attempt_at IS NOT NULL "
            "AND claimed_by IS NULL AND claimed_at IS NULL AND lease_expires_at IS NULL "
            "AND finished_at IS NULL) OR "
            "(status = 'processing' AND next_attempt_at IS NULL "
            "AND claimed_by IS NOT NULL AND claimed_at IS NOT NULL "
            "AND lease_expires_at IS NOT NULL AND finished_at IS NULL) OR "
            "(status IN ('succeeded', 'failed', 'unknown', 'cancelled') "
            "AND next_attempt_at IS NULL AND claimed_by IS NULL AND claimed_at IS NULL "
            "AND lease_expires_at IS NULL AND finished_at IS NOT NULL)",
            name="lifecycle_matches_status",
        ),
        CheckConstraint(
            "(status = 'succeeded' AND sent_at IS NOT NULL) OR "
            "(status <> 'succeeded' AND sent_at IS NULL)",
            name="sent_at_matches_status",
        ),
        CheckConstraint("attempt_count BETWEEN 0 AND 3", name="attempt_count_valid"),
        CheckConstraint(
            "claimed_at IS NULL OR lease_expires_at IS NULL OR claimed_at <= lease_expires_at",
            name="claim_time_order_valid",
        ),
        CheckConstraint(
            "started_at IS NULL OR started_at >= scheduled_at", name="started_after_scheduled"
        ),
        CheckConstraint(
            "finished_at IS NULL OR finished_at >= scheduled_at", name="finished_after_scheduled"
        ),
        CheckConstraint(
            "started_at IS NULL OR finished_at IS NULL OR started_at <= finished_at",
            name="start_finish_order_valid",
        ),
        CheckConstraint(
            "sent_at IS NULL OR (started_at IS NOT NULL AND sent_at >= started_at)",
            name="sent_after_started",
        ),
        Index("ix_notification_logs_status_created_at", "status", "created_at"),
        Index("ix_notification_logs_schedule_created_desc", "schedule_id", text("created_at DESC")),
        Index(
            "ix_notification_logs_pending_due",
            "next_attempt_at",
            "scheduled_at",
            "id",
            postgresql_where=text("status = 'pending'"),
        ),
        Index(
            "ix_notification_logs_processing_lease",
            "lease_expires_at",
            "id",
            postgresql_where=text("status = 'processing'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    schedule_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("schedules.id", ondelete="RESTRICT"), nullable=True
    )
    schedule_run_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("schedule_runs.id", ondelete="RESTRICT"), nullable=True
    )
    notification_type: Mapped[str] = mapped_column(String(32), nullable=False)
    recipient_type: Mapped[str] = mapped_column(String(32), nullable=False)
    recipient_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    deduplication_key: Mapped[str] = mapped_column(String(160), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default=text("0")
    )
    claimed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class NotificationAttempt(Base):
    """One physical attempt to deliver a notification route."""

    __tablename__ = "notification_attempts"
    __table_args__ = (
        UniqueConstraint("notification_log_id", "attempt_number"),
        CheckConstraint(
            f"status IN ({sql_values(NOTIFICATION_ATTEMPT_STATUSES)})", name="status_valid"
        ),
        CheckConstraint("attempt_number BETWEEN 1 AND 3", name="attempt_number_valid"),
        CheckConstraint(
            f"error_kind IS NULL OR error_kind IN ({sql_values(NOTIFICATION_ERROR_KINDS)})",
            name="error_kind_valid",
        ),
        CheckConstraint(
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
            name="fields_match_status",
        ),
        CheckConstraint(
            "discord_message_id IS NULL OR discord_message_id > 0", name="message_id_positive"
        ),
        CheckConstraint("claimed_at <= updated_at", name="claimed_before_updated"),
        CheckConstraint(
            "send_started_at IS NULL OR send_started_at >= claimed_at", name="send_after_claimed"
        ),
        CheckConstraint(
            "finished_at IS NULL OR finished_at >= claimed_at", name="finished_after_claimed"
        ),
        CheckConstraint(
            "send_started_at IS NULL OR finished_at IS NULL OR send_started_at <= finished_at",
            name="send_finish_order_valid",
        ),
        Index(
            "ix_notification_attempts_log_number",
            "notification_log_id",
            "attempt_number",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    notification_log_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("notification_logs.id", ondelete="RESTRICT"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    claimed_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    send_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    discord_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    error_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
