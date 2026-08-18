"""Create one-time and recurring schedules inside caller-owned transactions."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from discord_ai_reminder_bot.domain.enums import RunStatus, ScheduleStatus, ScheduleType
from discord_ai_reminder_bot.domain.exceptions import InvalidDateTimeError
from discord_ai_reminder_bot.domain.recurrence import first_daily_run, first_weekly_run, require_utc
from discord_ai_reminder_bot.domain.schedule_creation import (
    validate_create_content,
    validate_once_scheduled_for,
)
from discord_ai_reminder_bot.domain.state_transitions import initial_schedule_status
from discord_ai_reminder_bot.infrastructure.database.models import Schedule, ScheduleRun
from discord_ai_reminder_bot.infrastructure.database.repositories import (
    ScheduleRepository,
    ScheduleRunRepository,
)


@dataclass(frozen=True)
class CreatedOnceSchedule:
    public_id: uuid.UUID
    channel_id: int
    status: ScheduleStatus
    content: str | None
    scheduled_for: datetime


@dataclass(frozen=True)
class CreatedRecurringSchedule:
    public_id: uuid.UUID
    channel_id: int
    schedule_type: ScheduleType
    status: ScheduleStatus
    content: str | None
    local_time: time
    weekday: int | None
    end_date: date | None
    next_run_at: datetime


class DuplicateScheduleWarning(Exception):
    """Creation stopped because the user has not confirmed a duplicate candidate."""


class OnceScheduleCreationService:
    """Build a Schedule and its first pending run without committing or rolling back."""

    def __init__(self, session: AsyncSession) -> None:
        self._schedules = ScheduleRepository(session)
        self._runs = ScheduleRunRepository(session)

    async def create(
        self,
        *,
        guild_id: int,
        channel_id: int,
        creator_user_id: int,
        scheduled_for: datetime,
        content: str | None,
        allow_duplicate: bool,
        now: datetime,
    ) -> CreatedOnceSchedule:
        scheduled_for = validate_once_scheduled_for(require_utc(scheduled_for), now=now)
        content = validate_create_content(content)
        status = initial_schedule_status(content=content, next_run_at=scheduled_for, now=now)
        if not allow_duplicate and await self._schedules.has_once_duplicate(
            guild_id=guild_id,
            channel_id=channel_id,
            scheduled_for=scheduled_for,
            content=content,
        ):
            raise DuplicateScheduleWarning
        schedule = await self._schedules.add(
            Schedule(
                public_id=uuid.uuid7(),
                guild_id=guild_id,
                channel_id=channel_id,
                creator_user_id=creator_user_id,
                schedule_type=ScheduleType.ONCE.value,
                status=status.value,
                content=content,
                next_run_at=scheduled_for,
                version=1,
            )
        )
        await self._runs.add(
            ScheduleRun(
                schedule_id=schedule.id,
                scheduled_for=scheduled_for,
                status=RunStatus.PENDING.value,
                attempt_count=0,
                next_attempt_at=scheduled_for,
            )
        )
        return CreatedOnceSchedule(
            public_id=schedule.public_id,
            channel_id=channel_id,
            status=status,
            content=content,
            scheduled_for=scheduled_for,
        )


class RecurringScheduleCreationService:
    """Build a daily or weekly schedule and first run in a caller-owned transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._schedules = ScheduleRepository(session)
        self._runs = ScheduleRunRepository(session)

    async def create(
        self,
        *,
        guild_id: int,
        channel_id: int,
        creator_user_id: int,
        schedule_type: ScheduleType,
        local_time: time,
        weekday: int | None,
        end_date: date | None,
        content: str | None,
        allow_duplicate: bool,
        now: datetime,
    ) -> CreatedRecurringSchedule:
        now = require_utc(now)
        content = validate_create_content(content)
        next_run_at = _first_recurring_run(
            schedule_type=schedule_type,
            local_time=local_time,
            weekday=weekday,
            end_date=end_date,
            not_before=now + timedelta(minutes=5),
        )
        if next_run_at is None:
            raise InvalidDateTimeError("recurring schedule has no occurrence before end date")
        status = initial_schedule_status(content=content, next_run_at=next_run_at, now=now)
        if not allow_duplicate and await self._schedules.has_recurring_duplicate(
            guild_id=guild_id,
            channel_id=channel_id,
            schedule_type=schedule_type,
            local_time=local_time,
            weekday=weekday,
            end_date=end_date,
            content=content,
        ):
            raise DuplicateScheduleWarning
        schedule = await self._schedules.add(
            Schedule(
                public_id=uuid.uuid7(),
                guild_id=guild_id,
                channel_id=channel_id,
                creator_user_id=creator_user_id,
                schedule_type=schedule_type.value,
                status=status.value,
                content=content,
                next_run_at=next_run_at,
                local_time=local_time,
                weekday=weekday,
                end_date=end_date,
                version=1,
            )
        )
        await self._runs.add(
            ScheduleRun(
                schedule_id=schedule.id,
                scheduled_for=next_run_at,
                status=RunStatus.PENDING.value,
                attempt_count=0,
                next_attempt_at=next_run_at,
            )
        )
        return CreatedRecurringSchedule(
            public_id=schedule.public_id,
            channel_id=channel_id,
            schedule_type=schedule_type,
            status=status,
            content=content,
            local_time=local_time,
            weekday=weekday,
            end_date=end_date,
            next_run_at=next_run_at,
        )


def _first_recurring_run(
    *,
    schedule_type: ScheduleType,
    local_time: time,
    weekday: int | None,
    end_date: date | None,
    not_before: datetime,
) -> datetime | None:
    if local_time.tzinfo is not None or local_time.second or local_time.microsecond:
        raise InvalidDateTimeError("invalid local schedule time")
    if schedule_type is ScheduleType.DAILY:
        if weekday is not None:
            raise InvalidDateTimeError("daily schedule must not have weekday")
        return first_daily_run(local_time=local_time, not_before=not_before, end_date=end_date)
    if schedule_type is ScheduleType.WEEKLY:
        if weekday is None or isinstance(weekday, bool) or not 0 <= weekday <= 6:
            raise InvalidDateTimeError("weekly schedule requires valid weekday")
        return first_weekly_run(
            weekday=weekday,
            local_time=local_time,
            not_before=not_before,
            end_date=end_date,
        )
    raise InvalidDateTimeError("recurring schedule type is required")
