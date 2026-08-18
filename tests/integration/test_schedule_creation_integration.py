from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from discord_ai_reminder_bot.application.schedule_creation import (
    DuplicateScheduleWarning,
    OnceScheduleCreationService,
)
from discord_ai_reminder_bot.infrastructure.database.models import Schedule, ScheduleRun

pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 8, 18, 3, 0, tzinfo=UTC)
SCHEDULED_FOR = NOW + timedelta(hours=1)


async def test_real_postgres_creates_active_and_draft_with_consistent_runs(
    db_session: AsyncSession,
) -> None:
    service = OnceScheduleCreationService(db_session)
    active = await service.create(
        guild_id=9_100,
        channel_id=9_200,
        creator_user_id=9_300,
        scheduled_for=SCHEDULED_FOR,
        content="line one\n<@123>",
        allow_duplicate=False,
        now=NOW,
    )
    draft = await service.create(
        guild_id=9_100,
        channel_id=9_201,
        creator_user_id=9_300,
        scheduled_for=SCHEDULED_FOR,
        content=None,
        allow_duplicate=False,
        now=NOW,
    )

    schedules = list(
        (
            await db_session.execute(
                select(Schedule).where(Schedule.guild_id == 9_100).order_by(Schedule.channel_id)
            )
        ).scalars()
    )
    runs = list(
        (
            await db_session.execute(
                select(ScheduleRun)
                .where(ScheduleRun.schedule_id.in_([item.id for item in schedules]))
                .order_by(ScheduleRun.schedule_id)
            )
        ).scalars()
    )
    assert active.public_id.version == draft.public_id.version == 7
    assert [item.status for item in schedules] == ["active", "draft"]
    assert schedules[0].content == "line one\n<@123>"
    assert schedules[1].content is None
    assert len(runs) == 2
    assert all(run.status == "pending" and run.attempt_count == 0 for run in runs)
    assert all(run.scheduled_for == SCHEDULED_FOR for run in runs)
    assert all(run.next_attempt_at == run.scheduled_for for run in runs)
    assert all(schedule.next_run_at == run.scheduled_for for schedule, run in zip(schedules, runs))


async def test_real_postgres_duplicate_warning_and_explicit_override(
    db_session: AsyncSession,
) -> None:
    service = OnceScheduleCreationService(db_session)
    arguments = {
        "guild_id": 9_110,
        "channel_id": 9_210,
        "creator_user_id": 9_310,
        "scheduled_for": SCHEDULED_FOR,
        "content": None,
        "now": NOW,
    }
    await service.create(**arguments, allow_duplicate=False)
    with pytest.raises(DuplicateScheduleWarning):
        await service.create(**arguments, allow_duplicate=False)
    await service.create(**arguments, allow_duplicate=True)

    count = await db_session.scalar(
        select(func.count()).select_from(Schedule).where(Schedule.guild_id == 9_110)
    )
    assert count == 2


async def test_real_postgres_duplicate_boundary_excludes_other_values(
    db_session: AsyncSession,
) -> None:
    service = OnceScheduleCreationService(db_session)
    common = {
        "guild_id": 9_120,
        "channel_id": 9_220,
        "creator_user_id": 9_320,
        "scheduled_for": SCHEDULED_FOR,
        "content": "same",
        "allow_duplicate": False,
        "now": NOW,
    }
    await service.create(**common)
    await service.create(**{**common, "guild_id": 9_121})
    await service.create(**{**common, "channel_id": 9_221})
    await service.create(**{**common, "scheduled_for": SCHEDULED_FOR + timedelta(minutes=1)})
    await service.create(**{**common, "content": "different"})

    count = await db_session.scalar(select(func.count()).select_from(Schedule))
    assert count == 5


async def test_real_postgres_caller_transaction_rolls_back_both_rows(
    test_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    guild_id = 9_130
    with pytest.raises(RuntimeError, match="force rollback"):
        async with factory() as session, session.begin():
            await OnceScheduleCreationService(session).create(
                guild_id=guild_id,
                channel_id=9_230,
                creator_user_id=9_330,
                scheduled_for=SCHEDULED_FOR,
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
