from datetime import UTC, datetime

import pytest

from discord_ai_reminder_bot.domain.enums import ScheduleStatus, ScheduleType
from discord_ai_reminder_bot.domain.exceptions import InvalidStateTransitionError
from discord_ai_reminder_bot.domain.schedule_pause import (
    latest_scheduled_for,
    validate_pause_target,
    validate_resume_target,
)
from discord_ai_reminder_bot.infrastructure.database.repositories import (
    build_schedule_state_change_runs_statement,
)

NOW = datetime(2026, 8, 19, 0, 0, tzinfo=UTC)


@pytest.mark.parametrize("schedule_type", [ScheduleType.DAILY, ScheduleType.WEEKLY])
def test_active_recurring_can_pause(schedule_type: ScheduleType) -> None:
    validate_pause_target(schedule_type=schedule_type, status=ScheduleStatus.ACTIVE)


@pytest.mark.parametrize(
    ("schedule_type", "status"),
    [
        (ScheduleType.ONCE, ScheduleStatus.ACTIVE),
        (ScheduleType.DAILY, ScheduleStatus.DRAFT),
        (ScheduleType.DAILY, ScheduleStatus.PAUSED),
        (ScheduleType.DAILY, ScheduleStatus.ENDED),
    ],
)
def test_invalid_pause_targets_are_rejected(
    schedule_type: ScheduleType, status: ScheduleStatus
) -> None:
    with pytest.raises(InvalidStateTransitionError):
        validate_pause_target(schedule_type=schedule_type, status=status)


def test_only_paused_recurring_can_resume() -> None:
    validate_resume_target(schedule_type=ScheduleType.DAILY, status=ScheduleStatus.PAUSED)
    with pytest.raises(InvalidStateTransitionError):
        validate_resume_target(schedule_type=ScheduleType.DAILY, status=ScheduleStatus.ACTIVE)
    with pytest.raises(InvalidStateTransitionError):
        validate_resume_target(schedule_type=ScheduleType.ONCE, status=ScheduleStatus.PAUSED)


def test_latest_scheduled_for_uses_later_boundary() -> None:
    future = datetime(2026, 8, 20, 0, 0, tzinfo=UTC)
    assert latest_scheduled_for(scheduled_for=[future], resumed_at=NOW) == future
    assert latest_scheduled_for(scheduled_for=[], resumed_at=NOW) == NOW


def test_state_change_run_lock_orders_runs_before_schedule_lock() -> None:
    statement = build_schedule_state_change_runs_statement(schedule_id=10, lock=True)
    sql = str(statement)
    assert "ORDER BY schedule_runs.id ASC" in sql
    assert sql.endswith("FOR UPDATE")
