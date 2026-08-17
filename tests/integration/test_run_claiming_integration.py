import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from discord_ai_reminder_bot.infrastructure.database.models import (
    DeliveryAttempt,
    OperationLog,
    Schedule,
    ScheduleRun,
)
from discord_ai_reminder_bot.infrastructure.database.repositories import (
    ScheduleRepository,
    ScheduleRunRepository,
)

pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 8, 17, 3, 0, tzinfo=UTC)
LEASE = timedelta(minutes=2)


def make_schedule(*, guild_id: int = 400) -> Schedule:
    return Schedule(
        public_id=uuid.uuid7(),
        guild_id=guild_id,
        channel_id=401,
        creator_user_id=402,
        schedule_type="once",
        status="active",
        content="claim test",
        next_run_at=NOW + timedelta(days=1),
        version=1,
    )


def make_run(
    schedule_id: int,
    *,
    scheduled_for: datetime,
    next_attempt_at: datetime | None,
    status: str = "pending",
    attempt_count: int = 0,
    started_at: datetime | None = None,
) -> ScheduleRun:
    return ScheduleRun(
        schedule_id=schedule_id,
        scheduled_for=scheduled_for,
        status=status,
        attempt_count=attempt_count,
        next_attempt_at=next_attempt_at,
        started_at=started_at,
    )


async def test_claim_due_filters_orders_limits_and_creates_attempts(
    db_session: AsyncSession,
) -> None:
    schedule = await ScheduleRepository(db_session).add(make_schedule())
    runs = [
        make_run(
            schedule.id,
            scheduled_for=NOW - timedelta(minutes=20),
            next_attempt_at=NOW - timedelta(minutes=2),
        ),
        make_run(
            schedule.id,
            scheduled_for=NOW - timedelta(minutes=10),
            next_attempt_at=NOW - timedelta(minutes=1),
            started_at=NOW - timedelta(minutes=30),
        ),
        make_run(
            schedule.id,
            scheduled_for=NOW - timedelta(minutes=5),
            next_attempt_at=NOW,
        ),
        make_run(
            schedule.id,
            scheduled_for=NOW + timedelta(seconds=1),
            next_attempt_at=NOW,
        ),
        make_run(
            schedule.id,
            scheduled_for=NOW - timedelta(minutes=4),
            next_attempt_at=NOW + timedelta(seconds=1),
        ),
        make_run(
            schedule.id,
            scheduled_for=NOW - timedelta(minutes=3),
            next_attempt_at=None,
            status="processing",
        ),
        make_run(
            schedule.id,
            scheduled_for=NOW - timedelta(minutes=2),
            next_attempt_at=NOW,
            attempt_count=4,
        ),
    ]
    db_session.add_all(runs)
    await db_session.flush()

    worker_id = uuid.uuid7()
    claimed = await ScheduleRunRepository(db_session).claim_due(
        now=NOW,
        worker_id=worker_id,
        batch_size=2,
        lease_timeout=LEASE,
    )

    assert [item.run.id for item in claimed] == [runs[0].id, runs[1].id]
    assert len(claimed) == 2
    for item in claimed:
        assert item.run.status == "processing"
        assert item.run.attempt_count == 1
        assert item.run.next_attempt_at is None
        assert item.run.claimed_by == worker_id
        assert item.run.claimed_at == NOW
        assert item.run.lease_expires_at == NOW + LEASE
        assert item.run.updated_at == NOW
        assert item.run.finished_at is None
        assert item.attempt.schedule_run_id == item.run.id
        assert item.attempt.attempt_number == item.run.attempt_count
        assert item.attempt.status == "claimed"
        assert item.attempt.claimed_by == worker_id
        assert item.attempt.claimed_at == NOW
        assert item.attempt.send_started_at is None
        assert item.attempt.finished_at is None
        assert item.attempt.discord_message_id is None
        assert item.attempt.error_kind is None
        assert item.attempt.error_code is None
        assert item.attempt.error_summary is None
    assert claimed[0].run.started_at == NOW
    assert claimed[1].run.started_at == NOW - timedelta(minutes=30)
    assert runs[2].status == "pending"
    assert runs[3].status == "pending"
    assert runs[4].status == "pending"
    assert runs[5].status == "processing"
    assert runs[6].status == "pending"


async def _seed_committed_runs(engine: AsyncEngine, *, guild_id: int) -> tuple[int, list[int]]:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory.begin() as session:
        schedule = make_schedule(guild_id=guild_id)
        session.add(schedule)
        await session.flush()
        runs = [
            make_run(
                schedule.id,
                scheduled_for=NOW - timedelta(minutes=10 - offset),
                next_attempt_at=NOW - timedelta(minutes=2 - offset),
            )
            for offset in range(2)
        ]
        session.add_all(runs)
        await session.flush()
        return schedule.id, [run.id for run in runs]


async def _remove_committed_seed(engine: AsyncEngine, *, schedule_id: int) -> None:
    async with engine.begin() as connection:
        run_ids = select(ScheduleRun.id).where(ScheduleRun.schedule_id == schedule_id)
        await connection.execute(
            delete(DeliveryAttempt).where(DeliveryAttempt.schedule_run_id.in_(run_ids))
        )
        await connection.execute(
            delete(OperationLog).where(OperationLog.schedule_id == schedule_id)
        )
        await connection.execute(delete(ScheduleRun).where(ScheduleRun.schedule_id == schedule_id))
        await connection.execute(delete(Schedule).where(Schedule.id == schedule_id))


async def test_concurrent_claims_skip_locks_do_not_overlap_and_rollback(
    test_engine: AsyncEngine,
) -> None:
    schedule_id, run_ids = await _seed_committed_runs(test_engine, guild_id=499)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    first_session = factory()
    second_session = factory()
    first_transaction = await first_session.begin()
    second_transaction = await second_session.begin()
    try:
        first = await ScheduleRunRepository(first_session).claim_due(
            now=NOW,
            worker_id=uuid.uuid7(),
            batch_size=1,
            lease_timeout=LEASE,
        )
        second = await asyncio.wait_for(
            ScheduleRunRepository(second_session).claim_due(
                now=NOW,
                worker_id=uuid.uuid7(),
                batch_size=2,
                lease_timeout=LEASE,
            ),
            timeout=1,
        )
        assert [item.run.id for item in first] == [run_ids[0]]
        assert [item.run.id for item in second] == [run_ids[1]]
        assert {item.run.id for item in first}.isdisjoint(item.run.id for item in second)
    finally:
        await first_transaction.rollback()
        await second_transaction.rollback()
        await first_session.close()
        await second_session.close()

    verifier = factory()
    try:
        rows = list(
            (
                await verifier.execute(
                    select(ScheduleRun).where(ScheduleRun.id.in_(run_ids)).order_by(ScheduleRun.id)
                )
            ).scalars()
        )
        attempt_count = await verifier.scalar(
            select(func.count())
            .select_from(DeliveryAttempt)
            .where(DeliveryAttempt.schedule_run_id.in_(run_ids))
        )
        assert all(run.status == "pending" and run.attempt_count == 0 for run in rows)
        assert attempt_count == 0
    finally:
        await verifier.close()
        await _remove_committed_seed(test_engine, schedule_id=schedule_id)


async def test_claim_repository_does_not_auto_commit(test_engine: AsyncEngine) -> None:
    schedule_id, run_ids = await _seed_committed_runs(test_engine, guild_id=498)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    claiming_session = factory()
    transaction = await claiming_session.begin()
    try:
        claimed = await ScheduleRunRepository(claiming_session).claim_due(
            now=NOW,
            worker_id=uuid.uuid7(),
            batch_size=1,
            lease_timeout=LEASE,
        )
        assert len(claimed) == 1

        async with factory() as observer:
            observed_run = await observer.get(ScheduleRun, run_ids[0])
            observed_attempts = await observer.scalar(
                select(func.count())
                .select_from(DeliveryAttempt)
                .where(DeliveryAttempt.schedule_run_id == run_ids[0])
            )
            assert observed_run is not None
            assert observed_run.status == "pending"
            assert observed_attempts == 0
    finally:
        await transaction.rollback()
        await claiming_session.close()
        await _remove_committed_seed(test_engine, schedule_id=schedule_id)
