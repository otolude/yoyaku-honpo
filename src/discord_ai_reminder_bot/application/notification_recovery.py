"""Recover expired notification leases without contacting Discord."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from discord_ai_reminder_bot.application.notification_worker import add_fallback_notification
from discord_ai_reminder_bot.domain.enums import (
    NotificationAttemptStatus,
    NotificationErrorKind,
    NotificationStatus,
)
from discord_ai_reminder_bot.domain.notification import (
    NotificationDecisionAction,
    NotificationOutcome,
    decide_notification_result,
)
from discord_ai_reminder_bot.domain.recurrence import require_utc
from discord_ai_reminder_bot.infrastructure.database.notification_repositories import (
    NotificationAttemptRepository,
    NotificationLogRepository,
)


@dataclass(frozen=True)
class NotificationRecoverySummary:
    selected: int = 0
    retry_scheduled: int = 0
    failed: int = 0
    unknown: int = 0
    fallbacks_created: int = 0

    def add(self, other: NotificationRecoverySummary) -> NotificationRecoverySummary:
        return NotificationRecoverySummary(
            selected=self.selected + other.selected,
            retry_scheduled=self.retry_scheduled + other.retry_scheduled,
            failed=self.failed + other.failed,
            unknown=self.unknown + other.unknown,
            fallbacks_created=self.fallbacks_created + other.fallbacks_created,
        )


class NotificationRecoveryService:
    def __init__(
        self, session: AsyncSession, *, operator_channel_id: int, operator_user_id: int
    ) -> None:
        self._session = session
        self._operator_channel_id = operator_channel_id
        self._operator_user_id = operator_user_id

    async def recover_expired(
        self, *, recovered_at: datetime, batch_size: int
    ) -> NotificationRecoverySummary:
        recovered_at = require_utc(recovered_at)
        logs = NotificationLogRepository(self._session)
        attempts = NotificationAttemptRepository(self._session)
        selected = await logs.lock_expired(now=recovered_at, batch_size=batch_size)
        retry_scheduled = failed = unknown = fallbacks = 0
        for log in selected:
            attempt = await attempts.get_latest(notification_id=log.id)
            if not _consistent(log, attempt, recovered_at):
                _terminal_unknown(log, recovered_at)
                unknown += 1
                continue
            assert attempt is not None
            if attempt.status == NotificationAttemptStatus.SENDING.value:
                await attempts.mark_unknown(
                    attempt_id=attempt.id,
                    worker_id=attempt.claimed_by,
                    now=recovered_at,
                    error_code="notification_lease_result_unknown",
                    error_summary="Expired notification send result is unknown",
                )
                await logs.mark_unknown(
                    notification_id=log.id,
                    worker_id=attempt.claimed_by,
                    now=recovered_at,
                    error_code="notification_lease_result_unknown",
                    error_summary="Expired notification send result is unknown",
                )
                unknown += 1
                continue
            await attempts.mark_failed(
                attempt_id=attempt.id,
                worker_id=attempt.claimed_by,
                now=recovered_at,
                error_kind=NotificationErrorKind.TRANSIENT,
                error_code="notification_lease_expired_before_send",
                error_summary="Notification lease expired before sending",
            )
            decision = decide_notification_result(
                attempt_number=attempt.attempt_number,
                outcome=NotificationOutcome.TRANSIENT,
                decided_at=recovered_at,
            )
            if decision.action is NotificationDecisionAction.RETRY:
                assert decision.next_attempt_at is not None
                await logs.return_to_pending(
                    notification_id=log.id,
                    worker_id=attempt.claimed_by,
                    now=recovered_at,
                    retry_at=decision.next_attempt_at,
                    error_code="notification_lease_expired_before_send",
                    error_summary="Notification will be retried after an expired claim",
                )
                retry_scheduled += 1
            else:
                await logs.mark_failed(
                    notification_id=log.id,
                    worker_id=attempt.claimed_by,
                    now=recovered_at,
                    error_code="notification_retry_limit",
                    error_summary="Notification retry limit was reached",
                )
                fallbacks += int(
                    await add_fallback_notification(
                        self._session,
                        source=log,
                        now=recovered_at,
                        operator_channel_id=self._operator_channel_id,
                        operator_user_id=self._operator_user_id,
                    )
                )
                failed += 1
        await self._session.flush()
        return NotificationRecoverySummary(
            selected=len(selected),
            retry_scheduled=retry_scheduled,
            failed=failed,
            unknown=unknown,
            fallbacks_created=fallbacks,
        )


def _consistent(log, attempt, recovered_at: datetime) -> bool:
    return bool(
        attempt is not None
        and attempt.notification_log_id == log.id
        and attempt.attempt_number == log.attempt_count
        and attempt.claimed_by == log.claimed_by
        and attempt.claimed_at == log.claimed_at
        and log.claimed_at is not None
        and log.lease_expires_at is not None
        and log.claimed_at <= log.lease_expires_at <= recovered_at
        and log.started_at is not None
        and log.started_at <= recovered_at
        and (attempt.send_started_at is None or attempt.send_started_at <= recovered_at)
        and attempt.status
        in {NotificationAttemptStatus.CLAIMED.value, NotificationAttemptStatus.SENDING.value}
        and log.status == NotificationStatus.PROCESSING.value
    )


def _terminal_unknown(log, now: datetime) -> None:
    log.status = NotificationStatus.UNKNOWN.value
    log.next_attempt_at = None
    log.claimed_by = None
    log.claimed_at = None
    log.lease_expires_at = None
    log.finished_at = now
    log.sent_at = None
    log.error_code = "notification_recovery_inconsistent"
    log.error_summary = "Notification recovery found inconsistent processing state"
