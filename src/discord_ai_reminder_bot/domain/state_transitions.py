"""Pure validation of Phase 1 schedule state transitions."""

from datetime import date, datetime

from discord_ai_reminder_bot.domain.enums import ScheduleStatus, ScheduleType
from discord_ai_reminder_bot.domain.exceptions import InvalidStateTransitionError
from discord_ai_reminder_bot.domain.recurrence import require_utc, utc_to_tokyo

_RECURRING_TYPES = {ScheduleType.DAILY, ScheduleType.WEEKLY}
_TERMINAL_STATUSES = {
    ScheduleStatus.COMPLETED,
    ScheduleStatus.ENDED,
    ScheduleStatus.DELETED,
}


def _validate_content(content: str | None) -> None:
    if content is not None and not 1 <= len(content) <= 2000:
        raise InvalidStateTransitionError("content must contain between 1 and 2000 characters")


def initial_schedule_status(
    *, content: str | None, next_run_at: datetime, now: datetime
) -> ScheduleStatus:
    """Validate a new schedule and return its DB-compatible initial state."""
    _validate_content(content)
    if require_utc(next_run_at) <= require_utc(now):
        raise InvalidStateTransitionError("new schedule requires a future next run")
    return ScheduleStatus.DRAFT if content is None else ScheduleStatus.ACTIVE


def transition_schedule(
    current: ScheduleStatus,
    target: ScheduleStatus,
    *,
    schedule_type: ScheduleType,
    content: str | None,
    next_run_at: datetime | None = None,
    now: datetime | None = None,
    is_processing: bool = False,
) -> ScheduleStatus:
    """Validate one of the transitions explicitly listed in the design."""
    _validate_content(content)
    if current in _TERMINAL_STATUSES:
        raise InvalidStateTransitionError(f"{current} is terminal")

    allowed = {
        ScheduleStatus.DRAFT: {ScheduleStatus.ACTIVE, ScheduleStatus.DELETED},
        ScheduleStatus.ACTIVE: {
            ScheduleStatus.DRAFT,
            ScheduleStatus.PAUSED,
            ScheduleStatus.COMPLETED,
            ScheduleStatus.FAILED,
            ScheduleStatus.ENDED,
            ScheduleStatus.DELETED,
        },
        ScheduleStatus.PAUSED: {
            ScheduleStatus.ACTIVE,
            ScheduleStatus.DRAFT,
            ScheduleStatus.ENDED,
            ScheduleStatus.DELETED,
        },
        ScheduleStatus.FAILED: {ScheduleStatus.DELETED},
    }
    if target not in allowed.get(current, set()):
        raise InvalidStateTransitionError(f"transition from {current} to {target} is not allowed")

    if (
        current is ScheduleStatus.ACTIVE
        and target in {ScheduleStatus.DRAFT, ScheduleStatus.DELETED}
        and is_processing
    ):
        raise InvalidStateTransitionError("a processing schedule cannot be edited or deleted")

    if (
        target in {ScheduleStatus.PAUSED, ScheduleStatus.ENDED}
        and schedule_type not in _RECURRING_TYPES
    ):
        raise InvalidStateTransitionError(f"{target} is only valid for recurring schedules")
    if (
        target in {ScheduleStatus.COMPLETED, ScheduleStatus.FAILED}
        and schedule_type is not ScheduleType.ONCE
    ):
        raise InvalidStateTransitionError(f"{target} is only valid for one-time schedules")
    if target is ScheduleStatus.DRAFT and content is not None:
        raise InvalidStateTransitionError("draft requires missing content")
    if (
        target
        in {
            ScheduleStatus.ACTIVE,
            ScheduleStatus.FAILED,
            ScheduleStatus.COMPLETED,
            ScheduleStatus.ENDED,
        }
        and content is None
    ):
        raise InvalidStateTransitionError(f"{target} requires content")
    if target in {ScheduleStatus.DRAFT, ScheduleStatus.ACTIVE}:
        if next_run_at is None or now is None:
            raise InvalidStateTransitionError(f"{target} requires a future next run")
        if require_utc(next_run_at) <= require_utc(now):
            raise InvalidStateTransitionError("next run must be in the future")
    elif next_run_at is not None:
        raise InvalidStateTransitionError(f"{target} must not have a next run")
    return target


def pause_schedule(
    current: ScheduleStatus, schedule_type: ScheduleType, *, content: str | None
) -> ScheduleStatus:
    return transition_schedule(
        current,
        ScheduleStatus.PAUSED,
        schedule_type=schedule_type,
        content=content,
    )


def resume_schedule(
    *,
    schedule_type: ScheduleType,
    content: str | None,
    next_run_at: datetime | None,
    end_date: date | None,
    now: datetime,
) -> ScheduleStatus:
    """Resolve a paused recurring schedule to active, draft, or ended."""
    now = require_utc(now)
    if schedule_type not in _RECURRING_TYPES:
        raise InvalidStateTransitionError("only recurring schedules can be resumed")
    if end_date is not None and utc_to_tokyo(now).date() > end_date:
        if content is None:
            raise InvalidStateTransitionError(
                "a contentless paused schedule cannot end; set content or delete the schedule"
            )
        return transition_schedule(
            ScheduleStatus.PAUSED,
            ScheduleStatus.ENDED,
            schedule_type=schedule_type,
            content=content,
        )
    if next_run_at is None or require_utc(next_run_at) <= now:
        raise InvalidStateTransitionError("resume requires a future next run")
    target = ScheduleStatus.ACTIVE if content is not None else ScheduleStatus.DRAFT
    return transition_schedule(
        ScheduleStatus.PAUSED,
        target,
        schedule_type=schedule_type,
        content=content,
        next_run_at=next_run_at,
        now=now,
    )
