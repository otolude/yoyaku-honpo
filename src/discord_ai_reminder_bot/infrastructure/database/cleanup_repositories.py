"""PostgreSQL persistence boundary for bounded physical cleanup."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, exists, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from discord_ai_reminder_bot.domain.cleanup import is_global_notification_due, is_schedule_due
from discord_ai_reminder_bot.domain.enums import (
    DeliveryAttemptStatus,
    NotificationAttemptStatus,
    NotificationStatus,
    RunStatus,
    ScheduleStatus,
)
from discord_ai_reminder_bot.domain.recurrence import require_utc
from discord_ai_reminder_bot.infrastructure.database.models import (
    DeliveryAttempt,
    NotificationAttempt,
    NotificationLog,
    OperationLog,
    Schedule,
    ScheduleRun,
)

LOCK_TIMEOUT_SQL = "SET LOCAL lock_timeout = '1s'"
_SCHEDULE_STATUSES = tuple(
    status.value
    for status in (ScheduleStatus.COMPLETED, ScheduleStatus.ENDED, ScheduleStatus.DELETED)
)
_TERMINAL_NOTIFICATION_STATUSES = tuple(
    status.value
    for status in (
        NotificationStatus.SUCCEEDED,
        NotificationStatus.FAILED,
        NotificationStatus.UNKNOWN,
        NotificationStatus.CANCELLED,
    )
)


@dataclass(frozen=True, slots=True)
class CleanupDeleteCounts:
    notification_attempts: int = 0
    notification_logs: int = 0
    delivery_attempts: int = 0
    operation_logs: int = 0
    schedule_runs: int = 0
    schedules: int = 0


class CleanupRepository:
    """Delete one fully locked target without owning its transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def set_local_lock_timeout(self) -> None:
        await self._session.execute(text(LOCK_TIMEOUT_SQL))

    async def lock_next_schedule(
        self, *, retention_cutoff: datetime, excluded_ids: frozenset[int] = frozenset()
    ) -> Schedule | None:
        retention_cutoff = require_utc(retention_cutoff)
        statement = (
            select(Schedule)
            .where(
                Schedule.status.in_(_SCHEDULE_STATUSES),
                Schedule.terminal_at.is_not(None),
                Schedule.terminal_at <= retention_cutoff,
                ~self._schedule_has_in_flight_run(),
                ~self._schedule_has_in_flight_delivery(),
                ~self._schedule_has_in_flight_notification(),
                ~self._schedule_has_in_flight_notification_attempt(),
            )
            .order_by(Schedule.terminal_at.asc(), Schedule.id.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if excluded_ids:
            statement = statement.where(Schedule.id.not_in(excluded_ids))
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def schedule_is_still_deletable(
        self, *, schedule: Schedule, retention_cutoff: datetime
    ) -> bool:
        if not is_schedule_due(
            status=schedule.status,
            terminal_at=schedule.terminal_at,
            cutoff=retention_cutoff,
        ):
            return False
        blockers = await self._session.scalar(
            select(
                or_(
                    self._schedule_has_in_flight_run(),
                    self._schedule_has_in_flight_delivery(),
                    self._schedule_has_in_flight_notification(),
                    self._schedule_has_in_flight_notification_attempt(),
                )
            ).where(Schedule.id == schedule.id)
        )
        return blockers is False

    async def delete_schedule(self, *, schedule: Schedule) -> CleanupDeleteCounts:
        """Delete all RESTRICT children in the one mandated order."""
        run_ids = select(ScheduleRun.id).where(ScheduleRun.schedule_id == schedule.id)
        notification_ids = select(NotificationLog.id).where(
            or_(
                NotificationLog.schedule_id == schedule.id,
                NotificationLog.schedule_run_id.in_(run_ids),
            )
        )
        notification_attempts = await self._delete(
            delete(NotificationAttempt).where(
                NotificationAttempt.notification_log_id.in_(notification_ids)
            )
        )
        notification_logs = await self._delete(
            delete(NotificationLog).where(NotificationLog.id.in_(notification_ids))
        )
        delivery_attempts = await self._delete(
            delete(DeliveryAttempt).where(DeliveryAttempt.schedule_run_id.in_(run_ids))
        )
        operation_logs = await self._delete(
            delete(OperationLog).where(OperationLog.schedule_id == schedule.id)
        )
        schedule_runs = await self._delete(
            delete(ScheduleRun).where(ScheduleRun.schedule_id == schedule.id)
        )
        schedules = await self._delete(delete(Schedule).where(Schedule.id == schedule.id))
        await self._session.flush()
        return CleanupDeleteCounts(
            notification_attempts=notification_attempts,
            notification_logs=notification_logs,
            delivery_attempts=delivery_attempts,
            operation_logs=operation_logs,
            schedule_runs=schedule_runs,
            schedules=schedules,
        )

    async def lock_next_global_notification(
        self, *, retention_cutoff: datetime, excluded_ids: frozenset[int] = frozenset()
    ) -> NotificationLog | None:
        retention_cutoff = require_utc(retention_cutoff)
        statement = (
            select(NotificationLog)
            .where(
                NotificationLog.schedule_id.is_(None),
                NotificationLog.schedule_run_id.is_(None),
                NotificationLog.status.in_(_TERMINAL_NOTIFICATION_STATUSES),
                NotificationLog.finished_at.is_not(None),
                NotificationLog.finished_at <= retention_cutoff,
                ~exists().where(
                    NotificationAttempt.notification_log_id == NotificationLog.id,
                    NotificationAttempt.status.in_(
                        (
                            NotificationAttemptStatus.CLAIMED.value,
                            NotificationAttemptStatus.SENDING.value,
                        )
                    ),
                ),
            )
            .order_by(NotificationLog.finished_at.asc(), NotificationLog.id.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if excluded_ids:
            statement = statement.where(NotificationLog.id.not_in(excluded_ids))
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def global_notification_is_still_deletable(
        self, *, notification: NotificationLog, retention_cutoff: datetime
    ) -> bool:
        if not is_global_notification_due(
            status=notification.status,
            schedule_id=notification.schedule_id,
            schedule_run_id=notification.schedule_run_id,
            finished_at=notification.finished_at,
            cutoff=retention_cutoff,
        ):
            return False
        blocker = await self._session.scalar(
            select(
                exists().where(
                    NotificationAttempt.notification_log_id == notification.id,
                    NotificationAttempt.status.in_(
                        (
                            NotificationAttemptStatus.CLAIMED.value,
                            NotificationAttemptStatus.SENDING.value,
                        )
                    ),
                )
            )
        )
        return blocker is False

    async def delete_global_notification(
        self, *, notification: NotificationLog
    ) -> CleanupDeleteCounts:
        attempts = await self._delete(
            delete(NotificationAttempt).where(
                NotificationAttempt.notification_log_id == notification.id
            )
        )
        logs = await self._delete(
            delete(NotificationLog).where(NotificationLog.id == notification.id)
        )
        await self._session.flush()
        return CleanupDeleteCounts(notification_attempts=attempts, notification_logs=logs)

    async def count_due_schedules(self, *, retention_cutoff: datetime) -> int:
        statement = select(func.count(Schedule.id)).where(
            Schedule.status.in_(_SCHEDULE_STATUSES),
            Schedule.terminal_at.is_not(None),
            Schedule.terminal_at <= require_utc(retention_cutoff),
            ~self._schedule_has_in_flight_run(),
            ~self._schedule_has_in_flight_delivery(),
            ~self._schedule_has_in_flight_notification(),
            ~self._schedule_has_in_flight_notification_attempt(),
        )
        return int(await self._session.scalar(statement) or 0)

    async def count_due_global_notifications(self, *, retention_cutoff: datetime) -> int:
        statement = select(func.count(NotificationLog.id)).where(
            NotificationLog.schedule_id.is_(None),
            NotificationLog.schedule_run_id.is_(None),
            NotificationLog.status.in_(_TERMINAL_NOTIFICATION_STATUSES),
            NotificationLog.finished_at.is_not(None),
            NotificationLog.finished_at <= require_utc(retention_cutoff),
            ~exists().where(
                NotificationAttempt.notification_log_id == NotificationLog.id,
                NotificationAttempt.status.in_(
                    (
                        NotificationAttemptStatus.CLAIMED.value,
                        NotificationAttemptStatus.SENDING.value,
                    )
                ),
            ),
        )
        return int(await self._session.scalar(statement) or 0)

    @staticmethod
    async def _rowcount(result) -> int:
        return int(result.rowcount or 0)

    async def _delete(self, statement) -> int:
        return await self._rowcount(await self._session.execute(statement))

    @staticmethod
    def _schedule_has_in_flight_run():
        return exists().where(
            ScheduleRun.schedule_id == Schedule.id,
            ScheduleRun.status.in_((RunStatus.PENDING.value, RunStatus.PROCESSING.value)),
        )

    @staticmethod
    def _schedule_has_in_flight_delivery():
        return exists().where(
            ScheduleRun.schedule_id == Schedule.id,
            DeliveryAttempt.schedule_run_id == ScheduleRun.id,
            DeliveryAttempt.status.in_(
                (DeliveryAttemptStatus.CLAIMED.value, DeliveryAttemptStatus.SENDING.value)
            ),
        )

    @staticmethod
    def _schedule_has_in_flight_notification():
        run_for_schedule = exists().where(
            ScheduleRun.id == NotificationLog.schedule_run_id,
            ScheduleRun.schedule_id == Schedule.id,
        )
        return exists().where(
            or_(NotificationLog.schedule_id == Schedule.id, run_for_schedule),
            NotificationLog.status.in_(
                (NotificationStatus.PENDING.value, NotificationStatus.PROCESSING.value)
            ),
        )

    @staticmethod
    def _schedule_has_in_flight_notification_attempt():
        run_for_schedule = exists().where(
            ScheduleRun.id == NotificationLog.schedule_run_id,
            ScheduleRun.schedule_id == Schedule.id,
        )
        return exists().where(
            NotificationAttempt.notification_log_id == NotificationLog.id,
            or_(NotificationLog.schedule_id == Schedule.id, run_for_schedule),
            NotificationAttempt.status.in_(
                (
                    NotificationAttemptStatus.CLAIMED.value,
                    NotificationAttemptStatus.SENDING.value,
                )
            ),
        )
