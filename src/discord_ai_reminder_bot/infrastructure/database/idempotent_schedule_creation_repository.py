"""Short PostgreSQL transactions for one fixed schedule creation key."""

from __future__ import annotations

import asyncio
from enum import Enum, auto

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from discord_ai_reminder_bot.application.schedule_creation import (
    DuplicateScheduleWarning,
    IdempotentScheduleCreationCode,
    IdempotentScheduleCreationResult,
    ScheduleCreationAction,
    ScheduleCreationFingerprint,
    ScheduleCreationPublicId,
    ScheduleCreationRecord,
    classify_schedule_creation_record,
)
from discord_ai_reminder_bot.domain.enums import ActorType, OperationAction, RunStatus
from discord_ai_reminder_bot.infrastructure.database.models import (
    OperationLog,
    Schedule,
    ScheduleRun,
)

SCHEDULE_CREATION_LOCK_TIMEOUT_SQL = "SET LOCAL lock_timeout = '1s'"
_INVALID_SESSION_FACTORY = "invalid idempotent schedule creation session factory"


class _Observation(Enum):
    MISSING = auto()
    UNAVAILABLE = auto()


class PostgreSQLIdempotentScheduleCreationRepository:
    """Own write and reconciliation transactions without retrying an INSERT."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        if not isinstance(session_factory, async_sessionmaker):
            raise TypeError(_INVALID_SESSION_FACTORY)
        self._sessions = session_factory

    async def create(
        self,
        fingerprint: ScheduleCreationFingerprint,
        action: ScheduleCreationAction,
    ) -> IdempotentScheduleCreationResult:
        before = await self._observe(fingerprint.public_id)
        if before is _Observation.UNAVAILABLE:
            return IdempotentScheduleCreationResult(IdempotentScheduleCreationCode.UNKNOWN)
        if isinstance(before, ScheduleCreationRecord):
            return IdempotentScheduleCreationResult(
                classify_schedule_creation_record(fingerprint, before)
            )

        try:
            async with self._sessions() as session, session.begin():
                await session.execute(text(SCHEDULE_CREATION_LOCK_TIMEOUT_SQL))
                await action(session)
                await self._add_creation_log(session, fingerprint)
        except asyncio.CancelledError:
            raise
        except DuplicateScheduleWarning:
            return await self._reconcile_after_failure(
                fingerprint, missing=IdempotentScheduleCreationCode.CONFLICT
            )
        except Exception:  # noqa: BLE001 - persistence details must not cross this boundary
            return await self._reconcile_after_failure(
                fingerprint, missing=IdempotentScheduleCreationCode.UNKNOWN
            )
        return IdempotentScheduleCreationResult(IdempotentScheduleCreationCode.CREATED)

    async def _reconcile_after_failure(
        self,
        fingerprint: ScheduleCreationFingerprint,
        *,
        missing: IdempotentScheduleCreationCode,
    ) -> IdempotentScheduleCreationResult:
        observed = await self._observe(fingerprint.public_id)
        if observed is _Observation.UNAVAILABLE:
            code = IdempotentScheduleCreationCode.UNKNOWN
        elif observed is _Observation.MISSING:
            code = missing
        else:
            code = classify_schedule_creation_record(fingerprint, observed)
        return IdempotentScheduleCreationResult(code)

    async def _add_creation_log(
        self,
        session: AsyncSession,
        fingerprint: ScheduleCreationFingerprint,
    ) -> None:
        schedule_id = await session.scalar(
            select(Schedule.id).where(Schedule.public_id == fingerprint.public_id.value)
        )
        if schedule_id is None:
            raise RuntimeError("schedule creation graph is incomplete")
        session.add(
            OperationLog(
                schedule_id=schedule_id,
                action=OperationAction.CREATED.value,
                actor_type=ActorType.USER.value,
                actor_user_id=fingerprint.creator_user_id,
                changes={"allow_duplicate": fingerprint.allow_duplicate},
            )
        )
        await session.flush()

    async def _observe(
        self, public_id: ScheduleCreationPublicId
    ) -> ScheduleCreationRecord | _Observation:
        try:
            async with self._sessions() as session, session.begin():
                schedule = await session.scalar(
                    select(Schedule).where(Schedule.public_id == public_id.value)
                )
                if schedule is None:
                    return _Observation.MISSING
                runs = list(
                    (
                        await session.scalars(
                            select(ScheduleRun)
                            .where(ScheduleRun.schedule_id == schedule.id)
                            .order_by(ScheduleRun.id)
                        )
                    ).all()
                )
                creation_logs = list(
                    (
                        await session.scalars(
                            select(OperationLog)
                            .where(
                                OperationLog.schedule_id == schedule.id,
                                OperationLog.action == OperationAction.CREATED.value,
                            )
                            .order_by(OperationLog.id)
                        )
                    ).all()
                )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - an unprovable read has one fixed result
            return _Observation.UNAVAILABLE

        run = runs[0] if len(runs) == 1 else None
        creation_log = creation_logs[0] if len(creation_logs) == 1 else None
        changes = creation_log.changes if creation_log is not None else None
        allow_duplicate = (
            changes["allow_duplicate"]
            if isinstance(changes, dict)
            and set(changes) == {"allow_duplicate"}
            and isinstance(changes["allow_duplicate"], bool)
            else None
        )
        return ScheduleCreationRecord(
            public_id=ScheduleCreationPublicId.create(schedule.public_id),
            guild_id=schedule.guild_id,
            channel_id=schedule.channel_id,
            creator_user_id=schedule.creator_user_id,
            schedule_type=schedule.schedule_type,
            status=schedule.status,
            content=schedule.content,
            next_run_at=schedule.next_run_at,
            local_time=schedule.local_time,
            weekday=schedule.weekday,
            end_date=schedule.end_date,
            run_count=len(runs),
            pending_run_count=sum(item.status == RunStatus.PENDING.value for item in runs),
            run_scheduled_for=run.scheduled_for if run is not None else None,
            run_next_attempt_at=run.next_attempt_at if run is not None else None,
            run_attempt_count=run.attempt_count if run is not None else None,
            creation_log_count=len(creation_logs),
            creation_actor_user_id=(
                creation_log.actor_user_id
                if creation_log is not None and creation_log.actor_type == ActorType.USER.value
                else None
            ),
            creation_allow_duplicate=allow_duplicate,
        )
