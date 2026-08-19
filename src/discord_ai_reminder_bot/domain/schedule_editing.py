"""Pure validation and occurrence selection for schedule editing."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from discord_ai_reminder_bot.domain.enums import ScheduleStatus, ScheduleType
from discord_ai_reminder_bot.domain.exceptions import InvalidDateTimeError
from discord_ai_reminder_bot.domain.recurrence import (
    first_daily_run,
    first_weekly_run,
    next_daily_run,
    next_weekly_run,
    require_utc,
)
from discord_ai_reminder_bot.domain.schedule_creation import validate_create_content

EDIT_LEAD_TIME = timedelta(minutes=5)


class InvalidScheduleEditError(ValueError):
    """The requested edit is structurally invalid for the target schedule."""


def validate_edit_content(content: str | None) -> str | None:
    """Apply the same body rules used when a schedule is created."""
    return validate_create_content(content)


def validate_edit_target(
    *,
    schedule_type: ScheduleType,
    status: ScheduleStatus,
    next_run_at: datetime | None,
    now: datetime,
) -> None:
    """Require an editable state and the inclusive five-minute safety boundary."""
    now = require_utc(now)
    if status not in {ScheduleStatus.DRAFT, ScheduleStatus.ACTIVE, ScheduleStatus.PAUSED}:
        raise InvalidScheduleEditError("schedule status is not editable")
    if status is ScheduleStatus.PAUSED:
        if (
            schedule_type not in {ScheduleType.DAILY, ScheduleType.WEEKLY}
            or next_run_at is not None
        ):
            raise InvalidScheduleEditError("only recurring schedules can be edited while paused")
        return
    if next_run_at is None or require_utc(next_run_at) < now + EDIT_LEAD_TIME:
        raise InvalidScheduleEditError("schedule is inside the edit safety window")


def first_unused_recurring_edit_run(
    *,
    schedule_type: ScheduleType,
    local_time: time,
    weekday: int | None,
    end_date: date | None,
    edited_at: datetime,
    occupied: set[datetime],
    reusable_pending: datetime | None,
) -> datetime | None:
    """Select the first unused occurrence at least five minutes after an edit.

    The current pending occurrence may be retained. Every other historical occurrence is
    immutable. The number of advances is bounded by the number of occupied timestamps.
    """
    not_before = require_utc(edited_at) + EDIT_LEAD_TIME
    if schedule_type is ScheduleType.DAILY:
        candidate = first_daily_run(local_time=local_time, not_before=not_before, end_date=end_date)
    elif schedule_type is ScheduleType.WEEKLY:
        if weekday is None:
            raise InvalidDateTimeError("weekly schedule requires weekday")
        candidate = first_weekly_run(
            weekday=weekday,
            local_time=local_time,
            not_before=not_before,
            end_date=end_date,
        )
    else:
        raise InvalidDateTimeError("recurring schedule is required")

    for _ in range(len(occupied) + 1):
        if candidate is None or candidate == reusable_pending or candidate not in occupied:
            return candidate
        if schedule_type is ScheduleType.DAILY:
            candidate = next_daily_run(local_time=local_time, after=candidate, end_date=end_date)
        else:
            candidate = next_weekly_run(
                weekday=weekday,
                local_time=local_time,
                after=candidate,
                end_date=end_date,
            )
    raise InvalidScheduleEditError("unused occurrence search exceeded its safe bound")
