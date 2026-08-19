from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from discord_ai_reminder_bot.application.schedule_pause import (
    SchedulePauseService,
    ScheduleStateChangeUnavailable,
)
from discord_ai_reminder_bot.domain.enums import ScheduleStatus
from discord_ai_reminder_bot.infrastructure.database.models import (
    DeliveryAttempt,
    OperationLog,
    Schedule,
    ScheduleRun,
)
from discord_ai_reminder_bot.infrastructure.database.repositories import ScheduleRunRepository

pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 8, 18, 23, 0, tzinfo=UTC)  # 2026-08-19 08:00 JST
RESUMED = NOW + timedelta(minutes=30)
GUILD_ID = 18_100
CREATOR_ID = 18_200


async def add_recurring(
    session: AsyncSession,
    *,
    schedule_type: str = "daily",
    status: str = "active",
    content: str | None = "body",
    end_date: date | None = None,
    guild_id: int = GUILD_ID,
    creator_user_id: int = CREATOR_ID,
    next_at: datetime | None = None,
) -> tuple[Schedule, ScheduleRun | None]:
    next_at = next_at or datetime(2026, 8, 19, 0, 0, tzinfo=UTC)  # 09:00 JST
    schedule = Schedule(
        public_id=uuid.uuid7(),
        guild_id=guild_id,
        channel_id=18_300,
        creator_user_id=creator_user_id,
        schedule_type=schedule_type,
        status=status,
        content=content,
        next_run_at=next_at if status in {"active", "draft"} else None,
        local_time=time(9),
        weekday=2 if schedule_type == "weekly" else None,
        end_date=end_date,
        version=1,
        updated_at=NOW,
    )
    session.add(schedule)
    await session.flush()
    run = None
    if status in {"active", "draft"}:
        run = ScheduleRun(
            schedule_id=schedule.id,
            scheduled_for=next_at,
            status="pending",
            attempt_count=0,
            next_attempt_at=next_at,
            updated_at=NOW,
        )
        session.add(run)
        await session.flush()
    return schedule, run


async def pause(session: AsyncSession, schedule: Schedule, *, actor: int = CREATOR_ID):
    return await SchedulePauseService(session).pause(
        guild_id=schedule.guild_id,
        public_id=str(schedule.public_id),
        actor_user_id=actor,
        administrator=False,
        paused_at=NOW,
    )


async def resume(session: AsyncSession, schedule: Schedule, *, administrator: bool = False):
    return await SchedulePauseService(session).resume(
        guild_id=schedule.guild_id,
        public_id=str(schedule.public_id),
        actor_user_id=CREATOR_ID,
        administrator=administrator,
        resumed_at=RESUMED,
    )


@pytest.mark.parametrize("schedule_type", ["daily", "weekly"])
async def test_pause_skips_all_pending_and_preserves_configuration(
    db_session: AsyncSession, schedule_type: str
) -> None:
    schedule, first = await add_recurring(db_session, schedule_type=schedule_type)
    retry = ScheduleRun(
        schedule_id=schedule.id,
        scheduled_for=first.scheduled_for + timedelta(days=1),
        status="pending",
        attempt_count=2,
        next_attempt_at=NOW + timedelta(minutes=5),
        result_code="retry_pending",
        error_summary="safe prior failure",
        updated_at=NOW,
    )
    terminal = ScheduleRun(
        schedule_id=schedule.id,
        scheduled_for=first.scheduled_for - timedelta(days=1),
        status="succeeded",
        attempt_count=1,
        next_attempt_at=None,
        discord_message_id=99,
        result_code="delivered",
        finished_at=NOW,
        updated_at=NOW,
    )
    db_session.add_all([retry, terminal])
    await db_session.flush()
    values = (schedule.content, schedule.local_time, schedule.weekday, schedule.end_date)
    result = await pause(db_session, schedule)
    assert result.pending_runs_skipped == 2
    assert schedule.status == "paused" and schedule.next_run_at is None
    assert schedule.version == 2 and schedule.updated_at == NOW
    assert schedule.terminal_at is schedule.deleted_at is None
    assert (schedule.content, schedule.local_time, schedule.weekday, schedule.end_date) == values
    for run in (first, retry):
        assert run.status == "skipped"
        assert run.next_attempt_at is None and run.finished_at == run.updated_at == NOW
        assert run.result_code == "schedule_paused"
        assert run.error_summary == "Schedule was paused before Discord delivery"
        assert run.claimed_by is run.claimed_at is run.lease_expires_at is None
        assert run.discord_message_id is None
    assert terminal.status == "succeeded" and terminal.discord_message_id == 99
    assert await db_session.scalar(select(func.count()).select_from(DeliveryAttempt)) == 0
    operation = (
        await db_session.scalars(
            select(OperationLog).where(OperationLog.schedule_id == schedule.id)
        )
    ).one()
    assert operation.action == "paused" and operation.actor_type == "user"
    assert operation.actor_user_id == CREATOR_ID
    assert operation.delete_kind is operation.delete_reason is None
    assert operation.changes == {
        "status": {"from": "active", "to": "paused"},
        "pending_runs_skipped": 2,
    }


@pytest.mark.parametrize(("schedule_type", "status"), [("once", "active"), ("daily", "draft")])
async def test_pause_rejects_once_and_draft_without_changes(
    db_session: AsyncSession, schedule_type: str, status: str
) -> None:
    if schedule_type == "once":
        scheduled = datetime(2026, 8, 19, 0, 0, tzinfo=UTC)
        schedule = Schedule(
            public_id=uuid.uuid7(),
            guild_id=GUILD_ID,
            channel_id=18_300,
            creator_user_id=CREATOR_ID,
            schedule_type="once",
            status="active",
            content="body",
            next_run_at=scheduled,
            version=1,
            updated_at=NOW,
        )
        db_session.add(schedule)
        await db_session.flush()
        run = ScheduleRun(
            schedule_id=schedule.id,
            scheduled_for=scheduled,
            status="pending",
            attempt_count=0,
            next_attempt_at=scheduled,
            updated_at=NOW,
        )
        db_session.add(run)
        await db_session.flush()
    else:
        schedule, run = await add_recurring(db_session, status="draft", content=None)
    with pytest.raises(ScheduleStateChangeUnavailable):
        await pause(db_session, schedule)
    assert schedule.status == status and run.status == "pending"
    assert await db_session.scalar(select(func.count()).select_from(OperationLog)) == 0


async def test_daily_pause_at_0800_resume_at_0830_creates_next_day_0900(
    db_session: AsyncSession,
) -> None:
    schedule, skipped = await add_recurring(db_session)
    await pause(db_session, schedule)
    result = await resume(db_session, schedule)
    expected = datetime(2026, 8, 20, 0, 0, tzinfo=UTC)
    assert skipped.status == "skipped"
    assert result.status is ScheduleStatus.ACTIVE and result.next_run_at == expected
    runs = list(
        (
            await db_session.scalars(
                select(ScheduleRun)
                .where(ScheduleRun.schedule_id == schedule.id)
                .order_by(ScheduleRun.scheduled_for)
            )
        ).all()
    )
    assert [(run.scheduled_for, run.status) for run in runs] == [
        (datetime(2026, 8, 19, 0, 0, tzinfo=UTC), "skipped"),
        (expected, "pending"),
    ]
    assert runs[-1].next_attempt_at == schedule.next_run_at == expected
    assert runs[-1].attempt_count == 0 and runs[-1].result_code is None


async def test_weekly_resume_advances_past_skipped_occurrence(db_session: AsyncSession) -> None:
    schedule, skipped = await add_recurring(db_session, schedule_type="weekly")
    await pause(db_session, schedule)
    result = await resume(db_session, schedule)
    assert result.next_run_at == skipped.scheduled_for + timedelta(days=7)


@pytest.mark.parametrize("attempt_status", ["claimed", "sending"])
async def test_processing_claimed_and_sending_reject_pause(
    db_session: AsyncSession, attempt_status: str
) -> None:
    schedule, pending = await add_recurring(db_session)
    pending.status = "processing"
    pending.attempt_count = 1
    pending.next_attempt_at = None
    pending.claimed_by = uuid.uuid4()
    pending.claimed_at = NOW
    pending.lease_expires_at = NOW + timedelta(minutes=5)
    pending.started_at = NOW
    attempt = DeliveryAttempt(
        schedule_run_id=pending.id,
        attempt_number=1,
        status=attempt_status,
        claimed_by=pending.claimed_by,
        claimed_at=NOW,
        send_started_at=NOW if attempt_status == "sending" else None,
    )
    db_session.add(attempt)
    await db_session.flush()
    with pytest.raises(ScheduleStateChangeUnavailable):
        await pause(db_session, schedule)
    assert schedule.status == "active" and pending.status == "processing"
    assert attempt.status == attempt_status


async def test_terminal_current_run_rejects_pause_as_finalization_wait(
    db_session: AsyncSession,
) -> None:
    schedule, run = await add_recurring(db_session)
    run.status = "succeeded"
    run.next_attempt_at = None
    run.attempt_count = 1
    run.discord_message_id = 99
    run.result_code = "delivered"
    run.finished_at = NOW
    await db_session.flush()
    with pytest.raises(ScheduleStateChangeUnavailable):
        await pause(db_session, schedule)
    assert schedule.status == "active" and run.status == "succeeded"


async def test_resume_contentless_with_future_occurrence_returns_draft(
    db_session: AsyncSession,
) -> None:
    schedule, _ = await add_recurring(db_session, status="paused", content=None)
    result = await resume(db_session, schedule)
    assert result.status is ScheduleStatus.DRAFT
    assert schedule.status == "draft" and schedule.next_run_at == result.next_run_at


@pytest.mark.parametrize("content", ["body", None])
async def test_resume_without_occurrence_ends_only_contentful(
    db_session: AsyncSession, content: str | None
) -> None:
    schedule, _ = await add_recurring(
        db_session, status="paused", content=content, end_date=date(2026, 8, 18)
    )
    if content is None:
        with pytest.raises(ScheduleStateChangeUnavailable):
            await resume(db_session, schedule)
        assert schedule.status == "paused" and schedule.version == 1
        return
    result = await resume(db_session, schedule)
    assert result.status is ScheduleStatus.ENDED
    assert schedule.next_run_at is None and schedule.terminal_at == RESUMED


async def test_other_owner_and_other_guild_are_rejected(db_session: AsyncSession) -> None:
    schedule, run = await add_recurring(db_session)
    with pytest.raises(ScheduleStateChangeUnavailable):
        await SchedulePauseService(db_session).pause(
            guild_id=GUILD_ID,
            public_id=str(schedule.public_id),
            actor_user_id=CREATOR_ID + 1,
            administrator=False,
            paused_at=NOW,
        )
    with pytest.raises(ScheduleStateChangeUnavailable):
        await SchedulePauseService(db_session).pause(
            guild_id=GUILD_ID + 1,
            public_id=str(schedule.public_id),
            actor_user_id=CREATOR_ID,
            administrator=True,
            paused_at=NOW,
        )
    assert schedule.status == "active" and run.status == "pending"


async def test_admin_can_pause_another_creators_schedule(db_session: AsyncSession) -> None:
    schedule, run = await add_recurring(db_session, creator_user_id=CREATOR_ID + 1)
    await SchedulePauseService(db_session).pause(
        guild_id=GUILD_ID,
        public_id=str(schedule.public_id),
        actor_user_id=CREATOR_ID,
        administrator=True,
        paused_at=NOW,
    )
    operation = (
        await db_session.scalars(
            select(OperationLog).where(OperationLog.schedule_id == schedule.id)
        )
    ).one()
    assert schedule.status == "paused" and run.status == "skipped"
    assert operation.actor_user_id == CREATOR_ID


async def test_double_pause_and_double_resume_add_no_extra_log(db_session: AsyncSession) -> None:
    schedule, _ = await add_recurring(db_session)
    await pause(db_session, schedule)
    with pytest.raises(ScheduleStateChangeUnavailable):
        await pause(db_session, schedule)
    await resume(db_session, schedule)
    with pytest.raises(ScheduleStateChangeUnavailable):
        await resume(db_session, schedule)
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(OperationLog)
            .where(OperationLog.schedule_id == schedule.id)
        )
        == 2
    )


async def test_transaction_rollback_restores_pause(test_engine: AsyncEngine) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as seed, seed.begin():
        schedule, run = await add_recurring(seed, guild_id=18_900)
        schedule_id, run_id = schedule.id, run.id
    with pytest.raises(RuntimeError, match="force rollback"):
        async with factory() as session, session.begin():
            persisted = await session.get(Schedule, schedule_id)
            await pause(session, persisted)
            raise RuntimeError("force rollback")
    async with factory() as verify:
        persisted = await verify.get(Schedule, schedule_id)
        persisted_run = await verify.get(ScheduleRun, run_id)
        assert persisted.status == "active" and persisted.version == 1
        assert persisted_run.status == "pending"
        assert (
            await verify.scalar(
                select(func.count())
                .select_from(OperationLog)
                .where(OperationLog.schedule_id == schedule_id)
            )
            == 0
        )
    await _clean(test_engine, schedule_id)


async def test_pause_run_lock_makes_concurrent_worker_claim_skip(test_engine: AsyncEngine) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as seed, seed.begin():
        schedule, _ = await add_recurring(seed, guild_id=18_902, next_at=NOW)
        schedule_id = schedule.id
    async with factory() as pausing, pausing.begin():
        persisted = await pausing.get(Schedule, schedule_id)
        await pause(pausing, persisted)
        async with factory() as claiming, claiming.begin():
            claimed = await ScheduleRunRepository(claiming).claim_due(
                now=NOW,
                worker_id=uuid.uuid4(),
                batch_size=10,
                lease_timeout=timedelta(minutes=5),
            )
            assert all(item.run.schedule_id != schedule_id for item in claimed)
    await _clean(test_engine, schedule_id)


async def test_processing_recovery_state_blocks_pause_without_changes(
    db_session: AsyncSession,
) -> None:
    schedule, run = await add_recurring(db_session)
    run.status = "processing"
    run.attempt_count = 1
    run.next_attempt_at = None
    run.claimed_by = uuid.uuid4()
    run.claimed_at = NOW - timedelta(minutes=10)
    run.lease_expires_at = NOW - timedelta(minutes=5)
    run.started_at = run.claimed_at
    attempt = DeliveryAttempt(
        schedule_run_id=run.id,
        attempt_number=1,
        status="claimed",
        claimed_by=run.claimed_by,
        claimed_at=run.claimed_at,
    )
    db_session.add(attempt)
    await db_session.flush()
    with pytest.raises(ScheduleStateChangeUnavailable):
        await pause(db_session, schedule)
    assert schedule.status == "active" and run.status == "processing"


async def test_concurrent_resume_creates_one_run(test_engine: AsyncEngine) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as seed, seed.begin():
        schedule, _ = await add_recurring(seed, status="paused", guild_id=18_901)
        schedule_id, public_id = schedule.id, schedule.public_id

    async def perform() -> str:
        try:
            async with factory() as session, session.begin():
                await SchedulePauseService(session).resume(
                    guild_id=18_901,
                    public_id=str(public_id),
                    actor_user_id=CREATOR_ID,
                    administrator=False,
                    resumed_at=RESUMED,
                )
            return "ok"
        except ScheduleStateChangeUnavailable:
            return "rejected"

    assert sorted(await asyncio.gather(perform(), perform())) == ["ok", "rejected"]
    async with factory() as verify:
        schedule = await verify.get(Schedule, schedule_id)
        count = await verify.scalar(
            select(func.count())
            .select_from(ScheduleRun)
            .where(ScheduleRun.schedule_id == schedule_id)
        )
        assert schedule.status == "active" and count == 1
    await _clean(test_engine, schedule_id)


async def _clean(engine: AsyncEngine, schedule_id: int) -> None:
    from sqlalchemy import delete

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
