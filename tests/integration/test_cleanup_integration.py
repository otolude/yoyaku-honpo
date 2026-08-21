import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from discord_ai_reminder_bot.application.cleanup import CleanupService
from discord_ai_reminder_bot.domain.clock import FixedClock
from discord_ai_reminder_bot.infrastructure.database.cleanup_repositories import CleanupRepository
from discord_ai_reminder_bot.infrastructure.database.models import (
    DeliveryAttempt,
    NotificationAttempt,
    NotificationLog,
    OperationLog,
    Schedule,
    ScheduleRun,
)

pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 8, 20, 3, 0, tzinfo=UTC)
OLD = NOW - timedelta(days=30)
GUILD_ID = 7_300_001


async def _seed_schedule(
    factory,
    *,
    terminal_at: datetime = OLD,
    status: str = "completed",
    delete_kind: str | None = None,
) -> int:
    async with factory() as session, session.begin():
        schedule = Schedule(
            public_id=uuid.uuid7(),
            guild_id=GUILD_ID,
            channel_id=7_300_002,
            creator_user_id=7_300_003,
            schedule_type="once" if status == "completed" else "daily",
            status=status,
            content="cleanup integration body",
            next_run_at=None,
            local_time=None if status == "completed" else datetime.min.time(),
            version=1,
            terminal_at=terminal_at,
            deleted_at=terminal_at if status == "deleted" else None,
        )
        session.add(schedule)
        await session.flush()
        run = ScheduleRun(
            schedule_id=schedule.id,
            scheduled_for=terminal_at,
            status="failed",
            attempt_count=1,
            next_attempt_at=None,
            result_code="delivery_failed",
            finished_at=terminal_at,
        )
        session.add(run)
        await session.flush()
        session.add(
            DeliveryAttempt(
                schedule_run_id=run.id,
                attempt_number=1,
                status="failed",
                claimed_by=uuid.uuid7(),
                claimed_at=terminal_at,
                finished_at=terminal_at,
                error_kind="permanent",
                error_code="delivery_failed",
                error_summary="safe",
            )
        )
        if status == "deleted":
            session.add(
                OperationLog(
                    schedule_id=schedule.id,
                    action="deleted",
                    actor_type="user",
                    actor_user_id=7_300_003,
                    delete_kind=delete_kind or "creator_deleted",
                    delete_reason="confirmed",
                    created_at=terminal_at,
                )
            )
        else:
            session.add(
                OperationLog(
                    schedule_id=schedule.id,
                    action="completed" if status == "completed" else "ended",
                    actor_type="system",
                    actor_user_id=None,
                    created_at=terminal_at,
                )
            )
        direct = _terminal_log(schedule_id=schedule.id, run_id=None, at=terminal_at, suffix="d")
        via_run = _terminal_log(schedule_id=None, run_id=run.id, at=terminal_at, suffix="r")
        session.add_all((direct, via_run))
        await session.flush()
        session.add_all(
            (_terminal_attempt(direct.id, terminal_at), _terminal_attempt(via_run.id, terminal_at))
        )
        return schedule.id


def _terminal_log(
    *, schedule_id: int | None, run_id: int | None, at: datetime, suffix: str
) -> NotificationLog:
    return NotificationLog(
        schedule_id=schedule_id,
        schedule_run_id=run_id,
        notification_type="run_failed",
        recipient_type="log",
        recipient_id=None,
        status="failed",
        deduplication_key=f"cleanup-integration-{uuid.uuid7()}-{suffix}",
        error_code="notification_failed",
        error_summary="safe",
        scheduled_at=at,
        next_attempt_at=None,
        attempt_count=1,
        started_at=at,
        finished_at=at,
    )


def _terminal_attempt(log_id: int, at: datetime) -> NotificationAttempt:
    return NotificationAttempt(
        notification_log_id=log_id,
        attempt_number=1,
        status="failed",
        claimed_by=uuid.uuid7(),
        claimed_at=at,
        finished_at=at,
        error_kind="permanent",
        error_code="notification_failed",
        error_summary="safe",
        updated_at=at,
    )


async def _cleanup_seed(factory) -> None:
    async with factory() as session, session.begin():
        schedule_ids = select(Schedule.id).where(Schedule.guild_id == GUILD_ID)
        run_ids = select(ScheduleRun.id).where(ScheduleRun.schedule_id.in_(schedule_ids))
        log_ids = select(NotificationLog.id).where(
            (NotificationLog.schedule_id.in_(schedule_ids))
            | (NotificationLog.schedule_run_id.in_(run_ids))
            | NotificationLog.deduplication_key.like("cleanup-integration-%")
        )
        await session.execute(
            delete(NotificationAttempt).where(NotificationAttempt.notification_log_id.in_(log_ids))
        )
        await session.execute(delete(NotificationLog).where(NotificationLog.id.in_(log_ids)))
        await session.execute(
            delete(DeliveryAttempt).where(DeliveryAttempt.schedule_run_id.in_(run_ids))
        )
        await session.execute(
            delete(OperationLog).where(OperationLog.schedule_id.in_(schedule_ids))
        )
        await session.execute(delete(ScheduleRun).where(ScheduleRun.id.in_(run_ids)))
        await session.execute(delete(Schedule).where(Schedule.id.in_(schedule_ids)))


async def test_schedule_and_all_restrict_children_are_deleted_atomically(
    test_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    schedule_id = await _seed_schedule(factory)
    try:
        result = await CleanupService(session_factory=factory, clock=FixedClock(NOW)).run_cycle()
        assert result.schedules_deleted == 1
        assert result.notification_attempts_deleted == 2
        assert result.notification_logs_deleted == 2
        assert result.delivery_attempts_deleted == 1
        assert result.operation_logs_deleted == 1
        assert result.schedule_runs_deleted == 1
        async with factory() as verifier:
            assert await verifier.get(Schedule, schedule_id) is None
    finally:
        await _cleanup_seed(factory)


@pytest.mark.parametrize(
    "delete_kind", ["creator_deleted", "admin_deleted", "operator_resolved_failed"]
)
async def test_deleted_schedule_cleanup_does_not_depend_on_delete_kind(
    test_engine: AsyncEngine, delete_kind: str
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    schedule_id = await _seed_schedule(factory, status="deleted", delete_kind=delete_kind)
    try:
        result = await CleanupService(session_factory=factory, clock=FixedClock(NOW)).run_cycle()
        assert result.schedules_deleted == 1
        async with factory() as verifier:
            assert await verifier.get(Schedule, schedule_id) is None
    finally:
        await _cleanup_seed(factory)


async def test_global_uses_finished_at_and_deletes_attempt_first(
    test_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        due = _terminal_log(schedule_id=None, run_id=None, at=OLD, suffix="global")
        recent = _terminal_log(
            schedule_id=None,
            run_id=None,
            at=OLD + timedelta(microseconds=1),
            suffix="recent",
        )
        session.add_all((due, recent))
        await session.flush()
        session.add(_terminal_attempt(due.id, OLD))
        due_id, recent_id = due.id, recent.id
    try:
        result = await CleanupService(session_factory=factory, clock=FixedClock(NOW)).run_cycle()
        assert result.global_notifications_deleted == 1
        assert result.notification_attempts_deleted == 1
        async with factory() as verifier:
            assert await verifier.get(NotificationLog, due_id) is None
            assert await verifier.get(NotificationLog, recent_id) is not None
    finally:
        await _cleanup_seed(factory)


async def test_in_flight_run_keeps_due_schedule(test_engine: AsyncEngine) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    schedule_id = await _seed_schedule(factory)
    async with factory() as session, session.begin():
        run = (
            await session.execute(select(ScheduleRun).where(ScheduleRun.schedule_id == schedule_id))
        ).scalar_one()
        await session.execute(
            delete(DeliveryAttempt).where(DeliveryAttempt.schedule_run_id == run.id)
        )
        run.status = "processing"
        run.finished_at = None
        run.claimed_by = uuid.uuid7()
        run.claimed_at = NOW
        run.lease_expires_at = NOW + timedelta(minutes=2)
    try:
        result = await CleanupService(session_factory=factory, clock=FixedClock(NOW)).run_cycle()
        assert result.schedules_deleted == 0
        async with factory() as verifier:
            assert await verifier.get(Schedule, schedule_id) is not None
    finally:
        await _cleanup_seed(factory)


@pytest.mark.parametrize(
    "blocker_kind", ["delivery_attempt", "notification_log", "notification_attempt"]
)
async def test_other_in_flight_children_keep_due_schedule(
    test_engine: AsyncEngine, blocker_kind: str
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    schedule_id = await _seed_schedule(factory)
    async with factory() as session, session.begin():
        run_id = await session.scalar(
            select(ScheduleRun.id).where(ScheduleRun.schedule_id == schedule_id)
        )
        if blocker_kind == "delivery_attempt":
            attempt = (
                await session.execute(
                    select(DeliveryAttempt).where(DeliveryAttempt.schedule_run_id == run_id)
                )
            ).scalar_one()
            attempt.status = "claimed"
            attempt.finished_at = None
            attempt.error_kind = attempt.error_code = attempt.error_summary = None
        elif blocker_kind == "notification_log":
            notification = (
                await session.execute(
                    select(NotificationLog).where(NotificationLog.schedule_id == schedule_id)
                )
            ).scalar_one()
            notification.status = "pending"
            notification.next_attempt_at = NOW
            notification.started_at = None
            notification.finished_at = None
        else:
            notification_id = await session.scalar(
                select(NotificationLog.id).where(NotificationLog.schedule_id == schedule_id)
            )
            attempt = (
                await session.execute(
                    select(NotificationAttempt).where(
                        NotificationAttempt.notification_log_id == notification_id
                    )
                )
            ).scalar_one()
            attempt.status = "claimed"
            attempt.finished_at = None
            attempt.error_kind = attempt.error_code = attempt.error_summary = None
    try:
        result = await CleanupService(session_factory=factory, clock=FixedClock(NOW)).run_cycle()
        assert result.schedules_deleted == 0
        async with factory() as verifier:
            assert await verifier.get(Schedule, schedule_id) is not None
    finally:
        await _cleanup_seed(factory)


async def test_skip_locked_does_not_wait_for_other_session(test_engine: AsyncEngine) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    schedule_id = await _seed_schedule(factory)
    first = factory()
    transaction = await first.begin()
    await first.execute(select(Schedule).where(Schedule.id == schedule_id).with_for_update())
    try:
        async with factory() as second, second.begin():
            repository = CleanupRepository(second)
            await repository.set_local_lock_timeout()
            selected = await asyncio.wait_for(
                repository.lock_next_schedule(retention_cutoff=OLD), timeout=0.5
            )
            assert selected is None
    finally:
        await transaction.rollback()
        await first.close()
        await _cleanup_seed(factory)


async def test_two_cleanup_services_delete_a_schedule_only_once(test_engine: AsyncEngine) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    await _seed_schedule(factory)
    try:
        first, second = await asyncio.gather(
            CleanupService(session_factory=factory, clock=FixedClock(NOW)).run_cycle(),
            CleanupService(session_factory=factory, clock=FixedClock(NOW)).run_cycle(),
        )
        assert first.schedules_deleted + second.schedules_deleted == 1
    finally:
        await _cleanup_seed(factory)


async def test_uncommitted_schedule_delete_is_invisible_and_rollback_restores(
    test_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    schedule_id = await _seed_schedule(factory)
    session = factory()
    transaction = await session.begin()
    repository = CleanupRepository(session)
    await repository.set_local_lock_timeout()
    schedule = await repository.lock_next_schedule(retention_cutoff=OLD)
    assert schedule is not None
    await repository.delete_schedule(schedule=schedule)
    try:
        async with factory() as observer:
            assert await observer.get(Schedule, schedule_id) is not None
        await transaction.rollback()
        async with factory() as verifier:
            assert await verifier.get(Schedule, schedule_id) is not None
    finally:
        if transaction.is_active:
            await transaction.rollback()
        await session.close()
        await _cleanup_seed(factory)


async def test_set_local_lock_timeout_is_transaction_local(test_engine: AsyncEngine) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        await CleanupRepository(session).set_local_lock_timeout()
        assert await session.scalar(text("SHOW lock_timeout")) == "1s"
    async with factory() as session, session.begin():
        assert await session.scalar(text("SHOW lock_timeout")) == "0"


async def test_locked_child_times_out_and_rolls_back_only_that_schedule(
    test_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    schedule_id = await _seed_schedule(factory)
    blocker = factory()
    blocker_tx = await blocker.begin()
    locked_log = (
        await blocker.execute(
            select(NotificationLog)
            .where(NotificationLog.schedule_id == schedule_id)
            .with_for_update()
        )
    ).scalar_one()
    try:
        result = await asyncio.wait_for(
            CleanupService(session_factory=factory, clock=FixedClock(NOW)).run_cycle(), timeout=3
        )
        assert result.schedules_deleted == 0
        assert result.internal_errors == 1
        assert result.incomplete
        async with factory() as verifier:
            assert await verifier.get(Schedule, schedule_id) is not None
            assert await verifier.get(NotificationLog, locked_log.id) is not None
            assert (
                await verifier.scalar(
                    select(func.count(NotificationAttempt.id)).where(
                        NotificationAttempt.notification_log_id == locked_log.id
                    )
                )
                == 1
            )
    finally:
        await blocker_tx.rollback()
        await blocker.close()
        await _cleanup_seed(factory)


async def test_run_related_notification_is_not_global(test_engine: AsyncEngine) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    schedule_id = await _seed_schedule(factory, terminal_at=OLD + timedelta(days=1))
    try:
        result = await CleanupService(session_factory=factory, clock=FixedClock(NOW)).run_cycle()
        assert result.schedules_deleted == 0
        assert result.global_notifications_deleted == 0
        async with factory() as verifier:
            run_id = await verifier.scalar(
                select(ScheduleRun.id).where(ScheduleRun.schedule_id == schedule_id)
            )
            assert (
                await verifier.scalar(
                    select(func.count(NotificationLog.id)).where(
                        NotificationLog.schedule_id.is_(None),
                        NotificationLog.schedule_run_id == run_id,
                    )
                )
                == 1
            )
    finally:
        await _cleanup_seed(factory)


async def test_schedule_limit_deletes_100_and_leaves_101st(test_engine: AsyncEngine) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        session.add_all(
            Schedule(
                public_id=uuid.uuid7(),
                guild_id=GUILD_ID,
                channel_id=7_300_002,
                creator_user_id=7_300_003,
                schedule_type="once",
                status="completed",
                content="bounded cleanup",
                next_run_at=None,
                version=1,
                terminal_at=OLD,
            )
            for _ in range(101)
        )
    try:
        result = await CleanupService(session_factory=factory, clock=FixedClock(NOW)).run_cycle()
        assert result.schedules_deleted == 100
        assert result.schedules_remaining_due == 1
        assert result.incomplete
        async with factory() as verifier:
            assert (
                await verifier.scalar(
                    select(func.count(Schedule.id)).where(Schedule.guild_id == GUILD_ID)
                )
                == 1
            )
    finally:
        await _cleanup_seed(factory)


async def test_cleanup_cycle_reads_clock_once_and_reuses_one_cutoff(
    test_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    class CountingClock:
        def __init__(self) -> None:
            self.calls = 0

        def now(self) -> datetime:
            self.calls += 1
            return NOW

    clock = CountingClock()
    observed_cutoffs: list[datetime] = []
    original_schedule = CleanupRepository.lock_next_schedule
    original_global = CleanupRepository.lock_next_global_notification
    original_counts = CleanupRepository.count_due_schedules
    original_global_counts = CleanupRepository.count_due_global_notifications

    async def schedule(repository, *, retention_cutoff, excluded_ids=frozenset()):
        observed_cutoffs.append(retention_cutoff)
        return await original_schedule(
            repository, retention_cutoff=retention_cutoff, excluded_ids=excluded_ids
        )

    async def global_notification(repository, *, retention_cutoff, excluded_ids=frozenset()):
        observed_cutoffs.append(retention_cutoff)
        return await original_global(
            repository, retention_cutoff=retention_cutoff, excluded_ids=excluded_ids
        )

    async def schedule_counts(repository, *, retention_cutoff):
        observed_cutoffs.append(retention_cutoff)
        return await original_counts(repository, retention_cutoff=retention_cutoff)

    async def global_counts(repository, *, retention_cutoff):
        observed_cutoffs.append(retention_cutoff)
        return await original_global_counts(repository, retention_cutoff=retention_cutoff)

    monkeypatch.setattr(CleanupRepository, "lock_next_schedule", schedule)
    monkeypatch.setattr(CleanupRepository, "lock_next_global_notification", global_notification)
    monkeypatch.setattr(CleanupRepository, "count_due_schedules", schedule_counts)
    monkeypatch.setattr(CleanupRepository, "count_due_global_notifications", global_counts)

    result = await CleanupService(session_factory=factory, clock=clock).run_cycle()

    assert clock.calls == 1
    assert result.cleanup_cutoff == NOW
    assert observed_cutoffs
    assert set(observed_cutoffs) == {OLD}
