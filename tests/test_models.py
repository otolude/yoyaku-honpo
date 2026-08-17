from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import BigInteger, CheckConstraint, Identity, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from discord_ai_reminder_bot.infrastructure.database.base import Base
from discord_ai_reminder_bot.infrastructure.database.models import (
    DELIVERY_ATTEMPT_STATUSES,
    NOTIFICATION_STATUSES,
    RUN_STATUSES,
    SCHEDULE_STATUSES,
    DeliveryAttempt,
    NotificationLog,
    OperationLog,
    Schedule,
    ScheduleRun,
)

EXPECTED_TABLES = {
    "schedules",
    "schedule_runs",
    "delivery_attempts",
    "operation_logs",
    "notification_logs",
}


def constraint_names(table_name: str, constraint_type: type[object]) -> set[str]:
    table = Base.metadata.tables[table_name]
    return {
        str(constraint.name)
        for constraint in table.constraints
        if isinstance(constraint, constraint_type)
    }


def index_names(table_name: str) -> set[str]:
    return {str(index.name) for index in Base.metadata.tables[table_name].indexes}


def test_metadata_contains_exactly_phase_one_tables() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_internal_primary_keys_are_bigint_identity() -> None:
    for table in Base.metadata.sorted_tables:
        primary_key = table.c.id
        assert isinstance(primary_key.type, BigInteger)
        assert isinstance(primary_key.server_default, Identity)
        assert primary_key.primary_key is True


def test_schedule_public_id_uses_uuid7_and_is_unique() -> None:
    column = Schedule.__table__.c.public_id
    generated = column.default.arg(None)

    assert isinstance(column.type, UUID)
    assert column.nullable is False
    assert isinstance(generated, uuid.UUID)
    assert generated.version == 7
    assert "uq_schedules_public_id" in constraint_names("schedules", UniqueConstraint)


def test_foreign_keys_use_internal_bigint_and_restrict_deletion() -> None:
    expected = {
        ("schedule_runs", "schedule_id", "schedules.id"),
        ("delivery_attempts", "schedule_run_id", "schedule_runs.id"),
        ("operation_logs", "schedule_id", "schedules.id"),
        ("notification_logs", "schedule_id", "schedules.id"),
        ("notification_logs", "schedule_run_id", "schedule_runs.id"),
    }

    actual: set[tuple[str, str, str]] = set()
    for table in Base.metadata.tables.values():
        for foreign_key in table.foreign_keys:
            assert isinstance(foreign_key.parent.type, BigInteger)
            assert foreign_key.ondelete == "RESTRICT"
            actual.add((table.name, foreign_key.parent.name, foreign_key.target_fullname))

    assert actual == expected


def test_timestamps_use_timezone_aware_columns() -> None:
    timestamp_columns = {
        "schedules": ("next_run_at", "created_at", "updated_at", "deleted_at", "terminal_at"),
        "schedule_runs": (
            "scheduled_for",
            "next_attempt_at",
            "claimed_at",
            "lease_expires_at",
            "started_at",
            "finished_at",
            "created_at",
            "updated_at",
        ),
        "delivery_attempts": ("claimed_at", "send_started_at", "finished_at"),
        "operation_logs": ("created_at",),
        "notification_logs": ("created_at", "sent_at"),
    }

    for table_name, columns in timestamp_columns.items():
        table = Base.metadata.tables[table_name]
        for column_name in columns:
            assert table.c[column_name].type.timezone is True


def test_schedule_nullability_and_lengths_match_design() -> None:
    table = Schedule.__table__

    assert table.c.content.nullable is True
    assert table.c.content.type.length == 2000
    assert table.c.next_run_at.nullable is True
    assert table.c.local_time.nullable is True
    assert table.c.weekday.nullable is True
    assert table.c.end_date.nullable is True
    assert table.c.guild_id.nullable is False
    assert table.c.channel_id.nullable is False
    assert table.c.creator_user_id.nullable is False


def test_state_constants_match_technical_design() -> None:
    assert SCHEDULE_STATUSES == (
        "draft",
        "active",
        "paused",
        "failed",
        "completed",
        "ended",
        "deleted",
    )
    assert RUN_STATUSES == ("pending", "processing", "succeeded", "failed", "skipped")
    assert DELIVERY_ATTEMPT_STATUSES == (
        "claimed",
        "sending",
        "succeeded",
        "failed",
        "unknown",
    )
    assert NOTIFICATION_STATUSES == ("pending", "succeeded", "failed")


def test_major_check_constraints_exist() -> None:
    assert constraint_names("schedules", CheckConstraint) >= {
        "ck_schedules_content_matches_status",
        "ck_schedules_next_run_matches_status",
        "ck_schedules_recurrence_fields_valid",
        "ck_schedules_status_matches_schedule_type",
        "ck_schedules_terminal_at_matches_status",
    }
    assert constraint_names("schedule_runs", CheckConstraint) >= {
        "ck_schedule_runs_attempt_count_valid",
        "ck_schedule_runs_finished_at_matches_status",
    }
    assert constraint_names("delivery_attempts", CheckConstraint) >= {
        "ck_delivery_attempts_attempt_number_valid",
        "ck_delivery_attempts_send_started_at_matches_status",
    }
    assert constraint_names("operation_logs", CheckConstraint) >= {
        "ck_operation_logs_delete_fields_match_action",
    }
    assert constraint_names("notification_logs", CheckConstraint) >= {
        "ck_notification_logs_recipient_id_matches_type",
    }


def test_content_check_matches_schedule_status_rules() -> None:
    constraint = next(
        constraint
        for constraint in Schedule.__table__.constraints
        if constraint.name == "ck_schedules_content_matches_status"
    )
    sql = str(constraint.sqltext)

    assert "status = 'draft' AND content IS NULL" in sql
    assert "status IN ('paused', 'deleted')" in sql
    assert "status IN ('active', 'failed', 'completed', 'ended')" in sql
    assert "AND content IS NOT NULL" in sql


def test_required_indexes_exist() -> None:
    assert index_names("schedules") == {
        "ix_schedules_guild_status_next_run",
        "ix_schedules_creator_status_next_run",
        "ix_schedules_status_terminal_at",
    }
    assert index_names("schedule_runs") == {
        "ix_schedule_runs_pending_due",
        "ix_schedule_runs_processing_lease",
        "ix_schedule_runs_schedule_scheduled_desc",
        "ix_schedule_runs_status_finished_at",
    }
    assert index_names("delivery_attempts") == {"ix_delivery_attempts_status_claimed_at"}
    assert index_names("operation_logs") == {
        "ix_operation_logs_schedule_created_desc",
        "ix_operation_logs_actor_created_desc",
    }
    assert index_names("notification_logs") == {
        "ix_notification_logs_status_created_at",
        "ix_notification_logs_schedule_created_desc",
    }


def test_models_can_be_constructed_without_database_io() -> None:
    schedule = Schedule(
        guild_id=1,
        channel_id=2,
        creator_user_id=3,
        schedule_type="once",
        status="active",
        content="テスト",
        next_run_at=datetime.now(UTC),
    )
    run = ScheduleRun(schedule_id=1, scheduled_for=datetime.now(UTC), status="pending")
    attempt = DeliveryAttempt(
        schedule_run_id=1,
        attempt_number=1,
        status="claimed",
        claimed_by=uuid.uuid7(),
        claimed_at=datetime.now(UTC),
    )
    operation = OperationLog(schedule_id=1, action="created", actor_type="user", actor_user_id=3)
    notification = NotificationLog(
        notification_type="draft_24h",
        recipient_type="creator_dm",
        recipient_id=3,
        status="pending",
        deduplication_key="test-key",
    )

    assert schedule.schedule_type == "once"
    assert run.status == "pending"
    assert attempt.attempt_number == 1
    assert operation.action == "created"
    assert notification.status == "pending"


def test_partial_indexes_have_postgresql_predicates() -> None:
    indexes: dict[str, Index] = {str(index.name): index for index in ScheduleRun.__table__.indexes}

    assert str(indexes["ix_schedule_runs_pending_due"].dialect_options["postgresql"]["where"]) == (
        "status = 'pending'"
    )
    assert (
        str(indexes["ix_schedule_runs_processing_lease"].dialect_options["postgresql"]["where"])
        == "status = 'processing'"
    )
