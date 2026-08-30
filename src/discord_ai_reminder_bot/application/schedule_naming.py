"""Manual schedule-name editing inside caller-owned transactions."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from discord_ai_reminder_bot.application.schedule_queries import parse_public_id
from discord_ai_reminder_bot.domain.enums import (
    ActorType,
    DisplayNameSource,
    OperationAction,
    ScheduleStatus,
)
from discord_ai_reminder_bot.domain.recurrence import require_utc
from discord_ai_reminder_bot.domain.schedule_naming import normalize_manual_display_name
from discord_ai_reminder_bot.infrastructure.database.exceptions import RepositoryNotFoundError
from discord_ai_reminder_bot.infrastructure.database.models import OperationLog, Schedule
from discord_ai_reminder_bot.infrastructure.database.repositories import (
    OperationLogRepository,
    ScheduleRepository,
)

_EDITABLE_STATUSES = frozenset((ScheduleStatus.DRAFT, ScheduleStatus.ACTIVE, ScheduleStatus.PAUSED))


class ScheduleNameEditUnavailable(Exception):
    """The schedule is absent, unauthorized, conflicting, or not editable."""


class ScheduleNameVersionConflict(ScheduleNameEditUnavailable):
    """The displayed schedule version is no longer current."""


class ScheduleNameNoChanges(Exception):
    """The submitted name/source pair already matches persistence."""


@dataclass(frozen=True)
class EditedScheduleName:
    public_id: uuid.UUID
    display_name: str | None
    display_name_source: DisplayNameSource
    version: int


@dataclass(frozen=True)
class _Snapshot:
    schedule_id: int
    public_id: uuid.UUID
    guild_id: int
    creator_user_id: int
    version: int


class ScheduleNamingService:
    """Edit one persisted name without owning commit or rollback."""

    def __init__(self, session: AsyncSession) -> None:
        self._schedules = ScheduleRepository(session)
        self._operations = OperationLogRepository(session)

    async def edit_manual_name(
        self,
        *,
        guild_id: int,
        public_id: str,
        actor_user_id: int,
        administrator: bool,
        submitted_name: str,
        edited_at: datetime,
        expected_version: int,
    ) -> EditedScheduleName:
        edited_at = require_utc(edited_at)
        display_name, source = normalize_manual_display_name(submitted_name)
        try:
            unlocked = await self._schedules.get_by_public_id(
                guild_id=guild_id, public_id=parse_public_id(public_id)
            )
        except (RepositoryNotFoundError, ValueError) as error:
            raise ScheduleNameEditUnavailable from error
        snapshot = _snapshot(unlocked)
        _validate_expected_version(snapshot.version, expected_version)
        try:
            schedule = await self._schedules.lock_by_id_for_deletion(snapshot.schedule_id)
        except RepositoryNotFoundError as error:
            raise ScheduleNameEditUnavailable from error
        _validate_expected_version(schedule.version, expected_version)
        if (
            schedule.public_id != snapshot.public_id
            or schedule.guild_id != snapshot.guild_id
            or schedule.creator_user_id != snapshot.creator_user_id
            or schedule.version != snapshot.version
        ):
            raise ScheduleNameVersionConflict
        if actor_user_id != schedule.creator_user_id and not administrator:
            raise ScheduleNameEditUnavailable
        try:
            status = ScheduleStatus(schedule.status)
            previous_source = DisplayNameSource(schedule.display_name_source)
        except ValueError as error:
            raise ScheduleNameEditUnavailable from error
        if status not in _EDITABLE_STATUSES:
            raise ScheduleNameEditUnavailable
        if schedule.display_name == display_name and previous_source is source:
            raise ScheduleNameNoChanges

        schedule.display_name = display_name
        schedule.display_name_source = source.value
        schedule.updated_at = edited_at
        schedule.version += 1
        await self._schedules.flush_execution_update(schedule)
        await self._operations.add(
            OperationLog(
                schedule_id=schedule.id,
                action=OperationAction.EDITED.value,
                actor_type=ActorType.USER.value,
                actor_user_id=actor_user_id,
                delete_kind=None,
                delete_reason=None,
                changes={
                    "display_name_changed": True,
                    "display_name_source": {
                        "from": previous_source.value,
                        "to": source.value,
                    },
                },
                created_at=edited_at,
            )
        )
        return EditedScheduleName(
            public_id=schedule.public_id,
            display_name=display_name,
            display_name_source=source,
            version=schedule.version,
        )


def _snapshot(schedule: Schedule) -> _Snapshot:
    return _Snapshot(
        schedule_id=schedule.id,
        public_id=schedule.public_id,
        guild_id=schedule.guild_id,
        creator_user_id=schedule.creator_user_id,
        version=schedule.version,
    )


def _validate_expected_version(actual: int, expected: int) -> None:
    if isinstance(expected, bool) or not isinstance(expected, int) or expected <= 0:
        raise ValueError("expected_version must be a positive integer")
    if actual != expected:
        raise ScheduleNameVersionConflict
