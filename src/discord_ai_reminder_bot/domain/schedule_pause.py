"""Pure rules shared by schedule pause and resume operations."""

from datetime import datetime

from discord_ai_reminder_bot.domain.enums import RunStatus, ScheduleStatus, ScheduleType
from discord_ai_reminder_bot.domain.exceptions import InvalidStateTransitionError
from discord_ai_reminder_bot.domain.recurrence import require_utc

RECURRING_TYPES = frozenset({ScheduleType.DAILY, ScheduleType.WEEKLY})


def validate_pause_target(*, schedule_type: ScheduleType, status: ScheduleStatus) -> None:
    if schedule_type not in RECURRING_TYPES or status is not ScheduleStatus.ACTIVE:
        raise InvalidStateTransitionError("schedule cannot be paused")


def validate_resume_target(*, schedule_type: ScheduleType, status: ScheduleStatus) -> None:
    if schedule_type not in RECURRING_TYPES or status is not ScheduleStatus.PAUSED:
        raise InvalidStateTransitionError("schedule cannot be resumed")


def latest_scheduled_for(*, scheduled_for: list[datetime], resumed_at: datetime) -> datetime:
    """Return the later resume calculation boundary, preserving skipped occurrences."""
    boundary = require_utc(resumed_at)
    for value in scheduled_for:
        value = require_utc(value)
        boundary = max(boundary, value)
    return boundary


def has_conflicting_run_state(statuses: list[RunStatus]) -> bool:
    """Reject claimed work and terminal work waiting for Schedule finalization."""
    return any(status is RunStatus.PROCESSING for status in statuses)
