import asyncio
import uuid
from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from discord_ai_reminder_bot.application.delivery import RESULT_UNKNOWN
from discord_ai_reminder_bot.application.recovery import (
    BEFORE_SEND_CODE,
    UNKNOWN_CODE,
    ProcessingRecoveryService,
    RecoveryResult,
)
from discord_ai_reminder_bot.infrastructure.database.exceptions import (
    RepositoryStateConflictError,
)
from discord_ai_reminder_bot.infrastructure.database.models import (
    DeliveryAttempt,
    NotificationLog,
    OperationLog,
    Schedule,
    ScheduleRun,
)

pytestmark = pytest.mark.asyncio
CLAIMED_AT = datetime(2026, 8, 17, 3, 0, tzinfo=UTC)
RECOVERED_AT = CLAIMED_AT + timedelta(minutes=5)


def recovery(session: AsyncSession) -> ProcessingRecoveryService:
    return ProcessingRecoveryService(session, configured_guild_id=700, operator_channel_id=704)


async def add_expired(
    session: AsyncSession,
    *,
    attempt_number: int = 1,
    attempt_status: str | None = "claimed",
    lease_expires_at: datetime = RECOVERED_AT - timedelta(seconds=1),
    attempt_number_override: int | None = None,
    attempt_worker_override: uuid.UUID | None = None,
    schedule_type: str = "once",
    schedule_status: str = "active",
    end_date: date | None = None,
    guild_id: int = 700,
) -> tuple[ScheduleRun, DeliveryAttempt | None, uuid.UUID]:
    scheduled_for = CLAIMED_AT - timedelta(minutes=1)
    recurring = schedule_type in {"daily", "weekly"}
    schedule = Schedule(
        public_id=uuid.uuid7(),
        guild_id=guild_id,
        channel_id=701,
        creator_user_id=702,
        schedule_type=schedule_type,
        status=schedule_status,
        content=None if schedule_status == "draft" else "recovery",
        next_run_at=(scheduled_for if schedule_status in {"active", "draft"} else None),
        local_time=time(12, 0) if recurring else None,
        weekday=0 if schedule_type == "weekly" else None,
        end_date=end_date,
        version=1,
        deleted_at=RECOVERED_AT if schedule_status == "deleted" else None,
        terminal_at=(RECOVERED_AT if schedule_status in {"deleted", "ended"} else None),
    )
    session.add(schedule)
    await session.flush()
    worker_id = uuid.uuid7()
    run = ScheduleRun(
        schedule_id=schedule.id,
        scheduled_for=scheduled_for,
        status="processing",
        attempt_count=attempt_number,
        next_attempt_at=None,
        claimed_by=worker_id,
        claimed_at=CLAIMED_AT,
        lease_expires_at=lease_expires_at,
        started_at=CLAIMED_AT,
    )
    session.add(run)
    await session.flush()
    attempt = None
    if attempt_status is not None:
        sending = attempt_status in {"sending", "unknown", "succeeded"}
        finished = attempt_status in {"unknown", "succeeded", "failed"}
        attempt = DeliveryAttempt(
            schedule_run_id=run.id,
            attempt_number=attempt_number_override or attempt_number,
            status=attempt_status,
            claimed_by=attempt_worker_override or worker_id,
            claimed_at=CLAIMED_AT,
            send_started_at=CLAIMED_AT + timedelta(seconds=1) if sending else None,
            finished_at=CLAIMED_AT + timedelta(seconds=2) if finished else None,
            discord_message_id=800 if attempt_status == "succeeded" else None,
            error_kind="permanent" if attempt_status == "failed" else None,
        )
        session.add(attempt)
        await session.flush()
    return run, attempt, worker_id


@pytest.mark.parametrize(
    ("attempt_number", "delay", "run_status"),
    [
        (1, timedelta(minutes=1), "pending"),
        (2, timedelta(minutes=5), "pending"),
        (3, timedelta(minutes=15), "pending"),
        (4, None, "failed"),
    ],
)
async def test_claimed_expiry_uses_retry_policy(
    db_session: AsyncSession,
    attempt_number: int,
    delay: timedelta | None,
    run_status: str,
) -> None:
    run, attempt, _ = await add_expired(db_session, attempt_number=attempt_number)
    recovered = await recovery(db_session).recover_expired(recovered_at=RECOVERED_AT, batch_size=20)
    assert len(recovered) == 1
    assert attempt is not None
    assert attempt.status == "failed"
    assert attempt.attempt_number == attempt_number
    assert attempt.finished_at == RECOVERED_AT
    assert attempt.error_kind == "transient"
    assert attempt.error_code == BEFORE_SEND_CODE
    assert attempt.send_started_at is None
    assert run.attempt_count == attempt_number
    assert run.status == run_status
    assert run.next_attempt_at == (RECOVERED_AT + delay if delay else None)
    assert run.finished_at == (None if delay else RECOVERED_AT)
    assert run.claimed_by is None
    assert run.claimed_at is None
    assert run.lease_expires_at is None
    schedule = await db_session.get(Schedule, run.schedule_id)
    assert schedule is not None
    if attempt_number < 4:
        assert recovered[0].finalization is None
        assert schedule.status == "active"
        assert schedule.version == 1
        assert await db_session.scalar(select(func.count(NotificationLog.id))) == 0
    else:
        assert recovered[0].finalization is not None
        assert schedule.status == "failed"
        assert schedule.next_run_at is None
        assert schedule.version == 2
        notification = (await db_session.execute(select(NotificationLog))).scalar_one()
        assert notification.notification_type == "run_failed"
        assert notification.schedule_run_id == run.id
        assert notification.error_code is None


@pytest.mark.parametrize("attempt_status", ["sending", "unknown"])
async def test_sending_or_unknown_expiry_fails_without_retry(
    db_session: AsyncSession, attempt_status: str
) -> None:
    run, attempt, _ = await add_expired(db_session, attempt_status=attempt_status)
    recovered = await recovery(db_session).recover_expired(recovered_at=RECOVERED_AT, batch_size=20)
    assert recovered[0].result is RecoveryResult.FAILED_UNKNOWN
    assert attempt is not None
    assert attempt.status == "unknown"
    assert attempt.finished_at == RECOVERED_AT
    assert attempt.error_kind == "unknown"
    assert attempt.error_code == UNKNOWN_CODE
    assert run.status == "failed"
    assert run.result_code == RESULT_UNKNOWN
    assert run.next_attempt_at is None
    assert run.finished_at == RECOVERED_AT
    schedule = await db_session.get(Schedule, run.schedule_id)
    assert schedule is not None and schedule.status == "failed"
    assert recovered[0].finalization is not None
    notification = (await db_session.execute(select(NotificationLog))).scalar_one()
    assert notification.notification_type == "run_failed"
    assert notification.schedule_run_id == run.id


@pytest.mark.parametrize(
    "inconsistency", ["missing", "number", "worker", "time", "succeeded", "failed"]
)
async def test_inconsistent_state_never_returns_to_pending(
    db_session: AsyncSession, inconsistency: str
) -> None:
    kwargs = {}
    if inconsistency == "missing":
        kwargs["attempt_status"] = None
    elif inconsistency == "number":
        kwargs["attempt_number_override"] = 2
    elif inconsistency == "worker":
        kwargs["attempt_worker_override"] = uuid.uuid7()
    elif inconsistency in {"succeeded", "failed"}:
        kwargs["attempt_status"] = inconsistency
    run, attempt, _ = await add_expired(db_session, **kwargs)
    if inconsistency == "time":
        assert attempt is not None
        run.lease_expires_at = CLAIMED_AT - timedelta(seconds=1)
        await db_session.flush()
    recovered = await recovery(db_session).recover_expired(recovered_at=RECOVERED_AT, batch_size=20)
    assert recovered[0].result is RecoveryResult.FAILED_UNKNOWN
    assert run.status == "failed"
    assert run.next_attempt_at is None
    assert run.result_code == RESULT_UNKNOWN
    if attempt is not None:
        assert attempt.status == (kwargs.get("attempt_status") or "claimed")
    schedule = await db_session.get(Schedule, run.schedule_id)
    assert schedule is not None and schedule.status == "failed"
    assert recovered[0].finalization is not None
    notification = (await db_session.execute(select(NotificationLog))).scalar_one()
    assert notification.notification_type == "recovery"
    assert notification.schedule_run_id == run.id


async def test_recurring_terminal_recovery_creates_one_strictly_future_run(
    db_session: AsyncSession,
) -> None:
    run, _, _ = await add_expired(db_session, attempt_number=4, schedule_type="daily")
    recovered = await recovery(db_session).recover_expired(recovered_at=RECOVERED_AT, batch_size=20)
    schedule = await db_session.get(Schedule, run.schedule_id)
    runs = list(
        (
            await db_session.execute(
                select(ScheduleRun)
                .where(ScheduleRun.schedule_id == run.schedule_id)
                .order_by(ScheduleRun.scheduled_for)
            )
        ).scalars()
    )
    assert recovered[0].finalization is not None
    assert schedule is not None and schedule.status == "active"
    assert len(runs) == 2
    assert runs[1].scheduled_for > RECOVERED_AT
    assert schedule.next_run_at == runs[1].scheduled_for
    assert runs[1].status == "pending" and runs[1].attempt_count == 0


async def test_recurring_terminal_recovery_ends_and_logs(
    db_session: AsyncSession,
) -> None:
    run, _, _ = await add_expired(
        db_session,
        attempt_number=4,
        schedule_type="daily",
        end_date=date(2026, 8, 17),
    )
    await recovery(db_session).recover_expired(recovered_at=RECOVERED_AT, batch_size=20)
    schedule = await db_session.get(Schedule, run.schedule_id)
    operations = list(
        (
            await db_session.execute(
                select(OperationLog).where(OperationLog.schedule_id == run.schedule_id)
            )
        ).scalars()
    )
    assert schedule is not None and schedule.status == "ended"
    assert schedule.terminal_at == RECOVERED_AT
    assert [operation.action for operation in operations] == ["ended"]


@pytest.mark.parametrize("schedule_status", ["paused", "deleted", "ended"])
async def test_recurring_inactive_schedule_is_not_restored(
    db_session: AsyncSession, schedule_status: str
) -> None:
    run, _, _ = await add_expired(
        db_session,
        attempt_number=4,
        schedule_type="daily",
        schedule_status=schedule_status,
    )
    await recovery(db_session).recover_expired(recovered_at=RECOVERED_AT, batch_size=20)
    schedule = await db_session.get(Schedule, run.schedule_id)
    count = await db_session.scalar(
        select(func.count())
        .select_from(ScheduleRun)
        .where(ScheduleRun.schedule_id == run.schedule_id)
    )
    assert schedule is not None and schedule.status == schedule_status
    assert schedule.version == 1
    assert count == 1


async def test_failed_recurring_draft_is_rejected_without_notification_planning(
    db_session: AsyncSession,
) -> None:
    guild_id = 70_070
    _run, _, _ = await add_expired(
        db_session,
        attempt_status="sending",
        schedule_type="daily",
        schedule_status="draft",
        guild_id=guild_id,
    )
    # Recovery failures cannot advance a draft: this verifies the existing rule is preserved.
    with pytest.raises(RepositoryStateConflictError):
        await ProcessingRecoveryService(db_session, configured_guild_id=guild_id).recover_expired(
            recovered_at=RECOVERED_AT, batch_size=20
        )
    notifications = await db_session.scalar(select(func.count()).select_from(NotificationLog))
    assert notifications == 0


async def test_finalization_failure_rolls_back_run_attempt_and_schedule(
    test_engine: AsyncEngine,
) -> None:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory.begin() as seed:
        run, attempt, _ = await add_expired(
            seed,
            attempt_status="sending",
            schedule_type="daily",
            schedule_status="draft",
        )
        assert attempt is not None
        schedule_id, run_id, attempt_id = run.schedule_id, run.id, attempt.id

    with pytest.raises(RepositoryStateConflictError):
        async with factory.begin() as recovering:
            await ProcessingRecoveryService(
                recovering, configured_guild_id=700, operator_channel_id=704
            ).recover_expired(recovered_at=RECOVERED_AT, batch_size=20)

    async with factory() as verifier:
        schedule = await verifier.get(Schedule, schedule_id)
        persisted_run = await verifier.get(ScheduleRun, run_id)
        persisted_attempt = await verifier.get(DeliveryAttempt, attempt_id)
        assert schedule is not None and schedule.status == "draft" and schedule.version == 1
        assert persisted_run is not None and persisted_run.status == "processing"
        assert persisted_attempt is not None and persisted_attempt.status == "sending"
        assert await verifier.scalar(select(func.count(NotificationLog.id))) == 0

    await _cleanup(test_engine, [schedule_id], [run_id])


async def test_future_lease_is_excluded_and_batch_order_is_stable(
    db_session: AsyncSession,
) -> None:
    first, _, _ = await add_expired(
        db_session, lease_expires_at=RECOVERED_AT - timedelta(minutes=2)
    )
    second, _, _ = await add_expired(
        db_session, lease_expires_at=RECOVERED_AT - timedelta(minutes=1)
    )
    future, _, _ = await add_expired(
        db_session, lease_expires_at=RECOVERED_AT + timedelta(seconds=1)
    )
    recovered = await recovery(db_session).recover_expired(recovered_at=RECOVERED_AT, batch_size=1)
    assert [item.run.id for item in recovered] == [first.id]
    assert second.status == "processing"
    assert future.status == "processing"


async def _seed_committed(engine: AsyncEngine) -> tuple[list[int], list[int]]:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory.begin() as session:
        first, _, _ = await add_expired(session)
        second, _, _ = await add_expired(session)
        return [first.schedule_id, second.schedule_id], [first.id, second.id]


async def _cleanup(engine: AsyncEngine, schedule_ids: list[int], run_ids: list[int]) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            delete(DeliveryAttempt).where(DeliveryAttempt.schedule_run_id.in_(run_ids))
        )
        await connection.execute(delete(ScheduleRun).where(ScheduleRun.id.in_(run_ids)))
        await connection.execute(delete(Schedule).where(Schedule.id.in_(schedule_ids)))


async def test_concurrent_recovery_skips_locked_run_and_rolls_back(
    test_engine: AsyncEngine,
) -> None:
    schedule_ids, run_ids = await _seed_committed(test_engine)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    first_session, second_session = factory(), factory()
    first_tx, second_tx = await first_session.begin(), await second_session.begin()
    try:
        first = await ProcessingRecoveryService(first_session).recover_expired(
            recovered_at=RECOVERED_AT, batch_size=1
        )
        second = await asyncio.wait_for(
            ProcessingRecoveryService(second_session).recover_expired(
                recovered_at=RECOVERED_AT, batch_size=2
            ),
            timeout=1,
        )
        assert len(first) == len(second) == 1
        assert first[0].run.id != second[0].run.id
        async with factory() as observer:
            visible_runs = list(
                (
                    await observer.execute(select(ScheduleRun).where(ScheduleRun.id.in_(run_ids)))
                ).scalars()
            )
            assert all(run.status == "processing" for run in visible_runs)
    finally:
        await first_tx.rollback()
        await second_tx.rollback()
        await first_session.close()
        await second_session.close()

    async with factory() as verifier:
        runs = list(
            (
                await verifier.execute(select(ScheduleRun).where(ScheduleRun.id.in_(run_ids)))
            ).scalars()
        )
        attempts = await verifier.scalar(
            select(func.count())
            .select_from(DeliveryAttempt)
            .where(
                DeliveryAttempt.schedule_run_id.in_(run_ids), DeliveryAttempt.status != "claimed"
            )
        )
        assert all(run.status == "processing" for run in runs)
        assert attempts == 0
    await _cleanup(test_engine, schedule_ids, run_ids)
