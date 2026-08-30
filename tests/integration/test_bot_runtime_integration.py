import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from discord_ai_reminder_bot.bot.client import ReminderBot
from discord_ai_reminder_bot.config import Settings
from discord_ai_reminder_bot.domain.clock import FixedClock
from discord_ai_reminder_bot.infrastructure.database.models import (
    DeliveryAttempt,
    NotificationLog,
    OperationLog,
    Schedule,
    ScheduleRun,
)
from discord_ai_reminder_bot.infrastructure.database.schema import verify_schema_revision

NOW = datetime(2026, 8, 18, 3, 0, tzinfo=UTC)


def settings() -> Settings:
    return Settings(
        APP_ENV="test",
        TIMEZONE="Asia/Tokyo",
        DISCORD_BOT_TOKEN="integration-token-never-connect",
        DISCORD_GUILD_ID=100,
        DISCORD_ALLOWED_ROLE_IDS="200",
        DISCORD_OPERATOR_USER_ID=300,
        DISCORD_OPERATOR_CHANNEL_ID=400,
        DATABASE_URL="postgresql+psycopg://masked:masked@localhost/database_test",
        SCHEDULER_BATCH_SIZE=2,
        SCHEDULER_MAX_CONCURRENCY=1,
    )


async def clean(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        for model in (NotificationLog, OperationLog, DeliveryAttempt, ScheduleRun, Schedule):
            await connection.execute(delete(model))


@pytest.mark.asyncio
async def test_real_database_schema_is_current(test_engine: AsyncEngine) -> None:
    assert await verify_schema_revision(test_engine) == "a41f8c7d2e90"


@pytest.mark.asyncio
async def test_bot_recovery_commits_multiple_real_database_batches(
    test_engine: AsyncEngine,
) -> None:
    await clean(test_engine)
    sessions = async_sessionmaker(test_engine, expire_on_commit=False)
    try:
        async with sessions() as session, session.begin():
            for index in range(3):
                worker_id = uuid.uuid7()
                schedule = Schedule(
                    public_id=uuid.uuid7(),
                    guild_id=100,
                    channel_id=200 + index,
                    creator_user_id=300,
                    schedule_type="once",
                    status="active",
                    content="integration content",
                    next_run_at=NOW - timedelta(minutes=index + 1),
                    version=1,
                )
                session.add(schedule)
                await session.flush()
                claimed_at = NOW - timedelta(minutes=3)
                run = ScheduleRun(
                    schedule_id=schedule.id,
                    scheduled_for=schedule.next_run_at,
                    status="processing",
                    attempt_count=1,
                    next_attempt_at=None,
                    claimed_by=worker_id,
                    claimed_at=claimed_at,
                    lease_expires_at=NOW - timedelta(seconds=1),
                    started_at=claimed_at,
                )
                session.add(run)
                await session.flush()
                session.add(
                    DeliveryAttempt(
                        schedule_run_id=run.id,
                        attempt_number=1,
                        status="claimed",
                        claimed_by=worker_id,
                        claimed_at=claimed_at,
                    )
                )

        bot = ReminderBot(
            settings=settings(),
            engine=test_engine,
            session_factory=sessions,
            clock=FixedClock(NOW),
            worker_id=uuid.uuid7(),
            logger=logging.getLogger("test.integration.bot"),
        )
        assert await bot.recover_expired_processing() == 3

        async with sessions() as session:
            runs = list((await session.execute(select(ScheduleRun))).scalars())
            attempts = list((await session.execute(select(DeliveryAttempt))).scalars())
        assert len(runs) == len(attempts) == 3
        assert all(run.status == "pending" for run in runs)
        assert all(run.next_attempt_at == NOW + timedelta(minutes=1) for run in runs)
        assert all(attempt.status == "failed" for attempt in attempts)
    finally:
        await clean(test_engine)
