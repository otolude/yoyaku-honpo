"""Recover expired processing runs without contacting Discord."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from discord_ai_reminder_bot.application.delivery import (
    RESULT_FAILED,
    RESULT_RETRY_PENDING,
    RESULT_UNKNOWN,
)
from discord_ai_reminder_bot.domain.enums import DeliveryAttemptStatus, DeliveryErrorKind
from discord_ai_reminder_bot.domain.recovery import (
    InterruptedAttemptAction,
    classify_interrupted_attempt,
)
from discord_ai_reminder_bot.domain.recurrence import require_utc
from discord_ai_reminder_bot.domain.retry_policy import RetryAction, decide_retry
from discord_ai_reminder_bot.infrastructure.database.models import DeliveryAttempt, ScheduleRun
from discord_ai_reminder_bot.infrastructure.database.repositories import (
    DeliveryAttemptRepository,
    ScheduleRunRepository,
)

BEFORE_SEND_CODE = "lease_expired_before_send"
UNKNOWN_CODE = "delivery_result_unknown_after_lease_expiry"
BEFORE_SEND_SUMMARY = "Processing lease expired before Discord sending started"
UNKNOWN_SUMMARY = "Discord delivery result is unknown after lease expiry"
INCONSISTENT_SUMMARY = "Delivery state is inconsistent after lease expiry"


class RecoveryResult(StrEnum):
    RETRY_PENDING = "retry_pending"
    FAILED_BEFORE_SEND = "failed_before_send"
    FAILED_UNKNOWN = "failed_unknown"


@dataclass(frozen=True)
class RecoveredRun:
    run: ScheduleRun
    attempt: DeliveryAttempt | None
    result: RecoveryResult


class ProcessingRecoveryService:
    """Recover locked expired runs in the caller-owned transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._runs = ScheduleRunRepository(session)
        self._attempts = DeliveryAttemptRepository(session)

    async def recover_expired(
        self, *, recovered_at: datetime, batch_size: int
    ) -> list[RecoveredRun]:
        recovered_at = require_utc(recovered_at)
        runs = await self._runs.lock_expired_processing(
            recovered_at=recovered_at, batch_size=batch_size
        )
        recovered: list[RecoveredRun] = []
        for run in runs:
            attempt = await self._attempts.get_latest_by_run(run_id=run.id)
            recovered.append(
                await self._recover_one(run=run, attempt=attempt, recovered_at=recovered_at)
            )
        return recovered

    async def _recover_one(
        self,
        *,
        run: ScheduleRun,
        attempt: DeliveryAttempt | None,
        recovered_at: datetime,
    ) -> RecoveredRun:
        worker_id = run.claimed_by
        assert worker_id is not None
        if not _is_consistent(run, attempt, recovered_at):
            failed = await self._fail_unknown(
                run=run,
                recovered_at=recovered_at,
                error_summary=INCONSISTENT_SUMMARY,
            )
            return RecoveredRun(failed, attempt, RecoveryResult.FAILED_UNKNOWN)

        assert attempt is not None
        action = classify_interrupted_attempt(DeliveryAttemptStatus(attempt.status))
        if action is InterruptedAttemptAction.RETURN_TO_PENDING:
            failed_attempt = await self._attempts.mark_failed(
                attempt_id=attempt.id,
                worker_id=worker_id,
                now=recovered_at,
                error_kind=DeliveryErrorKind.TRANSIENT.value,
                error_code=BEFORE_SEND_CODE,
                error_summary=BEFORE_SEND_SUMMARY,
            )
            decision = decide_retry(
                attempt_number=attempt.attempt_number,
                error_kind=DeliveryErrorKind.TRANSIENT,
                failed_at=recovered_at,
            )
            failed_run = await self._runs.mark_failed_or_pending(
                run_id=run.id,
                worker_id=worker_id,
                now=recovered_at,
                retry_at=decision.next_attempt_at,
                result_code=(
                    RESULT_RETRY_PENDING if decision.action is RetryAction.RETRY else RESULT_FAILED
                ),
                error_summary=BEFORE_SEND_SUMMARY,
            )
            result = (
                RecoveryResult.RETRY_PENDING
                if decision.action is RetryAction.RETRY
                else RecoveryResult.FAILED_BEFORE_SEND
            )
            return RecoveredRun(failed_run, failed_attempt, result)

        if action is InterruptedAttemptAction.FAIL_WITH_UNKNOWN_RESULT:
            unknown_attempt = await self._attempts.mark_unknown_after_expiry(
                attempt_id=attempt.id,
                worker_id=worker_id,
                now=recovered_at,
                error_code=UNKNOWN_CODE,
                error_summary=UNKNOWN_SUMMARY,
            )
            failed_run = await self._fail_unknown(
                run=run,
                recovered_at=recovered_at,
                error_summary=UNKNOWN_SUMMARY,
            )
            return RecoveredRun(failed_run, unknown_attempt, RecoveryResult.FAILED_UNKNOWN)

        failed = await self._fail_unknown(
            run=run,
            recovered_at=recovered_at,
            error_summary=INCONSISTENT_SUMMARY,
        )
        return RecoveredRun(failed, attempt, RecoveryResult.FAILED_UNKNOWN)

    async def _fail_unknown(
        self,
        *,
        run: ScheduleRun,
        recovered_at: datetime,
        error_summary: str,
    ) -> ScheduleRun:
        assert run.claimed_by is not None
        return await self._runs.mark_failed_or_pending(
            run_id=run.id,
            worker_id=run.claimed_by,
            now=recovered_at,
            retry_at=None,
            result_code=RESULT_UNKNOWN,
            error_summary=error_summary,
        )


def _is_consistent(
    run: ScheduleRun, attempt: DeliveryAttempt | None, recovered_at: datetime
) -> bool:
    if attempt is None:
        return False
    if attempt.attempt_number != run.attempt_count or attempt.claimed_by != run.claimed_by:
        return False
    if run.claimed_at is None or run.lease_expires_at is None:
        return False
    if not run.claimed_at <= run.lease_expires_at <= recovered_at:
        return False
    if attempt.claimed_at != run.claimed_at:
        return False
    if attempt.claimed_at > recovered_at:
        return False
    status = DeliveryAttemptStatus(attempt.status)
    if status is DeliveryAttemptStatus.CLAIMED:
        return attempt.send_started_at is None and attempt.finished_at is None
    if status is DeliveryAttemptStatus.SENDING:
        return (
            attempt.send_started_at is not None
            and attempt.claimed_at <= attempt.send_started_at <= recovered_at
            and attempt.finished_at is None
        )
    if status is DeliveryAttemptStatus.UNKNOWN:
        return (
            attempt.send_started_at is not None
            and attempt.finished_at is not None
            and attempt.claimed_at <= attempt.send_started_at <= attempt.finished_at <= recovered_at
        )
    return False
