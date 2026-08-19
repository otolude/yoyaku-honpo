from datetime import UTC, date, datetime, time, timedelta

import pytest

from discord_ai_reminder_bot.domain.enums import ScheduleStatus, ScheduleType
from discord_ai_reminder_bot.domain.schedule_editing import (
    InvalidScheduleEditError,
    first_unused_recurring_edit_run,
    validate_edit_target,
)
from discord_ai_reminder_bot.infrastructure.database.repositories import (
    build_schedule_edit_runs_statement,
)

NOW = datetime(2026, 8, 19, 0, 0, tzinfo=UTC)


def test_edit_boundary_is_inclusive_and_applies_to_active_and_draft() -> None:
    for status in (ScheduleStatus.ACTIVE, ScheduleStatus.DRAFT):
        validate_edit_target(
            schedule_type=ScheduleType.ONCE,
            status=status,
            next_run_at=NOW + timedelta(minutes=5),
            now=NOW,
        )
        with pytest.raises(InvalidScheduleEditError):
            validate_edit_target(
                schedule_type=ScheduleType.ONCE,
                status=status,
                next_run_at=NOW + timedelta(minutes=5) - timedelta(microseconds=1),
                now=NOW,
            )


def test_only_recurring_paused_target_is_editable_without_next_run() -> None:
    validate_edit_target(
        schedule_type=ScheduleType.DAILY,
        status=ScheduleStatus.PAUSED,
        next_run_at=None,
        now=NOW,
    )
    with pytest.raises(InvalidScheduleEditError):
        validate_edit_target(
            schedule_type=ScheduleType.ONCE,
            status=ScheduleStatus.PAUSED,
            next_run_at=None,
            now=NOW,
        )


def test_recurring_edit_skips_used_occurrences_and_has_finite_end() -> None:
    first = NOW + timedelta(days=1)
    second = first + timedelta(days=1)
    assert first_unused_recurring_edit_run(
        schedule_type=ScheduleType.DAILY,
        local_time=time(9),
        weekday=None,
        end_date=date(2026, 8, 22),
        edited_at=NOW,
        occupied={first, second},
        reusable_pending=None,
    ) == second + timedelta(days=1)
    assert (
        first_unused_recurring_edit_run(
            schedule_type=ScheduleType.DAILY,
            local_time=time(9),
            weekday=None,
            end_date=date(2026, 8, 21),
            edited_at=NOW,
            occupied={first, second},
            reusable_pending=None,
        )
        is None
    )


def test_recurring_edit_can_retain_current_pending_occurrence() -> None:
    current = NOW + timedelta(days=1)
    assert (
        first_unused_recurring_edit_run(
            schedule_type=ScheduleType.WEEKLY,
            local_time=time(9),
            weekday=3,
            end_date=None,
            edited_at=NOW,
            occupied={current},
            reusable_pending=current,
        )
        == current
    )


def test_edit_locks_runs_in_id_order_before_the_service_locks_schedule() -> None:
    sql = str(build_schedule_edit_runs_statement(schedule_id=10, lock=True))
    assert "ORDER BY schedule_runs.id ASC" in sql
    assert sql.endswith("FOR UPDATE")
