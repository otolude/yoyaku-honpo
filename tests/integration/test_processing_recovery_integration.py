import asyncio
import uuid
from datetime import UTC, datetime, timedelta

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
from discord_ai_reminder_bot.infrastructure.database.models import (
    DeliveryAttempt,
    Schedule,
    ScheduleRun,
)

pytestmark = pytest.mark.asyncio
CLAIMED_AT = datetime(2026, 8, 17, 3, 0, tzinfo=UTC)
RECOVERED_AT = CLAIMED_AT + timedelta(minutes=5)


async def add_expired(
    session: AsyncSession,
    *,
    attempt_number: int = 1,
    attempt_status: str | None = "claimed",
    lease_expires_at: datetime = RECOVERED_AT - timedelta(seconds=1),
    attempt_number_override: int | None = None,
    attempt_worker_override: uuid.UUID | None = None,
) -> tuple[ScheduleRun, DeliveryAttempt | None, uuid.UUID]:
    schedule = Schedule(
        public_id=uuid.uuid7(),
        guild_id=700,
        channel_id=701,
        creator_user_id=702,
        schedule_type="once",
        status="active",
        content="recovery",
        next_run_at=RECOVERED_AT + timedelta(days=1),
        version=1,
    )
    session.add(schedule)
    await session.flush()
    worker_id = uuid.uuid7()
    run = ScheduleRun(
        schedule_id=schedule.id,
        scheduled_for=CLAIMED_AT - timedelta(minutes=1),
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
    recovered = await ProcessingRecoveryService(db_session).recover_expired(
        recovered_at=RECOVERED_AT, batch_size=20
    )
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


@pytest.mark.parametrize("attempt_status", ["sending", "unknown"])
async def test_sending_or_unknown_expiry_fails_without_retry(
    db_session: AsyncSession, attempt_status: str
) -> None:
    run, attempt, _ = await add_expired(db_session, attempt_status=attempt_status)
    recovered = await ProcessingRecoveryService(db_session).recover_expired(
        recovered_at=RECOVERED_AT, batch_size=20
    )
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
    recovered = await ProcessingRecoveryService(db_session).recover_expired(
        recovered_at=RECOVERED_AT, batch_size=20
    )
    assert recovered[0].result is RecoveryResult.FAILED_UNKNOWN
    assert run.status == "failed"
    assert run.next_attempt_at is None
    assert run.result_code == RESULT_UNKNOWN
    if attempt is not None:
        assert attempt.status == (kwargs.get("attempt_status") or "claimed")


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
    recovered = await ProcessingRecoveryService(db_session).recover_expired(
        recovered_at=RECOVERED_AT, batch_size=1
    )
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
