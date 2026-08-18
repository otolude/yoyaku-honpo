from datetime import UTC, date, datetime, time

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from discord_ai_reminder_bot.application.schedule_creation import (
    DuplicateScheduleWarning,
    RecurringScheduleCreationService,
)
from discord_ai_reminder_bot.domain.enums import ScheduleType
from discord_ai_reminder_bot.infrastructure.database.models import Schedule, ScheduleRun

pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 8, 18, 3, 0, tzinfo=UTC)


async def test_real_postgres_creates_daily_weekly_active_draft_and_consistent_runs(
    db_session: AsyncSession,
) -> None:
    service = RecurringScheduleCreationService(db_session)
    inputs = [
        (ScheduleType.DAILY, None, "daily body"),
        (ScheduleType.DAILY, None, None),
        (ScheduleType.WEEKLY, 1, "weekly body"),
        (ScheduleType.WEEKLY, 1, None),
    ]
    for index, (schedule_type, weekday, content) in enumerate(inputs):
        await service.create(
            guild_id=9_400,
            channel_id=9_500 + index,
            creator_user_id=9_600,
            schedule_type=schedule_type,
            local_time=time(12, 5),
            weekday=weekday,
            end_date=date(2026, 8, 18),
            content=content,
            allow_duplicate=False,
            now=NOW,
        )
    schedules = list(
        (await db_session.scalars(select(Schedule).where(Schedule.guild_id == 9_400))).all()
    )
    runs = list(
        (
            await db_session.scalars(
                select(ScheduleRun).where(
                    ScheduleRun.schedule_id.in_([schedule.id for schedule in schedules])
                )
            )
        ).all()
    )
    assert {schedule.status for schedule in schedules} == {"active", "draft"}
    assert all(schedule.next_run_at == NOW.replace(minute=5) for schedule in schedules)
    assert all(run.scheduled_for == run.next_attempt_at == NOW.replace(minute=5) for run in runs)
    assert all(run.status == "pending" for run in runs)
    assert all(
        schedule.weekday is None for schedule in schedules if schedule.schedule_type == "daily"
    )
    assert all(
        schedule.weekday == 1 for schedule in schedules if schedule.schedule_type == "weekly"
    )


async def test_real_postgres_recurring_duplicate_end_date_and_override(
    db_session: AsyncSession,
) -> None:
    service = RecurringScheduleCreationService(db_session)
    arguments = {
        "guild_id": 9_410,
        "channel_id": 9_510,
        "creator_user_id": 9_610,
        "schedule_type": ScheduleType.WEEKLY,
        "local_time": time(13),
        "weekday": 1,
        "end_date": date(2026, 8, 25),
        "content": None,
        "now": NOW,
    }
    await service.create(**arguments, allow_duplicate=False)
    with pytest.raises(DuplicateScheduleWarning):
        await service.create(**{**arguments, "creator_user_id": 9_611}, allow_duplicate=False)
    await service.create(**{**arguments, "end_date": date(2026, 9, 1)}, allow_duplicate=False)
    await service.create(**arguments, allow_duplicate=True)
    count = await db_session.scalar(
        select(func.count()).select_from(Schedule).where(Schedule.guild_id == 9_410)
    )
    assert count == 3


async def test_real_postgres_caller_rollback_removes_schedule_and_run(
    test_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    guild_id = 9_420
    with pytest.raises(RuntimeError, match="force rollback"):
        async with factory() as session, session.begin():
            await RecurringScheduleCreationService(session).create(
                guild_id=guild_id,
                channel_id=9_520,
                creator_user_id=9_620,
                schedule_type=ScheduleType.DAILY,
                local_time=time(13),
                weekday=None,
                end_date=None,
                content="body",
                allow_duplicate=False,
                now=NOW,
            )
            raise RuntimeError("force rollback")
    async with factory() as session:
        schedule_count = await session.scalar(
            select(func.count()).select_from(Schedule).where(Schedule.guild_id == guild_id)
        )
        run_count = await session.scalar(
            select(func.count())
            .select_from(ScheduleRun)
            .join(Schedule, Schedule.id == ScheduleRun.schedule_id)
            .where(Schedule.guild_id == guild_id)
        )
    assert schedule_count == run_count == 0
