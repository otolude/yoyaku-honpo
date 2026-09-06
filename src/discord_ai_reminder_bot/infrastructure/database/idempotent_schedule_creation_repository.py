"""PostgreSQL-owned transactions for one fixed schedule creation key."""

from __future__ import annotations

import asyncio
import math
from datetime import datetime
from enum import Enum, auto
from typing import NoReturn

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import InstrumentedAttribute

from discord_ai_reminder_bot.application.idempotent_schedule_creation import (
    CreationNotificationState,
    IdempotentScheduleCreationCode,
    IdempotentScheduleCreationResult,
    OnceScheduleCreationFingerprint,
    ScheduleCreationFingerprint,
    ScheduleCreationPublicId,
    ScheduleCreationRecord,
    ScheduleCreationWaitLimit,
    classify_schedule_creation_record,
)
from discord_ai_reminder_bot.application.name_generation import (
    NameGenerationRegistrationPolicy,
)
from discord_ai_reminder_bot.application.schedule_creation import (
    DuplicateScheduleWarning,
    OnceScheduleCreationService,
    RecurringScheduleCreationService,
)
from discord_ai_reminder_bot.domain.enums import (
    ActorType,
    NameGenerationJobStatus,
    OperationAction,
    RunStatus,
    ScheduleType,
)
from discord_ai_reminder_bot.infrastructure.database.models import (
    DeliveryAttempt,
    NameGenerationJob,
    NotificationAttempt,
    NotificationLog,
    OperationLog,
    Schedule,
    ScheduleRun,
)

SCHEDULE_CREATION_LOCK_TIMEOUT_SQL = "SELECT set_config('lock_timeout', :value, true)"
_INVALID_SESSION_FACTORY = "invalid idempotent schedule creation session factory"
_INCOMPLETE_GRAPH = "schedule creation graph is incomplete"


class _Observation(Enum):
    MISSING = auto()
    UNAVAILABLE = auto()


class _WriteFailure(Enum):
    BUSINESS_CONFLICT = auto()
    UNKNOWN = auto()


class PostgreSQLIdempotentScheduleCreationRepository:
    """Create once with short transactions and one non-retrying reconciliation read."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        wait_limit: ScheduleCreationWaitLimit,
    ) -> None:
        if not isinstance(session_factory, async_sessionmaker):
            raise TypeError(_INVALID_SESSION_FACTORY)
        if not isinstance(wait_limit, ScheduleCreationWaitLimit):
            raise ValueError("invalid schedule creation wait limit")  # noqa: TRY004
        self._sessions = session_factory
        self._wait_limit = wait_limit

    async def create(
        self,
        fingerprint: ScheduleCreationFingerprint,
        *,
        operation_at: datetime,
    ) -> IdempotentScheduleCreationResult:
        before = await self._observe(fingerprint.public_id)
        if before is _Observation.UNAVAILABLE:
            return IdempotentScheduleCreationResult(IdempotentScheduleCreationCode.UNKNOWN)
        if isinstance(before, ScheduleCreationRecord):
            return IdempotentScheduleCreationResult(
                classify_schedule_creation_record(fingerprint, before)
            )
        cancelled: asyncio.CancelledError | None = None
        failure: _WriteFailure | None = None
        try:
            async with self._sessions() as session, session.begin():
                await self._set_local_wait_limit(session)
                await self._create_graph(session, fingerprint, operation_at=operation_at)
                await self._add_creation_log(session, fingerprint)
                await session.flush()
                observed = await self._observe_in_session(session, fingerprint.public_id)
                if not isinstance(observed, ScheduleCreationRecord) or (
                    classify_schedule_creation_record(fingerprint, observed)
                    is not IdempotentScheduleCreationCode.ALREADY_CREATED
                ):
                    raise RuntimeError(_INCOMPLETE_GRAPH)
        except asyncio.CancelledError as caught:
            cancelled = caught
        except DuplicateScheduleWarning:
            failure = _WriteFailure.BUSINESS_CONFLICT
        except Exception:  # noqa: BLE001 - persistence details stop at this adapter
            failure = _WriteFailure.UNKNOWN

        if cancelled is not None:
            _raise_detached_cancellation(cancelled)
        if failure is not None:
            reconciliation: IdempotentScheduleCreationResult | None = None
            try:
                reconciliation = await self._reconcile_after_failure(fingerprint, failure=failure)
            except asyncio.CancelledError as caught:
                cancelled = caught
            except Exception:  # noqa: BLE001 - an unprovable read has one fixed result
                reconciliation = IdempotentScheduleCreationResult(
                    IdempotentScheduleCreationCode.UNKNOWN
                )
            if cancelled is not None:
                _raise_detached_cancellation(cancelled)
            if reconciliation is not None:
                return reconciliation
            return IdempotentScheduleCreationResult(IdempotentScheduleCreationCode.UNKNOWN)
        return IdempotentScheduleCreationResult(IdempotentScheduleCreationCode.CREATED)

    async def _set_local_wait_limit(self, session: AsyncSession) -> None:
        milliseconds = max(1, math.ceil(self._wait_limit.seconds * 1000))
        await session.execute(
            text(SCHEDULE_CREATION_LOCK_TIMEOUT_SQL),
            {"value": f"{milliseconds}ms"},
        )

    async def _create_graph(
        self,
        session: AsyncSession,
        fingerprint: ScheduleCreationFingerprint,
        *,
        operation_at: datetime,
    ) -> None:
        policy = NameGenerationRegistrationPolicy(
            enabled=fingerprint.name_generation_enabled,
            generator_available=fingerprint.name_generator_available,
        )
        configured_guild_id = (
            fingerprint.guild_id if fingerprint.notification_planning_enabled else None
        )
        if isinstance(fingerprint, OnceScheduleCreationFingerprint):
            await OnceScheduleCreationService(
                session,
                configured_guild_id=configured_guild_id,
                name_generation_policy=policy,
            ).create(
                guild_id=fingerprint.guild_id,
                channel_id=fingerprint.channel_id,
                creator_user_id=fingerprint.creator_user_id,
                scheduled_for=fingerprint.next_run_at,
                content=fingerprint.content,
                allow_duplicate=fingerprint.allow_duplicate,
                now=operation_at,
                public_id=fingerprint.public_id,
            )
            return
        await RecurringScheduleCreationService(
            session,
            configured_guild_id=configured_guild_id,
            name_generation_policy=policy,
        ).create(
            guild_id=fingerprint.guild_id,
            channel_id=fingerprint.channel_id,
            creator_user_id=fingerprint.creator_user_id,
            schedule_type=ScheduleType(fingerprint.schedule_type),
            local_time=fingerprint.local_time,
            weekday=fingerprint.weekday,
            end_date=fingerprint.end_date,
            content=fingerprint.content,
            allow_duplicate=fingerprint.allow_duplicate,
            now=operation_at,
            public_id=fingerprint.public_id,
        )

    async def _reconcile_after_failure(
        self,
        fingerprint: ScheduleCreationFingerprint,
        *,
        failure: _WriteFailure,
    ) -> IdempotentScheduleCreationResult:
        observed = await self._observe(fingerprint.public_id)
        if isinstance(observed, ScheduleCreationRecord):
            code = classify_schedule_creation_record(fingerprint, observed)
        elif failure is _WriteFailure.BUSINESS_CONFLICT:
            code = IdempotentScheduleCreationCode.CONFLICT
        else:
            code = IdempotentScheduleCreationCode.UNKNOWN
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
            raise RuntimeError(_INCOMPLETE_GRAPH)
        session.add(
            OperationLog(
                schedule_id=schedule_id,
                action=OperationAction.CREATED.value,
                actor_type=ActorType.USER.value,
                actor_user_id=fingerprint.creator_user_id,
                changes={
                    "allow_duplicate": fingerprint.allow_duplicate,
                    "name_generation_enabled": fingerprint.name_generation_enabled,
                    "name_generator_available": fingerprint.name_generator_available,
                    "notification_planning_enabled": fingerprint.notification_planning_enabled,
                },
            )
        )

    async def _observe(
        self, public_id: ScheduleCreationPublicId
    ) -> ScheduleCreationRecord | _Observation:
        cancelled: asyncio.CancelledError | None = None
        try:
            async with self._sessions() as session, session.begin():
                return await self._observe_in_session(session, public_id)
        except asyncio.CancelledError as caught:
            cancelled = caught
        except Exception:  # noqa: BLE001 - an unprovable read has one fixed result
            return _Observation.UNAVAILABLE
        if cancelled is not None:
            _raise_detached_cancellation(cancelled)
        return _Observation.UNAVAILABLE

    async def _observe_in_session(
        self,
        session: AsyncSession,
        public_id: ScheduleCreationPublicId,
    ) -> ScheduleCreationRecord | _Observation:
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
                    .where(OperationLog.schedule_id == schedule.id)
                    .order_by(OperationLog.id)
                )
            ).all()
        )
        notifications = list(
            (
                await session.scalars(
                    select(NotificationLog)
                    .where(NotificationLog.schedule_id == schedule.id)
                    .order_by(NotificationLog.notification_type, NotificationLog.scheduled_at)
                )
            ).all()
        )
        name_jobs = list(
            (
                await session.scalars(
                    select(NameGenerationJob)
                    .where(NameGenerationJob.schedule_id == schedule.id)
                    .order_by(NameGenerationJob.id)
                )
            ).all()
        )
        run_ids = [item.id for item in runs]
        notification_ids = [item.id for item in notifications]
        delivery_attempt_count = await _count_for_ids(
            session, DeliveryAttempt, DeliveryAttempt.schedule_run_id, run_ids
        )
        notification_attempt_count = await _count_for_ids(
            session, NotificationAttempt, NotificationAttempt.notification_log_id, notification_ids
        )

        run = runs[0] if len(runs) == 1 else None
        creation_log = creation_logs[0] if len(creation_logs) == 1 else None
        changes = creation_log.changes if creation_log is not None else None
        expected_change_keys = {
            "allow_duplicate",
            "name_generation_enabled",
            "name_generator_available",
            "notification_planning_enabled",
        }
        valid_changes = (
            isinstance(changes, dict)
            and set(changes) == expected_change_keys
            and all(isinstance(changes[key], bool) for key in expected_change_keys)
        )
        notification_states = tuple(
            CreationNotificationState(
                notification_type=item.notification_type,
                recipient_type=item.recipient_type,
                recipient_id=item.recipient_id,
                status=item.status,
                deduplication_key=item.deduplication_key,
                scheduled_at=item.scheduled_at,
                next_attempt_at=item.next_attempt_at,
                attempt_count=item.attempt_count,
                run_linked=(run is not None and item.schedule_run_id == run.id),
                pristine=(
                    item.created_at is not None
                    and item.claimed_by is None
                    and item.claimed_at is None
                    and item.lease_expires_at is None
                    and item.started_at is None
                    and item.finished_at is None
                    and item.sent_at is None
                    and item.error_code is None
                    and item.error_summary is None
                ),
            )
            for item in notifications
        )
        name_job = name_jobs[0] if len(name_jobs) == 1 else None
        name_job_pristine = name_job is None or (
            name_job.created_at is not None
            and name_job.updated_at is not None
            and name_job.expected_schedule_version == 1
            and name_job.status == NameGenerationJobStatus.PENDING.value
            and name_job.reserved_cost_microunits == 0
            and name_job.claimed_at is None
            and name_job.lease_expires_at is None
            and name_job.started_at is None
            and name_job.finished_at is None
            and name_job.result_code is None
            and name_job.created_at == name_job.updated_at
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
            schedule_version=schedule.version,
            schedule_timestamps_pristine=(
                schedule.created_at is not None
                and schedule.updated_at is not None
                and schedule.created_at == schedule.updated_at
            ),
            display_name=schedule.display_name,
            display_name_source=schedule.display_name_source,
            deleted_at=schedule.deleted_at,
            terminal_at=schedule.terminal_at,
            run_count=len(runs),
            pending_run_count=sum(item.status == RunStatus.PENDING.value for item in runs),
            run_scheduled_for=run.scheduled_for if run is not None else None,
            run_status=run.status if run is not None else None,
            run_next_attempt_at=run.next_attempt_at if run is not None else None,
            run_attempt_count=run.attempt_count if run is not None else None,
            run_claimed_by_present=run is not None and run.claimed_by is not None,
            run_claimed_at=run.claimed_at if run is not None else None,
            run_lease_expires_at=run.lease_expires_at if run is not None else None,
            run_started_at=run.started_at if run is not None else None,
            run_finished_at=run.finished_at if run is not None else None,
            run_discord_message_id_present=(run is not None and run.discord_message_id is not None),
            run_result_code_present=run is not None and run.result_code is not None,
            run_error_summary_present=run is not None and run.error_summary is not None,
            run_timestamps_pristine=(
                run is not None
                and run.created_at is not None
                and run.updated_at is not None
                and run.created_at == run.updated_at
            ),
            creation_log_count=len(creation_logs),
            creation_actor_user_id=(
                creation_log.actor_user_id
                if creation_log is not None and creation_log.actor_type == ActorType.USER.value
                else None
            ),
            creation_allow_duplicate=(changes["allow_duplicate"] if valid_changes else None),
            creation_notification_planning_enabled=(
                changes["notification_planning_enabled"] if valid_changes else False
            ),
            creation_name_generation_enabled=(
                changes["name_generation_enabled"] if valid_changes else False
            ),
            creation_name_generator_available=(
                changes["name_generator_available"] if valid_changes else False
            ),
            creation_log_pristine=(
                creation_log is not None
                and creation_log.action == OperationAction.CREATED.value
                and creation_log.created_at is not None
                and creation_log.delete_kind is None
                and creation_log.delete_reason is None
            ),
            notifications=notification_states,
            notification_attempt_count=notification_attempt_count,
            delivery_attempt_count=delivery_attempt_count,
            name_job_count=len(name_jobs),
            name_job_pristine=name_job_pristine,
        )


def _raise_detached_cancellation(error: asyncio.CancelledError) -> NoReturn:
    error.__cause__ = None
    error.__context__ = None
    error.__traceback__ = None
    raise error


async def _count_for_ids(
    session: AsyncSession,
    model: type[DeliveryAttempt | NotificationAttempt],
    column: InstrumentedAttribute[int],
    identifiers: list[int],
) -> int:
    if not identifiers:
        return 0
    statement = select(func.count()).select_from(model).where(column.in_(identifiers))
    return int(await session.scalar(statement) or 0)
