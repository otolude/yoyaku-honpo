from datetime import UTC, date, datetime, time, timedelta

import pytest

from discord_ai_reminder_bot.domain.clock import FixedClock
from discord_ai_reminder_bot.domain.enums import (
    DeliveryAttemptStatus,
    DeliveryErrorKind,
    RunStatus,
    ScheduleStatus,
    ScheduleType,
    enum_values,
)
from discord_ai_reminder_bot.domain.exceptions import (
    InvalidDateTimeError,
    InvalidStateTransitionError,
)
from discord_ai_reminder_bot.domain.recovery import (
    InterruptedAttemptAction,
    OverdueAction,
    classify_interrupted_attempt,
    classify_overdue,
)
from discord_ai_reminder_bot.domain.recurrence import (
    TOKYO,
    next_daily_run,
    next_weekly_run,
    tokyo_to_utc,
    utc_to_tokyo,
)
from discord_ai_reminder_bot.domain.retry_policy import RetryAction, decide_retry
from discord_ai_reminder_bot.domain.state_transitions import (
    initial_schedule_status,
    pause_schedule,
    resume_schedule,
    transition_schedule,
)
from discord_ai_reminder_bot.infrastructure.database.models import (
    DELIVERY_ATTEMPT_STATUSES,
    RUN_STATUSES,
    SCHEDULE_STATUSES,
    SCHEDULE_TYPES,
)

NOW = datetime(2026, 8, 17, 3, 0, tzinfo=UTC)


def test_domain_values_match_database_values() -> None:
    assert enum_values(ScheduleType) == SCHEDULE_TYPES
    assert enum_values(ScheduleStatus) == SCHEDULE_STATUSES
    assert enum_values(RunStatus) == RUN_STATUSES
    assert enum_values(DeliveryAttemptStatus) == DELIVERY_ATTEMPT_STATUSES


def test_initial_schedule_status_validates_future_next_run() -> None:
    future = NOW + timedelta(minutes=1)
    assert (
        initial_schedule_status(content=None, next_run_at=future, now=NOW) is ScheduleStatus.DRAFT
    )
    assert (
        initial_schedule_status(content="text", next_run_at=future, now=NOW)
        is ScheduleStatus.ACTIVE
    )
    with pytest.raises(InvalidStateTransitionError):
        initial_schedule_status(content="text", next_run_at=NOW, now=NOW)
    with pytest.raises(InvalidStateTransitionError):
        initial_schedule_status(content="", next_run_at=NOW + timedelta(minutes=1), now=NOW)


@pytest.mark.parametrize("naive_argument", ["now", "next_run_at"])
def test_initial_schedule_status_rejects_naive_datetime(naive_argument: str) -> None:
    arguments = {"content": "text", "next_run_at": NOW + timedelta(minutes=1), "now": NOW}
    arguments[naive_argument] = datetime(2026, 8, 17, 3, 0)  # noqa: DTZ001
    with pytest.raises(InvalidDateTimeError):
        initial_schedule_status(**arguments)


@pytest.mark.parametrize(
    ("current", "target", "schedule_type", "content", "needs_next_run"),
    [
        (ScheduleStatus.DRAFT, ScheduleStatus.ACTIVE, ScheduleType.ONCE, "text", True),
        (ScheduleStatus.DRAFT, ScheduleStatus.DELETED, ScheduleType.ONCE, None, False),
        (ScheduleStatus.ACTIVE, ScheduleStatus.DRAFT, ScheduleType.ONCE, None, True),
        (ScheduleStatus.ACTIVE, ScheduleStatus.COMPLETED, ScheduleType.ONCE, "text", False),
        (ScheduleStatus.ACTIVE, ScheduleStatus.FAILED, ScheduleType.ONCE, "text", False),
        (ScheduleStatus.ACTIVE, ScheduleStatus.ENDED, ScheduleType.DAILY, "text", False),
        (ScheduleStatus.FAILED, ScheduleStatus.DELETED, ScheduleType.ONCE, "text", False),
    ],
)
def test_allowed_schedule_transitions(
    current, target, schedule_type, content, needs_next_run
) -> None:
    kwargs = {
        "next_run_at": NOW + timedelta(minutes=1) if needs_next_run else None,
        "now": NOW,
    }
    assert (
        transition_schedule(current, target, schedule_type=schedule_type, content=content, **kwargs)
        is target
    )


@pytest.mark.parametrize(
    "terminal", [ScheduleStatus.COMPLETED, ScheduleStatus.ENDED, ScheduleStatus.DELETED]
)
def test_terminal_schedule_cannot_transition(terminal: ScheduleStatus) -> None:
    with pytest.raises(InvalidStateTransitionError):
        transition_schedule(
            terminal,
            ScheduleStatus.ACTIVE,
            schedule_type=ScheduleType.ONCE,
            content="text",
            next_run_at=NOW + timedelta(minutes=1),
            now=NOW,
        )


def test_one_time_schedule_cannot_pause() -> None:
    with pytest.raises(InvalidStateTransitionError):
        pause_schedule(ScheduleStatus.ACTIVE, ScheduleType.ONCE, content="text")


def test_draft_cannot_transition_directly_to_ended() -> None:
    with pytest.raises(InvalidStateTransitionError):
        transition_schedule(
            ScheduleStatus.DRAFT,
            ScheduleStatus.ENDED,
            schedule_type=ScheduleType.DAILY,
            content=None,
        )


@pytest.mark.parametrize("target", [ScheduleStatus.DRAFT, ScheduleStatus.DELETED])
def test_processing_active_schedule_cannot_be_edited_or_deleted(target: ScheduleStatus) -> None:
    with pytest.raises(InvalidStateTransitionError):
        transition_schedule(
            ScheduleStatus.ACTIVE,
            target,
            schedule_type=ScheduleType.ONCE,
            content=None,
            is_processing=True,
        )


def test_recurring_schedule_can_pause_and_resume() -> None:
    assert (
        pause_schedule(ScheduleStatus.ACTIVE, ScheduleType.DAILY, content="text")
        is ScheduleStatus.PAUSED
    )
    assert (
        pause_schedule(ScheduleStatus.ACTIVE, ScheduleType.WEEKLY, content=None)
        is ScheduleStatus.PAUSED
    )
    assert (
        resume_schedule(
            schedule_type=ScheduleType.DAILY,
            content="text",
            next_run_at=NOW + timedelta(days=1),
            end_date=None,
            now=NOW,
        )
        is ScheduleStatus.ACTIVE
    )
    assert (
        resume_schedule(
            schedule_type=ScheduleType.WEEKLY,
            content=None,
            next_run_at=NOW + timedelta(days=1),
            end_date=None,
            now=NOW,
        )
        is ScheduleStatus.DRAFT
    )


def test_resume_after_end_date_ends_schedule() -> None:
    assert (
        resume_schedule(
            schedule_type=ScheduleType.DAILY,
            content="text",
            next_run_at=None,
            end_date=date(2026, 8, 16),
            now=NOW,
        )
        is ScheduleStatus.ENDED
    )


def test_contentless_resume_after_end_date_is_rejected() -> None:
    with pytest.raises(InvalidStateTransitionError, match="set content or delete"):
        resume_schedule(
            schedule_type=ScheduleType.DAILY,
            content=None,
            next_run_at=None,
            end_date=date(2026, 8, 16),
            now=NOW,
        )


def test_tokyo_and_utc_conversion() -> None:
    tokyo = datetime(2026, 8, 17, 12, 0, tzinfo=TOKYO)
    assert tokyo_to_utc(tokyo) == NOW
    assert utc_to_tokyo(NOW) == tokyo


@pytest.mark.parametrize("function", [utc_to_tokyo, tokyo_to_utc])
def test_naive_datetime_is_rejected(function) -> None:
    with pytest.raises(InvalidDateTimeError):
        function(datetime(2026, 8, 17, 12, 0))  # noqa: DTZ001 - intentionally naive


def test_daily_next_run_is_strictly_future_and_honors_end_date() -> None:
    same_time = datetime(2026, 8, 17, 12, 0, tzinfo=TOKYO).astimezone(UTC)
    assert next_daily_run(
        local_time=time(12), after=same_time, end_date=date(2026, 8, 18)
    ) == datetime(2026, 8, 18, 3, 0, tzinfo=UTC)
    assert next_daily_run(local_time=time(12), after=same_time, end_date=date(2026, 8, 17)) is None


def test_weekly_next_run_is_strictly_future_and_honors_end_date() -> None:
    monday_at_noon = datetime(2026, 8, 17, 12, 0, tzinfo=TOKYO).astimezone(UTC)
    assert next_weekly_run(weekday=0, local_time=time(12), after=monday_at_noon) == datetime(
        2026, 8, 24, 3, 0, tzinfo=UTC
    )
    assert (
        next_weekly_run(
            weekday=0,
            local_time=time(12),
            after=monday_at_noon - timedelta(seconds=1),
            end_date=date(2026, 8, 17),
        )
        == monday_at_noon
    )


@pytest.mark.parametrize(
    ("attempt_number", "minutes"),
    [(1, 1), (2, 5), (3, 15)],
)
def test_transient_retry_intervals(attempt_number: int, minutes: int) -> None:
    decision = decide_retry(
        attempt_number=attempt_number,
        error_kind=DeliveryErrorKind.TRANSIENT,
        failed_at=NOW,
    )
    assert decision.action is RetryAction.RETRY
    assert decision.next_attempt_at == NOW + timedelta(minutes=minutes)


@pytest.mark.parametrize("kind", [DeliveryErrorKind.PERMANENT, DeliveryErrorKind.UNKNOWN])
def test_non_transient_error_is_not_retried(kind: DeliveryErrorKind) -> None:
    assert decide_retry(attempt_number=1, error_kind=kind, failed_at=NOW).action is RetryAction.FAIL


def test_fourth_attempt_is_not_retried_and_fifth_is_invalid() -> None:
    assert (
        decide_retry(
            attempt_number=4,
            error_kind=DeliveryErrorKind.TRANSIENT,
            failed_at=NOW,
        ).action
        is RetryAction.FAIL
    )
    with pytest.raises(ValueError):
        decide_retry(
            attempt_number=5,
            error_kind=DeliveryErrorKind.TRANSIENT,
            failed_at=NOW,
        )


def test_one_time_recovery_boundary() -> None:
    scheduled_for = NOW - timedelta(minutes=15)
    assert (
        classify_overdue(
            schedule_type=ScheduleType.ONCE, scheduled_for=scheduled_for, recovered_at=NOW
        )
        is OverdueAction.DELAYED_SEND
    )
    assert (
        classify_overdue(
            schedule_type=ScheduleType.ONCE,
            scheduled_for=scheduled_for - timedelta(seconds=1),
            recovered_at=NOW,
        )
        is OverdueAction.SKIP_AND_FAIL
    )


def test_recurring_past_run_is_skipped() -> None:
    assert (
        classify_overdue(
            schedule_type=ScheduleType.WEEKLY,
            scheduled_for=NOW - timedelta(days=1),
            recovered_at=NOW,
        )
        is OverdueAction.SKIP_RECURRING
    )


def test_interrupted_delivery_distinguishes_before_send_and_unknown() -> None:
    assert (
        classify_interrupted_attempt(DeliveryAttemptStatus.CLAIMED)
        is InterruptedAttemptAction.RETURN_TO_PENDING
    )
    assert (
        classify_interrupted_attempt(DeliveryAttemptStatus.SENDING)
        is InterruptedAttemptAction.FAIL_WITH_UNKNOWN_RESULT
    )
    assert (
        classify_interrupted_attempt(DeliveryAttemptStatus.UNKNOWN)
        is InterruptedAttemptAction.FAIL_WITH_UNKNOWN_RESULT
    )


def test_fixed_clock_returns_fixed_utc_time() -> None:
    assert FixedClock(NOW).now() == NOW
