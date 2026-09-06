"""Persistence-neutral types for one live schedule creation operation."""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field, fields
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from typing import Protocol

from discord_ai_reminder_bot.domain.enums import (
    DisplayNameSource,
    NotificationRecipientType,
    NotificationStatus,
    RunStatus,
    ScheduleStatus,
    ScheduleType,
)
from discord_ai_reminder_bot.domain.exceptions import InvalidDateTimeError
from discord_ai_reminder_bot.domain.notification import (
    notification_deduplication_key,
    plan_draft_notifications,
)
from discord_ai_reminder_bot.domain.recurrence import first_daily_run, first_weekly_run, require_utc
from discord_ai_reminder_bot.domain.schedule_creation import (
    validate_create_content,
    validate_once_scheduled_for,
)
from discord_ai_reminder_bot.domain.state_transitions import initial_schedule_status

_INVALID_PUBLIC_ID = "invalid schedule creation public id"
_INVALID_WAIT_LIMIT = "invalid schedule creation wait limit"


@dataclass(frozen=True, slots=True, init=False)
class ScheduleCreationPublicId:
    value: uuid.UUID = field(repr=False)

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise ValueError(_INVALID_PUBLIC_ID)

    @classmethod
    def create(cls, value: object) -> ScheduleCreationPublicId:
        if not isinstance(value, uuid.UUID) or value.version != 7:
            raise ValueError(_INVALID_PUBLIC_ID)
        instance = object.__new__(cls)
        object.__setattr__(instance, "value", value)
        return instance

    @classmethod
    def generate(cls) -> ScheduleCreationPublicId:
        return cls.create(uuid.uuid7())


@dataclass(frozen=True, slots=True, init=False)
class ScheduleCreationWaitLimit:
    """Finite technical lock-wait bound; it does not promise a conclusive replay result."""

    seconds: float = field(repr=False)

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise ValueError(_INVALID_WAIT_LIMIT)

    @classmethod
    def create(cls, value: object) -> ScheduleCreationWaitLimit:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError(_INVALID_WAIT_LIMIT)  # noqa: TRY004
        invalid_conversion = False
        try:
            seconds = float(value)
        except OverflowError, ValueError:
            invalid_conversion = True
            seconds = 0.0
        if invalid_conversion:
            raise ValueError(_INVALID_WAIT_LIMIT)
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError(_INVALID_WAIT_LIMIT)
        instance = object.__new__(cls)
        object.__setattr__(instance, "seconds", seconds)
        return instance


class IdempotentScheduleCreationCode(StrEnum):
    CREATED = "created"
    ALREADY_CREATED = "already_created"
    CONFLICT = "conflict"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class IdempotentScheduleCreationResult:
    """Content-free terminal result for one live operation.

    CREATED confirms the write commit. ALREADY_CREATED confirms the same key, complete
    fingerprint, and required graph. CONFLICT means a visible key or business duplicate
    disagrees. UNKNOWN covers unavailable or inconclusive reconciliation, including an
    uncommitted concurrent winner; callers must not retry it from the same live operation.
    """

    code: IdempotentScheduleCreationCode


@dataclass(frozen=True, slots=True, repr=False)
class CreationNotificationState:
    notification_type: str = field(repr=False)
    recipient_type: str = field(repr=False)
    recipient_id: int | None = field(repr=False)
    status: str = field(repr=False)
    deduplication_key: str = field(repr=False)
    scheduled_at: datetime = field(repr=False)
    next_attempt_at: datetime | None = field(repr=False)
    attempt_count: int = field(repr=False)
    run_linked: bool = field(repr=False)
    pristine: bool = field(repr=False)


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
    local_time: time | None = field(repr=False)
    weekday: int | None = field(repr=False)
    end_date: date | None = field(repr=False)
    allow_duplicate: bool = field(repr=False)
    expected_notifications: tuple[CreationNotificationState, ...] = field(repr=False)
    expect_name_job: bool = field(repr=False)
    name_generation_enabled: bool = field(repr=False)
    name_generator_available: bool = field(repr=False)
    notification_planning_enabled: bool = field(repr=False)

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
        notification_planning_enabled: bool = False,
        name_generation_enabled: bool = False,
        name_generator_available: bool = False,
    ) -> OnceScheduleCreationFingerprint:
        if not isinstance(public_id, ScheduleCreationPublicId):
            raise ValueError(_INVALID_PUBLIC_ID)  # noqa: TRY004
        now = require_utc(now)
        scheduled_for = validate_once_scheduled_for(require_utc(scheduled_for), now=now)
        content = validate_create_content(content)
        status = initial_schedule_status(content=content, next_run_at=scheduled_for, now=now)
        notifications = _expected_notifications(
            enabled=notification_planning_enabled,
            public_id=public_id,
            creator_user_id=creator_user_id,
            scheduled_for=scheduled_for,
            status=status,
            content=content,
            schedule_type=ScheduleType.ONCE,
            now=now,
        )
        return cls(
            public_id=public_id,
            guild_id=guild_id,
            channel_id=channel_id,
            creator_user_id=creator_user_id,
            schedule_type=ScheduleType.ONCE.value,
            status=status.value,
            content=content,
            next_run_at=scheduled_for,
            local_time=None,
            weekday=None,
            end_date=None,
            allow_duplicate=allow_duplicate,
            expected_notifications=notifications,
            expect_name_job=bool(content) and name_generation_enabled and name_generator_available,
            name_generation_enabled=name_generation_enabled,
            name_generator_available=name_generator_available,
            notification_planning_enabled=notification_planning_enabled,
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
    expected_notifications: tuple[CreationNotificationState, ...] = field(repr=False)
    expect_name_job: bool = field(repr=False)
    name_generation_enabled: bool = field(repr=False)
    name_generator_available: bool = field(repr=False)
    notification_planning_enabled: bool = field(repr=False)

    @classmethod
    def create(
        cls,
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
        notification_planning_enabled: bool = False,
        name_generation_enabled: bool = False,
        name_generator_available: bool = False,
    ) -> RecurringScheduleCreationFingerprint:
        if not isinstance(public_id, ScheduleCreationPublicId):
            raise ValueError(_INVALID_PUBLIC_ID)  # noqa: TRY004
        now = require_utc(now)
        content = validate_create_content(content)
        next_run_at = first_recurring_creation_run(
            schedule_type=schedule_type,
            local_time=local_time,
            weekday=weekday,
            end_date=end_date,
            not_before=now + timedelta(minutes=5),
        )
        if next_run_at is None:
            raise InvalidDateTimeError("recurring schedule has no occurrence before end date")
        status = initial_schedule_status(content=content, next_run_at=next_run_at, now=now)
        notifications = _expected_notifications(
            enabled=notification_planning_enabled,
            public_id=public_id,
            creator_user_id=creator_user_id,
            scheduled_for=next_run_at,
            status=status,
            content=content,
            schedule_type=schedule_type,
            now=now,
        )
        return cls(
            public_id=public_id,
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
            allow_duplicate=allow_duplicate,
            expected_notifications=notifications,
            expect_name_job=bool(content) and name_generation_enabled and name_generator_available,
            name_generation_enabled=name_generation_enabled,
            name_generator_available=name_generator_available,
            notification_planning_enabled=notification_planning_enabled,
        )


ScheduleCreationFingerprint = OnceScheduleCreationFingerprint | RecurringScheduleCreationFingerprint


@dataclass(frozen=True, slots=True, repr=False)
class ScheduleCreationRecord:
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
    creation_notification_planning_enabled: bool = field(default=False, repr=False)
    creation_name_generation_enabled: bool = field(default=False, repr=False)
    creation_name_generator_available: bool = field(default=False, repr=False)
    schedule_version: int = field(default=1, repr=False)
    schedule_timestamps_pristine: bool = field(default=True, repr=False)
    display_name: str | None = field(default=None, repr=False)
    display_name_source: str = field(default=DisplayNameSource.UNSET.value, repr=False)
    deleted_at: datetime | None = field(default=None, repr=False)
    terminal_at: datetime | None = field(default=None, repr=False)
    run_status: str | None = field(default=RunStatus.PENDING.value, repr=False)
    run_claimed_by_present: bool = field(default=False, repr=False)
    run_claimed_at: datetime | None = field(default=None, repr=False)
    run_lease_expires_at: datetime | None = field(default=None, repr=False)
    run_started_at: datetime | None = field(default=None, repr=False)
    run_finished_at: datetime | None = field(default=None, repr=False)
    run_discord_message_id_present: bool = field(default=False, repr=False)
    run_result_code_present: bool = field(default=False, repr=False)
    run_error_summary_present: bool = field(default=False, repr=False)
    run_timestamps_pristine: bool = field(default=True, repr=False)
    creation_log_pristine: bool = field(default=True, repr=False)
    notifications: tuple[CreationNotificationState, ...] = field(default=(), repr=False)
    notification_attempt_count: int = field(default=0, repr=False)
    delivery_attempt_count: int = field(default=0, repr=False)
    name_job_count: int = field(default=0, repr=False)
    name_job_pristine: bool = field(default=True, repr=False)

    def as_fields(self) -> dict[str, object]:
        """Return a value copy for compatibility with the original test-facing record."""
        return {item.name: getattr(self, item.name) for item in fields(self)}

    @classmethod
    def matching(cls, expected: ScheduleCreationFingerprint) -> ScheduleCreationRecord:
        return cls(
            public_id=expected.public_id,
            guild_id=expected.guild_id,
            channel_id=expected.channel_id,
            creator_user_id=expected.creator_user_id,
            schedule_type=expected.schedule_type,
            status=expected.status,
            content=expected.content,
            next_run_at=expected.next_run_at,
            local_time=expected.local_time,
            weekday=expected.weekday,
            end_date=expected.end_date,
            schedule_version=1,
            schedule_timestamps_pristine=True,
            display_name=None,
            display_name_source=DisplayNameSource.UNSET.value,
            deleted_at=None,
            terminal_at=None,
            run_count=1,
            pending_run_count=1,
            run_scheduled_for=expected.next_run_at,
            run_status=RunStatus.PENDING.value,
            run_next_attempt_at=expected.next_run_at,
            run_attempt_count=0,
            run_claimed_by_present=False,
            run_claimed_at=None,
            run_lease_expires_at=None,
            run_started_at=None,
            run_finished_at=None,
            run_discord_message_id_present=False,
            run_result_code_present=False,
            run_error_summary_present=False,
            run_timestamps_pristine=True,
            creation_log_count=1,
            creation_actor_user_id=expected.creator_user_id,
            creation_allow_duplicate=expected.allow_duplicate,
            creation_notification_planning_enabled=expected.notification_planning_enabled,
            creation_name_generation_enabled=expected.name_generation_enabled,
            creation_name_generator_available=expected.name_generator_available,
            creation_log_pristine=True,
            notifications=expected.expected_notifications,
            notification_attempt_count=0,
            delivery_attempt_count=0,
            name_job_count=int(expected.expect_name_job),
            name_job_pristine=True,
        )


def classify_schedule_creation_record(
    expected: ScheduleCreationFingerprint, actual: ScheduleCreationRecord
) -> IdempotentScheduleCreationCode:
    return (
        IdempotentScheduleCreationCode.ALREADY_CREATED
        if actual == ScheduleCreationRecord.matching(expected)
        else IdempotentScheduleCreationCode.CONFLICT
    )


class IdempotentScheduleCreationPort(Protocol):
    """One-shot port; UNKNOWN ends the operation and requires a creator/guild list check.

    A caller must not invoke ``create`` again for the same live operation after UNKNOWN,
    and no public ID is required in the user-facing list workflow.
    """

    async def create(
        self,
        fingerprint: ScheduleCreationFingerprint,
        *,
        operation_at: datetime,
    ) -> IdempotentScheduleCreationResult: ...


def first_recurring_creation_run(
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


def _expected_notifications(
    *,
    enabled: bool,
    public_id: ScheduleCreationPublicId,
    creator_user_id: int,
    scheduled_for: datetime,
    status: ScheduleStatus,
    content: str | None,
    schedule_type: ScheduleType,
    now: datetime,
) -> tuple[CreationNotificationState, ...]:
    if not enabled:
        return ()
    plans = plan_draft_notifications(
        event_at=now,
        scheduled_for=scheduled_for,
        schedule_status=status,
        content=content,
        schedule_type=schedule_type,
        run_status=RunStatus.PENDING,
        attempt_count=0,
        next_run_at=scheduled_for,
    )
    states = tuple(
        CreationNotificationState(
            notification_type=plan.notification_type.value,
            recipient_type=NotificationRecipientType.CREATOR_DM.value,
            recipient_id=creator_user_id,
            status=NotificationStatus.PENDING.value,
            deduplication_key=notification_deduplication_key(
                event_kind="draft_reminder",
                schedule_public_id=public_id.value,
                scheduled_for=scheduled_for,
                notification_type=plan.notification_type,
                recipient_type=NotificationRecipientType.CREATOR_DM,
            ),
            scheduled_at=plan.scheduled_at,
            next_attempt_at=plan.scheduled_at,
            attempt_count=0,
            run_linked=True,
            pristine=True,
        )
        for plan in plans
    )
    return tuple(sorted(states, key=lambda item: (item.notification_type, item.scheduled_at)))
