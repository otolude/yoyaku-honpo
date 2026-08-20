"""Pure retention rules for Phase 1 physical cleanup."""

from __future__ import annotations

from datetime import datetime, timedelta

from discord_ai_reminder_bot.domain.enums import NotificationStatus, ScheduleStatus
from discord_ai_reminder_bot.domain.recurrence import require_utc

RETENTION_PERIOD = timedelta(days=30)
MIN_CLEANUP_BATCH_SIZE = 1
MAX_CLEANUP_BATCH_SIZE = 100

_CLEANUP_SCHEDULE_STATUSES = frozenset(
    (ScheduleStatus.COMPLETED, ScheduleStatus.ENDED, ScheduleStatus.DELETED)
)
_TERMINAL_NOTIFICATION_STATUSES = frozenset(
    (
        NotificationStatus.SUCCEEDED,
        NotificationStatus.FAILED,
        NotificationStatus.UNKNOWN,
        NotificationStatus.CANCELLED,
    )
)


def retention_cutoff(cleanup_cutoff: datetime) -> datetime:
    """Return the inclusive 30-day retention boundary for one fixed cycle."""
    return require_utc(cleanup_cutoff) - RETENTION_PERIOD


def is_schedule_due(
    *, status: ScheduleStatus | str, terminal_at: datetime | None, cutoff: datetime
) -> bool:
    """Return whether a Schedule meets the basic status/time retention rule."""
    cutoff = require_utc(cutoff)
    try:
        parsed_status = ScheduleStatus(status)
    except ValueError:
        return False
    if terminal_at is None:
        return False
    return parsed_status in _CLEANUP_SCHEDULE_STATUSES and require_utc(terminal_at) <= cutoff


def is_global_notification_due(
    *,
    status: NotificationStatus | str,
    schedule_id: int | None,
    schedule_run_id: int | None,
    finished_at: datetime | None,
    cutoff: datetime,
) -> bool:
    """Return whether a truly global terminal NotificationLog is due."""
    cutoff = require_utc(cutoff)
    try:
        parsed_status = NotificationStatus(status)
    except ValueError:
        return False
    if schedule_id is not None or schedule_run_id is not None or finished_at is None:
        return False
    return parsed_status in _TERMINAL_NOTIFICATION_STATUSES and require_utc(finished_at) <= cutoff


def validate_cleanup_batch_size(batch_size: int) -> int:
    """Validate the independently bounded Schedule/global cleanup size."""
    if isinstance(batch_size, bool) or not isinstance(batch_size, int):
        raise ValueError("cleanup batch_size must be an integer between 1 and 100")  # noqa: TRY004
    if not MIN_CLEANUP_BATCH_SIZE <= batch_size <= MAX_CLEANUP_BATCH_SIZE:
        raise ValueError("cleanup batch_size must be between 1 and 100")
    return batch_size
