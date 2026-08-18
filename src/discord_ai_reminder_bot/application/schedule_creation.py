"""Create one-time schedules inside a caller-owned transaction."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from discord_ai_reminder_bot.domain.enums import RunStatus, ScheduleStatus, ScheduleType
from discord_ai_reminder_bot.domain.recurrence import require_utc
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
