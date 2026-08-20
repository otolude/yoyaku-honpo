from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from discord_ai_reminder_bot.application.pending_recovery import (
    PendingStartupRecoveryService,
)
from discord_ai_reminder_bot.infrastructure.database.models import (
    DeliveryAttempt,
    NotificationLog,
    OperationLog,
    Schedule,
    ScheduleRun,
)
from discord_ai_reminder_bot.infrastructure.database.repositories import ScheduleRunRepository

CUTOFF = datetime(2026, 8, 19, 3, 0, tzinfo=UTC)
EVENT_ARGS = {"configured_guild_id": 100, "operator_channel_id": 400}


async def add_schedule_run(
    session: AsyncSession,
    *,
    schedule_type: str = "once",
    status: str = "active",
    scheduled_for: datetime,
    content: str | None = "safe content",
    local_time: time | None = None,
    weekday: int | None = None,
    end_date: date | None = None,
    attempt_count: int = 0,
) -> tuple[Schedule, ScheduleRun]:
    schedule = Schedule(
        guild_id=100,
        channel_id=200,
        creator_user_id=300,
        schedule_type=schedule_type,
        status=status,
        content=content,
        next_run_at=scheduled_for,
        local_time=local_time,
        weekday=weekday,
        end_date=end_date,
        version=1,
        created_at=scheduled_for,
        updated_at=scheduled_for,
        deleted_at=None,
        terminal_at=None,
    )
    session.add(schedule)
    await session.flush()
    run = ScheduleRun(
        schedule_id=schedule.id,
        scheduled_for=scheduled_for,
        status="pending",
        attempt_count=attempt_count,
        next_attempt_at=scheduled_for,
        updated_at=scheduled_for,
    )
    session.add(run)
    await session.flush()
    return schedule, run


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("delay", "expected_status"),
    [
        (timedelta(minutes=14, seconds=59), "pending"),
        (timedelta(minutes=15), "pending"),
        (timedelta(minutes=15, microseconds=1), "skipped"),
    ],
)
async def test_once_active_inclusive_boundary(
    db_session: AsyncSession, delay: timedelta, expected_status: str
) -> None:
    schedule, run = await add_schedule_run(db_session, scheduled_for=CUTOFF - delay)
    result = await PendingStartupRecoveryService(db_session).recover_pending(
        recovery_cutoff=CUTOFF, batch_size=20, **EVENT_ARGS
    )
    await db_session.flush()
    assert run.status == expected_status
    if expected_status == "pending":
        assert schedule.status == "active"
        assert result.initial_pending_preserved == 1
    else:
        assert run.result_code == "startup_overdue"
        assert run.finished_at == CUTOFF
        assert schedule.status == "failed" and schedule.next_run_at is None
        operation = (
            await db_session.execute(
                select(OperationLog).where(OperationLog.schedule_id == schedule.id)
            )
        ).scalar_one()
        assert operation.action == "failed"
        assert operation.actor_type == "system"
        assert operation.changes == {
            "status_before": "active",
            "status_after": "failed",
            "skipped_count": 1,
            "startup_recovery": True,
        }
    notification = (
        await db_session.execute(
            select(NotificationLog).where(NotificationLog.schedule_run_id == run.id)
        )
    ).scalar_one()
    assert notification.notification_type == (
        "run_delayed" if expected_status == "pending" else "run_failed"
    )
    assert notification.scheduled_at == CUTOFF
    assert notification.error_code is None


@pytest.mark.asyncio
async def test_once_draft_skips_without_changing_schedule(db_session: AsyncSession) -> None:
    scheduled_for = CUTOFF - timedelta(seconds=1)
    schedule, run = await add_schedule_run(
        db_session, status="draft", content=None, scheduled_for=scheduled_for
    )
    await PendingStartupRecoveryService(db_session).recover_pending(
        recovery_cutoff=CUTOFF, batch_size=20, **EVENT_ARGS
    )
    await db_session.flush()
    assert run.status == "skipped" and run.result_code == "draft_without_content"
    assert schedule.status == "draft"
    assert schedule.next_run_at == scheduled_for and schedule.version == 1
    assert await db_session.scalar(select(func.count(OperationLog.id))) == 0
    notification = (
        await db_session.execute(
            select(NotificationLog).where(NotificationLog.schedule_run_id == run.id)
        )
    ).scalar_one()
    assert notification.notification_type == "run_skipped"


@pytest.mark.asyncio
@pytest.mark.parametrize(("schedule_type", "weekday"), [("daily", None), ("weekly", 2)])
async def test_recurring_skips_missed_and_creates_one_future(
    db_session: AsyncSession, schedule_type: str, weekday: int | None
) -> None:
    first = datetime(2026, 8, 17, 3, 0, tzinfo=UTC) if weekday is None else CUTOFF
    schedule, run = await add_schedule_run(
        db_session,
        schedule_type=schedule_type,
        scheduled_for=first,
        local_time=time(12, 0),
        weekday=weekday,
    )
    await PendingStartupRecoveryService(db_session).recover_pending(
        recovery_cutoff=CUTOFF, batch_size=20, **EVENT_ARGS
    )
    await db_session.flush()
    rows = list(
        (
            await db_session.execute(
                select(ScheduleRun)
                .where(ScheduleRun.schedule_id == schedule.id)
                .order_by(ScheduleRun.scheduled_for)
            )
        ).scalars()
    )
    assert run.status == "skipped"
    assert len([item for item in rows if item.status == "pending"]) == 1
    future = next(item for item in rows if item.status == "pending")
    assert future.scheduled_for > CUTOFF
    assert schedule.next_run_at == future.scheduled_for
    notifications = list(
        (
            await db_session.execute(
                select(NotificationLog).where(NotificationLog.schedule_id == schedule.id)
            )
        ).scalars()
    )
    assert len(notifications) == 1
    assert notifications[0].notification_type == "run_skipped"
    assert notifications[0].schedule_run_id is None


@pytest.mark.asyncio
async def test_five_hundred_recurring_misses_create_one_aggregate_notification(
    db_session: AsyncSession,
) -> None:
    first = CUTOFF - timedelta(days=499)
    schedule, _run = await add_schedule_run(
        db_session,
        schedule_type="daily",
        scheduled_for=first,
        local_time=time(12, 0),
    )
    await PendingStartupRecoveryService(db_session).recover_pending(
        recovery_cutoff=CUTOFF, batch_size=20, **EVENT_ARGS
    )
    skipped = await db_session.scalar(
        select(func.count())
        .select_from(ScheduleRun)
        .where(ScheduleRun.schedule_id == schedule.id, ScheduleRun.status == "skipped")
    )
    notifications = await db_session.scalar(
        select(func.count())
        .select_from(NotificationLog)
        .where(NotificationLog.schedule_id == schedule.id)
    )
    assert skipped == 500
    assert notifications == 1


@pytest.mark.asyncio
async def test_recurring_end_and_draft_no_next(db_session: AsyncSession) -> None:
    active, active_run = await add_schedule_run(
        db_session,
        schedule_type="daily",
        scheduled_for=CUTOFF,
        local_time=time(12, 0),
        end_date=date(2026, 8, 19),
    )
    draft, draft_run = await add_schedule_run(
        db_session,
        schedule_type="daily",
        status="draft",
        content=None,
        scheduled_for=CUTOFF,
        local_time=time(12, 0),
        end_date=date(2026, 8, 19),
    )
    await PendingStartupRecoveryService(db_session).recover_pending(
        recovery_cutoff=CUTOFF, batch_size=20, **EVENT_ARGS
    )
    await db_session.flush()
    assert active_run.status == "skipped" and active.status == "ended"
    assert active.terminal_at == CUTOFF and active.next_run_at is None
    assert draft_run.status == "skipped" and draft.status == "draft"
    assert draft.next_run_at == CUTOFF and draft.version == 1


@pytest.mark.asyncio
async def test_retry_is_preserved_and_inconsistent_attempt_is_failed(
    db_session: AsyncSession,
) -> None:
    _, healthy = await add_schedule_run(
        db_session, scheduled_for=CUTOFF - timedelta(days=2), attempt_count=1
    )
    db_session.add(
        DeliveryAttempt(
            schedule_run_id=healthy.id,
            attempt_number=1,
            status="failed",
            claimed_by=__import__("uuid").uuid7(),
            claimed_at=CUTOFF - timedelta(days=1),
            finished_at=CUTOFF - timedelta(days=1),
            error_kind="transient",
            error_code="temporary",
            error_summary="Temporary delivery failure",
        )
    )
    bad_schedule, bad = await add_schedule_run(
        db_session, scheduled_for=CUTOFF - timedelta(days=3), attempt_count=4
    )
    await db_session.flush()
    await PendingStartupRecoveryService(db_session).recover_pending(
        recovery_cutoff=CUTOFF, batch_size=20, **EVENT_ARGS
    )
    await db_session.flush()
    assert healthy.status == "pending"
    assert bad.status == "failed" and bad.result_code == "startup_inconsistent_pending"
    assert bad_schedule.status == "failed"
    notifications = list((await db_session.execute(select(NotificationLog))).scalars())
    assert len(notifications) == 1
    assert notifications[0].notification_type == "recovery"
    assert notifications[0].schedule_run_id == bad.id


@pytest.mark.asyncio
async def test_concurrent_pending_selection_uses_skip_locked(
    test_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as seed, seed.begin():
        first_schedule, _ = await add_schedule_run(seed, scheduled_for=CUTOFF - timedelta(hours=2))
        second_schedule, _ = await add_schedule_run(seed, scheduled_for=CUTOFF - timedelta(hours=1))
        schedule_ids = [first_schedule.id, second_schedule.id]
    try:
        async with factory() as first, factory() as second:
            first_tx = await first.begin()
            second_tx = await second.begin()
            first_rows = await ScheduleRunRepository(first).lock_startup_pending(
                recovered_at=CUTOFF, batch_size=1
            )
            second_rows = await ScheduleRunRepository(second).lock_startup_pending(
                recovered_at=CUTOFF, batch_size=1
            )
            assert len(first_rows) == len(second_rows) == 1
            assert first_rows[0].id != second_rows[0].id
            await first_tx.rollback()
            await second_tx.rollback()
    finally:
        async with factory() as cleanup, cleanup.begin():
            await cleanup.execute(
                delete(ScheduleRun).where(ScheduleRun.schedule_id.in_(schedule_ids))
            )
            await cleanup.execute(delete(Schedule).where(Schedule.id.in_(schedule_ids)))


@pytest.mark.asyncio
async def test_recovery_savepoint_rollback_restores_all_rows(db_session: AsyncSession) -> None:
    schedule, run = await add_schedule_run(db_session, scheduled_for=CUTOFF - timedelta(hours=1))
    with pytest.raises(RuntimeError, match="force rollback"):
        async with db_session.begin_nested():
            await PendingStartupRecoveryService(db_session).recover_pending(
                recovery_cutoff=CUTOFF, batch_size=20
            )
            await db_session.flush()
            raise RuntimeError("force rollback")
    await db_session.refresh(schedule)
    await db_session.refresh(run)
    assert schedule.status == "active" and schedule.next_run_at == run.scheduled_for
    assert run.status == "pending"
    assert await db_session.scalar(select(func.count(OperationLog.id))) == 0
