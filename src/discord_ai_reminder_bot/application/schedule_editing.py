"""Atomically edit schedules while preserving immutable occurrence history."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time

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
from discord_ai_reminder_bot.domain.schedule_creation import validate_once_scheduled_for
from discord_ai_reminder_bot.domain.schedule_editing import (
    InvalidScheduleEditError,
    first_unused_recurring_edit_run,
    validate_edit_content,
    validate_edit_target,
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
    DeliveryAttemptRepository,
    OperationLogRepository,
    ScheduleRepository,
    ScheduleRunRepository,
)


class ScheduleEditUnavailable(Exception):
    """The target is absent, unauthorized, conflicting, or not editable."""


class ScheduleEditNoChanges(Exception):
    """The request produces no persistent change."""


class InvalidScheduleEditOptions(Exception):
    """Options are invalid for the target schedule type."""


@dataclass(frozen=True)
class EditValues:
    channel_id: int | None = None
    scheduled_at: datetime | None = None
    local_time: time | None = None
    weekday: int | None = None
    end_date: date | None = None
    content: str | None = None
    clear_content: bool = False
    clear_end_date: bool = False
    end_date_supplied: bool = False
    weekday_supplied: bool = False

    def has_request(self) -> bool:
        return any(
            (
                self.channel_id is not None,
                self.scheduled_at is not None,
                self.local_time is not None,
                self.weekday_supplied,
                self.end_date_supplied,
                self.content is not None,
                self.clear_content,
                self.clear_end_date,
            )
        )


@dataclass(frozen=True)
class EditedSchedule:
    public_id: uuid.UUID
    channel_id: int
    schedule_type: ScheduleType
    status: ScheduleStatus
    content: str | None
    next_run_at: datetime | None
    local_time: time | None
    weekday: int | None
    end_date: date | None
    changed_fields: tuple[str, ...]
    pending_runs_skipped: int
    run_replaced: bool
    retry_pending_preserved: bool
    previous_status: ScheduleStatus


@dataclass(frozen=True)
class _Snapshot:
    schedule_id: int
    public_id: uuid.UUID
    guild_id: int
    creator_user_id: int
    version: int
    next_run_at: datetime | None


class ScheduleEditingService:
    """Edit a schedule without owning commit or rollback."""

    def __init__(self, session: AsyncSession) -> None:
        self._schedules = ScheduleRepository(session)
        self._runs = ScheduleRunRepository(session)
        self._operations = OperationLogRepository(session)
        self._attempts = DeliveryAttemptRepository(session)

    async def edit(
        self,
        *,
        guild_id: int,
        public_id: str,
        actor_user_id: int,
        administrator: bool,
        values: EditValues,
        edited_at: datetime,
    ) -> EditedSchedule:
        edited_at = require_utc(edited_at)
        if not values.has_request():
            raise InvalidScheduleEditOptions
        unlocked = await self._find(guild_id=guild_id, public_id=public_id)
        snapshot = _snapshot(unlocked)
        runs = await self._runs.list_for_edit(schedule_id=snapshot.schedule_id, lock=True)
        in_flight_attempts = await self._attempts.list_in_flight_for_runs(
            run_ids=[run.id for run in runs]
        )
        schedule = await self._lock_and_revalidate(snapshot)
        self._authorize(schedule, actor_user_id=actor_user_id, administrator=administrator)
        schedule_type = ScheduleType(schedule.schedule_type)
        status = ScheduleStatus(schedule.status)
        try:
            validate_edit_target(
                schedule_type=schedule_type,
                status=status,
                next_run_at=schedule.next_run_at,
                now=edited_at,
            )
            self._validate_options(schedule_type, values)
            self._validate_runs(schedule, runs)
            if in_flight_attempts:
                raise InvalidScheduleEditError("delivery attempt is in flight")
        except (InvalidScheduleEditError, ValueError, TypeError) as error:
            raise ScheduleEditUnavailable from error

        content = schedule.content
        if values.clear_content:
            content = None
        elif values.content is not None:
            try:
                content = validate_edit_content(values.content)
            except ValueError as error:
                raise InvalidScheduleEditOptions from error
        channel_id = values.channel_id if values.channel_id is not None else schedule.channel_id
        local_time = values.local_time if values.local_time is not None else schedule.local_time
        weekday = values.weekday if values.weekday_supplied else schedule.weekday
        end_date = schedule.end_date
        if values.clear_end_date:
            end_date = None
        elif values.end_date_supplied:
            end_date = values.end_date

        actual = {
            "channel_id": channel_id != schedule.channel_id,
            "content": content != schedule.content,
            "scheduled_at": (
                values.scheduled_at is not None and values.scheduled_at != schedule.next_run_at
            ),
            "local_time": local_time != schedule.local_time,
            "weekday": weekday != schedule.weekday,
            "end_date": end_date != schedule.end_date,
        }
        if not any(actual.values()):
            raise ScheduleEditNoChanges

        previous_status = status
        current_next = schedule.next_run_at
        next_run = current_next
        recurrence_changed = actual["local_time"] or actual["weekday"] or actual["end_date"]
        replace_run = False
        ended = False

        if status is not ScheduleStatus.PAUSED:
            if schedule_type is ScheduleType.ONCE and actual["scheduled_at"]:
                assert values.scheduled_at is not None
                try:
                    candidate = validate_once_scheduled_for(values.scheduled_at, now=edited_at)
                except ValueError as error:
                    raise ScheduleEditUnavailable from error
                if any(run.scheduled_for == candidate for run in runs):
                    raise ScheduleEditUnavailable
                next_run = candidate
                replace_run = True
            elif schedule_type is not ScheduleType.ONCE and recurrence_changed:
                if local_time is None:
                    raise ScheduleEditUnavailable
                occupied = {require_utc(run.scheduled_for) for run in runs}
                try:
                    candidate = first_unused_recurring_edit_run(
                        schedule_type=schedule_type,
                        local_time=local_time,
                        weekday=weekday,
                        end_date=end_date,
                        edited_at=edited_at,
                        occupied=occupied,
                        reusable_pending=current_next,
                    )
                except (ValueError, TypeError) as error:
                    raise ScheduleEditUnavailable from error
                if candidate is None:
                    if content is None:
                        raise ScheduleEditUnavailable
                    next_run = None
                    ended = True
                    replace_run = True
                elif candidate != current_next:
                    next_run = candidate
                    replace_run = True

        pending_count = 0
        if replace_run:
            pending_count = sum(run.status == RunStatus.PENDING.value for run in runs)
            await self._runs.skip_pending_for_edited_schedule(runs=runs, edited_at=edited_at)
            if next_run is not None:
                try:
                    await self._runs.add(
                        ScheduleRun(
                            schedule_id=schedule.id,
                            scheduled_for=next_run,
                            status=RunStatus.PENDING.value,
                            attempt_count=0,
                            next_attempt_at=next_run,
                            updated_at=edited_at,
                        )
                    )
                except DuplicateRecordError as error:
                    raise ScheduleEditUnavailable from error

        if status is ScheduleStatus.PAUSED:
            target = ScheduleStatus.PAUSED
            next_run = None
        elif ended:
            target = ScheduleStatus.ENDED
        else:
            target = ScheduleStatus.ACTIVE if content is not None else ScheduleStatus.DRAFT

        changes = self._operation_changes(
            schedule=schedule,
            channel_id=channel_id,
            content_changed=actual["content"],
            scheduled_at=next_run if actual["scheduled_at"] else None,
            local_time=local_time,
            weekday=weekday,
            end_date=end_date,
            actual=actual,
            previous_status=previous_status,
            target=target,
            pending_count=pending_count,
            recurrence_changed=recurrence_changed,
        )
        schedule.channel_id = channel_id
        schedule.content = content
        schedule.local_time = local_time
        schedule.weekday = weekday
        schedule.end_date = end_date
        schedule.status = target.value
        schedule.next_run_at = next_run
        schedule.terminal_at = edited_at if target is ScheduleStatus.ENDED else None
        schedule.deleted_at = None
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
                changes=changes,
                created_at=edited_at,
            )
        )
        retry_preserved = not replace_run and any(
            run.status == RunStatus.PENDING.value and run.attempt_count > 0 for run in runs
        )
        return EditedSchedule(
            public_id=schedule.public_id,
            channel_id=channel_id,
            schedule_type=schedule_type,
            status=target,
            content=content,
            next_run_at=next_run,
            local_time=local_time,
            weekday=weekday,
            end_date=end_date,
            changed_fields=tuple(name for name, changed in actual.items() if changed),
            pending_runs_skipped=pending_count,
            run_replaced=replace_run and next_run is not None,
            retry_pending_preserved=retry_preserved and (actual["content"] or actual["channel_id"]),
            previous_status=previous_status,
        )

    async def _find(self, *, guild_id: int, public_id: str) -> Schedule:
        try:
            return await self._schedules.get_by_public_id(
                guild_id=guild_id, public_id=parse_public_id(public_id)
            )
        except (RepositoryNotFoundError, ValueError) as error:
            raise ScheduleEditUnavailable from error

    async def _lock_and_revalidate(self, snapshot: _Snapshot) -> Schedule:
        try:
            schedule = await self._schedules.lock_by_id_for_deletion(snapshot.schedule_id)
        except RepositoryNotFoundError as error:
            raise ScheduleEditUnavailable from error
        if (
            schedule.public_id != snapshot.public_id
            or schedule.guild_id != snapshot.guild_id
            or schedule.creator_user_id != snapshot.creator_user_id
            or schedule.version != snapshot.version
            or schedule.next_run_at != snapshot.next_run_at
        ):
            raise ScheduleEditUnavailable
        return schedule

    @staticmethod
    def _authorize(schedule: Schedule, *, actor_user_id: int, administrator: bool) -> None:
        if actor_user_id != schedule.creator_user_id and not administrator:
            raise ScheduleEditUnavailable

    @staticmethod
    def _validate_options(schedule_type: ScheduleType, values: EditValues) -> None:
        if values.content is not None and values.clear_content:
            raise InvalidScheduleEditOptions
        if values.end_date_supplied and values.clear_end_date:
            raise InvalidScheduleEditOptions
        if schedule_type is ScheduleType.ONCE:
            if any(
                (
                    values.local_time is not None,
                    values.weekday_supplied,
                    values.end_date_supplied,
                    values.clear_end_date,
                )
            ):
                raise InvalidScheduleEditOptions
        elif schedule_type is ScheduleType.DAILY:
            if values.scheduled_at is not None or values.weekday_supplied:
                raise InvalidScheduleEditOptions
        elif schedule_type is ScheduleType.WEEKLY and values.scheduled_at is not None:
            raise InvalidScheduleEditOptions

    @staticmethod
    def _validate_runs(schedule: Schedule, runs: list[ScheduleRun]) -> None:
        if any(run.status == RunStatus.PROCESSING.value for run in runs):
            raise ScheduleEditUnavailable
        if schedule.next_run_at is not None and (
            len(current := [run for run in runs if run.scheduled_for == schedule.next_run_at]) != 1
            or current[0].status != RunStatus.PENDING.value
        ):
            raise ScheduleEditUnavailable

    @staticmethod
    def _operation_changes(
        *,
        schedule: Schedule,
        channel_id: int,
        content_changed: bool,
        scheduled_at: datetime | None,
        local_time: time | None,
        weekday: int | None,
        end_date: date | None,
        actual: dict[str, bool],
        previous_status: ScheduleStatus,
        target: ScheduleStatus,
        pending_count: int,
        recurrence_changed: bool,
    ) -> dict[str, object]:
        changes: dict[str, object] = {}
        if actual["channel_id"]:
            changes["channel_id"] = {"from": schedule.channel_id, "to": channel_id}
        if content_changed:
            changes["content_changed"] = True
        if actual["scheduled_at"]:
            changes["scheduled_at"] = {
                "from": schedule.next_run_at.isoformat() if schedule.next_run_at else None,
                "to": scheduled_at.isoformat() if scheduled_at else None,
            }
        if actual["local_time"]:
            changes["local_time"] = {
                "from": schedule.local_time.strftime("%H:%M") if schedule.local_time else None,
                "to": local_time.strftime("%H:%M") if local_time else None,
            }
        if actual["weekday"]:
            changes["weekday"] = {"from": schedule.weekday, "to": weekday}
        if actual["end_date"]:
            changes["end_date"] = {
                "from": schedule.end_date.isoformat() if schedule.end_date else None,
                "to": end_date.isoformat() if end_date else None,
            }
        if previous_status is not target:
            changes["status"] = {"from": previous_status.value, "to": target.value}
        changes["pending_runs_skipped"] = pending_count
        changes["next_run_recalculated"] = recurrence_changed or actual["scheduled_at"]
        return changes


def _snapshot(schedule: Schedule) -> _Snapshot:
    return _Snapshot(
        schedule_id=schedule.id,
        public_id=schedule.public_id,
        guild_id=schedule.guild_id,
        creator_user_id=schedule.creator_user_id,
        version=schedule.version,
        next_run_at=schedule.next_run_at,
    )
