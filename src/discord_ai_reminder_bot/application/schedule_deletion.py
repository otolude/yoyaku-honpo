"""Preview and atomically record user-requested logical schedule deletion."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from discord_ai_reminder_bot.application.schedule_queries import parse_public_id
from discord_ai_reminder_bot.domain.enums import (
    ActorType,
    OperationAction,
    RunStatus,
    ScheduleStatus,
    ScheduleType,
)
from discord_ai_reminder_bot.domain.recurrence import require_utc
from discord_ai_reminder_bot.domain.schedule_deletion import (
    DELETABLE_STATUSES,
    deletion_kind,
    validate_delete_reason,
)
from discord_ai_reminder_bot.infrastructure.database.exceptions import RepositoryNotFoundError
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


class ScheduleDeletionUnavailable(Exception):
    """The target is absent, unauthorized, conflicting, or not deletable."""


@dataclass(frozen=True)
class ScheduleDeletionView:
    public_id: uuid.UUID
    channel_id: int
    schedule_type: ScheduleType
    previous_status: ScheduleStatus
    content: str | None
    next_run_at: datetime | None
    reason: str


@dataclass(frozen=True)
class DeletedSchedule(ScheduleDeletionView):
    deleted_at: datetime
    pending_runs_skipped: int


@dataclass(frozen=True)
class _TargetSnapshot:
    schedule_id: int
    public_id: uuid.UUID
    guild_id: int
    version: int
    next_run_at: datetime | None


class ScheduleDeletionService:
    """Delete without owning commit or rollback and without exposing internal IDs."""

    def __init__(self, session: AsyncSession) -> None:
        self._schedules = ScheduleRepository(session)
        self._runs = ScheduleRunRepository(session)
        self._operations = OperationLogRepository(session)

    async def preview(
        self,
        *,
        guild_id: int,
        public_id: str,
        actor_user_id: int,
        administrator: bool,
        reason: str,
    ) -> ScheduleDeletionView:
        reason = validate_delete_reason(reason)
        schedule = await self._find(guild_id=guild_id, public_id=public_id)
        runs = await self._runs.list_for_deletion(
            schedule_id=schedule.id,
            current_scheduled_for=schedule.next_run_at,
            lock=False,
        )
        self._validate(schedule, runs, actor_user_id=actor_user_id, administrator=administrator)
        return _to_view(schedule, reason=reason)

    async def delete(
        self,
        *,
        guild_id: int,
        public_id: str,
        actor_user_id: int,
        administrator: bool,
        reason: str,
        deleted_at: datetime,
    ) -> DeletedSchedule:
        deleted_at = require_utc(deleted_at)
        reason = validate_delete_reason(reason)
        unlocked = await self._find(guild_id=guild_id, public_id=public_id)
        snapshot = _snapshot(unlocked)
        runs = await self._runs.list_for_deletion(
            schedule_id=snapshot.schedule_id,
            current_scheduled_for=snapshot.next_run_at,
            lock=True,
        )
        try:
            schedule = await self._schedules.lock_by_id_for_deletion(snapshot.schedule_id)
        except RepositoryNotFoundError as error:
            raise ScheduleDeletionUnavailable from error
        if (
            schedule.guild_id != snapshot.guild_id
            or schedule.public_id != snapshot.public_id
            or schedule.version != snapshot.version
            or schedule.next_run_at != snapshot.next_run_at
        ):
            raise ScheduleDeletionUnavailable
        self._validate(schedule, runs, actor_user_id=actor_user_id, administrator=administrator)
        previous_status = ScheduleStatus(schedule.status)
        kind = deletion_kind(
            actor_user_id=actor_user_id,
            creator_user_id=schedule.creator_user_id,
            administrator=administrator,
            status=previous_status,
        )
        pending_count = sum(run.status == RunStatus.PENDING.value for run in runs)
        await self._runs.skip_pending_for_deleted_schedule(runs=runs, deleted_at=deleted_at)
        schedule.status = ScheduleStatus.DELETED.value
        schedule.next_run_at = None
        schedule.deleted_at = deleted_at
        schedule.terminal_at = deleted_at
        schedule.updated_at = deleted_at
        schedule.version += 1
        await self._schedules.flush_execution_update(schedule)
        await self._operations.add(
            OperationLog(
                schedule_id=schedule.id,
                action=OperationAction.DELETED.value,
                actor_type=ActorType.USER.value,
                actor_user_id=actor_user_id,
                delete_kind=kind.value,
                delete_reason=reason,
                changes={
                    "status": {"from": previous_status.value, "to": ScheduleStatus.DELETED.value},
                    "pending_runs_skipped": pending_count,
                },
                created_at=deleted_at,
            )
        )
        return DeletedSchedule(
            public_id=schedule.public_id,
            channel_id=schedule.channel_id,
            schedule_type=ScheduleType(schedule.schedule_type),
            previous_status=previous_status,
            content=schedule.content,
            next_run_at=None,
            reason=reason,
            deleted_at=deleted_at,
            pending_runs_skipped=pending_count,
        )

    async def _find(self, *, guild_id: int, public_id: str) -> Schedule:
        try:
            parsed = parse_public_id(public_id)
            return await self._schedules.get_by_public_id(guild_id=guild_id, public_id=parsed)
        except (RepositoryNotFoundError, ValueError) as error:
            raise ScheduleDeletionUnavailable from error

    @staticmethod
    def _validate(
        schedule: Schedule,
        runs: list[ScheduleRun],
        *,
        actor_user_id: int,
        administrator: bool,
    ) -> None:
        status = ScheduleStatus(schedule.status)
        if status not in DELETABLE_STATUSES:
            raise ScheduleDeletionUnavailable
        if actor_user_id != schedule.creator_user_id and not administrator:
            raise ScheduleDeletionUnavailable
        if any(run.status == RunStatus.PROCESSING.value for run in runs):
            raise ScheduleDeletionUnavailable
        if schedule.next_run_at is not None:
            current = [run for run in runs if run.scheduled_for == schedule.next_run_at]
            if len(current) != 1 or current[0].status != RunStatus.PENDING.value:
                raise ScheduleDeletionUnavailable


def _snapshot(schedule: Schedule) -> _TargetSnapshot:
    return _TargetSnapshot(
        schedule_id=schedule.id,
        public_id=schedule.public_id,
        guild_id=schedule.guild_id,
        version=schedule.version,
        next_run_at=schedule.next_run_at,
    )


def _to_view(
    schedule: Schedule,
    *,
    reason: str,
    previous_status: ScheduleStatus | None = None,
) -> ScheduleDeletionView:
    return ScheduleDeletionView(
        public_id=schedule.public_id,
        channel_id=schedule.channel_id,
        schedule_type=ScheduleType(schedule.schedule_type),
        previous_status=previous_status or ScheduleStatus(schedule.status),
        content=schedule.content,
        next_run_at=schedule.next_run_at,
        reason=reason,
    )
