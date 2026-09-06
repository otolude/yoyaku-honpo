"""Create one-time and recurring schedules inside caller-owned transactions."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, fields
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

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

_INVALID_CREATION_PUBLIC_ID = "invalid schedule creation public id"


@dataclass(frozen=True, slots=True, init=False)
class ScheduleCreationPublicId:
    """Opaque UUID7 generated once when a live final-confirmation View is built."""

    value: uuid.UUID = field(repr=False)

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise ValueError(_INVALID_CREATION_PUBLIC_ID)

    @classmethod
    def create(cls, value: object) -> ScheduleCreationPublicId:
        if not isinstance(value, uuid.UUID) or value.version != 7:
            raise ValueError(_INVALID_CREATION_PUBLIC_ID)
        instance = object.__new__(cls)
        object.__setattr__(instance, "value", value)
        return instance

    @classmethod
    def generate(cls) -> ScheduleCreationPublicId:
        return cls.create(uuid.uuid7())


class IdempotentScheduleCreationCode(StrEnum):
    CREATED = "created"
    ALREADY_CREATED = "already_created"
    CONFLICT = "conflict"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class IdempotentScheduleCreationResult:
    code: IdempotentScheduleCreationCode


@dataclass(frozen=True, slots=True, repr=False)
class OnceScheduleCreationFingerprint:
    public_id: ScheduleCreationPublicId = field(repr=False)
    guild_id: int = field(repr=False)
    channel_id: int = field(repr=False)
    creator_user_id: int = field(repr=False)
    schedule_type: str = field(repr=False)
    status: str = field(repr=False)
    content: str | None = field(repr=False)
    next_run_at: datetime = field(repr=False)
    local_time: time | None = field(default=None, repr=False)
    weekday: int | None = field(default=None, repr=False)
    end_date: date | None = field(default=None, repr=False)
    allow_duplicate: bool = field(default=False, repr=False)

    @classmethod
    def create(
        cls,
        *,
        public_id: ScheduleCreationPublicId,
        guild_id: int,
        channel_id: int,
        creator_user_id: int,
        scheduled_for: datetime,
        content: str | None,
        allow_duplicate: bool,
        now: datetime,
    ) -> OnceScheduleCreationFingerprint:
        if not isinstance(public_id, ScheduleCreationPublicId):
            raise ValueError(_INVALID_CREATION_PUBLIC_ID)  # noqa: TRY004
        normalized_at = validate_once_scheduled_for(require_utc(scheduled_for), now=now)
        normalized_content = validate_create_content(content)
        status = initial_schedule_status(
            content=normalized_content, next_run_at=normalized_at, now=now
        )
        return cls(
            public_id=public_id,
            guild_id=guild_id,
            channel_id=channel_id,
            creator_user_id=creator_user_id,
            schedule_type=ScheduleType.ONCE.value,
            status=status.value,
            content=normalized_content,
            next_run_at=normalized_at,
            allow_duplicate=allow_duplicate,
        )


@dataclass(frozen=True, slots=True, repr=False)
class RecurringScheduleCreationFingerprint:
    public_id: ScheduleCreationPublicId = field(repr=False)
    guild_id: int = field(repr=False)
    channel_id: int = field(repr=False)
    creator_user_id: int = field(repr=False)
    schedule_type: str = field(repr=False)
    status: str = field(repr=False)
    content: str | None = field(repr=False)
    next_run_at: datetime = field(repr=False)
    local_time: time = field(repr=False)
    weekday: int | None = field(repr=False)
    end_date: date | None = field(repr=False)
    allow_duplicate: bool = field(repr=False)


ScheduleCreationFingerprint = OnceScheduleCreationFingerprint | RecurringScheduleCreationFingerprint


@dataclass(frozen=True, slots=True, repr=False)
class ScheduleCreationRecord:
    """Content-bearing private projection used only for same-key reconciliation."""

    public_id: ScheduleCreationPublicId = field(repr=False)
    guild_id: int = field(repr=False)
    channel_id: int = field(repr=False)
    creator_user_id: int = field(repr=False)
    schedule_type: str = field(repr=False)
    status: str = field(repr=False)
    content: str | None = field(repr=False)
    next_run_at: datetime | None = field(repr=False)
    local_time: time | None = field(repr=False)
    weekday: int | None = field(repr=False)
    end_date: date | None = field(repr=False)
    run_count: int = field(repr=False)
    pending_run_count: int = field(repr=False)
    run_scheduled_for: datetime | None = field(repr=False)
    run_next_attempt_at: datetime | None = field(repr=False)
    run_attempt_count: int | None = field(repr=False)
    creation_log_count: int = field(repr=False)
    creation_actor_user_id: int | None = field(repr=False)
    creation_allow_duplicate: bool | None = field(repr=False)

    def as_fields(self) -> dict[str, object]:
        return {item.name: getattr(self, item.name) for item in fields(self)}


def classify_schedule_creation_record(
    expected: ScheduleCreationFingerprint, record: ScheduleCreationRecord
) -> IdempotentScheduleCreationCode:
    expected_values = (
        expected.public_id,
        expected.guild_id,
        expected.channel_id,
        expected.creator_user_id,
        expected.schedule_type,
        expected.status,
        expected.content,
        expected.next_run_at,
        expected.local_time,
        expected.weekday,
        expected.end_date,
        expected.allow_duplicate,
    )
    actual_values = (
        record.public_id,
        record.guild_id,
        record.channel_id,
        record.creator_user_id,
        record.schedule_type,
        record.status,
        record.content,
        record.next_run_at,
        record.local_time,
        record.weekday,
        record.end_date,
        record.creation_allow_duplicate,
    )
    related_rows_match = (
        record.run_count == 1
        and record.pending_run_count == 1
        and record.run_scheduled_for == expected.next_run_at
        and record.run_next_attempt_at == expected.next_run_at
        and record.run_attempt_count == 0
        and record.creation_log_count == 1
        and record.creation_actor_user_id == expected.creator_user_id
    )
    if actual_values == expected_values and related_rows_match:
        return IdempotentScheduleCreationCode.ALREADY_CREATED
    return IdempotentScheduleCreationCode.CONFLICT


ScheduleCreationAction = Callable[[AsyncSession], Awaitable[object]]


class IdempotentScheduleCreationRepository(Protocol):
    async def create(
        self,
        fingerprint: ScheduleCreationFingerprint,
        action: ScheduleCreationAction,
    ) -> IdempotentScheduleCreationResult: ...


def _resolve_creation_public_id(
    supplied: ScheduleCreationPublicId | None,
) -> ScheduleCreationPublicId:
    if supplied is None:
        return ScheduleCreationPublicId.generate()
    if not isinstance(supplied, ScheduleCreationPublicId):
        raise ValueError(_INVALID_CREATION_PUBLIC_ID)  # noqa: TRY004
    return supplied


@dataclass(frozen=True)
class CreatedOnceSchedule:
    public_id: uuid.UUID = field(repr=False)
    channel_id: int
    status: ScheduleStatus
    content: str | None = field(repr=False)
    scheduled_for: datetime
    display_name: str | None = None
    display_name_source: DisplayNameSource = DisplayNameSource.UNSET


@dataclass(frozen=True)
class CreatedRecurringSchedule:
    public_id: uuid.UUID = field(repr=False)
    channel_id: int
    schedule_type: ScheduleType
    status: ScheduleStatus
    content: str | None = field(repr=False)
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
    """Validate one fixed key and delegate transaction ownership to a repository port."""

    def __init__(
        self,
        repository: IdempotentScheduleCreationRepository,
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
        fingerprint = OnceScheduleCreationFingerprint.create(
            public_id=public_id,
            guild_id=guild_id,
            channel_id=channel_id,
            creator_user_id=creator_user_id,
            scheduled_for=scheduled_for,
            content=content,
            allow_duplicate=allow_duplicate,
            now=now,
        )

        async def create(session: AsyncSession) -> object:
            return await OnceScheduleCreationService(
                session,
                configured_guild_id=self._configured_guild_id,
                name_generation_policy=self._name_generation_policy,
                logger=self._logger,
            ).create(
                guild_id=fingerprint.guild_id,
                channel_id=fingerprint.channel_id,
                creator_user_id=fingerprint.creator_user_id,
                scheduled_for=fingerprint.next_run_at,
                content=fingerprint.content,
                allow_duplicate=fingerprint.allow_duplicate,
                now=now,
                public_id=fingerprint.public_id,
            )

        return await self._repository.create(fingerprint, create)

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
        now = require_utc(now)
        normalized_content = validate_create_content(content)
        next_run_at = _first_recurring_run(
            schedule_type=schedule_type,
            local_time=local_time,
            weekday=weekday,
            end_date=end_date,
            not_before=now + timedelta(minutes=5),
        )
        if next_run_at is None:
            raise InvalidDateTimeError("recurring schedule has no occurrence before end date")
        if not isinstance(public_id, ScheduleCreationPublicId):
            raise ValueError(_INVALID_CREATION_PUBLIC_ID)  # noqa: TRY004
        status = initial_schedule_status(
            content=normalized_content, next_run_at=next_run_at, now=now
        )
        fingerprint = RecurringScheduleCreationFingerprint(
            public_id=public_id,
            guild_id=guild_id,
            channel_id=channel_id,
            creator_user_id=creator_user_id,
            schedule_type=schedule_type.value,
            status=status.value,
            content=normalized_content,
            next_run_at=next_run_at,
            local_time=local_time,
            weekday=weekday,
            end_date=end_date,
            allow_duplicate=allow_duplicate,
        )

        async def create(session: AsyncSession) -> object:
            return await RecurringScheduleCreationService(
                session,
                configured_guild_id=self._configured_guild_id,
                name_generation_policy=self._name_generation_policy,
                logger=self._logger,
            ).create(
                guild_id=fingerprint.guild_id,
                channel_id=fingerprint.channel_id,
                creator_user_id=fingerprint.creator_user_id,
                schedule_type=ScheduleType(fingerprint.schedule_type),
                local_time=fingerprint.local_time,
                weekday=fingerprint.weekday,
                end_date=fingerprint.end_date,
                content=fingerprint.content,
                allow_duplicate=fingerprint.allow_duplicate,
                now=now,
                public_id=fingerprint.public_id,
            )

        return await self._repository.create(fingerprint, create)


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
