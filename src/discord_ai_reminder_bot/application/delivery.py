"""Coordinate delivery-attempt and schedule-run lifecycle updates."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from discord_ai_reminder_bot.config import MAX_POSTGRES_BIGINT
from discord_ai_reminder_bot.domain.enums import DeliveryErrorKind
from discord_ai_reminder_bot.domain.recurrence import require_utc
from discord_ai_reminder_bot.domain.retry_policy import RetryAction, decide_retry
from discord_ai_reminder_bot.infrastructure.database.models import DeliveryAttempt, ScheduleRun
from discord_ai_reminder_bot.infrastructure.database.repositories import (
    DeliveryAttemptRepository,
    ScheduleRunRepository,
)

RESULT_SUCCEEDED = "delivery_succeeded"
RESULT_RETRY_PENDING = "retry_pending"
RESULT_FAILED = "delivery_failed"
RESULT_UNKNOWN = "delivery_result_unknown"
_SENSITIVE_MARKERS = (
    "postgresql://",
    "postgresql+psycopg://",
    "discord.com/api",
    "token",
    "traceback (",
)


@dataclass(frozen=True)
class DeliveryUpdate:
    attempt: DeliveryAttempt
    run: ScheduleRun


def validate_message_id(message_id: int) -> int:
    if isinstance(message_id, bool) or not 1 <= message_id <= MAX_POSTGRES_BIGINT:
        raise ValueError("message_id must be a positive PostgreSQL BIGINT")
    return message_id


def validate_safe_error_text(value: str, *, field: str, maximum: int) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} must not be empty")
    if len(normalized) > maximum:
        raise ValueError(f"{field} must be at most {maximum} characters")
    lowered = normalized.casefold()
    if any(marker in lowered for marker in _SENSITIVE_MARKERS):
        raise ValueError(f"{field} contains sensitive or unsafe text")
    return normalized


class DeliveryService:
    """Update one attempt and run atomically in the caller-owned transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._attempts = DeliveryAttemptRepository(session)
        self._runs = ScheduleRunRepository(session)

    async def start_sending(
        self, *, attempt_id: int, worker_id: uuid.UUID, now: datetime
    ) -> DeliveryUpdate:
        now = require_utc(now)
        attempt = await self._attempts.mark_sending(
            attempt_id=attempt_id, worker_id=worker_id, now=now
        )
        run = await self._runs.mark_sending_started(
            run_id=attempt.schedule_run_id, worker_id=worker_id, now=now
        )
        return DeliveryUpdate(attempt=attempt, run=run)

    async def skip_before_send(
        self,
        *,
        attempt_id: int,
        worker_id: uuid.UUID,
        now: datetime,
        result_code: str,
        error_summary: str,
    ) -> DeliveryUpdate:
        """Record a claimed attempt as failed and its run as skipped, without sending."""
        now = require_utc(now)
        result_code = validate_safe_error_text(result_code, field="result_code", maximum=64)
        error_summary = validate_safe_error_text(error_summary, field="error_summary", maximum=500)
        attempt = await self._attempts.mark_failed(
            attempt_id=attempt_id,
            worker_id=worker_id,
            now=now,
            error_kind=DeliveryErrorKind.PERMANENT.value,
            error_code="skipped_before_send",
            error_summary=error_summary,
        )
        run = await self._runs.mark_skipped(
            run_id=attempt.schedule_run_id,
            worker_id=worker_id,
            now=now,
            result_code=result_code,
            error_summary=error_summary,
        )
        return DeliveryUpdate(attempt=attempt, run=run)

    async def complete_success(
        self,
        *,
        attempt_id: int,
        worker_id: uuid.UUID,
        now: datetime,
        message_id: int,
    ) -> DeliveryUpdate:
        now = require_utc(now)
        message_id = validate_message_id(message_id)
        attempt = await self._attempts.mark_succeeded(
            attempt_id=attempt_id,
            worker_id=worker_id,
            now=now,
            message_id=message_id,
        )
        run = await self._runs.mark_succeeded(
            run_id=attempt.schedule_run_id,
            worker_id=worker_id,
            now=now,
            message_id=message_id,
            result_code=RESULT_SUCCEEDED,
        )
        return DeliveryUpdate(attempt=attempt, run=run)

    async def complete_failure(
        self,
        *,
        attempt_id: int,
        worker_id: uuid.UUID,
        now: datetime,
        error_kind: DeliveryErrorKind,
        error_code: str,
        error_summary: str,
        retry_at: datetime | None = None,
    ) -> DeliveryUpdate:
        now = require_utc(now)
        if error_kind is DeliveryErrorKind.UNKNOWN:
            raise ValueError("unknown delivery results must use complete_unknown")
        error_code = validate_safe_error_text(error_code, field="error_code", maximum=64)
        error_summary = validate_safe_error_text(error_summary, field="error_summary", maximum=500)
        attempt = await self._attempts.mark_failed(
            attempt_id=attempt_id,
            worker_id=worker_id,
            now=now,
            error_kind=error_kind.value,
            error_code=error_code,
            error_summary=error_summary,
        )
        decision = decide_retry(
            attempt_number=attempt.attempt_number,
            error_kind=error_kind,
            failed_at=now,
            retry_at=retry_at,
        )
        run = await self._runs.mark_failed_or_pending(
            run_id=attempt.schedule_run_id,
            worker_id=worker_id,
            now=now,
            retry_at=decision.next_attempt_at,
            result_code=(
                RESULT_RETRY_PENDING if decision.action is RetryAction.RETRY else RESULT_FAILED
            ),
            error_summary=error_summary,
        )
        return DeliveryUpdate(attempt=attempt, run=run)

    async def complete_unknown(
        self, *, attempt_id: int, worker_id: uuid.UUID, now: datetime
    ) -> DeliveryUpdate:
        now = require_utc(now)
        attempt = await self._attempts.mark_unknown(
            attempt_id=attempt_id, worker_id=worker_id, now=now
        )
        run = await self._runs.mark_failed_or_pending(
            run_id=attempt.schedule_run_id,
            worker_id=worker_id,
            now=now,
            retry_at=None,
            result_code=RESULT_UNKNOWN,
            error_summary="Discord delivery result is unknown",
        )
        return DeliveryUpdate(attempt=attempt, run=run)
