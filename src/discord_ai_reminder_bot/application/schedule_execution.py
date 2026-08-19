"""Reflect terminal runs onto schedules and create one future recurring run."""

from dataclasses import dataclass
from datetime import date, datetime, time
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from discord_ai_reminder_bot.application.notification_planning import NotificationPlanningService
from discord_ai_reminder_bot.domain.enums import (
    ActorType,
    OperationAction,
    RunStatus,
    ScheduleStatus,
    ScheduleType,
)
from discord_ai_reminder_bot.domain.exceptions import InvalidStateTransitionError
from discord_ai_reminder_bot.domain.recurrence import (
    next_daily_run,
    next_weekly_run,
    require_utc,
)
from discord_ai_reminder_bot.infrastructure.database.exceptions import (
    RepositoryStateConflictError,
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

_TERMINAL_RUN_STATUSES = {
    RunStatus.SUCCEEDED,
    RunStatus.FAILED,
    RunStatus.SKIPPED,
}


class FinalizationResult(StrEnum):
    APPLIED = "applied"
    ALREADY_FINALIZED = "already_finalized"
    NO_ACTION = "no_action"


@dataclass(frozen=True)
class FinalizedScheduleRun:
    schedule: Schedule
    run: ScheduleRun
    next_run: ScheduleRun | None
    operation_log: OperationLog | None
    result: FinalizationResult


def once_target_status(run_status: RunStatus) -> ScheduleStatus:
    """Map a terminal one-time run result to its schedule result."""
    if run_status is RunStatus.SUCCEEDED:
        return ScheduleStatus.COMPLETED
    if run_status in {RunStatus.FAILED, RunStatus.SKIPPED}:
        return ScheduleStatus.FAILED
    raise InvalidStateTransitionError("only a terminal run can be finalized")


def recurring_next_run(
    *,
    schedule_type: ScheduleType,
    local_time: time | None,
    weekday: int | None,
    end_date: date | None,
    finalized_at: datetime,
) -> datetime | None:
    """Use the domain recurrence functions as the sole calculation rules."""
    finalized_at = require_utc(finalized_at)
    if local_time is None:
        raise InvalidStateTransitionError("recurring schedule requires local_time")
    if schedule_type is ScheduleType.DAILY:
        if weekday is not None:
            raise InvalidStateTransitionError("daily schedule must not have weekday")
        return next_daily_run(
            local_time=local_time,
            after=finalized_at,
            end_date=end_date,
        )
    if schedule_type is ScheduleType.WEEKLY:
        if weekday is None:
            raise InvalidStateTransitionError("weekly schedule requires weekday")
        return next_weekly_run(
            weekday=weekday,
            local_time=local_time,
            after=finalized_at,
            end_date=end_date,
        )
    raise InvalidStateTransitionError("one-time schedule has no recurring next run")


class ScheduleExecutionService:
    """Finalize one locked run in a caller-owned transaction."""

    def __init__(self, session: AsyncSession, *, configured_guild_id: int | None = None) -> None:
        self._session = session
        self._schedules = ScheduleRepository(session)
        self._runs = ScheduleRunRepository(session)
        self._operations = OperationLogRepository(session)
        self._configured_guild_id = configured_guild_id

    async def finalize_run(self, *, run_id: int, finalized_at: datetime) -> FinalizedScheduleRun:
        finalized_at = require_utc(finalized_at)
        run = await self._runs.lock_for_finalization(run_id=run_id)
        run_status = RunStatus(run.status)
        if run_status not in _TERMINAL_RUN_STATUSES:
            raise InvalidStateTransitionError("pending or processing run cannot be finalized")
        require_utc(run.scheduled_for)
        if run.finished_at is None:
            raise RepositoryStateConflictError("terminal run requires finished_at")
        require_utc(run.finished_at)

        schedule = await self._schedules.lock_by_id(run.schedule_id)
        if schedule.next_run_at is not None:
            require_utc(schedule.next_run_at)
        schedule_type = ScheduleType(schedule.schedule_type)
        if schedule_type is ScheduleType.ONCE:
            return await self._finalize_once(
                schedule=schedule,
                run=run,
                run_status=run_status,
                finalized_at=finalized_at,
            )
        return await self._finalize_recurring(
            schedule=schedule,
            run=run,
            finalized_at=finalized_at,
        )

    async def _finalize_once(
        self,
        *,
        schedule: Schedule,
        run: ScheduleRun,
        run_status: RunStatus,
        finalized_at: datetime,
    ) -> FinalizedScheduleRun:
        target = once_target_status(run_status)
        current = ScheduleStatus(schedule.status)
        if current is ScheduleStatus.DELETED:
            return FinalizedScheduleRun(schedule, run, None, None, FinalizationResult.NO_ACTION)
        if current is target:
            return FinalizedScheduleRun(
                schedule, run, None, None, FinalizationResult.ALREADY_FINALIZED
            )
        if current is ScheduleStatus.DRAFT and run_status is RunStatus.SKIPPED:
            return FinalizedScheduleRun(schedule, run, None, None, FinalizationResult.NO_ACTION)
        if current is not ScheduleStatus.ACTIVE:
            raise RepositoryStateConflictError(
                "one-time schedule state conflicts with the terminal run"
            )
        if schedule.next_run_at != run.scheduled_for:
            raise RepositoryStateConflictError(
                "one-time schedule does not point to the finalized run"
            )

        schedule.status = target.value
        schedule.next_run_at = None
        schedule.terminal_at = finalized_at if target is ScheduleStatus.COMPLETED else None
        schedule.updated_at = finalized_at
        schedule.version += 1
        await self._schedules.flush_execution_update(schedule)

        action = (
            OperationAction.COMPLETED
            if target is ScheduleStatus.COMPLETED
            else OperationAction.FAILED
        )
        operation = await self._add_system_operation(
            schedule_id=schedule.id,
            action=action,
            finalized_at=finalized_at,
        )
        return FinalizedScheduleRun(schedule, run, None, operation, FinalizationResult.APPLIED)

    async def _finalize_recurring(
        self,
        *,
        schedule: Schedule,
        run: ScheduleRun,
        finalized_at: datetime,
    ) -> FinalizedScheduleRun:
        current = ScheduleStatus(schedule.status)
        if current in {
            ScheduleStatus.PAUSED,
            ScheduleStatus.DELETED,
            ScheduleStatus.ENDED,
        }:
            return FinalizedScheduleRun(schedule, run, None, None, FinalizationResult.NO_ACTION)
        if current is ScheduleStatus.DRAFT and RunStatus(run.status) is not RunStatus.SKIPPED:
            raise RepositoryStateConflictError("only a skipped run can advance a recurring draft")
        if current not in {ScheduleStatus.ACTIVE, ScheduleStatus.DRAFT}:
            raise RepositoryStateConflictError(
                "recurring schedule state conflicts with the terminal run"
            )
        if current is ScheduleStatus.ACTIVE and schedule.content is None:
            raise RepositoryStateConflictError("active recurring schedule requires content")
        if current is ScheduleStatus.DRAFT and schedule.content is not None:
            raise RepositoryStateConflictError("draft recurring schedule must not have content")

        if schedule.next_run_at != run.scheduled_for:
            existing = await self._runs.get_first_after(
                schedule_id=schedule.id,
                scheduled_for=run.scheduled_for,
            )
            if existing is not None and schedule.next_run_at == existing.scheduled_for:
                return FinalizedScheduleRun(
                    schedule,
                    run,
                    existing,
                    None,
                    FinalizationResult.ALREADY_FINALIZED,
                )
            raise RepositoryStateConflictError("recurring schedule points to a conflicting run")

        next_at = recurring_next_run(
            schedule_type=ScheduleType(schedule.schedule_type),
            local_time=schedule.local_time,
            weekday=schedule.weekday,
            end_date=schedule.end_date,
            finalized_at=finalized_at,
        )
        if next_at is None:
            if current is ScheduleStatus.DRAFT:
                return FinalizedScheduleRun(
                    schedule,
                    run,
                    None,
                    None,
                    FinalizationResult.NO_ACTION,
                )
            schedule.status = ScheduleStatus.ENDED.value
            schedule.next_run_at = None
            schedule.terminal_at = finalized_at
            schedule.updated_at = finalized_at
            schedule.version += 1
            await self._schedules.flush_execution_update(schedule)
            operation = await self._add_system_operation(
                schedule_id=schedule.id,
                action=OperationAction.ENDED,
                finalized_at=finalized_at,
            )
            return FinalizedScheduleRun(schedule, run, None, operation, FinalizationResult.APPLIED)

        existing = await self._runs.get_by_schedule_and_time(
            schedule_id=schedule.id,
            scheduled_for=next_at,
        )
        if existing is not None:
            _validate_initial_next_run(existing, next_at)
            next_run = existing
        else:
            next_run = await self._runs.add(
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
                    updated_at=finalized_at,
                )
            )
        schedule.next_run_at = next_at
        schedule.updated_at = finalized_at
        schedule.version += 1
        await self._schedules.flush_execution_update(schedule)
        if current is ScheduleStatus.DRAFT and self._configured_guild_id == schedule.guild_id:
            await NotificationPlanningService(
                self._session, configured_guild_id=self._configured_guild_id
            ).plan_for_run(schedule=schedule, run=next_run, event_at=finalized_at)
        return FinalizedScheduleRun(schedule, run, next_run, None, FinalizationResult.APPLIED)

    async def _add_system_operation(
        self,
        *,
        schedule_id: int,
        action: OperationAction,
        finalized_at: datetime,
    ) -> OperationLog:
        return await self._operations.add(
            OperationLog(
                schedule_id=schedule_id,
                action=action.value,
                actor_type=ActorType.SYSTEM.value,
                actor_user_id=None,
                delete_kind=None,
                delete_reason=None,
                changes={"status": action.value},
                created_at=finalized_at,
            )
        )


def _validate_initial_next_run(run: ScheduleRun, scheduled_for: datetime) -> None:
    expected_nulls = (
        run.claimed_by,
        run.claimed_at,
        run.lease_expires_at,
        run.discord_message_id,
        run.result_code,
        run.error_summary,
        run.started_at,
        run.finished_at,
    )
    if (
        run.status != RunStatus.PENDING.value
        or run.attempt_count != 0
        or run.next_attempt_at != scheduled_for
        or any(value is not None for value in expected_nulls)
    ):
        raise RepositoryStateConflictError("existing next run has conflicting state")
