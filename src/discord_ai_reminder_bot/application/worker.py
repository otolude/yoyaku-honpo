"""One-cycle, Discord-independent database polling worker."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from discord_ai_reminder_bot.application.delivery import DeliveryService
from discord_ai_reminder_bot.application.gateway import (
    MessageGateway,
    OutboundMessage,
    PermanentGatewayError,
    RateLimitGatewayError,
    TransientGatewayError,
    UnknownGatewayError,
)
from discord_ai_reminder_bot.application.notification_events import NotificationEventService
from discord_ai_reminder_bot.application.schedule_execution import ScheduleExecutionService
from discord_ai_reminder_bot.config import MAX_POSTGRES_BIGINT
from discord_ai_reminder_bot.domain.clock import Clock
from discord_ai_reminder_bot.domain.enums import (
    DeliveryAttemptStatus,
    DeliveryErrorKind,
    NotificationType,
    RunStatus,
    ScheduleStatus,
)
from discord_ai_reminder_bot.domain.recurrence import require_utc
from discord_ai_reminder_bot.infrastructure.database.models import (
    DeliveryAttempt,
    Schedule,
    ScheduleRun,
)
from discord_ai_reminder_bot.infrastructure.database.repositories import ScheduleRunRepository


class ItemResult(StrEnum):
    SUCCEEDED = "succeeded"
    RETRY_SCHEDULED = "retry_scheduled"
    FAILED = "failed"
    UNKNOWN = "unknown"
    SKIPPED = "skipped"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True)
class PollResult:
    claimed: int = 0
    succeeded: int = 0
    retry_scheduled: int = 0
    failed: int = 0
    unknown: int = 0
    skipped: int = 0
    internal_errors: int = 0


@dataclass(frozen=True)
class _Claim:
    run_id: int
    attempt_id: int


@dataclass(frozen=True)
class _Ready:
    claim: _Claim
    message: OutboundMessage


_SKIP_CODES = {
    ScheduleStatus.DRAFT: "draft_without_content",
    ScheduleStatus.PAUSED: "schedule_paused",
    ScheduleStatus.DELETED: "schedule_deleted",
    ScheduleStatus.ENDED: "schedule_ended",
}


class PollingWorker:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        gateway: MessageGateway,
        clock: Clock,
        worker_id: uuid.UUID,
        batch_size: int,
        max_concurrency: int,
        lease_timeout: timedelta,
        logger: logging.Logger,
        configured_guild_id: int | None = None,
        operator_channel_id: int | None = None,
    ) -> None:
        if not isinstance(worker_id, uuid.UUID):
            raise TypeError("worker_id must be a UUID")
        if not 1 <= batch_size <= 20:
            raise ValueError("batch_size must be between 1 and 20")
        if not 1 <= max_concurrency <= batch_size:
            raise ValueError("max_concurrency must be between 1 and batch_size")
        if lease_timeout <= timedelta(0):
            raise ValueError("lease_timeout must be positive")
        self._sessions = session_factory
        self._gateway = gateway
        self._clock = clock
        self._worker_id = worker_id
        self._batch_size = batch_size
        self._lease_timeout = lease_timeout
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._logger = logger
        self._configured_guild_id = configured_guild_id
        self._operator_channel_id = operator_channel_id

    async def poll_once(self) -> PollResult:
        """Claim and completely process at most one configured batch, without waiting."""
        now = require_utc(self._clock.now())
        try:
            async with self._sessions() as session, session.begin():
                rows = await ScheduleRunRepository(session).claim_due(
                    now=now,
                    worker_id=self._worker_id,
                    batch_size=self._batch_size,
                    lease_timeout=self._lease_timeout,
                )
                claims = [_Claim(item.run.id, item.attempt.id) for item in rows]
        except Exception:  # noqa: BLE001 - transaction failures are isolated from delivery
            self._logger.error("poll_claim_failed", extra={"worker_id": str(self._worker_id)})
            return PollResult(internal_errors=1)

        tasks = [asyncio.create_task(self._guarded_process(item)) for item in claims]
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        counts = {item: 0 for item in ItemResult}
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                counts[ItemResult.INTERNAL_ERROR] += 1
                self._logger.error(
                    "poll_task_unexpected_failure", extra={"worker_id": str(self._worker_id)}
                )
            else:
                counts[outcome] += 1
        return PollResult(
            claimed=len(claims),
            succeeded=counts[ItemResult.SUCCEEDED],
            retry_scheduled=counts[ItemResult.RETRY_SCHEDULED],
            failed=counts[ItemResult.FAILED],
            unknown=counts[ItemResult.UNKNOWN],
            skipped=counts[ItemResult.SKIPPED],
            internal_errors=counts[ItemResult.INTERNAL_ERROR],
        )

    async def _guarded_process(self, claim: _Claim) -> ItemResult:
        async with self._semaphore:
            try:
                return await self._process(claim)
            except Exception:  # noqa: BLE001 - one item must not cancel sibling tasks
                self._logger.error("poll_item_internal_error")
                return ItemResult.INTERNAL_ERROR

    async def _process(self, claim: _Claim) -> ItemResult:
        ready = await self._prepare(claim)
        if ready is None:
            return ItemResult.SKIPPED

        try:
            message_id = await self._gateway.send(ready.message)
        except UnknownGatewayError:
            return await self._save_unknown(claim)
        except RateLimitGatewayError as error:
            if error.retry_at <= require_utc(self._clock.now()):
                return await self._save_failure(
                    claim, DeliveryErrorKind.PERMANENT, "invalid_rate_limit", None
                )
            return await self._save_failure(
                claim, DeliveryErrorKind.TRANSIENT, error.error_code, error.retry_at
            )
        except TransientGatewayError as error:
            return await self._save_failure(
                claim, DeliveryErrorKind.TRANSIENT, error.error_code, None
            )
        except PermanentGatewayError as error:
            return await self._save_failure(
                claim, DeliveryErrorKind.PERMANENT, error.error_code, None
            )
        except Exception:  # noqa: BLE001 - unclassified adapter failures are result-unknown
            # An unclassified adapter exception may have crossed the HTTP boundary.
            return await self._save_unknown(claim)

        if isinstance(message_id, bool) or not 1 <= message_id <= MAX_POSTGRES_BIGINT:
            return await self._save_unknown(claim)
        try:
            async with self._sessions() as session, session.begin():
                update = await DeliveryService(session).complete_success(
                    attempt_id=claim.attempt_id,
                    worker_id=self._worker_id,
                    now=require_utc(self._clock.now()),
                    message_id=message_id,
                )
                run_id = update.run.id
        except Exception:  # noqa: BLE001 - preserve sending state for lease recovery
            self._logger.error(
                "delivery_success_persist_failed",
                extra={"worker_id": str(self._worker_id), "run_id": claim.run_id},
            )
            return ItemResult.INTERNAL_ERROR
        await self._finalize(run_id)
        return ItemResult.SUCCEEDED

    async def _prepare(self, claim: _Claim) -> _Ready | None:
        now = require_utc(self._clock.now())
        async with self._sessions() as session, session.begin():
            run = await _lock_one(session, ScheduleRun, claim.run_id)
            attempt = await _lock_one(session, DeliveryAttempt, claim.attempt_id)
            if run.id != attempt.schedule_run_id or run.schedule_id <= 0:
                raise RuntimeError("claimed run and attempt are inconsistent")
            if (
                run.status != RunStatus.PROCESSING.value
                or attempt.status != DeliveryAttemptStatus.CLAIMED.value
                or run.claimed_by != self._worker_id
                or attempt.claimed_by != self._worker_id
                or run.lease_expires_at is None
                or run.lease_expires_at < now
            ):
                raise RuntimeError("claimed run ownership or lease is invalid")
            schedule = await _lock_one(session, Schedule, run.schedule_id)
            status = ScheduleStatus(schedule.status)
            if status in _SKIP_CODES:
                update = await DeliveryService(session).skip_before_send(
                    attempt_id=attempt.id,
                    worker_id=self._worker_id,
                    now=now,
                    result_code=_SKIP_CODES[status],
                    error_summary="Schedule was not sendable before Discord delivery",
                )
                if status is ScheduleStatus.DRAFT:
                    events = self._notification_events(session)
                    if events is not None:
                        await events.add_run_event(
                            schedule=schedule,
                            run=update.run,
                            notification_type=NotificationType.RUN_SKIPPED,
                            event_at=now,
                        )
                skipped_id = run.id
            elif status is not ScheduleStatus.ACTIVE:
                raise RuntimeError("schedule is not eligible for delivery")
            else:
                if (
                    not isinstance(schedule.content, str)
                    or not 1 <= len(schedule.content) <= 2000
                    or not 1 <= schedule.guild_id <= MAX_POSTGRES_BIGINT
                    or not 1 <= schedule.channel_id <= MAX_POSTGRES_BIGINT
                    or schedule.next_run_at != run.scheduled_for
                ):
                    raise RuntimeError("active schedule and run are inconsistent")
                await DeliveryService(session).start_sending(
                    attempt_id=attempt.id, worker_id=self._worker_id, now=now
                )
                return _Ready(
                    claim,
                    OutboundMessage(
                        guild_id=schedule.guild_id,
                        channel_id=schedule.channel_id,
                        content=schedule.content,
                        schedule_public_id=schedule.public_id,
                        schedule_run_id=run.id,
                    ),
                )
        await self._finalize(skipped_id)
        return None

    async def _save_failure(
        self,
        claim: _Claim,
        kind: DeliveryErrorKind,
        code: str,
        retry_at,
    ) -> ItemResult:
        async with self._sessions() as session, session.begin():
            now = require_utc(self._clock.now())
            _run, _attempt, schedule = await _lock_delivery_context(session, claim)
            update = await DeliveryService(session).complete_failure(
                attempt_id=claim.attempt_id,
                worker_id=self._worker_id,
                now=now,
                error_kind=kind,
                error_code=code,
                error_summary=(
                    "Discord delivery was permanently rejected"
                    if kind is DeliveryErrorKind.PERMANENT
                    else "Discord delivery failed temporarily before a result was returned"
                ),
                retry_at=retry_at,
            )
            run_id, pending = update.run.id, update.run.status == RunStatus.PENDING.value
            if not pending:
                events = self._notification_events(session)
                if events is not None:
                    await events.add_run_event(
                        schedule=schedule,
                        run=update.run,
                        notification_type=NotificationType.RUN_FAILED,
                        event_at=now,
                    )
        if pending:
            return ItemResult.RETRY_SCHEDULED
        await self._finalize(run_id)
        return ItemResult.FAILED

    async def _save_unknown(self, claim: _Claim) -> ItemResult:
        async with self._sessions() as session, session.begin():
            now = require_utc(self._clock.now())
            _run, _attempt, schedule = await _lock_delivery_context(session, claim)
            update = await DeliveryService(session).complete_unknown(
                attempt_id=claim.attempt_id,
                worker_id=self._worker_id,
                now=now,
            )
            events = self._notification_events(session)
            if events is not None:
                await events.add_run_event(
                    schedule=schedule,
                    run=update.run,
                    notification_type=NotificationType.RUN_FAILED,
                    event_at=now,
                )
            run_id = update.run.id
        await self._finalize(run_id)
        return ItemResult.UNKNOWN

    async def _finalize(self, run_id: int) -> None:
        async with self._sessions() as session, session.begin():
            await ScheduleExecutionService(
                session, configured_guild_id=self._configured_guild_id
            ).finalize_run(run_id=run_id, finalized_at=require_utc(self._clock.now()))

    def _notification_events(self, session: AsyncSession) -> NotificationEventService | None:
        if self._configured_guild_id is None or self._operator_channel_id is None:
            return None
        return NotificationEventService(
            session,
            configured_guild_id=self._configured_guild_id,
            operator_channel_id=self._operator_channel_id,
        )


async def _lock_one(session: AsyncSession, model, row_id: int):
    row = (
        await session.execute(select(model).where(model.id == row_id).with_for_update())
    ).scalar_one_or_none()
    if row is None:
        raise RuntimeError("worker row was not found")
    return row


async def _lock_delivery_context(
    session: AsyncSession, claim: _Claim
) -> tuple[ScheduleRun, DeliveryAttempt, Schedule]:
    run = await _lock_one(session, ScheduleRun, claim.run_id)
    attempt = await _lock_one(session, DeliveryAttempt, claim.attempt_id)
    if attempt.schedule_run_id != run.id:
        raise RuntimeError("claimed run and attempt are inconsistent")
    schedule = await _lock_one(session, Schedule, run.schedule_id)
    return run, attempt, schedule
