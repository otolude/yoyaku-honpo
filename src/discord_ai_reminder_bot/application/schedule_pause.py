"""Atomically pause and resume recurring schedules."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time
from enum import StrEnum
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from discord_ai_reminder_bot.application.notification_planning import NotificationPlanningService
from discord_ai_reminder_bot.application.schedule_execution import recurring_next_run
from discord_ai_reminder_bot.application.schedule_queries import parse_public_id
from discord_ai_reminder_bot.domain.enums import (
    ActorType,
    OperationAction,
    RunStatus,
    ScheduleStatus,
    ScheduleType,
)
from discord_ai_reminder_bot.domain.recurrence import require_utc
from discord_ai_reminder_bot.domain.schedule_pause import (
    latest_scheduled_for,
    validate_pause_target,
    validate_resume_target,
)
from discord_ai_reminder_bot.infrastructure.database.exceptions import (
    DuplicateRecordError,
    RepositoryNotFoundError,
)
from discord_ai_reminder_bot.infrastructure.database.models import (
    OperationLog,
    Schedule,
    ScheduleRun,
)
from discord_ai_reminder_bot.infrastructure.database.repositories import (
    OperationLogRepository,
    ScheduleRepository,
    ScheduleRunRepository,
)

_TOKYO = ZoneInfo("Asia/Tokyo")


class ScheduleStateChangeUnavailable(Exception):
    """The target is absent, unauthorized, conflicting, or ineligible."""


class ResumeMode(StrEnum):
    NEXT_REGULAR = "next_regular"
    IMMEDIATE_ONCE = "immediate_once"
    RESCHEDULED_ONCE = "rescheduled_once"


@dataclass(frozen=True)
class PausedSchedule:
    public_id: uuid.UUID
    channel_id: int
    schedule_type: ScheduleType
    previous_status: ScheduleStatus
    pending_runs_skipped: int
    local_time: time
    weekday: int | None
    end_date: date | None
    held_run_at: datetime | None = None


@dataclass(frozen=True)
class ResumedSchedule:
    public_id: uuid.UUID
    channel_id: int
    schedule_type: ScheduleType
    status: ScheduleStatus
    next_run_at: datetime | None
    local_time: time
    weekday: int | None
    end_date: date | None
    content: str | None
    held_run_reused: bool
    resume_mode: ResumeMode = ResumeMode.NEXT_REGULAR
    missed_scheduled_for: datetime | None = None
    replacement_scheduled_for: datetime | None = None
    next_regular_at: datetime | None = None


@dataclass(frozen=True)
class ResumePreview:
    public_id: uuid.UUID
    held_run_at: datetime | None
    same_tokyo_date: bool
    rescue_allowed: bool


@dataclass(frozen=True)
class _TargetSnapshot:
    schedule_id: int
    public_id: uuid.UUID
    guild_id: int
    version: int
    next_run_at: datetime | None


class SchedulePauseService:
    """Change recurring state without owning commit or rollback."""

    def __init__(self, session: AsyncSession, *, configured_guild_id: int | None = None) -> None:
        self._session = session
        self._schedules = ScheduleRepository(session)
        self._runs = ScheduleRunRepository(session)
        self._operations = OperationLogRepository(session)
        self._configured_guild_id = configured_guild_id

    async def pause(
        self,
        *,
        guild_id: int,
        public_id: str,
        actor_user_id: int,
        administrator: bool,
        paused_at: datetime,
    ) -> PausedSchedule:
        paused_at = require_utc(paused_at)
        snapshot = _snapshot(await self._find(guild_id=guild_id, public_id=public_id))
        runs = await self._runs.list_for_schedule_state_change(
            schedule_id=snapshot.schedule_id, lock=True
        )
        schedule = await self._lock_and_revalidate(snapshot)
        self._authorize(schedule, actor_user_id=actor_user_id, administrator=administrator)
        try:
            validate_pause_target(
                schedule_type=ScheduleType(schedule.schedule_type),
                status=ScheduleStatus(schedule.status),
            )
        except (ValueError, TypeError) as error:
            raise ScheduleStateChangeUnavailable from error
        self._validate_runs(schedule, runs)
        held = await self._held_candidate(schedule, runs, paused_at)
        pending_count = sum(
            run.status == RunStatus.PENDING.value and run is not held for run in runs
        )
        await self._runs.skip_pending_for_paused_schedule(
            runs=runs, paused_at=paused_at, preserve_run_id=held.id if held else None
        )
        if held is not None:
            await self._runs.cancel_pristine_draft_notifications_for_pause(
                run_id=held.id, paused_at=paused_at
            )
        schedule.status = ScheduleStatus.PAUSED.value
        schedule.next_run_at = None
        schedule.terminal_at = None
        schedule.deleted_at = None
        schedule.updated_at = paused_at
        schedule.version += 1
        await self._schedules.flush_execution_update(schedule)
        await self._add_operation(
            schedule_id=schedule.id,
            action=OperationAction.PAUSED,
            actor_user_id=actor_user_id,
            at=paused_at,
            changes={
                "status": {"from": "active", "to": "paused"},
                "pending_runs_skipped": pending_count,
                "held_scheduled_for": held.scheduled_for.isoformat() if held else None,
            },
        )
        return PausedSchedule(
            public_id=schedule.public_id,
            channel_id=schedule.channel_id,
            schedule_type=ScheduleType(schedule.schedule_type),
            previous_status=ScheduleStatus.ACTIVE,
            pending_runs_skipped=pending_count,
            local_time=schedule.local_time,
            weekday=schedule.weekday,
            end_date=schedule.end_date,
            held_run_at=held.scheduled_for if held else None,
        )

    async def preview_resume(
        self,
        *,
        guild_id: int,
        public_id: str,
        actor_user_id: int,
        administrator: bool,
        resumed_at: datetime,
    ) -> ResumePreview:
        resumed_at = require_utc(resumed_at)
        schedule = await self._find(guild_id=guild_id, public_id=public_id)
        self._authorize(schedule, actor_user_id=actor_user_id, administrator=administrator)
        try:
            validate_resume_target(
                schedule_type=ScheduleType(schedule.schedule_type),
                status=ScheduleStatus(schedule.status),
            )
        except (ValueError, TypeError) as error:
            raise ScheduleStateChangeUnavailable from error
        runs = await self._runs.list_for_schedule_state_change(schedule_id=schedule.id, lock=False)
        held = await self._paused_held_run(schedule, runs)
        same_day = bool(
            held
            and held.scheduled_for.astimezone(_TOKYO).date() == resumed_at.astimezone(_TOKYO).date()
        )
        rescue = bool(
            same_day
            and (
                schedule.end_date is None
                or resumed_at.astimezone(_TOKYO).date() <= schedule.end_date
            )
        )
        return ResumePreview(
            schedule.public_id, held.scheduled_for if held else None, same_day, rescue
        )

    async def resume(
        self,
        *,
        guild_id: int,
        public_id: str,
        actor_user_id: int,
        administrator: bool,
        resumed_at: datetime,
        configured_guild_id: int | None = None,
        mode: ResumeMode = ResumeMode.NEXT_REGULAR,
        replacement_at: datetime | None = None,
    ) -> ResumedSchedule:
        resumed_at = require_utc(resumed_at)
        snapshot = _snapshot(await self._find(guild_id=guild_id, public_id=public_id))
        runs = await self._runs.list_for_schedule_state_change(
            schedule_id=snapshot.schedule_id, lock=True
        )
        schedule = await self._lock_and_revalidate(snapshot)
        self._authorize(schedule, actor_user_id=actor_user_id, administrator=administrator)
        try:
            schedule_type = ScheduleType(schedule.schedule_type)
            validate_resume_target(
                schedule_type=schedule_type,
                status=ScheduleStatus(schedule.status),
            )
        except (ValueError, TypeError) as error:
            raise ScheduleStateChangeUnavailable from error
        self._validate_runs(schedule, runs)
        held = await self._paused_held_run(schedule, runs)
        if schedule.local_time is None:
            raise ScheduleStateChangeUnavailable
        if held is not None and held.scheduled_for > resumed_at:
            if mode is not ResumeMode.NEXT_REGULAR or replacement_at is not None:
                raise ScheduleStateChangeUnavailable
            next_at = held.scheduled_for
            created_run = held
            missed = None
            held_run_reused = True
        else:
            held_run_reused = False
            missed = held.scheduled_for if held else None
            if mode in {ResumeMode.IMMEDIATE_ONCE, ResumeMode.RESCHEDULED_ONCE}:
                replacement_at = resumed_at if mode is ResumeMode.IMMEDIATE_ONCE else replacement_at
                if held is None or replacement_at is None:
                    raise ScheduleStateChangeUnavailable
                replacement_at = require_utc(replacement_at)
                local_today = resumed_at.astimezone(_TOKYO).date()
                if (
                    held.scheduled_for.astimezone(_TOKYO).date() != local_today
                    or replacement_at.astimezone(_TOKYO).date() != local_today
                    or replacement_at < resumed_at
                    or (schedule.end_date is not None and local_today > schedule.end_date)
                ):
                    raise ScheduleStateChangeUnavailable
                await self._runs.skip_pending_for_paused_schedule(runs=[held], paused_at=resumed_at)
                try:
                    created_run = await self._runs.add(
                        ScheduleRun(
                            schedule_id=schedule.id,
                            scheduled_for=replacement_at,
                            status=RunStatus.PENDING.value,
                            attempt_count=0,
                            next_attempt_at=replacement_at,
                            updated_at=resumed_at,
                        )
                    )
                except DuplicateRecordError as error:
                    raise ScheduleStateChangeUnavailable from error
                next_at = replacement_at
            else:
                if held is not None:
                    await self._runs.skip_pending_for_paused_schedule(
                        runs=[held], paused_at=resumed_at
                    )
                boundary = latest_scheduled_for(
                    scheduled_for=[run.scheduled_for for run in runs], resumed_at=resumed_at
                )
                try:
                    next_at = recurring_next_run(
                        schedule_type=schedule_type,
                        local_time=schedule.local_time,
                        weekday=schedule.weekday,
                        end_date=schedule.end_date,
                        finalized_at=boundary,
                    )
                except (ValueError, TypeError) as error:
                    raise ScheduleStateChangeUnavailable from error
                created_run = None

        if next_at is None:
            if schedule.content is None:
                raise ScheduleStateChangeUnavailable
            target = ScheduleStatus.ENDED
            schedule.next_run_at = None
            schedule.terminal_at = resumed_at
        else:
            target = ScheduleStatus.ACTIVE if schedule.content is not None else ScheduleStatus.DRAFT
            if created_run is None:
                try:
                    created_run = await self._runs.add(
                        ScheduleRun(
                            schedule_id=schedule.id,
                            scheduled_for=next_at,
                            status=RunStatus.PENDING.value,
                            attempt_count=0,
                            next_attempt_at=next_at,
                            claimed_by=None,
                            claimed_at=None,
                            lease_expires_at=None,
                            discord_message_id=None,
                            result_code=None,
                            error_summary=None,
                            started_at=None,
                            finished_at=None,
                            updated_at=resumed_at,
                        )
                    )
                except DuplicateRecordError as error:
                    raise ScheduleStateChangeUnavailable from error
            schedule.next_run_at = next_at
            schedule.terminal_at = None
        schedule.status = target.value
        schedule.deleted_at = None
        schedule.updated_at = resumed_at
        schedule.version += 1
        await self._schedules.flush_execution_update(schedule)
        await self._add_operation(
            schedule_id=schedule.id,
            action=OperationAction.RESUMED,
            actor_user_id=actor_user_id,
            at=resumed_at,
            changes={
                "status": {"from": "paused", "to": target.value},
                "next_run_recalculated": next_at is not None and not held_run_reused,
                "resume_mode": mode.value,
                "missed_scheduled_for": missed.isoformat() if missed else None,
                "replacement_scheduled_for": (
                    replacement_at.isoformat()
                    if mode is not ResumeMode.NEXT_REGULAR and replacement_at
                    else None
                ),
                "regular_local_time": schedule.local_time.strftime("%H:%M"),
                "pending_runs_skipped": int(missed is not None),
            },
        )
        configured_guild_id = configured_guild_id or self._configured_guild_id
        if (
            next_at is not None
            and target is ScheduleStatus.DRAFT
            and configured_guild_id == schedule.guild_id
        ):
            await NotificationPlanningService(
                self._session, configured_guild_id=schedule.guild_id
            ).plan_for_run(schedule=schedule, run=created_run, event_at=resumed_at)
        return ResumedSchedule(
            public_id=schedule.public_id,
            channel_id=schedule.channel_id,
            schedule_type=schedule_type,
            status=target,
            next_run_at=next_at,
            local_time=schedule.local_time,
            weekday=schedule.weekday,
            end_date=schedule.end_date,
            content=schedule.content,
            held_run_reused=held_run_reused,
            resume_mode=mode,
            missed_scheduled_for=missed,
            replacement_scheduled_for=(
                replacement_at if mode is not ResumeMode.NEXT_REGULAR else None
            ),
            next_regular_at=(
                recurring_next_run(
                    schedule_type=schedule_type,
                    local_time=schedule.local_time,
                    weekday=schedule.weekday,
                    end_date=schedule.end_date,
                    finalized_at=next_at,
                )
                if next_at is not None
                else None
            ),
        )

    async def _held_candidate(
        self, schedule: Schedule, runs: list[ScheduleRun], at: datetime
    ) -> ScheduleRun | None:
        future = [
            run for run in runs if run.status == RunStatus.PENDING.value and run.scheduled_for > at
        ]
        valid = [run for run in future if await self._is_pristine(run)]
        if len(valid) > 1:
            raise ScheduleStateChangeUnavailable
        if valid and valid[0].scheduled_for != schedule.next_run_at:
            raise ScheduleStateChangeUnavailable
        return valid[0] if valid else None

    async def _paused_held_run(
        self, schedule: Schedule, runs: list[ScheduleRun]
    ) -> ScheduleRun | None:
        pending = [run for run in runs if run.status == RunStatus.PENDING.value]
        valid = [run for run in pending if await self._is_pristine(run)]
        if len(pending) > 1 or len(valid) != len(pending):
            raise ScheduleStateChangeUnavailable
        return valid[0] if valid else None

    async def _is_pristine(self, run: ScheduleRun) -> bool:
        attempts = await self._runs.list_attempts(run_id=run.id)
        return (
            run.attempt_count == 0
            and run.next_attempt_at == run.scheduled_for
            and run.claimed_by is None
            and run.claimed_at is None
            and run.lease_expires_at is None
            and not attempts
        )

    async def _find(self, *, guild_id: int, public_id: str) -> Schedule:
        try:
            parsed = parse_public_id(public_id)
            return await self._schedules.get_by_public_id(guild_id=guild_id, public_id=parsed)
        except (RepositoryNotFoundError, ValueError) as error:
            raise ScheduleStateChangeUnavailable from error

    async def _lock_and_revalidate(self, snapshot: _TargetSnapshot) -> Schedule:
        try:
            schedule = await self._schedules.lock_by_id_for_deletion(snapshot.schedule_id)
        except RepositoryNotFoundError as error:
            raise ScheduleStateChangeUnavailable from error
        if (
            schedule.public_id != snapshot.public_id
            or schedule.guild_id != snapshot.guild_id
            or schedule.version != snapshot.version
            or schedule.next_run_at != snapshot.next_run_at
        ):
            raise ScheduleStateChangeUnavailable
        return schedule

    @staticmethod
    def _authorize(schedule: Schedule, *, actor_user_id: int, administrator: bool) -> None:
        if actor_user_id != schedule.creator_user_id and not administrator:
            raise ScheduleStateChangeUnavailable

    @staticmethod
    def _validate_runs(schedule: Schedule, runs: list[ScheduleRun]) -> None:
        if any(run.status == RunStatus.PROCESSING.value for run in runs):
            raise ScheduleStateChangeUnavailable
        if schedule.next_run_at is not None:
            current = [run for run in runs if run.scheduled_for == schedule.next_run_at]
            if len(current) != 1 or current[0].status != RunStatus.PENDING.value:
                raise ScheduleStateChangeUnavailable

    async def _add_operation(
        self,
        *,
        schedule_id: int,
        action: OperationAction,
        actor_user_id: int,
        at: datetime,
        changes: dict[str, object],
    ) -> None:
        await self._operations.add(
            OperationLog(
                schedule_id=schedule_id,
                action=action.value,
                actor_type=ActorType.USER.value,
                actor_user_id=actor_user_id,
                delete_kind=None,
                delete_reason=None,
                changes=changes,
                created_at=at,
            )
        )


def _snapshot(schedule: Schedule) -> _TargetSnapshot:
    return _TargetSnapshot(
        schedule_id=schedule.id,
        public_id=schedule.public_id,
        guild_id=schedule.guild_id,
        version=schedule.version,
        next_run_at=schedule.next_run_at,
    )
