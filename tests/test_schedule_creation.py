from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from discord_ai_reminder_bot.application.schedule_creation import (
    DuplicateScheduleWarning,
    OnceScheduleCreationService,
)
from discord_ai_reminder_bot.domain.enums import ScheduleStatus
from discord_ai_reminder_bot.domain.exceptions import InvalidDateTimeError
from discord_ai_reminder_bot.domain.schedule_creation import (
    InvalidScheduleContentError,
    parse_once_scheduled_at,
    validate_create_content,
)

NOW = datetime(2026, 8, 18, 3, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-08-18 12:05", NOW + timedelta(minutes=5)),
        ("2026-08-20 19:30", datetime(2026, 8, 20, 10, 30, tzinfo=UTC)),
    ],
)
def test_parses_tokyo_minute_to_utc(value: str, expected: datetime) -> None:
    assert parse_once_scheduled_at(value, now=NOW) == expected


@pytest.mark.parametrize(
    "value",
    ["2026-08-18 12:04", "2026-08-18 12:00", "2026-02-30 12:30", "invalid", "2026-8-18 12:30"],
)
def test_rejects_too_soon_or_invalid_datetime(value: str) -> None:
    with pytest.raises(InvalidDateTimeError):
        parse_once_scheduled_at(value, now=NOW)


@pytest.mark.parametrize("value", ["", " ", "\n\t", "x" * 2_001, "hello @everyone", "@HERE"])
def test_rejects_invalid_content(value: str) -> None:
    with pytest.raises(InvalidScheduleContentError):
        validate_create_content(value)


def test_preserves_valid_content_and_none() -> None:
    assert validate_create_content(None) is None
    assert validate_create_content("a") == "a"
    assert validate_create_content("line 1\n<@123>\n" + "x" * 1_980).startswith("line 1\n")
    assert validate_create_content("x" * 2_000) == "x" * 2_000


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "expected_status"),
    [("body", ScheduleStatus.ACTIVE), (None, ScheduleStatus.DRAFT)],
)
async def test_service_creates_consistent_once_schedule_and_run(
    monkeypatch: pytest.MonkeyPatch,
    content: str | None,
    expected_status: ScheduleStatus,
) -> None:
    schedules = AsyncMock()
    schedules.has_once_duplicate.return_value = False

    async def add_schedule(schedule):
        schedule.id = 99
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
    created = await OnceScheduleCreationService(AsyncMock()).create(
        guild_id=10,
        channel_id=20,
        creator_user_id=30,
        scheduled_for=NOW + timedelta(minutes=5),
        content=content,
        allow_duplicate=False,
        now=NOW,
    )
    schedule = schedules.add.await_args.args[0]
    run = runs.add.await_args.args[0]
    assert created.status is expected_status
    assert created.public_id.version == 7
    assert schedule.guild_id == 10
    assert schedule.creator_user_id == 30
    assert schedule.next_run_at == run.scheduled_for
    assert run.schedule_id == 99
    assert run.status == "pending"
    assert run.next_attempt_at == run.scheduled_for


@pytest.mark.asyncio
async def test_duplicate_requires_confirmation_and_allow_duplicate_bypasses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schedules = AsyncMock()
    schedules.has_once_duplicate.return_value = True
    runs = AsyncMock()
    monkeypatch.setattr(
        "discord_ai_reminder_bot.application.schedule_creation.ScheduleRepository",
        lambda unused: schedules,
    )
    monkeypatch.setattr(
        "discord_ai_reminder_bot.application.schedule_creation.ScheduleRunRepository",
        lambda unused: runs,
    )
    service = OnceScheduleCreationService(AsyncMock())
    arguments = {
        "guild_id": 10,
        "channel_id": 20,
        "creator_user_id": 30,
        "scheduled_for": NOW + timedelta(minutes=5),
        "content": None,
        "now": NOW,
    }
    with pytest.raises(DuplicateScheduleWarning):
        await service.create(**arguments, allow_duplicate=False)
    schedules.add.assert_not_awaited()
    schedules.has_once_duplicate.reset_mock()

    async def add_schedule(schedule):
        schedule.id = 1
        return schedule

    schedules.add.side_effect = add_schedule
    await service.create(**arguments, allow_duplicate=True)
    schedules.has_once_duplicate.assert_not_awaited()
    runs.add.assert_awaited_once()
