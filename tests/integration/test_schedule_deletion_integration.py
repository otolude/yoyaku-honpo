from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, time, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from discord_ai_reminder_bot.application.delivery import DeliveryService
from discord_ai_reminder_bot.application.recovery import ProcessingRecoveryService
from discord_ai_reminder_bot.application.schedule_deletion import (
    DeleteReasonRequired,
    ScheduleDeletionService,
    ScheduleDeletionUnavailable,
)
from discord_ai_reminder_bot.application.schedule_execution import ScheduleExecutionService
from discord_ai_reminder_bot.application.worker import PollingWorker
from discord_ai_reminder_bot.domain.clock import FixedClock
from discord_ai_reminder_bot.domain.enums import ScheduleStatus
from discord_ai_reminder_bot.infrastructure.database.models import (
    DeliveryAttempt,
    OperationLog,
    Schedule,
    ScheduleRun,
)
from discord_ai_reminder_bot.infrastructure.database.repositories import ScheduleRunRepository

pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 8, 18, 3, 0, tzinfo=UTC)
GUILD_ID = 8_100
CREATOR_ID = 8_200
ADMIN_ID = 8_201


async def add_schedule(
    session: AsyncSession,
    *,
    status: str = "active",
    guild_id: int = GUILD_ID,
    creator_user_id: int = CREATOR_ID,
    current_run_status: str | None = "pending",
    run_count: int = 1,
) -> tuple[Schedule, list[ScheduleRun]]:
    recurring = status in {"paused", "ended"}
    has_next = status in {"active", "draft"}
    scheduled_for = NOW + timedelta(hours=1)
    terminal = status in {"completed", "ended", "deleted"}
    schedule = Schedule(
        public_id=uuid.uuid7(),
        guild_id=guild_id,
        channel_id=8_300,
        creator_user_id=creator_user_id,
        schedule_type="daily" if recurring else "once",
        status=status,
        content=None if status == "draft" else "body",
        next_run_at=scheduled_for if has_next else None,
        local_time=time(13) if recurring else None,
        weekday=None,
        version=1,
        terminal_at=NOW if terminal else None,
        deleted_at=NOW if status == "deleted" else None,
        updated_at=NOW,
    )
    session.add(schedule)
    await session.flush()
    runs: list[ScheduleRun] = []
    if current_run_status is not None:
        for index in range(run_count):
            run_time = scheduled_for + timedelta(minutes=index)
            run = ScheduleRun(
                schedule_id=schedule.id,
                scheduled_for=run_time,
                status=current_run_status,
                attempt_count=0,
                next_attempt_at=run_time if current_run_status == "pending" else None,
                discord_message_id=(9_000 + index if current_run_status == "succeeded" else None),
                result_code=(
                    current_run_status
                    if current_run_status in {"succeeded", "failed", "skipped"}
                    else None
                ),
                error_summary=None,
                started_at=None,
                finished_at=NOW
                if current_run_status in {"succeeded", "failed", "skipped"}
                else None,
                updated_at=NOW,
            )
            session.add(run)
            runs.append(run)
        await session.flush()
    return schedule, runs


async def delete(
    session: AsyncSession,
    schedule: Schedule,
    *,
    actor_user_id: int = CREATOR_ID,
    administrator: bool = False,
    reason: str | None = "  planned deletion  ",
):
    return await ScheduleDeletionService(session).delete(
        guild_id=schedule.guild_id,
        public_id=str(schedule.public_id),
        actor_user_id=actor_user_id,
        administrator=administrator,
        reason=reason,
        deleted_at=NOW,
    )


@pytest.mark.parametrize("status", ["draft", "active", "paused", "failed"])
async def test_real_postgres_deletes_allowed_states_and_records_creator_log(
    db_session: AsyncSession, status: str
) -> None:
    current_run = "pending" if status in {"draft", "active"} else None
    schedule, runs = await add_schedule(
        db_session, status=status, current_run_status=current_run, run_count=2 if current_run else 0
    )
    result = await delete(db_session, schedule)
    await db_session.flush()
    await db_session.refresh(schedule)
    logs = list(
        (
            await db_session.scalars(
                select(OperationLog).where(OperationLog.schedule_id == schedule.id)
            )
        ).all()
    )
    assert schedule.status == "deleted"
    assert schedule.next_run_at is None
    assert schedule.deleted_at == schedule.terminal_at == schedule.updated_at == NOW
    assert schedule.version == 2
    assert schedule.content == (None if status == "draft" else "body")
    assert result.previous_status is ScheduleStatus(status)
    assert result.pending_runs_skipped == len(runs)
    assert all(
        run.status == "skipped"
        and run.next_attempt_at is None
        and run.finished_at == NOW
        and run.result_code == "schedule_deleted"
        and run.claimed_by is None
        and run.discord_message_id is None
        for run in runs
    )
    assert len(logs) == 1
    assert logs[0].action == "deleted"
    assert logs[0].actor_type == "user"
    assert logs[0].actor_user_id == CREATOR_ID
    assert logs[0].delete_kind == "creator_deleted"
    assert logs[0].delete_reason == "planned deletion"
    assert logs[0].created_at == NOW
    assert logs[0].changes == {
        "status": {"from": status, "to": "deleted"},
        "pending_runs_skipped": len(runs),
    }
    attempt_count = await db_session.scalar(
        select(func.count())
        .select_from(DeliveryAttempt)
        .join(ScheduleRun, ScheduleRun.id == DeliveryAttempt.schedule_run_id)
        .where(ScheduleRun.schedule_id == schedule.id)
    )
    assert attempt_count == 0


@pytest.mark.parametrize("status", ["completed", "ended", "deleted"])
async def test_real_postgres_rejects_terminal_and_redelete_without_new_log(
    db_session: AsyncSession, status: str
) -> None:
    schedule, _ = await add_schedule(db_session, status=status, current_run_status=None)
    with pytest.raises(ScheduleDeletionUnavailable):
        await delete(db_session, schedule)
    count = await db_session.scalar(
        select(func.count())
        .select_from(OperationLog)
        .where(OperationLog.schedule_id == schedule.id)
    )
    assert count == 0
    assert schedule.status == status


async def test_real_postgres_second_delete_does_not_add_operation_log(
    db_session: AsyncSession,
) -> None:
    schedule, _ = await add_schedule(db_session)
    await delete(db_session, schedule)
    with pytest.raises(ScheduleDeletionUnavailable):
        await delete(db_session, schedule)
    count = await db_session.scalar(
        select(func.count())
        .select_from(OperationLog)
        .where(OperationLog.schedule_id == schedule.id)
    )
    assert count == 1


async def test_real_postgres_non_admin_cannot_delete_other_owner(
    db_session: AsyncSession,
) -> None:
    schedule, runs = await add_schedule(db_session)
    with pytest.raises(ScheduleDeletionUnavailable):
        await delete(db_session, schedule, actor_user_id=ADMIN_ID, administrator=False)
    assert schedule.status == "active"
    assert runs[0].status == "pending"
    assert await db_session.scalar(select(func.count()).select_from(OperationLog)) == 0


@pytest.mark.parametrize(
    ("status", "expected_kind"),
    [("active", "admin_deleted"), ("failed", "operator_resolved_failed")],
)
async def test_real_postgres_admin_deletes_other_owner_with_expected_kind(
    db_session: AsyncSession, status: str, expected_kind: str
) -> None:
    schedule, _ = await add_schedule(
        db_session,
        status=status,
        current_run_status="pending" if status == "active" else None,
    )
    await delete(db_session, schedule, actor_user_id=ADMIN_ID, administrator=True)
    operation = (
        await db_session.scalars(
            select(OperationLog).where(OperationLog.schedule_id == schedule.id)
        )
    ).one()
    assert operation.delete_kind == expected_kind


async def test_real_postgres_admin_own_failed_is_creator_deleted(
    db_session: AsyncSession,
) -> None:
    schedule, runs = await add_schedule(
        db_session,
        status="failed",
        creator_user_id=ADMIN_ID,
        current_run_status="failed",
    )
    await delete(db_session, schedule, actor_user_id=ADMIN_ID, administrator=True)
    operation = (
        await db_session.scalars(
            select(OperationLog).where(OperationLog.schedule_id == schedule.id)
        )
    ).one()
    assert operation.delete_kind == "creator_deleted"
    assert runs[0].status == "failed"


async def test_real_postgres_creator_without_reason_stores_fixed_value(
    db_session: AsyncSession,
) -> None:
    schedule, _ = await add_schedule(db_session)
    await delete(db_session, schedule, reason=None)
    operation = (
        await db_session.scalars(
            select(OperationLog).where(OperationLog.schedule_id == schedule.id)
        )
    ).one()
    assert operation.delete_reason == "理由未入力"
    assert operation.delete_kind == "creator_deleted"


async def test_real_postgres_admin_own_schedule_without_reason_is_creator_deleted(
    db_session: AsyncSession,
) -> None:
    schedule, _ = await add_schedule(
        db_session,
        status="failed",
        creator_user_id=ADMIN_ID,
        current_run_status=None,
    )
    await delete(
        db_session,
        schedule,
        actor_user_id=ADMIN_ID,
        administrator=True,
        reason=None,
    )
    operation = (
        await db_session.scalars(
            select(OperationLog).where(OperationLog.schedule_id == schedule.id)
        )
    ).one()
    assert operation.delete_reason == "理由未入力"
    assert operation.delete_kind == "creator_deleted"


async def test_real_postgres_admin_other_without_reason_changes_nothing(
    db_session: AsyncSession,
) -> None:
    schedule, runs = await add_schedule(db_session)
    with pytest.raises(DeleteReasonRequired):
        await delete(
            db_session,
            schedule,
            actor_user_id=ADMIN_ID,
            administrator=True,
            reason=None,
        )
    assert schedule.status == "active"
    assert runs[0].status == "pending"
    assert await db_session.scalar(select(func.count()).select_from(OperationLog)) == 0


async def test_real_postgres_preview_and_other_guild_do_not_update(
    db_session: AsyncSession,
) -> None:
    schedule, runs = await add_schedule(db_session)
    before = (schedule.status, schedule.version, runs[0].status)
    preview = await ScheduleDeletionService(db_session).preview(
        guild_id=GUILD_ID,
        public_id=str(schedule.public_id),
        actor_user_id=CREATOR_ID,
        administrator=False,
        reason=" reason ",
    )
    assert preview.reason == "reason"
    assert (schedule.status, schedule.version, runs[0].status) == before
    assert await db_session.scalar(select(func.count()).select_from(OperationLog)) == 0
    with pytest.raises(ScheduleDeletionUnavailable):
        await ScheduleDeletionService(db_session).delete(
            guild_id=GUILD_ID + 1,
            public_id=str(schedule.public_id),
            actor_user_id=CREATOR_ID,
            administrator=True,
            reason="reason",
            deleted_at=NOW,
        )
    assert (schedule.status, schedule.version, runs[0].status) == before


@pytest.mark.parametrize("attempt_status", ["claimed", "sending"])
async def test_real_postgres_processing_claimed_or_sending_is_unchanged(
    db_session: AsyncSession, attempt_status: str
) -> None:
    schedule, runs = await add_schedule(db_session, current_run_status=None)
    run = ScheduleRun(
        schedule_id=schedule.id,
        scheduled_for=schedule.next_run_at,
        status="processing",
        attempt_count=1,
        next_attempt_at=None,
        claimed_by=uuid.uuid4(),
        claimed_at=NOW,
        lease_expires_at=NOW + timedelta(minutes=5),
        started_at=NOW,
        updated_at=NOW,
    )
    db_session.add(run)
    await db_session.flush()
    attempt = DeliveryAttempt(
        schedule_run_id=run.id,
        attempt_number=1,
        status=attempt_status,
        claimed_by=run.claimed_by,
        claimed_at=NOW,
        send_started_at=NOW if attempt_status == "sending" else None,
    )
    db_session.add(attempt)
    await db_session.flush()
    with pytest.raises(ScheduleDeletionUnavailable):
        await delete(db_session, schedule)
    assert schedule.status == "active"
    assert run.status == "processing"
    assert attempt.status == attempt_status
    assert not runs


@pytest.mark.parametrize("run_status", ["succeeded", "failed", "skipped"])
async def test_real_postgres_rejects_finalize_wait_and_preserves_terminal_run(
    db_session: AsyncSession, run_status: str
) -> None:
    schedule, runs = await add_schedule(db_session, current_run_status=run_status)
    before = (
        runs[0].status,
        runs[0].finished_at,
        runs[0].discord_message_id,
        runs[0].result_code,
    )
    with pytest.raises(ScheduleDeletionUnavailable):
        await delete(db_session, schedule)
    assert schedule.status == "active"
    assert (
        runs[0].status,
        runs[0].finished_at,
        runs[0].discord_message_id,
        runs[0].result_code,
    ) == before


async def test_real_postgres_transaction_rollback_restores_all_rows(
    test_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as seed, seed.begin():
        schedule, runs = await add_schedule(seed, guild_id=8_110)
        public_id, schedule_id, run_id = schedule.public_id, schedule.id, runs[0].id
    with pytest.raises(RuntimeError, match="force rollback"):
        async with factory() as session, session.begin():
            persisted = (
                await session.scalars(select(Schedule).where(Schedule.id == schedule_id))
            ).one()
            await delete(session, persisted)
            raise RuntimeError("force rollback")
    async with factory() as verifier:
        persisted = await verifier.get(Schedule, schedule_id)
        run = await verifier.get(ScheduleRun, run_id)
        log_count = await verifier.scalar(
            select(func.count())
            .select_from(OperationLog)
            .where(OperationLog.schedule_id == schedule_id)
        )
        assert persisted is not None and persisted.public_id == public_id
        assert persisted.status == "active" and persisted.version == 1
        assert run is not None and run.status == "pending"
        assert log_count == 0
    await _clean_committed(test_engine, schedule_id)


async def test_delete_lock_makes_concurrent_claim_skip_the_run(test_engine: AsyncEngine) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as seed, seed.begin():
        schedule, runs = await add_schedule(seed, guild_id=8_120)
        schedule.next_run_at = NOW
        runs[0].scheduled_for = NOW
        runs[0].next_attempt_at = NOW
        schedule_id = schedule.id
    async with factory() as deleting, deleting.begin():
        persisted = await deleting.get(Schedule, schedule_id)
        assert persisted is not None
        await delete(deleting, persisted)
        async with factory() as claiming, claiming.begin():
            claimed = await ScheduleRunRepository(claiming).claim_due(
                now=NOW,
                worker_id=uuid.uuid4(),
                batch_size=10,
                lease_timeout=timedelta(minutes=5),
            )
            assert all(item.run.schedule_id != schedule_id for item in claimed)
    await _clean_committed(test_engine, schedule_id)


async def test_claimed_worker_run_blocks_delete_without_changes(test_engine: AsyncEngine) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    worker_id = uuid.uuid4()
    async with factory() as seed, seed.begin():
        schedule, runs = await add_schedule(seed, guild_id=8_130)
        schedule.next_run_at = NOW
        runs[0].scheduled_for = NOW
        runs[0].next_attempt_at = NOW
        schedule_id = schedule.id
    async with factory() as claiming, claiming.begin():
        claimed = await ScheduleRunRepository(claiming).claim_due(
            now=NOW,
            worker_id=worker_id,
            batch_size=10,
            lease_timeout=timedelta(minutes=5),
        )
        target = next(item for item in claimed if item.run.schedule_id == schedule_id)
    async with factory() as deleting, deleting.begin():
        persisted = await deleting.get(Schedule, schedule_id)
        assert persisted is not None
        with pytest.raises(ScheduleDeletionUnavailable):
            await delete(deleting, persisted)
    async with factory() as sending, sending.begin():
        await DeliveryService(sending).start_sending(
            attempt_id=target.attempt.id,
            worker_id=worker_id,
            now=NOW,
        )
    async with factory() as deleting, deleting.begin():
        persisted = await deleting.get(Schedule, schedule_id)
        assert persisted is not None
        with pytest.raises(ScheduleDeletionUnavailable):
            await delete(deleting, persisted)
    await _clean_committed(test_engine, schedule_id)


async def test_polling_worker_gateway_phase_rejects_delete_without_deadlock(
    test_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as seed, seed.begin():
        schedule, runs = await add_schedule(seed, guild_id=8_135)
        schedule.next_run_at = NOW
        runs[0].scheduled_for = NOW
        runs[0].next_attempt_at = NOW
        schedule_id, public_id = schedule.id, schedule.public_id

    class BlockingGateway:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def send(self, message) -> int:
            self.started.set()
            await self.release.wait()
            return 9_999

    gateway = BlockingGateway()
    worker = PollingWorker(
        session_factory=factory,
        gateway=gateway,
        clock=FixedClock(NOW),
        worker_id=uuid.uuid4(),
        batch_size=10,
        max_concurrency=1,
        lease_timeout=timedelta(minutes=5),
        logger=logging.getLogger("test.schedule_deletion.worker"),
    )
    worker_task = asyncio.create_task(worker.poll_once())
    await gateway.started.wait()
    async with factory() as deleting, deleting.begin():
        with pytest.raises(ScheduleDeletionUnavailable):
            await ScheduleDeletionService(deleting).delete(
                guild_id=8_135,
                public_id=str(public_id),
                actor_user_id=CREATOR_ID,
                administrator=False,
                reason="reason",
                deleted_at=NOW,
            )
    gateway.release.set()
    result = await asyncio.wait_for(worker_task, timeout=2)
    assert result.succeeded == 1
    async with factory() as verifier:
        persisted = await verifier.get(Schedule, schedule_id)
        assert persisted is not None and persisted.status == "completed"
    await _clean_committed(test_engine, schedule_id)


async def test_finalize_then_delete_observes_version_conflict_without_deadlock(
    test_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as seed, seed.begin():
        schedule, runs = await add_schedule(seed, guild_id=8_140, current_run_status="succeeded")
        schedule_id, run_id, public_id = schedule.id, runs[0].id, schedule.public_id

    async def deleting() -> None:
        async with factory() as session, session.begin():
            with pytest.raises(ScheduleDeletionUnavailable):
                await ScheduleDeletionService(session).delete(
                    guild_id=8_140,
                    public_id=str(public_id),
                    actor_user_id=CREATOR_ID,
                    administrator=False,
                    reason="reason",
                    deleted_at=NOW + timedelta(seconds=1),
                )

    async with factory() as finalizing, finalizing.begin():
        locked_run = (
            await finalizing.scalars(
                select(ScheduleRun).where(ScheduleRun.id == run_id).with_for_update()
            )
        ).one()
        task = asyncio.create_task(deleting())
        await asyncio.sleep(0)
        assert locked_run.status == "succeeded"
        await ScheduleExecutionService(finalizing).finalize_run(run_id=run_id, finalized_at=NOW)
    await asyncio.wait_for(task, timeout=2)
    await _clean_committed(test_engine, schedule_id)


async def test_recovery_then_delete_completes_without_deadlock(test_engine: AsyncEngine) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    worker_id = uuid.uuid4()
    async with factory() as seed, seed.begin():
        schedule, _ = await add_schedule(seed, guild_id=8_150, current_run_status=None)
        run = ScheduleRun(
            schedule_id=schedule.id,
            scheduled_for=schedule.next_run_at,
            status="processing",
            attempt_count=1,
            next_attempt_at=None,
            claimed_by=worker_id,
            claimed_at=NOW - timedelta(minutes=10),
            lease_expires_at=NOW - timedelta(minutes=5),
            started_at=NOW - timedelta(minutes=10),
            updated_at=NOW - timedelta(minutes=10),
        )
        seed.add(run)
        await seed.flush()
        seed.add(
            DeliveryAttempt(
                schedule_run_id=run.id,
                attempt_number=1,
                status="claimed",
                claimed_by=worker_id,
                claimed_at=run.claimed_at,
            )
        )
        schedule_id, public_id = schedule.id, schedule.public_id

    async with factory() as recovering, recovering.begin():
        recovered = await ProcessingRecoveryService(recovering).recover_expired(
            recovered_at=NOW, batch_size=10
        )
        assert any(item.run.schedule_id == schedule_id for item in recovered)

        async def deleting() -> None:
            async with factory() as session, session.begin():
                await ScheduleDeletionService(session).delete(
                    guild_id=8_150,
                    public_id=str(public_id),
                    actor_user_id=CREATOR_ID,
                    administrator=False,
                    reason="reason",
                    deleted_at=NOW,
                )

        task = asyncio.create_task(deleting())
        await asyncio.sleep(0)
    await asyncio.wait_for(task, timeout=2)
    async with factory() as verifier:
        persisted = await verifier.get(Schedule, schedule_id)
        assert persisted is not None and persisted.status == "deleted"
    await _clean_committed(test_engine, schedule_id)


async def _clean_committed(engine: AsyncEngine, schedule_id: int) -> None:
    from sqlalchemy import delete as sql_delete

    async with engine.begin() as connection:
        run_ids = select(ScheduleRun.id).where(ScheduleRun.schedule_id == schedule_id)
        await connection.execute(
            sql_delete(DeliveryAttempt).where(DeliveryAttempt.schedule_run_id.in_(run_ids))
        )
        await connection.execute(
            sql_delete(OperationLog).where(OperationLog.schedule_id == schedule_id)
        )
        await connection.execute(
            sql_delete(ScheduleRun).where(ScheduleRun.schedule_id == schedule_id)
        )
        await connection.execute(sql_delete(Schedule).where(Schedule.id == schedule_id))
