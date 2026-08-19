"""Recover overdue pending runs without contacting Discord."""

from dataclasses import dataclass, fields
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from discord_ai_reminder_bot.domain.enums import (
    ActorType,
    DeliveryAttemptStatus,
    OperationAction,
    RunStatus,
    ScheduleStatus,
    ScheduleType,
)
from discord_ai_reminder_bot.domain.recurrence import next_daily_run, next_weekly_run, require_utc
from discord_ai_reminder_bot.infrastructure.database.models import (
    DeliveryAttempt,
    OperationLog,
    Schedule,
    ScheduleRun,
)
from discord_ai_reminder_bot.infrastructure.database.repositories import (
    OperationLogRepository,
    ScheduleRepository,
    ScheduleRunRepository,
)

STARTUP_OVERDUE = "startup_overdue"
STARTUP_INCONSISTENT = "startup_inconsistent_pending"
DRAFT_WITHOUT_CONTENT = "draft_without_content"
STARTUP_OVERDUE_SUMMARY = "One-time occurrence exceeded the startup recovery grace period"
STARTUP_INCONSISTENT_SUMMARY = "Pending occurrence state is unsafe for automatic delivery"
DRAFT_WITHOUT_CONTENT_SUMMARY = "Draft occurrence has no content for delivery"
RECURRING_MISSED_SUMMARY = "Recurring occurrence passed while the bot was unavailable"
MAX_MISSED_OCCURRENCES_PER_SCHEDULE = 500


class RecurringRecoveryLimitError(RuntimeError):
    """A schedule exceeded the missed-occurrence safety limit."""


@dataclass
class PendingRecoverySummary:
    selected: int = 0
    initial_pending_preserved: int = 0
    retry_pending_preserved: int = 0
    runs_skipped: int = 0
    runs_failed: int = 0
    once_schedules_failed: int = 0
    future_runs_created: int = 0
    schedules_ended: int = 0
    inconsistencies_detected: int = 0

    def add(self, other: PendingRecoverySummary) -> None:
        for item in fields(self):
            setattr(self, item.name, getattr(self, item.name) + getattr(other, item.name))


class PendingStartupRecoveryService:
    """Normalize one locked batch in the caller-owned transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._runs = ScheduleRunRepository(session)
        self._schedules = ScheduleRepository(session)
        self._operations = OperationLogRepository(session)

    async def recover_pending(
        self, *, recovery_cutoff: datetime, batch_size: int
    ) -> PendingRecoverySummary:
        recovery_cutoff = require_utc(recovery_cutoff)
        selected = await self._runs.lock_startup_pending(
            recovered_at=recovery_cutoff, batch_size=batch_size
        )
        result = PendingRecoverySummary(selected=len(selected))
        handled: set[int] = set()
        for selected_run in selected:
            if selected_run.schedule_id in handled:
                continue
            runs = await self._runs.list_all_by_schedule(
                schedule_id=selected_run.schedule_id, lock=True
            )
            schedule = await self._schedules.lock_by_id(selected_run.schedule_id)
            handled.add(schedule.id)
            await self._recover_one(schedule, selected_run, runs, recovery_cutoff, result)
        if len(selected) < batch_size:
            initial, retry = await self._runs.count_startup_preserved(recovered_at=recovery_cutoff)
            result.initial_pending_preserved = initial
            result.retry_pending_preserved = retry
        return result

    async def _recover_one(
        self,
        schedule: Schedule,
        run: ScheduleRun,
        runs: list[ScheduleRun],
        now: datetime,
        result: PendingRecoverySummary,
    ) -> None:
        status = ScheduleStatus(schedule.status)
        attempts = await self._runs.list_attempts(run_id=run.id)
        if status in {
            ScheduleStatus.PAUSED,
            ScheduleStatus.DELETED,
            ScheduleStatus.ENDED,
            ScheduleStatus.COMPLETED,
            ScheduleStatus.FAILED,
        }:
            self._finish(
                run,
                RunStatus.SKIPPED,
                f"schedule_{status.value}",
                "Schedule is not eligible for automatic delivery",
                now,
            )
            result.runs_skipped += 1
            return
        if self._inconsistent(schedule, run, attempts):
            await self._fail_inconsistent(schedule, run, now, result)
            return
        if 1 <= run.attempt_count <= 3:
            result.retry_pending_preserved += 1
            return
        if schedule.schedule_type == ScheduleType.ONCE.value:
            if status is ScheduleStatus.DRAFT:
                self._finish(
                    run,
                    RunStatus.SKIPPED,
                    DRAFT_WITHOUT_CONTENT,
                    DRAFT_WITHOUT_CONTENT_SUMMARY,
                    now,
                )
                result.runs_skipped += 1
            elif now - run.scheduled_for <= timedelta(minutes=15):
                result.initial_pending_preserved += 1
            else:
                self._finish(run, RunStatus.SKIPPED, STARTUP_OVERDUE, STARTUP_OVERDUE_SUMMARY, now)
                result.runs_skipped += 1
                await self._fail_once(schedule, now, result, skipped=True)
            return
        await self._recover_recurring(schedule, runs, now, result)

    @staticmethod
    def _inconsistent(
        schedule: Schedule, run: ScheduleRun, attempts: list[DeliveryAttempt]
    ) -> bool:
        if run.attempt_count == 4:
            return True
        if run.attempt_count == 0:
            attempts_valid = not attempts
        else:
            attempts_valid = (
                len(attempts) == run.attempt_count
                and [a.attempt_number for a in attempts] == list(range(1, run.attempt_count + 1))
                and all(a.status == DeliveryAttemptStatus.FAILED.value for a in attempts)
            )
        return (
            not attempts_valid
            or (schedule.status == ScheduleStatus.ACTIVE.value and schedule.content is None)
            or (schedule.status == ScheduleStatus.DRAFT.value and schedule.content is not None)
            or schedule.next_run_at != run.scheduled_for
        )

    async def _fail_inconsistent(
        self, schedule: Schedule, run: ScheduleRun, now: datetime, result: PendingRecoverySummary
    ) -> None:
        self._finish(run, RunStatus.FAILED, STARTUP_INCONSISTENT, STARTUP_INCONSISTENT_SUMMARY, now)
        result.runs_failed += 1
        result.inconsistencies_detected += 1
        if (
            schedule.schedule_type == ScheduleType.ONCE.value
            and schedule.status == ScheduleStatus.ACTIVE.value
        ):
            await self._fail_once(schedule, now, result, skipped=False)

    async def _fail_once(
        self, schedule: Schedule, now: datetime, result: PendingRecoverySummary, *, skipped: bool
    ) -> None:
        schedule.status = ScheduleStatus.FAILED.value
        schedule.next_run_at = None
        schedule.terminal_at = None
        schedule.updated_at = now
        schedule.version += 1
        await self._schedules.flush_execution_update(schedule)
        await self._operation(
            schedule.id,
            OperationAction.FAILED,
            now,
            {
                "status_before": "active",
                "status_after": "failed",
                "skipped_count": int(skipped),
                "startup_recovery": True,
            },
        )
        result.once_schedules_failed += 1

    async def _recover_recurring(
        self,
        schedule: Schedule,
        runs: list[ScheduleRun],
        now: datetime,
        result: PendingRecoverySummary,
    ) -> None:
        by_time = {item.scheduled_for: item for item in runs}
        candidate = schedule.next_run_at
        assert candidate is not None
        count = 0
        while candidate <= now:
            count += 1
            if count > MAX_MISSED_OCCURRENCES_PER_SCHEDULE:
                raise RecurringRecoveryLimitError(
                    "Recurring startup recovery exceeded its occurrence safety limit"
                )
            existing = by_time.get(candidate)
            if existing is None:
                existing = ScheduleRun(
                    schedule_id=schedule.id,
                    scheduled_for=candidate,
                    status=RunStatus.SKIPPED.value,
                    attempt_count=0,
                    next_attempt_at=None,
                    result_code="startup_recurring_missed",
                    error_summary=RECURRING_MISSED_SUMMARY,
                    finished_at=now,
                    updated_at=now,
                )
                await self._runs.add(existing)
                by_time[candidate] = existing
                result.runs_skipped += 1
            elif existing.status == RunStatus.PENDING.value:
                self._finish(
                    existing,
                    RunStatus.SKIPPED,
                    "startup_recurring_missed",
                    RECURRING_MISSED_SUMMARY,
                    now,
                )
                result.runs_skipped += 1
            candidate = self._next(schedule, candidate)
            if candidate is None:
                break
        while candidate is not None and candidate in by_time:
            candidate = self._next(schedule, candidate)
        if candidate is None:
            if schedule.status == ScheduleStatus.ACTIVE.value:
                schedule.status = ScheduleStatus.ENDED.value
                schedule.next_run_at = None
                schedule.terminal_at = now
                schedule.updated_at = now
                schedule.version += 1
                await self._schedules.flush_execution_update(schedule)
                await self._operation(
                    schedule.id,
                    OperationAction.ENDED,
                    now,
                    {
                        "status_before": "active",
                        "status_after": "ended",
                        "startup_recovery": True,
                    },
                )
                result.schedules_ended += 1
            return
        await self._runs.add(
            ScheduleRun(
                schedule_id=schedule.id,
                scheduled_for=candidate,
                status=RunStatus.PENDING.value,
                attempt_count=0,
                next_attempt_at=candidate,
                updated_at=now,
            )
        )
        schedule.next_run_at = candidate
        schedule.updated_at = now
        schedule.version += 1
        await self._schedules.flush_execution_update(schedule)
        result.future_runs_created += 1

    @staticmethod
    def _next(schedule: Schedule, after: datetime) -> datetime | None:
        assert schedule.local_time is not None
        if schedule.schedule_type == ScheduleType.DAILY.value:
            return next_daily_run(
                local_time=schedule.local_time, after=after, end_date=schedule.end_date
            )
        assert schedule.weekday is not None
        return next_weekly_run(
            weekday=schedule.weekday,
            local_time=schedule.local_time,
            after=after,
            end_date=schedule.end_date,
        )

    @staticmethod
    def _finish(
        run: ScheduleRun, status: RunStatus, code: str, summary: str, now: datetime
    ) -> None:
        run.status = status.value
        run.next_attempt_at = None
        run.claimed_by = run.claimed_at = run.lease_expires_at = None
        run.discord_message_id = None
        run.result_code = code
        run.error_summary = summary
        run.finished_at = run.updated_at = now

    async def _operation(
        self, schedule_id: int, action: OperationAction, now: datetime, changes: dict[str, object]
    ) -> None:
        await self._operations.add(
            OperationLog(
                schedule_id=schedule_id,
                action=action.value,
                actor_type=ActorType.SYSTEM.value,
                actor_user_id=None,
                delete_kind=None,
                delete_reason=None,
                changes=changes,
                created_at=now,
            )
        )
