from datetime import UTC, date, datetime, time, timedelta
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from discord_ai_reminder_bot.application.schedule_creation import (
    DuplicateScheduleWarning,
    RecurringScheduleCreationService,
)
from discord_ai_reminder_bot.domain.enums import ScheduleStatus, ScheduleType
from discord_ai_reminder_bot.domain.exceptions import InvalidDateTimeError
from discord_ai_reminder_bot.domain.recurrence import (
    first_daily_run,
    first_weekly_run,
    next_daily_run,
    next_weekly_run,
)
from discord_ai_reminder_bot.domain.schedule_creation import parse_end_date, parse_local_time

NOW = datetime(2026, 8, 18, 3, 0, tzinfo=UTC)  # Tuesday 12:00 JST


@pytest.mark.parametrize(
    ("value", "expected"),
    [("00:00", time(0, 0)), ("09:05", time(9, 5)), ("23:59", time(23, 59))],
)
def test_parse_exact_local_time(value: str, expected: time) -> None:
    assert parse_local_time(value) == expected


@pytest.mark.parametrize("value", ["0:00", "00:0", "24:00", "12:60", "12:00:00", "text"])
def test_reject_invalid_local_time(value: str) -> None:
    with pytest.raises(InvalidDateTimeError):
        parse_local_time(value)


@pytest.mark.parametrize("value", ["2026-8-18", "2026-02-30", "18-08-2026", "text"])
def test_reject_invalid_end_date(value: str) -> None:
    with pytest.raises(InvalidDateTimeError):
        parse_end_date(value)


def test_parse_optional_end_date() -> None:
    assert parse_end_date(None) is None
    assert parse_end_date("2026-08-18") == date(2026, 8, 18)


def test_initial_daily_boundary_is_inclusive_without_changing_strict_next() -> None:
    boundary = NOW + timedelta(minutes=5)
    assert first_daily_run(local_time=time(12, 5), not_before=boundary) == boundary
    assert first_daily_run(local_time=time(12, 4), not_before=boundary) == datetime(
        2026, 8, 19, 3, 4, tzinfo=UTC
    )
    assert next_daily_run(local_time=time(12, 5), after=boundary) == datetime(
        2026, 8, 19, 3, 5, tzinfo=UTC
    )


def test_initial_weekly_boundary_is_inclusive_and_other_weekday_is_next() -> None:
    boundary = NOW + timedelta(minutes=5)
    assert first_weekly_run(weekday=1, local_time=time(12, 5), not_before=boundary) == boundary
    assert first_weekly_run(weekday=1, local_time=time(12, 4), not_before=boundary) == datetime(
        2026, 8, 25, 3, 4, tzinfo=UTC
    )
    assert first_weekly_run(weekday=2, local_time=time(9), not_before=boundary) == datetime(
        2026, 8, 19, 0, 0, tzinfo=UTC
    )
    assert next_weekly_run(weekday=1, local_time=time(12, 5), after=boundary) == datetime(
        2026, 8, 25, 3, 5, tzinfo=UTC
    )


@pytest.mark.parametrize("weekday", range(7))
def test_all_weekdays_are_supported(weekday: int) -> None:
    result = first_weekly_run(weekday=weekday, local_time=time(9), not_before=NOW)
    assert result is not None
    assert result.astimezone(ZoneInfo("Asia/Tokyo")).weekday() == weekday


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("schedule_type", "weekday", "content", "expected_status"),
    [
        (ScheduleType.DAILY, None, "body", ScheduleStatus.ACTIVE),
        (ScheduleType.DAILY, None, None, ScheduleStatus.DRAFT),
        (ScheduleType.WEEKLY, 1, "body", ScheduleStatus.ACTIVE),
        (ScheduleType.WEEKLY, 1, None, ScheduleStatus.DRAFT),
    ],
)
async def test_service_creates_recurring_schedule_and_consistent_first_run(
    monkeypatch: pytest.MonkeyPatch,
    schedule_type: ScheduleType,
    weekday: int | None,
    content: str | None,
    expected_status: ScheduleStatus,
) -> None:
    schedules = AsyncMock()
    schedules.has_recurring_duplicate.return_value = False

    async def add_schedule(schedule):
        schedule.id = 91
        return schedule

    schedules.add.side_effect = add_schedule
    runs = AsyncMock()
    monkeypatch.setattr(
        "discord_ai_reminder_bot.application.schedule_creation.ScheduleRepository",
        lambda unused: schedules,
    )
    monkeypatch.setattr(
        "discord_ai_reminder_bot.application.schedule_creation.ScheduleRunRepository",
        lambda unused: runs,
    )
    created = await RecurringScheduleCreationService(AsyncMock()).create(
        guild_id=10,
        channel_id=20,
        creator_user_id=30,
        schedule_type=schedule_type,
        local_time=time(12, 5),
        weekday=weekday,
        end_date=date(2026, 8, 18),
        content=content,
        allow_duplicate=False,
        now=NOW,
    )
    schedule = schedules.add.await_args.args[0]
    run = runs.add.await_args.args[0]
    assert created.status is expected_status
    assert created.public_id.version == 7
    assert (
        schedule.next_run_at
        == run.scheduled_for
        == run.next_attempt_at
        == NOW + timedelta(minutes=5)
    )
    assert schedule.weekday == weekday
    assert run.status == "pending"


@pytest.mark.asyncio
async def test_service_rejects_end_date_before_first_run_without_saving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schedules = AsyncMock()
    runs = AsyncMock()
    monkeypatch.setattr(
        "discord_ai_reminder_bot.application.schedule_creation.ScheduleRepository",
        lambda unused: schedules,
    )
    monkeypatch.setattr(
        "discord_ai_reminder_bot.application.schedule_creation.ScheduleRunRepository",
        lambda unused: runs,
    )
    with pytest.raises(InvalidDateTimeError):
        await RecurringScheduleCreationService(AsyncMock()).create(
            guild_id=10,
            channel_id=20,
            creator_user_id=30,
            schedule_type=ScheduleType.DAILY,
            local_time=time(11),
            weekday=None,
            end_date=date(2026, 8, 18),
            content="body",
            allow_duplicate=False,
            now=NOW,
        )
    schedules.add.assert_not_awaited()
    runs.add.assert_not_awaited()


@pytest.mark.asyncio
async def test_recurring_duplicate_requires_explicit_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schedules = AsyncMock()
    schedules.has_recurring_duplicate.return_value = True
    runs = AsyncMock()
    monkeypatch.setattr(
        "discord_ai_reminder_bot.application.schedule_creation.ScheduleRepository",
        lambda unused: schedules,
    )
    monkeypatch.setattr(
        "discord_ai_reminder_bot.application.schedule_creation.ScheduleRunRepository",
        lambda unused: runs,
    )
    service = RecurringScheduleCreationService(AsyncMock())
    arguments = {
        "guild_id": 10,
        "channel_id": 20,
        "creator_user_id": 30,
        "schedule_type": ScheduleType.DAILY,
        "local_time": time(13),
        "weekday": None,
        "end_date": None,
        "content": None,
        "now": NOW,
    }
    with pytest.raises(DuplicateScheduleWarning):
        await service.create(**arguments, allow_duplicate=False)
    schedules.add.assert_not_awaited()

    async def add_schedule(schedule):
        schedule.id = 1
        return schedule

    schedules.add.side_effect = add_schedule
    await service.create(**arguments, allow_duplicate=True)
    schedules.has_recurring_duplicate.assert_awaited_once()
    runs.add.assert_awaited_once()
