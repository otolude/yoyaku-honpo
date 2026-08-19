"""Basic async repositories without transaction ownership."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import Select, and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from discord_ai_reminder_bot.domain.enums import (
    DeliveryAttemptStatus,
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
            limit=limit,
            offset=offset,
            exclude_deleted=exclude_deleted,
        )

    async def list_by_guild(
        self,
        *,
        guild_id: int,
        status: ScheduleStatus | None = None,
        limit: int = 10,
        offset: int = 0,
        exclude_deleted: bool = False,
    ) -> list[Schedule]:
        statement = select(Schedule).where(Schedule.guild_id == guild_id)
        return await self._list(
            statement,
            status=status,
            limit=limit,
            offset=offset,
            exclude_deleted=exclude_deleted,
        )

    async def _list(
        self,
        statement,
        *,
        status: ScheduleStatus | None,
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
        self, *, runs: list[ScheduleRun], paused_at: datetime
    ) -> list[ScheduleRun]:
        paused_at = require_utc(paused_at)
        for run in runs:
            if run.status != RunStatus.PENDING.value:
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
