"""Atomically pause and resume recurring schedules."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time

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


class ScheduleStateChangeUnavailable(Exception):
    """The target is absent, unauthorized, conflicting, or ineligible."""


@dataclass(frozen=True)
class PausedSchedule:
    public_id: uuid.UUID
    channel_id: int
    schedule_type: ScheduleType
    previous_status: ScheduleStatus
    pending_runs_skipped: int


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
        pending_count = sum(run.status == RunStatus.PENDING.value for run in runs)
        await self._runs.skip_pending_for_paused_schedule(runs=runs, paused_at=paused_at)
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
            },
        )
        return PausedSchedule(
            public_id=schedule.public_id,
            channel_id=schedule.channel_id,
            schedule_type=ScheduleType(schedule.schedule_type),
            previous_status=ScheduleStatus.ACTIVE,
            pending_runs_skipped=pending_count,
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
        if schedule.local_time is None:
            raise ScheduleStateChangeUnavailable
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

        if next_at is None:
            if schedule.content is None:
                raise ScheduleStateChangeUnavailable
            target = ScheduleStatus.ENDED
            schedule.next_run_at = None
            schedule.terminal_at = resumed_at
        else:
            target = ScheduleStatus.ACTIVE if schedule.content is not None else ScheduleStatus.DRAFT
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
                "next_run_recalculated": next_at is not None,
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
