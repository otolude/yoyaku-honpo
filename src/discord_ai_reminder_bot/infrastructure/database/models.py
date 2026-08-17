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
    DeleteKind,
    DeliveryAttemptStatus,
    DeliveryErrorKind,
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
RUN_STATUSES = enum_values(RunStatus)
DELIVERY_ATTEMPT_STATUSES = enum_values(DeliveryAttemptStatus)
DELIVERY_ERROR_KINDS = enum_values(DeliveryErrorKind)
OPERATION_ACTIONS = enum_values(OperationAction)
ACTOR_TYPES = enum_values(ActorType)
DELETE_KINDS = enum_values(DeleteKind)
NOTIFICATION_TYPES = enum_values(NotificationType)
NOTIFICATION_RECIPIENT_TYPES = enum_values(NotificationRecipientType)
NOTIFICATION_STATUSES = enum_values(NotificationStatus)


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
            "(status = 'succeeded' AND sent_at IS NOT NULL) "
            "OR (status <> 'succeeded' AND sent_at IS NULL)",
            name="sent_at_matches_status",
        ),
        Index("ix_notification_logs_status_created_at", "status", "created_at"),
        Index("ix_notification_logs_schedule_created_desc", "schedule_id", text("created_at DESC")),
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
