"""Basic async repositories without transaction ownership."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Select, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from discord_ai_reminder_bot.domain.enums import DeliveryAttemptStatus, RunStatus, ScheduleStatus
from discord_ai_reminder_bot.domain.recurrence import require_utc
from discord_ai_reminder_bot.infrastructure.database.exceptions import (
    DuplicateRecordError,
    OptimisticLockError,
    RepositoryNotFoundError,
)
from discord_ai_reminder_bot.infrastructure.database.models import (
    DeliveryAttempt,
    OperationLog,
    Schedule,
    ScheduleRun,
)

MAX_LIST_LIMIT = 100
MAX_CLAIM_BATCH_SIZE = 20
_UPDATABLE_SCHEDULE_FIELDS = {
    "channel_id",
    "schedule_type",
    "status",
    "content",
    "next_run_at",
    "local_time",
    "weekday",
    "end_date",
    "updated_at",
    "deleted_at",
    "terminal_at",
}


def _validate_limit(limit: int) -> None:
    if not 1 <= limit <= MAX_LIST_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIST_LIMIT}")


@dataclass(frozen=True)
class ClaimedScheduleRun:
    """A locked run and the delivery attempt created for its current claim."""

    run: ScheduleRun
    attempt: DeliveryAttempt


def build_due_runs_claim_statement(*, now: datetime, batch_size: int) -> Select[tuple[ScheduleRun]]:
    """Build the PostgreSQL-only due-run locking statement."""
    now = require_utc(now)
    if isinstance(batch_size, bool) or not 1 <= batch_size <= MAX_CLAIM_BATCH_SIZE:
        raise ValueError(f"batch_size must be between 1 and {MAX_CLAIM_BATCH_SIZE}")
    return (
        select(ScheduleRun)
        .where(
            ScheduleRun.status == RunStatus.PENDING.value,
            ScheduleRun.scheduled_for <= now,
            ScheduleRun.next_attempt_at <= now,
            ScheduleRun.attempt_count < 4,
        )
        .order_by(
            ScheduleRun.next_attempt_at.asc(),
            ScheduleRun.scheduled_for.asc(),
            ScheduleRun.id.asc(),
        )
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )


class ScheduleRepository:
    """Read and write schedules while leaving commit and rollback to the caller."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, schedule: Schedule) -> Schedule:
        self._session.add(schedule)
        try:
            await self._session.flush()
        except IntegrityError as error:
            raise DuplicateRecordError("schedule violates a uniqueness constraint") from error
        return schedule

    async def get_by_public_id(self, *, guild_id: int, public_id: uuid.UUID) -> Schedule:
        statement = select(Schedule).where(
            Schedule.guild_id == guild_id,
            Schedule.public_id == public_id,
        )
        schedule = (await self._session.execute(statement)).scalar_one_or_none()
        if schedule is None:
            raise RepositoryNotFoundError("schedule was not found")
        return schedule

    async def get_by_id(self, schedule_id: int) -> Schedule:
        """Return a row for internal application use; never expose this ID externally."""
        schedule = await self._session.get(Schedule, schedule_id)
        if schedule is None:
            raise RepositoryNotFoundError("schedule was not found")
        return schedule

    async def list_by_creator(
        self,
        *,
        guild_id: int,
        creator_user_id: int,
        status: ScheduleStatus | None = None,
        limit: int = 10,
    ) -> list[Schedule]:
        statement = select(Schedule).where(
            Schedule.guild_id == guild_id,
            Schedule.creator_user_id == creator_user_id,
        )
        return await self._list(statement, status=status, limit=limit)

    async def list_by_guild(
        self,
        *,
        guild_id: int,
        status: ScheduleStatus | None = None,
        limit: int = 10,
    ) -> list[Schedule]:
        statement = select(Schedule).where(Schedule.guild_id == guild_id)
        return await self._list(statement, status=status, limit=limit)

    async def _list(
        self, statement, *, status: ScheduleStatus | None, limit: int
    ) -> list[Schedule]:
        _validate_limit(limit)
        if status is not None:
            statement = statement.where(Schedule.status == status.value)
        statement = statement.order_by(Schedule.next_run_at.asc().nulls_last(), Schedule.id.asc())
        result = await self._session.execute(statement.limit(limit))
        return list(result.scalars())

    async def update_with_version(
        self,
        *,
        guild_id: int,
        schedule_id: int,
        expected_version: int,
        changes: Mapping[str, Any],
    ) -> Schedule:
        unknown_fields = set(changes) - _UPDATABLE_SCHEDULE_FIELDS
        if unknown_fields:
            raise ValueError(f"unsupported schedule fields: {', '.join(sorted(unknown_fields))}")
        if not changes:
            raise ValueError("at least one schedule field must change")

        statement = (
            update(Schedule)
            .where(
                Schedule.id == schedule_id,
                Schedule.guild_id == guild_id,
                Schedule.version == expected_version,
            )
            .values(**changes, version=Schedule.version + 1)
            .returning(Schedule)
            .execution_options(synchronize_session=False, populate_existing=True)
        )
        schedule = (await self._session.execute(statement)).scalar_one_or_none()
        if schedule is not None:
            return schedule

        exists = await self._session.scalar(
            select(Schedule.id).where(
                Schedule.id == schedule_id,
                Schedule.guild_id == guild_id,
            )
        )
        if exists is None:
            raise RepositoryNotFoundError("schedule was not found")
        raise OptimisticLockError("schedule version has changed")


class ScheduleRunRepository:
    """Store and list occurrence history without worker claiming behavior."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, run: ScheduleRun) -> ScheduleRun:
        self._session.add(run)
        try:
            await self._session.flush()
        except IntegrityError as error:
            raise DuplicateRecordError("schedule run already exists") from error
        return run

    async def list_by_schedule(self, *, schedule_id: int, limit: int = 100) -> list[ScheduleRun]:
        _validate_limit(limit)
        statement = (
            select(ScheduleRun)
            .where(ScheduleRun.schedule_id == schedule_id)
            .order_by(ScheduleRun.scheduled_for.desc(), ScheduleRun.id.desc())
            .limit(limit)
        )
        return list((await self._session.execute(statement)).scalars())

    async def claim_due(
        self,
        *,
        now: datetime,
        worker_id: uuid.UUID,
        batch_size: int,
        lease_timeout: timedelta,
    ) -> list[ClaimedScheduleRun]:
        """Lock and claim due runs without committing the caller's transaction."""
        now = require_utc(now)
        if not isinstance(worker_id, uuid.UUID):
            raise TypeError("worker_id must be a UUID")
        if lease_timeout <= timedelta(0):
            raise ValueError("lease_timeout must be positive")

        statement = build_due_runs_claim_statement(now=now, batch_size=batch_size)
        runs = list((await self._session.execute(statement)).scalars())
        claimed: list[ClaimedScheduleRun] = []
        for run in runs:
            run.status = RunStatus.PROCESSING.value
            run.attempt_count += 1
            run.next_attempt_at = None
            run.claimed_by = worker_id
            run.claimed_at = now
            run.lease_expires_at = now + lease_timeout
            run.started_at = run.started_at or now
            run.updated_at = now
            run.finished_at = None

            attempt = DeliveryAttempt(
                schedule_run_id=run.id,
                attempt_number=run.attempt_count,
                status=DeliveryAttemptStatus.CLAIMED.value,
                claimed_by=worker_id,
                claimed_at=now,
                send_started_at=None,
                finished_at=None,
                discord_message_id=None,
                error_kind=None,
                error_code=None,
                error_summary=None,
            )
            self._session.add(attempt)
            claimed.append(ClaimedScheduleRun(run=run, attempt=attempt))

        await self._session.flush()
        return claimed


class OperationLogRepository:
    """Store and list immutable schedule operation history."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, operation: OperationLog) -> OperationLog:
        self._session.add(operation)
        await self._session.flush()
        return operation

    async def list_by_schedule(self, *, schedule_id: int, limit: int = 100) -> list[OperationLog]:
        _validate_limit(limit)
        statement = (
            select(OperationLog)
            .where(OperationLog.schedule_id == schedule_id)
            .order_by(OperationLog.created_at.desc(), OperationLog.id.desc())
            .limit(limit)
        )
        return list((await self._session.execute(statement)).scalars())
