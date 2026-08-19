"""One-cycle notification outbox worker."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from discord_ai_reminder_bot.application.notification_gateway import (
    NotificationGateway,
    NotificationPermanentError,
    NotificationRateLimitError,
    NotificationTransientError,
    NotificationUnknownError,
)
from discord_ai_reminder_bot.application.notification_presenter import (
    NotificationPresentation,
    build_notification_message,
)
from discord_ai_reminder_bot.config import MAX_POSTGRES_BIGINT
from discord_ai_reminder_bot.domain.clock import Clock
from discord_ai_reminder_bot.domain.enums import (
    NotificationAttemptStatus,
    NotificationErrorKind,
    NotificationRecipientType,
    NotificationStatus,
    NotificationType,
    RunStatus,
    ScheduleStatus,
)
from discord_ai_reminder_bot.domain.notification import (
    NotificationDecisionAction,
    NotificationOutcome,
    decide_notification_result,
    fallback_notification_deduplication_key,
)
from discord_ai_reminder_bot.domain.recurrence import require_utc
from discord_ai_reminder_bot.infrastructure.database.models import (
    NotificationAttempt,
    NotificationLog,
    Schedule,
    ScheduleRun,
)
from discord_ai_reminder_bot.infrastructure.database.notification_repositories import (
    NotificationAttemptRepository,
    NotificationLogRepository,
)


class NotificationItemResult(StrEnum):
    SUCCEEDED = "succeeded"
    RETRY_SCHEDULED = "retry_scheduled"
    FAILED = "failed"
    UNKNOWN = "unknown"
    CANCELLED = "cancelled"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True)
class NotificationPollResult:
    claimed: int = 0
    succeeded: int = 0
    retry_scheduled: int = 0
    failed: int = 0
    unknown: int = 0
    cancelled: int = 0
    fallbacks_created: int = 0
    internal_errors: int = 0


@dataclass(frozen=True)
class _Claim:
    notification_id: int
    attempt_id: int


@dataclass(frozen=True)
class _Ready:
    claim: _Claim
    message: object


_DRAFT_TYPES = {
    NotificationType.DRAFT_24H,
    NotificationType.DRAFT_1H,
    NotificationType.DRAFT_IMMEDIATE,
}
_FALLBACK_ROUTES = {
    NotificationRecipientType.CREATOR_DM: NotificationRecipientType.OPERATOR_CHANNEL,
    NotificationRecipientType.OPERATOR_CHANNEL: NotificationRecipientType.OPERATOR_DM,
    NotificationRecipientType.OPERATOR_DM: NotificationRecipientType.LOG,
}


class NotificationWorker:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        gateway: NotificationGateway,
        clock: Clock,
        worker_id: uuid.UUID,
        configured_guild_id: int,
        operator_channel_id: int,
        operator_user_id: int,
        batch_size: int,
        max_concurrency: int,
        lease_timeout: timedelta,
        logger: logging.Logger,
    ) -> None:
        if not isinstance(worker_id, uuid.UUID):
            raise TypeError("worker_id must be a UUID")
        if isinstance(batch_size, bool) or not 1 <= batch_size <= 20:
            raise ValueError("batch_size must be between 1 and 20")
        if isinstance(max_concurrency, bool) or not 1 <= max_concurrency <= batch_size:
            raise ValueError("max_concurrency must be between 1 and batch_size")
        if not isinstance(lease_timeout, timedelta) or lease_timeout <= timedelta(0):
            raise ValueError("lease_timeout must be positive")
        for value in (configured_guild_id, operator_channel_id, operator_user_id):
            if isinstance(value, bool) or not 1 <= value <= MAX_POSTGRES_BIGINT:
                raise ValueError("configured Discord IDs must be positive BIGINT values")
        self._sessions = session_factory
        self._gateway = gateway
        self._clock = clock
        self._worker_id = worker_id
        self._guild_id = configured_guild_id
        self._operator_channel_id = operator_channel_id
        self._operator_user_id = operator_user_id
        self._batch_size = batch_size
        self._lease_timeout = lease_timeout
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._logger = logger

    async def poll_once(self) -> NotificationPollResult:
        now = require_utc(self._clock.now())
        try:
            async with self._sessions() as session, session.begin():
                rows = await NotificationLogRepository(session).claim_due(
                    now=now,
                    worker_id=self._worker_id,
                    batch_size=self._batch_size,
                    lease_timeout=self._lease_timeout,
                )
                claims = [_Claim(row.notification.id, row.attempt.id) for row in rows]
        except Exception:  # noqa: BLE001
            self._logger.error("notification_claim_failed")
            return NotificationPollResult(internal_errors=1)
        tasks = [asyncio.create_task(self._guarded_process(claim)) for claim in claims]
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        counts = {result: 0 for result in NotificationItemResult}
        fallbacks = 0
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                counts[NotificationItemResult.INTERNAL_ERROR] += 1
                self._logger.error("notification_task_unexpected_failure")
            else:
                result, created = outcome
                counts[result] += 1
                fallbacks += int(created)
        return NotificationPollResult(
            claimed=len(claims),
            succeeded=counts[NotificationItemResult.SUCCEEDED],
            retry_scheduled=counts[NotificationItemResult.RETRY_SCHEDULED],
            failed=counts[NotificationItemResult.FAILED],
            unknown=counts[NotificationItemResult.UNKNOWN],
            cancelled=counts[NotificationItemResult.CANCELLED],
            fallbacks_created=fallbacks,
            internal_errors=counts[NotificationItemResult.INTERNAL_ERROR],
        )

    async def _guarded_process(self, claim: _Claim) -> tuple[NotificationItemResult, bool]:
        async with self._semaphore:
            try:
                return await self._process(claim)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                self._logger.error("notification_item_internal_error")
                return NotificationItemResult.INTERNAL_ERROR, False

    async def _process(self, claim: _Claim) -> tuple[NotificationItemResult, bool]:
        ready = await self._prepare(claim)
        if ready is None:
            return NotificationItemResult.CANCELLED, False
        try:
            message_id = await self._gateway.send(ready.message)  # type: ignore[arg-type]
        except NotificationRateLimitError as error:
            return await self._save_failure(
                claim, NotificationOutcome.RATE_LIMITED, error.error_code, error.retry_at
            )
        except NotificationTransientError as error:
            return await self._save_failure(
                claim, NotificationOutcome.TRANSIENT, error.error_code, None
            )
        except NotificationPermanentError as error:
            return await self._save_failure(
                claim, NotificationOutcome.PERMANENT, error.error_code, None
            )
        except NotificationUnknownError:
            return await self._save_unknown(claim), False
        except Exception:  # noqa: BLE001
            return await self._save_unknown(claim), False
        if message_id is None:
            return await self._save_failure(
                claim, NotificationOutcome.PERMANENT, "log_route_terminal", None
            )
        if (
            isinstance(message_id, bool)
            or not isinstance(message_id, int)
            or not 1 <= message_id <= MAX_POSTGRES_BIGINT
        ):
            return await self._save_unknown(claim), False
        try:
            async with self._sessions() as session, session.begin():
                attempts = NotificationAttemptRepository(session)
                logs = NotificationLogRepository(session)
                now = require_utc(self._clock.now())
                await attempts.mark_succeeded(
                    attempt_id=claim.attempt_id,
                    worker_id=self._worker_id,
                    now=now,
                    message_id=message_id,
                )
                await logs.mark_succeeded(
                    notification_id=claim.notification_id, worker_id=self._worker_id, now=now
                )
        except Exception:  # noqa: BLE001
            self._logger.error("notification_success_persist_failed")
            return NotificationItemResult.INTERNAL_ERROR, False
        return NotificationItemResult.SUCCEEDED, False

    async def _prepare(self, claim: _Claim) -> _Ready | None:
        now = require_utc(self._clock.now())
        async with self._sessions() as session, session.begin():
            log = await _lock_one(session, NotificationLog, claim.notification_id)
            attempt = await _lock_one(session, NotificationAttempt, claim.attempt_id)
            if not _owned_claim(log, attempt, self._worker_id, now):
                raise RuntimeError("notification claim ownership is invalid")
            context = await self._validate_context(session, log, now)
            if context is None:
                await NotificationAttemptRepository(session).mark_failed(
                    attempt_id=attempt.id,
                    worker_id=self._worker_id,
                    now=now,
                    error_kind=NotificationErrorKind.PERMANENT,
                    error_code="notification_stale",
                    error_summary="Notification is no longer applicable",
                )
                await NotificationLogRepository(session).cancel(
                    notification_id=log.id,
                    worker_id=self._worker_id,
                    now=now,
                    error_code="notification_stale",
                    error_summary="Notification is no longer applicable",
                )
                return None
            await NotificationAttemptRepository(session).mark_sending(
                attempt_id=attempt.id, worker_id=self._worker_id, now=now
            )
            await NotificationLogRepository(session).mark_sending_started(
                notification_id=log.id, worker_id=self._worker_id, now=now
            )
            return _Ready(claim, build_notification_message(context))

    async def _validate_context(
        self, session: AsyncSession, log: NotificationLog, now
    ) -> NotificationPresentation | None:
        route = NotificationRecipientType(log.recipient_type)
        kind = NotificationType(log.notification_type)
        schedule = None
        run = None
        if log.schedule_id is not None:
            schedule = await _lock_one(session, Schedule, log.schedule_id)
            if schedule.guild_id != self._guild_id:
                return None
        if log.schedule_run_id is not None:
            run = await _lock_one(session, ScheduleRun, log.schedule_run_id)
            if schedule is None or run.schedule_id != schedule.id:
                return None
        if kind in _DRAFT_TYPES:
            if schedule is None or run is None:
                return None
            if (
                schedule.status != ScheduleStatus.DRAFT.value
                or schedule.content is not None
                or schedule.next_run_at != run.scheduled_for
                or run.status != RunStatus.PENDING.value
                or run.attempt_count != 0
                or run.scheduled_for <= now
                or _instant_token(run.scheduled_for) not in log.deduplication_key
                or not _draft_timing_is_valid(kind, log.scheduled_at, run.scheduled_for)
            ):
                return None
            if (
                route is NotificationRecipientType.CREATOR_DM
                and log.recipient_id != schedule.creator_user_id
            ):
                return None
            if (
                route is NotificationRecipientType.OPERATOR_CHANNEL
                and log.recipient_id != self._operator_channel_id
            ):
                return None
            if (
                route is NotificationRecipientType.OPERATOR_DM
                and log.recipient_id != self._operator_user_id
            ):
                return None
        else:
            if route is NotificationRecipientType.OPERATOR_CHANNEL:
                if log.recipient_id != self._operator_channel_id:
                    return None
            elif (
                route is NotificationRecipientType.OPERATOR_DM
                and log.recipient_id != self._operator_user_id
            ):
                return None
            if kind is not NotificationType.RECOVERY and (schedule is None or run is None):
                return None
            if (
                run is not None
                and kind is NotificationType.RUN_FAILED
                and run.status != RunStatus.FAILED.value
            ):
                return None
            if (
                run is not None
                and kind is NotificationType.RUN_SKIPPED
                and run.status != RunStatus.SKIPPED.value
            ):
                return None
        return NotificationPresentation(
            notification_type=kind,
            recipient_type=route,
            recipient_id=log.recipient_id,
            schedule_public_id=schedule.public_id if schedule else None,
            scheduled_for=run.scheduled_for if run else None,
            channel_id=schedule.channel_id if schedule else None,
            current_status=(run.status if run else "recovery_required"),
            is_fallback=route is not NotificationRecipientType.CREATOR_DM,
        )

    async def _save_failure(
        self,
        claim: _Claim,
        outcome: NotificationOutcome,
        error_code: str,
        retry_at,
    ) -> tuple[NotificationItemResult, bool]:
        async with self._sessions() as session, session.begin():
            log = await _lock_one(session, NotificationLog, claim.notification_id)
            attempt = await _lock_one(session, NotificationAttempt, claim.attempt_id)
            decision = decide_notification_result(
                attempt_number=attempt.attempt_number,
                outcome=outcome,
                decided_at=require_utc(self._clock.now()),
                retry_at=retry_at,
            )
            error_kind = {
                NotificationOutcome.TRANSIENT: NotificationErrorKind.TRANSIENT,
                NotificationOutcome.RATE_LIMITED: NotificationErrorKind.RATE_LIMITED,
                NotificationOutcome.PERMANENT: NotificationErrorKind.PERMANENT,
            }[outcome]
            now = require_utc(self._clock.now())
            await NotificationAttemptRepository(session).mark_failed(
                attempt_id=attempt.id,
                worker_id=self._worker_id,
                now=now,
                error_kind=error_kind,
                error_code=error_code,
                error_summary="Notification delivery failed safely",
            )
            logs = NotificationLogRepository(session)
            if decision.action is NotificationDecisionAction.RETRY:
                assert decision.next_attempt_at is not None
                await logs.return_to_pending(
                    notification_id=log.id,
                    worker_id=self._worker_id,
                    now=now,
                    retry_at=decision.next_attempt_at,
                    error_code=error_code,
                    error_summary="Notification delivery will be retried",
                )
                return NotificationItemResult.RETRY_SCHEDULED, False
            await logs.mark_failed(
                notification_id=log.id,
                worker_id=self._worker_id,
                now=now,
                error_code=error_code,
                error_summary="Notification delivery reached a safe terminal state",
            )
            fallback = await add_fallback_notification(
                session,
                source=log,
                now=now,
                operator_channel_id=self._operator_channel_id,
                operator_user_id=self._operator_user_id,
            )
            return NotificationItemResult.FAILED, fallback

    async def _save_unknown(self, claim: _Claim) -> NotificationItemResult:
        async with self._sessions() as session, session.begin():
            now = require_utc(self._clock.now())
            await NotificationAttemptRepository(session).mark_unknown(
                attempt_id=claim.attempt_id,
                worker_id=self._worker_id,
                now=now,
                error_code="notification_result_unknown",
                error_summary="Notification delivery result is unknown",
            )
            await NotificationLogRepository(session).mark_unknown(
                notification_id=claim.notification_id,
                worker_id=self._worker_id,
                now=now,
                error_code="notification_result_unknown",
                error_summary="Notification delivery result is unknown",
            )
        return NotificationItemResult.UNKNOWN


def _draft_timing_is_valid(kind: NotificationType, scheduled_at, run_at) -> bool:
    scheduled_at = require_utc(scheduled_at)
    run_at = require_utc(run_at)
    if kind is NotificationType.DRAFT_24H:
        return scheduled_at == run_at - timedelta(hours=24)
    if kind is NotificationType.DRAFT_1H:
        return scheduled_at == run_at - timedelta(hours=1)
    return scheduled_at < run_at and run_at - scheduled_at < timedelta(hours=1)


async def _lock_one(session: AsyncSession, model, row_id: int):
    row = (
        await session.execute(select(model).where(model.id == row_id).with_for_update())
    ).scalar_one_or_none()
    if row is None:
        raise RuntimeError("notification worker row was not found")
    return row


def _owned_claim(log, attempt, worker_id, now) -> bool:
    return bool(
        log.status == NotificationStatus.PROCESSING.value
        and attempt.status == NotificationAttemptStatus.CLAIMED.value
        and attempt.notification_log_id == log.id
        and attempt.attempt_number == log.attempt_count
        and log.claimed_by == worker_id
        and attempt.claimed_by == worker_id
        and log.lease_expires_at is not None
        and log.lease_expires_at >= now
    )


def _instant_token(value) -> str:
    return require_utc(value).strftime("%Y%m%dT%H%M%S.%fZ")


async def add_fallback_notification(
    session: AsyncSession,
    *,
    source: NotificationLog,
    now,
    operator_channel_id: int,
    operator_user_id: int,
) -> bool:
    """Atomically ensure the next route exists; return whether it was inserted."""
    now = require_utc(now)
    route = NotificationRecipientType(source.recipient_type)
    next_route = _FALLBACK_ROUTES.get(route)
    if next_route is None:
        return False
    recipient_id = {
        NotificationRecipientType.OPERATOR_CHANNEL: operator_channel_id,
        NotificationRecipientType.OPERATOR_DM: operator_user_id,
        NotificationRecipientType.LOG: None,
    }[next_route]
    fallback = NotificationLog(
        schedule_id=source.schedule_id,
        schedule_run_id=source.schedule_run_id,
        notification_type=source.notification_type,
        recipient_type=next_route.value,
        recipient_id=recipient_id,
        status=NotificationStatus.PENDING.value,
        deduplication_key=fallback_notification_deduplication_key(
            source.deduplication_key, next_route
        ),
        error_code=None,
        error_summary=None,
        scheduled_at=source.scheduled_at,
        next_attempt_at=now,
        attempt_count=0,
        claimed_by=None,
        claimed_at=None,
        lease_expires_at=None,
        started_at=None,
        finished_at=None,
        sent_at=None,
    )
    stored = await NotificationLogRepository(session).add_idempotent(fallback)
    return stored.id == fallback.id
