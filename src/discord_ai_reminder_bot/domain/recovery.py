"""Pure decisions used while recovering schedules after Bot downtime."""

from datetime import datetime, timedelta
from enum import StrEnum

from discord_ai_reminder_bot.domain.enums import DeliveryAttemptStatus, ScheduleType
from discord_ai_reminder_bot.domain.recurrence import require_utc


class OverdueAction(StrEnum):
    DELAYED_SEND = "delayed_send"
    SKIP_AND_FAIL = "skip_and_fail"
    SKIP_RECURRING = "skip_recurring"


class InterruptedAttemptAction(StrEnum):
    RETURN_TO_PENDING = "return_to_pending"
    FAIL_WITH_UNKNOWN_RESULT = "fail_with_unknown_result"
    NO_RECOVERY = "no_recovery"


def classify_overdue(
    *, schedule_type: ScheduleType, scheduled_for: datetime, recovered_at: datetime
) -> OverdueAction:
    scheduled_for = require_utc(scheduled_for)
    recovered_at = require_utc(recovered_at)
    if scheduled_for > recovered_at:
        raise ValueError("scheduled_for is not overdue")
    if schedule_type is not ScheduleType.ONCE:
        return OverdueAction.SKIP_RECURRING
    if recovered_at - scheduled_for <= timedelta(minutes=15):
        return OverdueAction.DELAYED_SEND
    return OverdueAction.SKIP_AND_FAIL


def classify_interrupted_attempt(status: DeliveryAttemptStatus) -> InterruptedAttemptAction:
    if status is DeliveryAttemptStatus.CLAIMED:
        return InterruptedAttemptAction.RETURN_TO_PENDING
    if status in {DeliveryAttemptStatus.SENDING, DeliveryAttemptStatus.UNKNOWN}:
        return InterruptedAttemptAction.FAIL_WITH_UNKNOWN_RESULT
    return InterruptedAttemptAction.NO_RECOVERY
