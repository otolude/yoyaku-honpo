"""Basic async repositories without transaction ownership."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from discord_ai_reminder_bot.domain.enums import ScheduleStatus
from discord_ai_reminder_bot.infrastructure.database.exceptions import (
    DuplicateRecordError,
    OptimisticLockError,
    RepositoryNotFoundError,
)
from discord_ai_reminder_bot.infrastructure.database.models import (
    OperationLog,
    Schedule,
    ScheduleRun,
)

MAX_LIST_LIMIT = 100
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
