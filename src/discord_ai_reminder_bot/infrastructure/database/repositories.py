"""Basic async repositories without transaction ownership."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import Select, String, and_, cast, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from discord_ai_reminder_bot.domain.enums import (
    DeliveryAttemptStatus,
    NotificationStatus,
    NotificationType,
    RunStatus,
    ScheduleStatus,
    ScheduleType,
)
from discord_ai_reminder_bot.domain.recurrence import require_utc
from discord_ai_reminder_bot.infrastructure.database.exceptions import (
    DuplicateRecordError,
    OptimisticLockError,
    RepositoryNotFoundError,
    RepositoryOwnershipError,
    RepositoryStateConflictError,
)
from discord_ai_reminder_bot.infrastructure.database.models import (
    DeliveryAttempt,
    NotificationAttempt,
    NotificationLog,
    OperationLog,
    Schedule,
    ScheduleRun,
)

MAX_LIST_LIMIT = 100
MAX_CLAIM_BATCH_SIZE = 20
_UPDATABLE_SCHEDULE_FIELDS = {
    "channel_id",
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


def _validate_offset(offset: int) -> None:
    if isinstance(offset, bool) or offset < 0:
        raise ValueError("offset must be a non-negative integer")


@dataclass(frozen=True)
class ClaimedScheduleRun:
    """A locked run and the delivery attempt created for its current claim."""

    run: ScheduleRun
    attempt: DeliveryAttempt


@dataclass(frozen=True)
class ScheduleAutocompleteRow:
    """Minimal read projection for schedule ID autocomplete."""

    public_id: uuid.UUID
    channel_id: int
    creator_user_id: int
    schedule_type: str
    status: str
    display_at: datetime | None


def build_due_runs_claim_statement(*, now: datetime, batch_size: int) -> Select[tuple[ScheduleRun]]:
    """Build the PostgreSQL-only due-run locking statement."""
    now = require_utc(now)
    if isinstance(batch_size, bool) or not 1 <= batch_size <= MAX_CLAIM_BATCH_SIZE:
        raise ValueError(f"batch_size must be between 1 and {MAX_CLAIM_BATCH_SIZE}")
    return (
        select(ScheduleRun)
        .join(Schedule, Schedule.id == ScheduleRun.schedule_id)
        .where(
            ScheduleRun.status == RunStatus.PENDING.value,
            ScheduleRun.scheduled_for <= now,
            ScheduleRun.next_attempt_at <= now,
            ScheduleRun.attempt_count < 4,
            Schedule.status.in_((ScheduleStatus.ACTIVE.value, ScheduleStatus.DRAFT.value)),
        )
        .order_by(
            ScheduleRun.next_attempt_at.asc(),
            ScheduleRun.scheduled_for.asc(),
            ScheduleRun.id.asc(),
        )
        .limit(batch_size)
        .with_for_update(of=ScheduleRun, skip_locked=True)
    )


def build_expired_processing_statement(
    *, recovered_at: datetime, batch_size: int
) -> Select[tuple[ScheduleRun]]:
    """Build the PostgreSQL-only expired processing-run locking statement."""
    recovered_at = require_utc(recovered_at)
    if isinstance(batch_size, bool) or not 1 <= batch_size <= MAX_CLAIM_BATCH_SIZE:
        raise ValueError(f"batch_size must be between 1 and {MAX_CLAIM_BATCH_SIZE}")
    return (
        select(ScheduleRun)
        .where(
            ScheduleRun.status == RunStatus.PROCESSING.value,
            ScheduleRun.lease_expires_at <= recovered_at,
            ScheduleRun.claimed_by.is_not(None),
            ScheduleRun.claimed_at.is_not(None),
            ScheduleRun.lease_expires_at.is_not(None),
        )
        .order_by(ScheduleRun.lease_expires_at.asc(), ScheduleRun.id.asc())
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )


def build_startup_pending_statement(
    *, recovered_at: datetime, batch_size: int
) -> Select[tuple[ScheduleRun]]:
    """Lock overdue initial or unsafe pending runs, excluding healthy retries."""
    recovered_at = require_utc(recovered_at)
    if isinstance(batch_size, bool) or not 1 <= batch_size <= MAX_CLAIM_BATCH_SIZE:
        raise ValueError(f"batch_size must be between 1 and {MAX_CLAIM_BATCH_SIZE}")
    attempt_count = (
        select(func.count(DeliveryAttempt.id))
        .where(DeliveryAttempt.schedule_run_id == ScheduleRun.id)
        .correlate(ScheduleRun)
        .scalar_subquery()
    )
    failed_attempt_count = (
        select(func.count(DeliveryAttempt.id))
        .where(
            DeliveryAttempt.schedule_run_id == ScheduleRun.id,
            DeliveryAttempt.status == DeliveryAttemptStatus.FAILED.value,
        )
        .correlate(ScheduleRun)
        .scalar_subquery()
    )
    max_attempt_number = (
        select(func.max(DeliveryAttempt.attempt_number))
        .where(DeliveryAttempt.schedule_run_id == ScheduleRun.id)
        .correlate(ScheduleRun)
        .scalar_subquery()
    )
    healthy_retry = and_(
        ScheduleRun.attempt_count.between(1, 3),
        attempt_count == ScheduleRun.attempt_count,
        failed_attempt_count == ScheduleRun.attempt_count,
        max_attempt_number == ScheduleRun.attempt_count,
        Schedule.status.in_((ScheduleStatus.ACTIVE.value, ScheduleStatus.DRAFT.value)),
    )
    healthy_delayed_once = and_(
        ScheduleRun.attempt_count == 0,
        attempt_count == 0,
        ScheduleRun.scheduled_for >= recovered_at - timedelta(minutes=15),
        Schedule.schedule_type == ScheduleType.ONCE.value,
        Schedule.status == ScheduleStatus.ACTIVE.value,
        Schedule.content.is_not(None),
        Schedule.next_run_at == ScheduleRun.scheduled_for,
    )
    healthy_paused_hold = and_(
        ScheduleRun.attempt_count == 0,
        attempt_count == 0,
        Schedule.status == ScheduleStatus.PAUSED.value,
        Schedule.schedule_type.in_((ScheduleType.DAILY.value, ScheduleType.WEEKLY.value)),
        Schedule.next_run_at.is_(None),
        ScheduleRun.next_attempt_at == ScheduleRun.scheduled_for,
    )
    return (
        select(ScheduleRun)
        .join(Schedule, Schedule.id == ScheduleRun.schedule_id)
        .where(
            ScheduleRun.status == RunStatus.PENDING.value,
            ScheduleRun.scheduled_for <= recovered_at,
            ~healthy_retry,
            ~healthy_delayed_once,
            ~healthy_paused_hold,
        )
        .order_by(ScheduleRun.scheduled_for.asc(), ScheduleRun.id.asc())
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )


def build_startup_delayed_notification_statement(
    *, recovered_at: datetime, batch_size: int
) -> Select[tuple[ScheduleRun]]:
    """Lock healthy delayed one-time runs that still need their startup event."""
    recovered_at = require_utc(recovered_at)
    if isinstance(batch_size, bool) or not 1 <= batch_size <= MAX_CLAIM_BATCH_SIZE:
        raise ValueError(f"batch_size must be between 1 and {MAX_CLAIM_BATCH_SIZE}")
    has_attempt = (
        select(DeliveryAttempt.id).where(DeliveryAttempt.schedule_run_id == ScheduleRun.id).exists()
    )
    has_delayed_event = (
        select(NotificationLog.id)
        .where(
            NotificationLog.schedule_run_id == ScheduleRun.id,
            NotificationLog.notification_type == NotificationType.RUN_DELAYED.value,
        )
        .exists()
    )
    return (
        select(ScheduleRun)
        .join(Schedule, Schedule.id == ScheduleRun.schedule_id)
        .where(
            ScheduleRun.status == RunStatus.PENDING.value,
            ScheduleRun.attempt_count == 0,
            ScheduleRun.scheduled_for <= recovered_at,
            ScheduleRun.scheduled_for >= recovered_at - timedelta(minutes=15),
            ~has_attempt,
            ~has_delayed_event,
            Schedule.schedule_type == ScheduleType.ONCE.value,
            Schedule.status == ScheduleStatus.ACTIVE.value,
            Schedule.content.is_not(None),
            Schedule.next_run_at == ScheduleRun.scheduled_for,
        )
        .order_by(ScheduleRun.scheduled_for.asc(), ScheduleRun.id.asc())
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )


def build_run_finalization_statement(*, run_id: int) -> Select[tuple[ScheduleRun]]:
    """Lock one run before locking its parent schedule."""
    return select(ScheduleRun).where(ScheduleRun.id == run_id).with_for_update()


def build_schedule_lock_statement(*, schedule_id: int) -> Select[tuple[Schedule]]:
    """Lock one parent schedule after its run has been locked."""
    return select(Schedule).where(Schedule.id == schedule_id).with_for_update()


def build_deletion_runs_statement(
    *, schedule_id: int, current_scheduled_for: datetime | None, lock: bool
) -> Select[tuple[ScheduleRun]]:
    """Select current and nonterminal runs in stable run-before-schedule lock order."""
    conditions = [ScheduleRun.status.in_((RunStatus.PENDING.value, RunStatus.PROCESSING.value))]
    if current_scheduled_for is not None:
        conditions.append(ScheduleRun.scheduled_for == require_utc(current_scheduled_for))
    statement = (
        select(ScheduleRun)
        .where(ScheduleRun.schedule_id == schedule_id, or_(*conditions))
        .order_by(ScheduleRun.id.asc())
    )
    return statement.with_for_update() if lock else statement


def build_schedule_state_change_runs_statement(
    *, schedule_id: int, lock: bool
) -> Select[tuple[ScheduleRun]]:
    """Select every run in stable order before locking its parent schedule."""
    statement = (
        select(ScheduleRun)
        .where(ScheduleRun.schedule_id == schedule_id)
        .order_by(ScheduleRun.id.asc())
    )
    return statement.with_for_update() if lock else statement


def build_schedule_edit_runs_statement(
    *, schedule_id: int, lock: bool
) -> Select[tuple[ScheduleRun]]:
    """Select all occurrence history in the common run-before-schedule lock order."""
    return build_schedule_state_change_runs_statement(schedule_id=schedule_id, lock=lock)


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

    async def autocomplete_schedules(
        self,
        *,
        guild_id: int,
        creator_user_id: int | None,
        operation: str,
        now: datetime,
        limit: int,
        uuid_prefix: str | None = None,
        schedule_type: ScheduleType | None = None,
        status: ScheduleStatus | None = None,
        channel_id: int | None = None,
        channel_ids: frozenset[int] = frozenset(),
    ) -> list[ScheduleAutocompleteRow]:
        """Return a bounded, stable, read-only autocomplete projection."""
        _validate_limit(limit)
        now = require_utc(now)
        allowed_statuses = {
            "show": tuple(item.value for item in ScheduleStatus),
            "edit": (
                ScheduleStatus.DRAFT.value,
                ScheduleStatus.ACTIVE.value,
                ScheduleStatus.PAUSED.value,
            ),
            "delete": (
                ScheduleStatus.DRAFT.value,
                ScheduleStatus.ACTIVE.value,
                ScheduleStatus.PAUSED.value,
                ScheduleStatus.FAILED.value,
            ),
            "pause": (ScheduleStatus.ACTIVE.value,),
            "resume": (ScheduleStatus.PAUSED.value,),
        }
        if operation not in allowed_statuses:
            raise ValueError("unsupported autocomplete operation")

        statement = select(
            Schedule.public_id,
            Schedule.channel_id,
            Schedule.creator_user_id,
            Schedule.schedule_type,
            Schedule.status,
            Schedule.next_run_at,
        ).where(
            Schedule.guild_id == guild_id,
            Schedule.status.in_(allowed_statuses[operation]),
        )
        if creator_user_id is not None:
            statement = statement.where(Schedule.creator_user_id == creator_user_id)
        search_conditions = []
        if uuid_prefix is not None:
            if len(uuid_prefix) == 36:
                search_conditions.append(Schedule.public_id == uuid.UUID(uuid_prefix))
            else:
                search_conditions.append(cast(Schedule.public_id, String).like(f"{uuid_prefix}%"))
        if schedule_type is not None:
            search_conditions.append(Schedule.schedule_type == schedule_type.value)
        if status is not None:
            search_conditions.append(Schedule.status == status.value)
        if channel_id is not None:
            search_conditions.append(Schedule.channel_id == channel_id)
        if channel_ids:
            search_conditions.append(Schedule.channel_id.in_(channel_ids))
        if search_conditions:
            statement = statement.where(or_(*search_conditions))

        if operation in {"pause", "resume"}:
            statement = statement.where(
                Schedule.schedule_type.in_((ScheduleType.DAILY.value, ScheduleType.WEEKLY.value))
            )
        if operation != "show":
            processing_run = (
                select(ScheduleRun.id)
                .where(
                    ScheduleRun.schedule_id == Schedule.id,
                    ScheduleRun.status == RunStatus.PROCESSING.value,
                )
                .exists()
            )
            in_flight_attempt = (
                select(DeliveryAttempt.id)
                .join(ScheduleRun, ScheduleRun.id == DeliveryAttempt.schedule_run_id)
                .where(
                    ScheduleRun.schedule_id == Schedule.id,
                    DeliveryAttempt.status.in_(
                        (
                            DeliveryAttemptStatus.CLAIMED.value,
                            DeliveryAttemptStatus.SENDING.value,
                        )
                    ),
                )
                .exists()
            )
            statement = statement.where(~processing_run, ~in_flight_attempt)

        current_run_count = (
            select(func.count(ScheduleRun.id))
            .where(
                ScheduleRun.schedule_id == Schedule.id,
                ScheduleRun.scheduled_for == Schedule.next_run_at,
                ScheduleRun.status == RunStatus.PENDING.value,
            )
            .correlate(Schedule)
            .scalar_subquery()
        )
        if operation in {"delete", "pause"}:
            statement = statement.where(or_(Schedule.next_run_at.is_(None), current_run_count == 1))
        elif operation == "edit":
            statement = statement.where(
                or_(
                    and_(
                        Schedule.status == ScheduleStatus.PAUSED.value,
                        Schedule.next_run_at.is_(None),
                        Schedule.schedule_type.in_(
                            (ScheduleType.DAILY.value, ScheduleType.WEEKLY.value)
                        ),
                    ),
                    and_(
                        Schedule.status.in_(
                            (ScheduleStatus.DRAFT.value, ScheduleStatus.ACTIVE.value)
                        ),
                        Schedule.next_run_at >= now + timedelta(minutes=5),
                        current_run_count == 1,
                    ),
                )
            )
        elif operation == "resume":
            pending_count = (
                select(func.count(ScheduleRun.id))
                .where(
                    ScheduleRun.schedule_id == Schedule.id,
                    ScheduleRun.status == RunStatus.PENDING.value,
                )
                .correlate(Schedule)
                .scalar_subquery()
            )
            invalid_pending = (
                select(ScheduleRun.id)
                .where(
                    ScheduleRun.schedule_id == Schedule.id,
                    ScheduleRun.status == RunStatus.PENDING.value,
                    or_(
                        ScheduleRun.attempt_count != 0,
                        ScheduleRun.next_attempt_at != ScheduleRun.scheduled_for,
                        ScheduleRun.claimed_by.is_not(None),
                        ScheduleRun.claimed_at.is_not(None),
                        ScheduleRun.lease_expires_at.is_not(None),
                        select(DeliveryAttempt.id)
                        .where(DeliveryAttempt.schedule_run_id == ScheduleRun.id)
                        .exists(),
                    ),
                )
                .exists()
            )
            statement = statement.where(pending_count <= 1, ~invalid_pending)

        result = await self._session.execute(
            statement.order_by(Schedule.next_run_at.asc().nulls_last(), Schedule.id.asc()).limit(
                limit
            )
        )
        return [ScheduleAutocompleteRow(*row) for row in result.all()]

    async def has_once_duplicate(
        self,
        *,
        guild_id: int,
        channel_id: int,
        scheduled_for: datetime,
        content: str | None,
    ) -> bool:
        scheduled_for = require_utc(scheduled_for)
        content_match = (
            Schedule.content.is_(None) if content is None else Schedule.content == content
        )
        statement = select(Schedule.id).where(
            Schedule.guild_id == guild_id,
            Schedule.channel_id == channel_id,
            Schedule.next_run_at == scheduled_for,
            Schedule.schedule_type == ScheduleType.ONCE.value,
            Schedule.status.in_(
                (
                    ScheduleStatus.DRAFT.value,
                    ScheduleStatus.ACTIVE.value,
                    ScheduleStatus.PAUSED.value,
                )
            ),
            and_(content_match),
        )
        return await self._session.scalar(statement.limit(1)) is not None

    async def has_recurring_duplicate(
        self,
        *,
        guild_id: int,
        channel_id: int,
        schedule_type: ScheduleType,
        local_time: time,
        weekday: int | None,
        end_date: date | None,
        content: str | None,
    ) -> bool:
        if schedule_type not in (ScheduleType.DAILY, ScheduleType.WEEKLY):
            raise ValueError("recurring schedule type is required")
        content_match = (
            Schedule.content.is_(None) if content is None else Schedule.content == content
        )
        end_date_match = (
            Schedule.end_date.is_(None) if end_date is None else Schedule.end_date == end_date
        )
        weekday_match = (
            Schedule.weekday.is_(None) if weekday is None else Schedule.weekday == weekday
        )
        statement = select(Schedule.id).where(
            Schedule.guild_id == guild_id,
            Schedule.channel_id == channel_id,
            Schedule.schedule_type == schedule_type.value,
            Schedule.local_time == local_time,
            weekday_match,
            end_date_match,
            content_match,
            Schedule.status.in_(
                (
                    ScheduleStatus.DRAFT.value,
                    ScheduleStatus.ACTIVE.value,
                    ScheduleStatus.PAUSED.value,
                )
            ),
        )
        return await self._session.scalar(statement.limit(1)) is not None

    async def get_by_id(self, schedule_id: int) -> Schedule:
        """Return a row for internal application use; never expose this ID externally."""
        schedule = await self._session.get(Schedule, schedule_id)
        if schedule is None:
            raise RepositoryNotFoundError("schedule was not found")
        return schedule

    async def lock_by_id(self, schedule_id: int) -> Schedule:
        schedule = (
            await self._session.execute(build_schedule_lock_statement(schedule_id=schedule_id))
        ).scalar_one_or_none()
        if schedule is None:
            raise RepositoryNotFoundError("schedule was not found")
        return schedule

    async def lock_by_id_for_deletion(self, schedule_id: int) -> Schedule:
        """Lock and refresh a deletion target after its runs have been locked."""
        statement = build_schedule_lock_statement(schedule_id=schedule_id).execution_options(
            populate_existing=True
        )
        schedule = (await self._session.execute(statement)).scalar_one_or_none()
        if schedule is None:
            raise RepositoryNotFoundError("schedule was not found")
        return schedule

    async def flush_execution_update(self, schedule: Schedule) -> Schedule:
        """Flush an execution-driven change without committing it."""
        await self._session.flush()
        return schedule

    async def list_by_creator(
        self,
        *,
        guild_id: int,
        creator_user_id: int,
        status: ScheduleStatus | None = None,
        schedule_type: ScheduleType | None = None,
        limit: int = 10,
        offset: int = 0,
        exclude_deleted: bool = False,
    ) -> list[Schedule]:
        statement = select(Schedule).where(
            Schedule.guild_id == guild_id,
            Schedule.creator_user_id == creator_user_id,
        )
        return await self._list(
            statement,
            status=status,
            schedule_type=schedule_type,
            limit=limit,
            offset=offset,
            exclude_deleted=exclude_deleted,
        )

    async def list_by_guild(
        self,
        *,
        guild_id: int,
        status: ScheduleStatus | None = None,
        schedule_type: ScheduleType | None = None,
        limit: int = 10,
        offset: int = 0,
        exclude_deleted: bool = False,
    ) -> list[Schedule]:
        statement = select(Schedule).where(Schedule.guild_id == guild_id)
        return await self._list(
            statement,
            status=status,
            schedule_type=schedule_type,
            limit=limit,
            offset=offset,
            exclude_deleted=exclude_deleted,
        )

    async def count_by_creator(
        self,
        *,
        guild_id: int,
        creator_user_id: int,
        status: ScheduleStatus | None = None,
        schedule_type: ScheduleType | None = None,
        exclude_deleted: bool = False,
    ) -> int:
        statement = select(func.count(Schedule.id)).where(
            Schedule.guild_id == guild_id,
            Schedule.creator_user_id == creator_user_id,
        )
        return await self._count(
            statement,
            status=status,
            schedule_type=schedule_type,
            exclude_deleted=exclude_deleted,
        )

    async def count_by_guild(
        self,
        *,
        guild_id: int,
        status: ScheduleStatus | None = None,
        schedule_type: ScheduleType | None = None,
        exclude_deleted: bool = False,
    ) -> int:
        statement = select(func.count(Schedule.id)).where(Schedule.guild_id == guild_id)
        return await self._count(
            statement,
            status=status,
            schedule_type=schedule_type,
            exclude_deleted=exclude_deleted,
        )

    async def _count(
        self,
        statement,
        *,
        status: ScheduleStatus | None,
        schedule_type: ScheduleType | None,
        exclude_deleted: bool,
    ) -> int:
        if status is not None:
            statement = statement.where(Schedule.status == status.value)
        elif exclude_deleted:
            statement = statement.where(Schedule.status != ScheduleStatus.DELETED.value)
        if schedule_type is not None:
            statement = statement.where(Schedule.schedule_type == schedule_type.value)
        return int((await self._session.scalar(statement)) or 0)

    async def _list(
        self,
        statement,
        *,
        status: ScheduleStatus | None,
        schedule_type: ScheduleType | None,
        limit: int,
        offset: int,
        exclude_deleted: bool,
    ) -> list[Schedule]:
        _validate_limit(limit)
        _validate_offset(offset)
        if status is not None:
            statement = statement.where(Schedule.status == status.value)
        elif exclude_deleted:
            statement = statement.where(Schedule.status != ScheduleStatus.DELETED.value)
        if schedule_type is not None:
            statement = statement.where(Schedule.schedule_type == schedule_type.value)
        statement = statement.order_by(Schedule.next_run_at.asc().nulls_last(), Schedule.id.asc())
        result = await self._session.execute(statement.offset(offset).limit(limit))
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

    async def list_for_deletion(
        self,
        *,
        schedule_id: int,
        current_scheduled_for: datetime | None,
        lock: bool,
    ) -> list[ScheduleRun]:
        statement = build_deletion_runs_statement(
            schedule_id=schedule_id,
            current_scheduled_for=current_scheduled_for,
            lock=lock,
        )
        return list((await self._session.execute(statement)).scalars())

    async def list_for_schedule_state_change(
        self, *, schedule_id: int, lock: bool
    ) -> list[ScheduleRun]:
        statement = build_schedule_state_change_runs_statement(
            schedule_id=schedule_id,
            lock=lock,
        )
        return list((await self._session.execute(statement)).scalars())

    async def list_for_edit(self, *, schedule_id: int, lock: bool) -> list[ScheduleRun]:
        statement = build_schedule_edit_runs_statement(schedule_id=schedule_id, lock=lock)
        return list((await self._session.execute(statement)).scalars())

    async def skip_pending_for_edited_schedule(
        self, *, runs: list[ScheduleRun], edited_at: datetime
    ) -> list[ScheduleRun]:
        edited_at = require_utc(edited_at)
        for run in runs:
            if run.status != RunStatus.PENDING.value:
                continue
            run.status = RunStatus.SKIPPED.value
            run.next_attempt_at = None
            run.claimed_by = None
            run.claimed_at = None
            run.lease_expires_at = None
            run.discord_message_id = None
            run.result_code = "schedule_edited"
            run.error_summary = "Schedule occurrence was replaced by a user edit"
            run.finished_at = edited_at
            run.updated_at = edited_at
        await self._session.flush()
        return runs

    async def skip_pending_for_paused_schedule(
        self, *, runs: list[ScheduleRun], paused_at: datetime, preserve_run_id: int | None = None
    ) -> list[ScheduleRun]:
        paused_at = require_utc(paused_at)
        for run in runs:
            if run.status != RunStatus.PENDING.value or run.id == preserve_run_id:
                continue
            run.status = RunStatus.SKIPPED.value
            run.next_attempt_at = None
            run.claimed_by = None
            run.claimed_at = None
            run.lease_expires_at = None
            run.discord_message_id = None
            run.result_code = "schedule_paused"
            run.error_summary = "Schedule was paused before Discord delivery"
            run.finished_at = paused_at
            run.updated_at = paused_at
        await self._session.flush()
        return runs

    async def cancel_pristine_draft_notifications_for_pause(
        self, *, run_id: int, paused_at: datetime
    ) -> int:
        """Cancel only never-claimed draft reminders made stale by schedule pause."""
        paused_at = require_utc(paused_at)
        draft_types = (
            NotificationType.DRAFT_24H.value,
            NotificationType.DRAFT_1H.value,
            NotificationType.DRAFT_IMMEDIATE.value,
        )
        has_attempt = (
            select(NotificationAttempt.id)
            .where(NotificationAttempt.notification_log_id == NotificationLog.id)
            .exists()
        )
        result = await self._session.execute(
            update(NotificationLog)
            .where(
                NotificationLog.schedule_run_id == run_id,
                NotificationLog.notification_type.in_(draft_types),
                NotificationLog.status == NotificationStatus.PENDING.value,
                NotificationLog.attempt_count == 0,
                NotificationLog.claimed_by.is_(None),
                NotificationLog.claimed_at.is_(None),
                NotificationLog.lease_expires_at.is_(None),
                NotificationLog.started_at.is_(None),
                NotificationLog.finished_at.is_(None),
                ~has_attempt,
            )
            .values(
                status=NotificationStatus.CANCELLED.value,
                scheduled_at=paused_at,
                next_attempt_at=None,
                finished_at=paused_at,
                error_code="schedule_paused",
                error_summary="Draft notification was cancelled when the schedule was paused",
            )
            .returning(NotificationLog)
            .execution_options(synchronize_session=False, populate_existing=True)
        )
        rows = list(result.scalars())
        await self._session.flush()
        return len(rows)

    async def skip_pending_for_deleted_schedule(
        self, *, runs: list[ScheduleRun], deleted_at: datetime
    ) -> list[ScheduleRun]:
        deleted_at = require_utc(deleted_at)
        for run in runs:
            if run.status != RunStatus.PENDING.value:
                continue
            run.status = RunStatus.SKIPPED.value
            run.next_attempt_at = None
            run.claimed_by = None
            run.claimed_at = None
            run.lease_expires_at = None
            run.discord_message_id = None
            run.result_code = "schedule_deleted"
            run.error_summary = "Schedule was deleted before Discord delivery"
            run.finished_at = deleted_at
            run.updated_at = deleted_at
        await self._session.flush()
        return runs

    async def lock_for_finalization(self, *, run_id: int) -> ScheduleRun:
        run = (
            await self._session.execute(build_run_finalization_statement(run_id=run_id))
        ).scalar_one_or_none()
        if run is None:
            raise RepositoryNotFoundError("schedule run was not found")
        return run

    async def get_by_schedule_and_time(
        self, *, schedule_id: int, scheduled_for: datetime
    ) -> ScheduleRun | None:
        scheduled_for = require_utc(scheduled_for)
        return (
            await self._session.execute(
                select(ScheduleRun).where(
                    ScheduleRun.schedule_id == schedule_id,
                    ScheduleRun.scheduled_for == scheduled_for,
                )
            )
        ).scalar_one_or_none()

    async def get_first_after(
        self, *, schedule_id: int, scheduled_for: datetime
    ) -> ScheduleRun | None:
        scheduled_for = require_utc(scheduled_for)
        return (
            await self._session.execute(
                select(ScheduleRun)
                .where(
                    ScheduleRun.schedule_id == schedule_id,
                    ScheduleRun.scheduled_for > scheduled_for,
                )
                .order_by(ScheduleRun.scheduled_for.asc(), ScheduleRun.id.asc())
                .limit(1)
            )
        ).scalar_one_or_none()

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

    async def lock_expired_processing(
        self, *, recovered_at: datetime, batch_size: int
    ) -> list[ScheduleRun]:
        statement = build_expired_processing_statement(
            recovered_at=recovered_at, batch_size=batch_size
        )
        return list((await self._session.execute(statement)).scalars())

    async def lock_startup_pending(
        self, *, recovered_at: datetime, batch_size: int
    ) -> list[ScheduleRun]:
        statement = build_startup_pending_statement(
            recovered_at=recovered_at, batch_size=batch_size
        )
        return list((await self._session.execute(statement)).scalars())

    async def lock_startup_delayed_without_notification(
        self, *, recovered_at: datetime, batch_size: int
    ) -> list[ScheduleRun]:
        statement = build_startup_delayed_notification_statement(
            recovered_at=recovered_at, batch_size=batch_size
        )
        return list((await self._session.execute(statement)).scalars())

    async def lock_draft_notification_bootstrap(
        self, *, recovery_cutoff: datetime, configured_guild_id: int, batch_size: int
    ) -> list[ScheduleRun]:
        recovery_cutoff = require_utc(recovery_cutoff)
        if isinstance(batch_size, bool) or not 1 <= batch_size <= MAX_CLAIM_BATCH_SIZE:
            raise ValueError("batch_size must be between 1 and 20")
        draft_types = ("draft_24h", "draft_1h", "draft_immediate")
        existing_future = (
            select(NotificationLog.id)
            .where(
                NotificationLog.schedule_run_id == ScheduleRun.id,
                NotificationLog.notification_type.in_(draft_types),
                NotificationLog.scheduled_at >= recovery_cutoff,
            )
            .exists()
        )
        stale_pending = (
            select(NotificationLog.id)
            .where(
                NotificationLog.schedule_run_id == ScheduleRun.id,
                NotificationLog.notification_type.in_(draft_types),
                NotificationLog.status == "pending",
                NotificationLog.scheduled_at < recovery_cutoff,
            )
            .exists()
        )
        statement = (
            select(ScheduleRun)
            .join(Schedule, Schedule.id == ScheduleRun.schedule_id)
            .where(
                Schedule.guild_id == configured_guild_id,
                Schedule.status == ScheduleStatus.DRAFT.value,
                Schedule.content.is_(None),
                Schedule.next_run_at == ScheduleRun.scheduled_for,
                ScheduleRun.status == RunStatus.PENDING.value,
                ScheduleRun.attempt_count == 0,
                ScheduleRun.scheduled_for > recovery_cutoff,
                or_(stale_pending, ~existing_future),
            )
            .order_by(ScheduleRun.scheduled_for.asc(), ScheduleRun.id.asc())
            .limit(batch_size)
            .with_for_update(of=ScheduleRun, skip_locked=True)
        )
        return list((await self._session.execute(statement)).scalars())

    async def count_startup_preserved(self, *, recovered_at: datetime) -> tuple[int, int]:
        """Count normal overdue initial and retry pending rows excluded from recovery."""
        recovered_at = require_utc(recovered_at)
        attempt_count = (
            select(func.count(DeliveryAttempt.id))
            .where(DeliveryAttempt.schedule_run_id == ScheduleRun.id)
            .correlate(ScheduleRun)
            .scalar_subquery()
        )
        failed_count = (
            select(func.count(DeliveryAttempt.id))
            .where(
                DeliveryAttempt.schedule_run_id == ScheduleRun.id,
                DeliveryAttempt.status == DeliveryAttemptStatus.FAILED.value,
            )
            .correlate(ScheduleRun)
            .scalar_subquery()
        )
        max_number = (
            select(func.max(DeliveryAttempt.attempt_number))
            .where(DeliveryAttempt.schedule_run_id == ScheduleRun.id)
            .correlate(ScheduleRun)
            .scalar_subquery()
        )
        base = (
            ScheduleRun.status == RunStatus.PENDING.value,
            ScheduleRun.scheduled_for <= recovered_at,
        )
        initial = await self._session.scalar(
            select(func.count(ScheduleRun.id))
            .join(Schedule, Schedule.id == ScheduleRun.schedule_id)
            .where(
                *base,
                ScheduleRun.attempt_count == 0,
                attempt_count == 0,
                ScheduleRun.scheduled_for >= recovered_at - timedelta(minutes=15),
                Schedule.schedule_type == ScheduleType.ONCE.value,
                Schedule.status == ScheduleStatus.ACTIVE.value,
                Schedule.content.is_not(None),
                Schedule.next_run_at == ScheduleRun.scheduled_for,
            )
        )
        retry = await self._session.scalar(
            select(func.count(ScheduleRun.id)).where(
                *base,
                ScheduleRun.attempt_count.between(1, 3),
                attempt_count == ScheduleRun.attempt_count,
                failed_count == ScheduleRun.attempt_count,
                max_number == ScheduleRun.attempt_count,
            )
        )
        return int(initial or 0), int(retry or 0)

    async def list_attempts(self, *, run_id: int) -> list[DeliveryAttempt]:
        statement = (
            select(DeliveryAttempt)
            .where(DeliveryAttempt.schedule_run_id == run_id)
            .order_by(DeliveryAttempt.attempt_number.asc(), DeliveryAttempt.id.asc())
        )
        return list((await self._session.execute(statement)).scalars())

    async def list_all_by_schedule(
        self, *, schedule_id: int, lock: bool = False
    ) -> list[ScheduleRun]:
        statement = (
            select(ScheduleRun)
            .where(ScheduleRun.schedule_id == schedule_id)
            .order_by(ScheduleRun.scheduled_for.asc(), ScheduleRun.id.asc())
        )
        if lock:
            statement = statement.with_for_update()
        return list((await self._session.execute(statement)).scalars())

    async def mark_sending_started(
        self, *, run_id: int, worker_id: uuid.UUID, now: datetime
    ) -> ScheduleRun:
        now = require_utc(now)
        _validate_worker_id(worker_id)
        statement = (
            update(ScheduleRun)
            .where(
                ScheduleRun.id == run_id,
                ScheduleRun.status == RunStatus.PROCESSING.value,
                ScheduleRun.claimed_by == worker_id,
                ScheduleRun.lease_expires_at >= now,
            )
            .values(updated_at=now)
            .returning(ScheduleRun)
            .execution_options(synchronize_session=False, populate_existing=True)
        )
        run = (await self._session.execute(statement)).scalar_one_or_none()
        if run is None:
            await self._raise_run_transition_error(run_id=run_id, worker_id=worker_id)
        return run

    async def mark_succeeded(
        self,
        *,
        run_id: int,
        worker_id: uuid.UUID,
        now: datetime,
        message_id: int,
        result_code: str,
    ) -> ScheduleRun:
        return await self._finish(
            run_id=run_id,
            worker_id=worker_id,
            now=now,
            status=RunStatus.SUCCEEDED,
            next_attempt_at=None,
            finished_at=now,
            message_id=message_id,
            result_code=result_code,
            error_summary=None,
        )

    async def mark_skipped(
        self,
        *,
        run_id: int,
        worker_id: uuid.UUID,
        now: datetime,
        result_code: str,
        error_summary: str,
    ) -> ScheduleRun:
        return await self._finish(
            run_id=run_id,
            worker_id=worker_id,
            now=now,
            status=RunStatus.SKIPPED,
            next_attempt_at=None,
            finished_at=require_utc(now),
            message_id=None,
            result_code=result_code,
            error_summary=error_summary,
        )

    async def mark_failed_or_pending(
        self,
        *,
        run_id: int,
        worker_id: uuid.UUID,
        now: datetime,
        retry_at: datetime | None,
        result_code: str,
        error_summary: str,
    ) -> ScheduleRun:
        status = RunStatus.PENDING if retry_at is not None else RunStatus.FAILED
        return await self._finish(
            run_id=run_id,
            worker_id=worker_id,
            now=now,
            status=status,
            next_attempt_at=retry_at,
            finished_at=None if retry_at is not None else now,
            message_id=None,
            result_code=result_code,
            error_summary=error_summary,
        )

    async def _finish(
        self,
        *,
        run_id: int,
        worker_id: uuid.UUID,
        now: datetime,
        status: RunStatus,
        next_attempt_at: datetime | None,
        finished_at: datetime | None,
        message_id: int | None,
        result_code: str,
        error_summary: str | None,
    ) -> ScheduleRun:
        now = require_utc(now)
        _validate_worker_id(worker_id)
        statement = (
            update(ScheduleRun)
            .where(
                ScheduleRun.id == run_id,
                ScheduleRun.status == RunStatus.PROCESSING.value,
                ScheduleRun.claimed_by == worker_id,
            )
            .values(
                status=status.value,
                next_attempt_at=next_attempt_at,
                discord_message_id=message_id,
                result_code=result_code,
                error_summary=error_summary,
                finished_at=finished_at,
                claimed_by=None,
                claimed_at=None,
                lease_expires_at=None,
                updated_at=now,
            )
            .returning(ScheduleRun)
            .execution_options(synchronize_session=False, populate_existing=True)
        )
        run = (await self._session.execute(statement)).scalar_one_or_none()
        if run is None:
            await self._raise_run_transition_error(run_id=run_id, worker_id=worker_id)
        return run

    async def _raise_run_transition_error(self, *, run_id: int, worker_id: uuid.UUID) -> None:
        run = await self._session.get(ScheduleRun, run_id, populate_existing=True)
        if run is None:
            raise RepositoryNotFoundError("schedule run was not found")
        if run.claimed_by != worker_id:
            raise RepositoryOwnershipError("schedule run belongs to another worker")
        raise RepositoryStateConflictError("schedule run state or lease does not permit the update")


def _validate_worker_id(worker_id: uuid.UUID) -> None:
    if not isinstance(worker_id, uuid.UUID):
        raise TypeError("worker_id must be a UUID")


class DeliveryAttemptRepository:
    """Conditionally advance delivery attempts without owning the transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_in_flight_for_runs(self, *, run_ids: list[int]) -> list[DeliveryAttempt]:
        if not run_ids:
            return []
        statement = (
            select(DeliveryAttempt)
            .where(
                DeliveryAttempt.schedule_run_id.in_(run_ids),
                DeliveryAttempt.status.in_(
                    (
                        DeliveryAttemptStatus.CLAIMED.value,
                        DeliveryAttemptStatus.SENDING.value,
                    )
                ),
            )
            .order_by(DeliveryAttempt.schedule_run_id.asc(), DeliveryAttempt.id.asc())
        )
        return list((await self._session.execute(statement)).scalars())

    async def get_by_id(self, attempt_id: int) -> DeliveryAttempt:
        attempt = await self._session.get(DeliveryAttempt, attempt_id)
        if attempt is None:
            raise RepositoryNotFoundError("delivery attempt was not found")
        return attempt

    async def get_by_run_and_number(self, *, run_id: int, attempt_number: int) -> DeliveryAttempt:
        attempt = (
            await self._session.execute(
                select(DeliveryAttempt).where(
                    DeliveryAttempt.schedule_run_id == run_id,
                    DeliveryAttempt.attempt_number == attempt_number,
                )
            )
        ).scalar_one_or_none()
        if attempt is None:
            raise RepositoryNotFoundError("delivery attempt was not found")
        return attempt

    async def get_latest_by_run(self, *, run_id: int) -> DeliveryAttempt | None:
        return (
            await self._session.execute(
                select(DeliveryAttempt)
                .where(DeliveryAttempt.schedule_run_id == run_id)
                .order_by(DeliveryAttempt.attempt_number.desc(), DeliveryAttempt.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def lock_latest_by_run(self, *, run_id: int) -> DeliveryAttempt | None:
        """Lock the latest attempt after its parent run has been locked."""
        return (
            await self._session.execute(
                select(DeliveryAttempt)
                .where(DeliveryAttempt.schedule_run_id == run_id)
                .order_by(DeliveryAttempt.attempt_number.desc(), DeliveryAttempt.id.desc())
                .limit(1)
                .with_for_update()
            )
        ).scalar_one_or_none()

    async def mark_sending(
        self, *, attempt_id: int, worker_id: uuid.UUID, now: datetime
    ) -> DeliveryAttempt:
        return await self._transition(
            attempt_id=attempt_id,
            worker_id=worker_id,
            expected=(DeliveryAttemptStatus.CLAIMED,),
            target=DeliveryAttemptStatus.SENDING,
            now=now,
            require_send_started=False,
            values={
                "send_started_at": now,
                "finished_at": None,
                "discord_message_id": None,
                "error_kind": None,
                "error_code": None,
                "error_summary": None,
            },
        )

    async def mark_succeeded(
        self,
        *,
        attempt_id: int,
        worker_id: uuid.UUID,
        now: datetime,
        message_id: int,
    ) -> DeliveryAttempt:
        return await self._transition(
            attempt_id=attempt_id,
            worker_id=worker_id,
            expected=(DeliveryAttemptStatus.SENDING,),
            target=DeliveryAttemptStatus.SUCCEEDED,
            now=now,
            require_send_started=True,
            values={
                "finished_at": now,
                "discord_message_id": message_id,
                "error_kind": None,
                "error_code": None,
                "error_summary": None,
            },
        )

    async def mark_failed(
        self,
        *,
        attempt_id: int,
        worker_id: uuid.UUID,
        now: datetime,
        error_kind: str,
        error_code: str,
        error_summary: str,
    ) -> DeliveryAttempt:
        return await self._transition(
            attempt_id=attempt_id,
            worker_id=worker_id,
            expected=(DeliveryAttemptStatus.CLAIMED, DeliveryAttemptStatus.SENDING),
            target=DeliveryAttemptStatus.FAILED,
            now=now,
            require_send_started=False,
            values={
                "finished_at": now,
                "discord_message_id": None,
                "error_kind": error_kind,
                "error_code": error_code,
                "error_summary": error_summary,
            },
        )

    async def mark_unknown(
        self,
        *,
        attempt_id: int,
        worker_id: uuid.UUID,
        now: datetime,
        error_code: str | None = None,
        error_summary: str | None = None,
    ) -> DeliveryAttempt:
        return await self._transition(
            attempt_id=attempt_id,
            worker_id=worker_id,
            expected=(DeliveryAttemptStatus.SENDING,),
            target=DeliveryAttemptStatus.UNKNOWN,
            now=now,
            require_send_started=True,
            values={
                "finished_at": now,
                "discord_message_id": None,
                "error_kind": "unknown",
                "error_code": error_code,
                "error_summary": error_summary,
            },
        )

    async def mark_unknown_after_expiry(
        self,
        *,
        attempt_id: int,
        worker_id: uuid.UUID,
        now: datetime,
        error_code: str,
        error_summary: str,
    ) -> DeliveryAttempt:
        return await self._transition(
            attempt_id=attempt_id,
            worker_id=worker_id,
            expected=(DeliveryAttemptStatus.SENDING, DeliveryAttemptStatus.UNKNOWN),
            target=DeliveryAttemptStatus.UNKNOWN,
            now=now,
            require_send_started=True,
            values={
                "finished_at": now,
                "discord_message_id": None,
                "error_kind": "unknown",
                "error_code": error_code,
                "error_summary": error_summary,
            },
        )

    async def _transition(
        self,
        *,
        attempt_id: int,
        worker_id: uuid.UUID,
        expected: tuple[DeliveryAttemptStatus, ...],
        target: DeliveryAttemptStatus,
        now: datetime,
        require_send_started: bool,
        values: Mapping[str, Any],
    ) -> DeliveryAttempt:
        now = require_utc(now)
        _validate_worker_id(worker_id)
        conditions = [
            DeliveryAttempt.id == attempt_id,
            DeliveryAttempt.claimed_by == worker_id,
            DeliveryAttempt.status.in_(status.value for status in expected),
            DeliveryAttempt.claimed_at <= now,
        ]
        if require_send_started:
            conditions.append(DeliveryAttempt.send_started_at <= now)
        elif DeliveryAttemptStatus.SENDING in expected:
            conditions.append(
                or_(
                    DeliveryAttempt.status == DeliveryAttemptStatus.CLAIMED.value,
                    DeliveryAttempt.send_started_at <= now,
                )
            )
        statement = (
            update(DeliveryAttempt)
            .where(*conditions)
            .values(status=target.value, **values)
            .returning(DeliveryAttempt)
            .execution_options(synchronize_session=False, populate_existing=True)
        )
        attempt = (await self._session.execute(statement)).scalar_one_or_none()
        if attempt is None:
            await self._raise_transition_error(
                attempt_id=attempt_id, worker_id=worker_id, expected=expected
            )
        return attempt

    async def _raise_transition_error(
        self,
        *,
        attempt_id: int,
        worker_id: uuid.UUID,
        expected: tuple[DeliveryAttemptStatus, ...],
    ) -> None:
        attempt = await self._session.get(DeliveryAttempt, attempt_id, populate_existing=True)
        if attempt is None:
            raise RepositoryNotFoundError("delivery attempt was not found")
        if attempt.claimed_by != worker_id:
            raise RepositoryOwnershipError("delivery attempt belongs to another worker")
        expected_values = {status.value for status in expected}
        if attempt.status not in expected_values:
            raise RepositoryStateConflictError("delivery attempt is not in the expected state")
        raise RepositoryStateConflictError("delivery attempt timestamps do not permit the update")


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
