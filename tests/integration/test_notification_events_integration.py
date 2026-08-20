import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from discord_ai_reminder_bot.application.notification_events import NotificationEventService
from discord_ai_reminder_bot.domain.enums import NotificationType
from discord_ai_reminder_bot.infrastructure.database.models import (
    NotificationLog,
    Schedule,
    ScheduleRun,
)

pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 8, 19, 3, 0, tzinfo=UTC)


async def _seed(factory) -> tuple[int, int]:
    async with factory() as session, session.begin():
        schedule = Schedule(
            public_id=uuid.uuid7(),
            guild_id=100,
            channel_id=200,
            creator_user_id=300,
            schedule_type="once",
            status="active",
            content="private body",
            next_run_at=NOW,
            version=1,
        )
        session.add(schedule)
        await session.flush()
        run = ScheduleRun(
            schedule_id=schedule.id,
            scheduled_for=NOW,
            status="failed",
            attempt_count=4,
            next_attempt_at=None,
            result_code="delivery_failed",
            finished_at=NOW,
        )
        session.add(run)
        await session.flush()
        return schedule.id, run.id


async def _add(factory, schedule_id: int, run_id: int):
    session = factory()
    transaction = await session.begin()
    schedule = await session.get(Schedule, schedule_id)
    run = await session.get(ScheduleRun, run_id)
    assert schedule is not None and run is not None
    task = asyncio.create_task(
        NotificationEventService(
            session, configured_guild_id=100, operator_channel_id=400
        ).add_run_event(
            schedule=schedule,
            run=run,
            notification_type=NotificationType.RUN_FAILED,
            event_at=NOW,
        )
    )
    return session, transaction, task


async def test_two_sessions_create_one_idempotent_business_event(
    test_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    schedule_id, run_id = await _seed(factory)
    first_session, first_tx, first_task = await _add(factory, schedule_id, run_id)
    first = await first_task
    second_session, second_tx, second_task = await _add(factory, schedule_id, run_id)
    await asyncio.sleep(0.05)
    assert not second_task.done()
    await first_tx.commit()
    second = await asyncio.wait_for(second_task, timeout=1)
    await second_tx.commit()
    await first_session.close()
    await second_session.close()
    try:
        assert first is not None and second is not None and first.id == second.id
        async with factory() as verifier:
            count = await verifier.scalar(
                select(func.count())
                .select_from(NotificationLog)
                .where(NotificationLog.schedule_run_id == run_id)
            )
            stored = (
                await verifier.execute(
                    select(NotificationLog).where(NotificationLog.schedule_run_id == run_id)
                )
            ).scalar_one()
            assert count == 1
            assert stored.error_code is None and stored.error_summary is None
            assert stored.scheduled_at == stored.next_attempt_at == NOW
    finally:
        async with factory() as cleanup, cleanup.begin():
            await cleanup.execute(
                delete(NotificationLog).where(NotificationLog.schedule_id == schedule_id)
            )
            await cleanup.execute(delete(ScheduleRun).where(ScheduleRun.schedule_id == schedule_id))
            await cleanup.execute(delete(Schedule).where(Schedule.id == schedule_id))


async def test_business_event_rollback_is_not_visible_or_persisted(
    test_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    schedule_id, run_id = await _seed(factory)
    session, transaction, task = await _add(factory, schedule_id, run_id)
    await task
    async with factory() as observer:
        assert (
            await observer.scalar(
                select(func.count())
                .select_from(NotificationLog)
                .where(NotificationLog.schedule_run_id == run_id)
            )
            == 0
        )
    await transaction.rollback()
    await session.close()
    try:
        async with factory() as verifier:
            assert (
                await verifier.scalar(
                    select(func.count())
                    .select_from(NotificationLog)
                    .where(NotificationLog.schedule_run_id == run_id)
                )
                == 0
            )
    finally:
        async with factory() as cleanup, cleanup.begin():
            await cleanup.execute(delete(ScheduleRun).where(ScheduleRun.schedule_id == schedule_id))
            await cleanup.execute(delete(Schedule).where(Schedule.id == schedule_id))


async def test_polling_and_startup_event_times_reuse_same_run_key(
    test_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    schedule_id, run_id = await _seed(factory)
    try:
        async with factory() as session, session.begin():
            schedule = await session.get(Schedule, schedule_id)
            run = await session.get(ScheduleRun, run_id)
            assert schedule is not None and run is not None
            events = NotificationEventService(
                session, configured_guild_id=100, operator_channel_id=400
            )
            first = await events.add_run_event(
                schedule=schedule,
                run=run,
                notification_type=NotificationType.RUN_SKIPPED,
                event_at=NOW,
            )
            second = await events.add_run_event(
                schedule=schedule,
                run=run,
                notification_type=NotificationType.RUN_SKIPPED,
                event_at=NOW.replace(microsecond=1),
            )
            assert first is not None and second is not None and first.id == second.id
            assert await session.scalar(select(func.count(NotificationLog.id))) == 1
    finally:
        async with factory() as cleanup, cleanup.begin():
            await cleanup.execute(
                delete(NotificationLog).where(NotificationLog.schedule_id == schedule_id)
            )
            await cleanup.execute(delete(ScheduleRun).where(ScheduleRun.schedule_id == schedule_id))
            await cleanup.execute(delete(Schedule).where(Schedule.id == schedule_id))
