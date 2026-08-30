"""Bounded, Discord-independent Phase 1 retention cleanup."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from discord_ai_reminder_bot.domain.cleanup import retention_cutoff, validate_cleanup_batch_size
from discord_ai_reminder_bot.domain.clock import Clock
from discord_ai_reminder_bot.infrastructure.database.cleanup_repositories import (
    CleanupDeleteCounts,
    CleanupRepository,
)


@dataclass(frozen=True, slots=True)
class CleanupResult:
    cleanup_cutoff: datetime
    name_generation_jobs_deleted: int = 0
    schedules_deleted: int = 0
    global_notifications_deleted: int = 0
    notification_attempts_deleted: int = 0
    notification_logs_deleted: int = 0
    delivery_attempts_deleted: int = 0
    operation_logs_deleted: int = 0
    schedule_runs_deleted: int = 0
    internal_errors: int = 0
    schedules_remaining_due: int = 0
    global_notifications_remaining_due: int = 0
    incomplete: bool = False


@dataclass(slots=True)
class _MutableCounts:
    name_generation_jobs_deleted: int = 0
    schedules_deleted: int = 0
    global_notifications_deleted: int = 0
    notification_attempts_deleted: int = 0
    notification_logs_deleted: int = 0
    delivery_attempts_deleted: int = 0
    operation_logs_deleted: int = 0
    schedule_runs_deleted: int = 0
    internal_errors: int = 0

    def add(self, deleted: CleanupDeleteCounts, *, global_notification: bool = False) -> None:
        self.name_generation_jobs_deleted += deleted.name_generation_jobs
        self.schedules_deleted += deleted.schedules
        self.global_notifications_deleted += int(
            global_notification and deleted.notification_logs == 1
        )
        self.notification_attempts_deleted += deleted.notification_attempts
        self.notification_logs_deleted += deleted.notification_logs
        self.delivery_attempts_deleted += deleted.delivery_attempts
        self.operation_logs_deleted += deleted.operation_logs
        self.schedule_runs_deleted += deleted.schedule_runs


class _TargetCleanupError(Exception):
    """Carry only the failed target identity inside one cleanup cycle."""

    def __init__(self, target_id: int) -> None:
        super().__init__("cleanup target failed")
        self.target_id = target_id


class CleanupService:
    """Run one fixed-cutoff cleanup cycle using one transaction per target."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        clock: Clock,
        schedule_batch_size: int = 100,
        global_notification_batch_size: int = 100,
    ) -> None:
        self._sessions = session_factory
        self._clock = clock
        self._schedule_batch_size = validate_cleanup_batch_size(schedule_batch_size)
        self._global_batch_size = validate_cleanup_batch_size(global_notification_batch_size)

    async def run_cycle(self) -> CleanupResult:
        cleanup_cutoff = self._clock.now()
        cutoff = retention_cutoff(cleanup_cutoff)
        counts = _MutableCounts()
        failed_schedule_ids: set[int] = set()
        failed_notification_ids: set[int] = set()

        for _ in range(self._schedule_batch_size):
            try:
                deleted, target_id = await self._delete_one_schedule(
                    cutoff=cutoff, excluded_ids=frozenset(failed_schedule_ids)
                )
            except asyncio.CancelledError:
                raise
            except _TargetCleanupError as error:
                counts.internal_errors += 1
                failed_schedule_ids.add(error.target_id)
                continue
            except Exception:  # noqa: BLE001 - target data and DB details must stay private
                counts.internal_errors += 1
                break
                continue
            if target_id is None:
                break
            if deleted is None:
                failed_schedule_ids.add(target_id)
                counts.internal_errors += 1
                continue
            counts.add(deleted)

        for _ in range(self._global_batch_size):
            try:
                deleted, target_id = await self._delete_one_global_notification(
                    cutoff=cutoff, excluded_ids=frozenset(failed_notification_ids)
                )
            except asyncio.CancelledError:
                raise
            except _TargetCleanupError as error:
                counts.internal_errors += 1
                failed_notification_ids.add(error.target_id)
                continue
            except Exception:  # noqa: BLE001 - target data and DB details must stay private
                counts.internal_errors += 1
                break
                continue
            if target_id is None:
                break
            if deleted is None:
                failed_notification_ids.add(target_id)
                counts.internal_errors += 1
                continue
            counts.add(deleted, global_notification=True)

        schedules_remaining, globals_remaining = await self._count_remaining(cutoff=cutoff)
        incomplete = bool(counts.internal_errors or schedules_remaining or globals_remaining)
        return CleanupResult(
            cleanup_cutoff=cleanup_cutoff,
            name_generation_jobs_deleted=counts.name_generation_jobs_deleted,
            schedules_deleted=counts.schedules_deleted,
            global_notifications_deleted=counts.global_notifications_deleted,
            notification_attempts_deleted=counts.notification_attempts_deleted,
            notification_logs_deleted=counts.notification_logs_deleted,
            delivery_attempts_deleted=counts.delivery_attempts_deleted,
            operation_logs_deleted=counts.operation_logs_deleted,
            schedule_runs_deleted=counts.schedule_runs_deleted,
            internal_errors=counts.internal_errors,
            schedules_remaining_due=schedules_remaining,
            global_notifications_remaining_due=globals_remaining,
            incomplete=incomplete,
        )

    async def _delete_one_schedule(
        self, *, cutoff: datetime, excluded_ids: frozenset[int]
    ) -> tuple[CleanupDeleteCounts | None, int | None]:
        target_id: int | None = None
        async with self._sessions() as session, session.begin():
            repository = CleanupRepository(session)
            await repository.set_local_lock_timeout()
            schedule = await repository.lock_next_schedule(
                retention_cutoff=cutoff, excluded_ids=excluded_ids
            )
            if schedule is None:
                return None, None
            target_id = schedule.id
            if not await repository.schedule_is_still_deletable(
                schedule=schedule, retention_cutoff=cutoff
            ):
                return None, target_id
            try:
                return await repository.delete_schedule(
                    schedule=schedule, retention_cutoff=cutoff
                ), target_id
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - force rollback without leaking DB details
                raise _TargetCleanupError(target_id) from None

    async def _delete_one_global_notification(
        self, *, cutoff: datetime, excluded_ids: frozenset[int]
    ) -> tuple[CleanupDeleteCounts | None, int | None]:
        target_id: int | None = None
        async with self._sessions() as session, session.begin():
            repository = CleanupRepository(session)
            await repository.set_local_lock_timeout()
            notification = await repository.lock_next_global_notification(
                retention_cutoff=cutoff, excluded_ids=excluded_ids
            )
            if notification is None:
                return None, None
            target_id = notification.id
            if not await repository.global_notification_is_still_deletable(
                notification=notification, retention_cutoff=cutoff
            ):
                return None, target_id
            try:
                return await repository.delete_global_notification(
                    notification=notification
                ), target_id
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - force rollback without leaking DB details
                raise _TargetCleanupError(target_id) from None

    async def _count_remaining(self, *, cutoff: datetime) -> tuple[int, int]:
        async with self._sessions() as session, session.begin():
            repository = CleanupRepository(session)
            return (
                await repository.count_due_schedules(retention_cutoff=cutoff),
                await repository.count_due_global_notifications(retention_cutoff=cutoff),
            )
