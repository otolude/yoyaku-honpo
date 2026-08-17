from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy.dialects import postgresql

from discord_ai_reminder_bot.application.schedule_execution import (
    once_target_status,
    recurring_next_run,
)
from discord_ai_reminder_bot.domain.enums import (
    OperationAction,
    RunStatus,
    ScheduleStatus,
    ScheduleType,
)
from discord_ai_reminder_bot.domain.exceptions import (
    InvalidDateTimeError,
    InvalidStateTransitionError,
)
from discord_ai_reminder_bot.infrastructure.database.repositories import (
    build_run_finalization_statement,
    build_schedule_lock_statement,
)

FINALIZED_AT = datetime(2026, 8, 17, 3, 5, tzinfo=UTC)


@pytest.mark.parametrize(
    ("run_status", "target"),
    [
        (RunStatus.SUCCEEDED, ScheduleStatus.COMPLETED),
        (RunStatus.FAILED, ScheduleStatus.FAILED),
        (RunStatus.SKIPPED, ScheduleStatus.FAILED),
    ],
)
def test_once_terminal_result_mapping(run_status: RunStatus, target: ScheduleStatus) -> None:
    assert once_target_status(run_status) is target


@pytest.mark.parametrize("run_status", [RunStatus.PENDING, RunStatus.PROCESSING])
def test_once_rejects_nonterminal_result(run_status: RunStatus) -> None:
    with pytest.raises(InvalidStateTransitionError):
        once_target_status(run_status)


def test_daily_and_weekly_use_strictly_future_tokyo_occurrence() -> None:
    daily = recurring_next_run(
        schedule_type=ScheduleType.DAILY,
        local_time=time(12, 0),
        weekday=None,
        end_date=None,
        finalized_at=FINALIZED_AT,
    )
    weekly = recurring_next_run(
        schedule_type=ScheduleType.WEEKLY,
        local_time=time(12, 0),
        weekday=0,
        end_date=None,
        finalized_at=FINALIZED_AT,
    )
    assert daily == FINALIZED_AT.replace(hour=3, minute=0) + timedelta(days=1)
    assert weekly == FINALIZED_AT.replace(hour=3, minute=0) + timedelta(days=7)
    assert daily > FINALIZED_AT
    assert weekly > FINALIZED_AT


def test_end_date_includes_the_last_tokyo_day() -> None:
    assert recurring_next_run(
        schedule_type=ScheduleType.DAILY,
        local_time=time(12, 0),
        weekday=None,
        end_date=date(2026, 8, 18),
        finalized_at=FINALIZED_AT,
    ) == datetime(2026, 8, 18, 3, 0, tzinfo=UTC)
    assert (
        recurring_next_run(
            schedule_type=ScheduleType.DAILY,
            local_time=time(12, 0),
            weekday=None,
            end_date=date(2026, 8, 17),
            finalized_at=FINALIZED_AT,
        )
        is None
    )


def test_recurring_calculation_rejects_naive_time() -> None:
    with pytest.raises(InvalidDateTimeError):
        recurring_next_run(
            schedule_type=ScheduleType.DAILY,
            local_time=time(12, 0),
            weekday=None,
            end_date=None,
            finalized_at=datetime(2026, 8, 17, 3, 5),  # noqa: DTZ001
        )


def test_finalization_queries_use_row_locks() -> None:
    run_sql = str(
        build_run_finalization_statement(run_id=1).compile(dialect=postgresql.dialect())
    ).upper()
    schedule_sql = str(
        build_schedule_lock_statement(schedule_id=1).compile(dialect=postgresql.dialect())
    ).upper()
    assert "FOR UPDATE" in run_sql
    assert "FOR UPDATE" in schedule_sql
    assert "SKIP LOCKED" not in run_sql


def test_operation_actions_include_terminal_system_changes() -> None:
    assert OperationAction.COMPLETED.value == "completed"
    assert OperationAction.ENDED.value == "ended"
    assert OperationAction.FAILED.value == "failed"
