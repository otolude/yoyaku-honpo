"""Create one-time and recurring schedules inside caller-owned transactions."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from discord_ai_reminder_bot.application import idempotent_schedule_creation as _idempotency
from discord_ai_reminder_bot.application.idempotent_schedule_creation import (
    IdempotentScheduleCreationPort,
    IdempotentScheduleCreationResult,
    OnceScheduleCreationFingerprint,
    RecurringScheduleCreationFingerprint,
    ScheduleCreationPublicId,
    first_recurring_creation_run,
)
from discord_ai_reminder_bot.application.name_generation import (
    NameGenerationRegistrationPolicy,
    register_generation_job,
)
from discord_ai_reminder_bot.application.notification_planning import NotificationPlanningService
from discord_ai_reminder_bot.domain.enums import (
    DisplayNameSource,
    RunStatus,
    ScheduleStatus,
    ScheduleType,
)
from discord_ai_reminder_bot.domain.exceptions import InvalidDateTimeError
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

IdempotentScheduleCreationCode = _idempotency.IdempotentScheduleCreationCode
IdempotentScheduleCreationRepository = IdempotentScheduleCreationPort
ScheduleCreationFingerprint = _idempotency.ScheduleCreationFingerprint
ScheduleCreationRecord = _idempotency.ScheduleCreationRecord
classify_schedule_creation_record = _idempotency.classify_schedule_creation_record


def _resolve_creation_public_id(
    supplied: ScheduleCreationPublicId | None,
) -> ScheduleCreationPublicId:
    if supplied is None:
        return ScheduleCreationPublicId.generate()
    if not isinstance(supplied, ScheduleCreationPublicId):
        raise ValueError("invalid schedule creation public id")  # noqa: TRY004
    return supplied


@dataclass(frozen=True)
class CreatedOnceSchedule:
    public_id: uuid.UUID
    channel_id: int
    status: ScheduleStatus
    content: str | None
    scheduled_for: datetime
    display_name: str | None = None
    display_name_source: DisplayNameSource = DisplayNameSource.UNSET


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
    display_name: str | None = None
    display_name_source: DisplayNameSource = DisplayNameSource.UNSET


class DuplicateScheduleWarning(Exception):
    """Creation stopped because the user has not confirmed a duplicate candidate."""


class OnceScheduleCreationService:
    """Build a Schedule and its first pending run without committing or rolling back."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        configured_guild_id: int | None = None,
        name_generation_policy: NameGenerationRegistrationPolicy | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._session = session
        self._schedules = ScheduleRepository(session)
        self._runs = ScheduleRunRepository(session)
        self._configured_guild_id = configured_guild_id
        self._name_generation_policy = name_generation_policy or NameGenerationRegistrationPolicy()
        self._logger = logger

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
        configured_guild_id: int | None = None,
        public_id: ScheduleCreationPublicId | None = None,
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
                public_id=_resolve_creation_public_id(public_id).value,
                guild_id=guild_id,
                channel_id=channel_id,
                creator_user_id=creator_user_id,
                schedule_type=ScheduleType.ONCE.value,
                status=status.value,
                content=content,
                display_name=None,
                display_name_source=DisplayNameSource.UNSET.value,
                next_run_at=scheduled_for,
                version=1,
            )
        )
        run = ScheduleRun(
            schedule_id=schedule.id,
            scheduled_for=scheduled_for,
            status=RunStatus.PENDING.value,
            attempt_count=0,
            next_attempt_at=scheduled_for,
        )
        await self._runs.add(run)
        configured_guild_id = configured_guild_id or self._configured_guild_id
        if configured_guild_id is not None and guild_id == configured_guild_id:
            planner = NotificationPlanningService(self._session, configured_guild_id=guild_id)
            await planner.plan_for_run(schedule=schedule, run=run, event_at=now)
        if content is not None:
            await register_generation_job(
                session=self._session,
                schedule_id=schedule.id,
                expected_schedule_version=schedule.version,
                created_at=now,
                policy=self._name_generation_policy,
                logger=self._logger,
            )
        return CreatedOnceSchedule(
            public_id=schedule.public_id,
            channel_id=channel_id,
            status=status,
            content=content,
            display_name=None,
            display_name_source=DisplayNameSource.UNSET,
            scheduled_for=scheduled_for,
        )


class RecurringScheduleCreationService:
    """Build a daily or weekly schedule and first run in a caller-owned transaction."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        configured_guild_id: int | None = None,
        name_generation_policy: NameGenerationRegistrationPolicy | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._schedules = ScheduleRepository(session)
        self._runs = ScheduleRunRepository(session)
        self._session = session
        self._configured_guild_id = configured_guild_id
        self._name_generation_policy = name_generation_policy or NameGenerationRegistrationPolicy()
        self._logger = logger

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
        configured_guild_id: int | None = None,
        public_id: ScheduleCreationPublicId | None = None,
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
                public_id=_resolve_creation_public_id(public_id).value,
                guild_id=guild_id,
                channel_id=channel_id,
                creator_user_id=creator_user_id,
                schedule_type=schedule_type.value,
                status=status.value,
                content=content,
                display_name=None,
                display_name_source=DisplayNameSource.UNSET.value,
                next_run_at=next_run_at,
                local_time=local_time,
                weekday=weekday,
                end_date=end_date,
                version=1,
            )
        )
        run = ScheduleRun(
            schedule_id=schedule.id,
            scheduled_for=next_run_at,
            status=RunStatus.PENDING.value,
            attempt_count=0,
            next_attempt_at=next_run_at,
        )
        await self._runs.add(run)
        configured_guild_id = configured_guild_id or self._configured_guild_id
        if configured_guild_id is not None and guild_id == configured_guild_id:
            await NotificationPlanningService(
                self._session, configured_guild_id=guild_id
            ).plan_for_run(schedule=schedule, run=run, event_at=now)
        if content is not None:
            await register_generation_job(
                session=self._session,
                schedule_id=schedule.id,
                expected_schedule_version=schedule.version,
                created_at=now,
                policy=self._name_generation_policy,
                logger=self._logger,
            )
        return CreatedRecurringSchedule(
            public_id=schedule.public_id,
            channel_id=channel_id,
            schedule_type=schedule_type,
            status=status,
            content=content,
            display_name=None,
            display_name_source=DisplayNameSource.UNSET,
            local_time=local_time,
            weekday=weekday,
            end_date=end_date,
            next_run_at=next_run_at,
        )


class IdempotentScheduleCreationService:
    """Build persistence-neutral inputs and invoke one one-shot creation port."""

    def __init__(
        self,
        repository: IdempotentScheduleCreationPort,
        *,
        configured_guild_id: int | None = None,
        name_generation_policy: NameGenerationRegistrationPolicy | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._repository = repository
        self._configured_guild_id = configured_guild_id
        self._name_generation_policy = name_generation_policy
        self._logger = logger

    async def create_once(
        self,
        *,
        public_id: ScheduleCreationPublicId,
        guild_id: int,
        channel_id: int,
        creator_user_id: int,
        scheduled_for: datetime,
        content: str | None,
        allow_duplicate: bool,
        now: datetime,
    ) -> IdempotentScheduleCreationResult:
        operation_at = require_utc(now)
        fingerprint = OnceScheduleCreationFingerprint.create(
            public_id=public_id,
            guild_id=guild_id,
            channel_id=channel_id,
            creator_user_id=creator_user_id,
            scheduled_for=scheduled_for,
            content=content,
            allow_duplicate=allow_duplicate,
            now=operation_at,
            notification_planning_enabled=(
                self._configured_guild_id is not None and self._configured_guild_id == guild_id
            ),
            name_generation_enabled=(
                self._name_generation_policy.enabled
                if self._name_generation_policy is not None
                else False
            ),
            name_generator_available=(
                self._name_generation_policy.generator_available
                if self._name_generation_policy is not None
                else False
            ),
        )
        return await self._repository.create(fingerprint, operation_at=operation_at)

    async def create_recurring(
        self,
        *,
        public_id: ScheduleCreationPublicId,
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
    ) -> IdempotentScheduleCreationResult:
        operation_at = require_utc(now)
        fingerprint = RecurringScheduleCreationFingerprint.create(
            public_id=public_id,
            guild_id=guild_id,
            channel_id=channel_id,
            creator_user_id=creator_user_id,
            schedule_type=schedule_type,
            local_time=local_time,
            weekday=weekday,
            end_date=end_date,
            content=content,
            allow_duplicate=allow_duplicate,
            now=operation_at,
            notification_planning_enabled=(
                self._configured_guild_id is not None and self._configured_guild_id == guild_id
            ),
            name_generation_enabled=(
                self._name_generation_policy.enabled
                if self._name_generation_policy is not None
                else False
            ),
            name_generator_available=(
                self._name_generation_policy.generator_available
                if self._name_generation_policy is not None
                else False
            ),
        )
        return await self._repository.create(fingerprint, operation_at=operation_at)


def _first_recurring_run(
    *,
    schedule_type: ScheduleType,
    local_time: time,
    weekday: int | None,
    end_date: date | None,
    not_before: datetime,
) -> datetime | None:
    return first_recurring_creation_run(
        schedule_type=schedule_type,
        local_time=local_time,
        weekday=weekday,
        end_date=end_date,
        not_before=not_before,
    )
