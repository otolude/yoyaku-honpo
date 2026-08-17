"""Phase 1 Discord delivery retry policy."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from discord_ai_reminder_bot.domain.enums import DeliveryErrorKind
from discord_ai_reminder_bot.domain.recurrence import require_utc

MAX_ATTEMPTS = 4
RETRY_DELAYS = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=15))


class RetryAction(StrEnum):
    RETRY = "retry"
    FAIL = "fail"


@dataclass(frozen=True)
class RetryDecision:
    action: RetryAction
    next_attempt_at: datetime | None


def decide_retry(
    *,
    attempt_number: int,
    error_kind: DeliveryErrorKind,
    failed_at: datetime,
    retry_at: datetime | None = None,
) -> RetryDecision:
    """Return a UTC retry instant only for a transient failure before attempt four."""
    failed_at = require_utc(failed_at)
    if not 1 <= attempt_number <= MAX_ATTEMPTS:
        raise ValueError("attempt_number must be between 1 and 4")
    if error_kind is not DeliveryErrorKind.TRANSIENT or attempt_number == MAX_ATTEMPTS:
        return RetryDecision(RetryAction.FAIL, None)
    if retry_at is not None:
        next_attempt_at = require_utc(retry_at)
        if next_attempt_at <= failed_at:
            raise ValueError("retry_at must be after failed_at")
    else:
        next_attempt_at = failed_at + RETRY_DELAYS[attempt_number - 1]
    return RetryDecision(RetryAction.RETRY, next_attempt_at)
